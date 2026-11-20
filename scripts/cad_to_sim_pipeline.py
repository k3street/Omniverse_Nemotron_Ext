#!/usr/bin/env python3
"""End-to-end CAD -> SimReady OpenUSD -> Isaac Sim 7.0.0a1 Dual-Physics -> Omniverse Workcell pipeline.

Bridges Stage 0-1 (Parametric B-Rep CAD in FreeCAD / STEP assemblies from Fusion 360 or SolidWorks)
into Omniverse_Nemotron_Ext's SimReady asset pipeline, zero-Kit Isaac Sim 7.0.0a1 dual-engine
verification (ovphysx + ovnewton), governed OpenUSD LIVRPS sublayer workcells, and live
Isaac Assist + ROS 2 Jazzy execution.
"""
from __future__ import annotations

import argparse
import ctypes
import glob
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]

MATERIAL_PRESETS: Dict[str, Dict[str, Any]] = {
    "6061_t6_aluminum": {
        "density_kg_m3": 2700.0,
        "static_friction": 0.45,
        "dynamic_friction": 0.38,
        "restitution": 0.15,
        "diffuse_rgb": (0.76, 0.78, 0.80),
        "metallic": 0.88,
        "roughness": 0.28,
        "finish_variant": "anodized_aluminum",
    },
    "stainless_304": {
        "density_kg_m3": 8000.0,
        "static_friction": 0.50,
        "dynamic_friction": 0.42,
        "restitution": 0.12,
        "diffuse_rgb": (0.68, 0.70, 0.72),
        "metallic": 0.92,
        "roughness": 0.22,
        "finish_variant": "machined_steel",
    },
    "pla": {
        "density_kg_m3": 1240.0,
        "static_friction": 0.52,
        "dynamic_friction": 0.45,
        "restitution": 0.20,
        "diffuse_rgb": (0.18, 0.52, 0.86),
        "metallic": 0.05,
        "roughness": 0.48,
        "finish_variant": "matte_polymer",
    },
    "abs_plastic": {
        "density_kg_m3": 1040.0,
        "static_friction": 0.48,
        "dynamic_friction": 0.40,
        "restitution": 0.22,
        "diffuse_rgb": (0.22, 0.24, 0.27),
        "metallic": 0.04,
        "roughness": 0.52,
        "finish_variant": "matte_polymer",
    },
    "tpu_95a": {
        "density_kg_m3": 1210.0,
        "static_friction": 0.85,
        "dynamic_friction": 0.78,
        "restitution": 0.08,
        "diffuse_rgb": (0.90, 0.42, 0.12),
        "metallic": 0.02,
        "roughness": 0.68,
        "finish_variant": "matte_polymer",
    },
}


def preload_msvc_1444_runtime() -> List[str]:
    """Preload MSVC 14.44+ CRT DLLs on Windows so ovphysx/ovstage avoid older 14.42 CRT warnings."""
    loaded: List[str] = []
    if sys.platform != "win32":
        return loaded
    patterns = [
        r"C:\Program Files (x86)\Microsoft Visual Studio\2022\*\VC\Redist\MSVC\14.44*\x64\Microsoft.VC143.CRT",
        r"C:\Program Files\Microsoft Visual Studio\2022\*\VC\Redist\MSVC\14.44*\x64\Microsoft.VC143.CRT",
        r"H:\isaacsim7\_build\windows-x86_64\release",
    ]
    crt_dirs: List[Path] = []
    for pattern in patterns:
        crt_dirs.extend(sorted((Path(p) for p in glob.glob(pattern) if Path(p).is_dir()), reverse=True))
    if not crt_dirs:
        return loaded
    crt_dir = crt_dirs[0]
    if hasattr(os, "add_dll_directory"):
        try:
            os.add_dll_directory(str(crt_dir))
        except OSError:
            pass
    for dll in ("vcruntime140.dll", "vcruntime140_1.dll", "msvcp140.dll"):
        candidate = crt_dir / dll
        if candidate.is_file():
            try:
                ctypes.CDLL(str(candidate))
                loaded.append(str(candidate))
            except OSError:
                pass
    return loaded


def resolve_freecad_python() -> str:
    """Locate FreeCAD's headless Python interpreter across Windows and Linux."""
    explicit = os.environ.get("FREECAD_PYTHON")
    if explicit and Path(explicit).is_file():
        return explicit
    candidates = [
        r"C:\Program Files\FreeCAD 1.0\bin\python.exe",
        r"C:\Program Files\FreeCAD 1.1\bin\python.exe",
        r"C:\Program Files\FreeCAD\bin\python.exe",
        "/usr/bin/freecadcmd",
        "/usr/local/bin/freecadcmd",
    ]
    for candidate in candidates:
        if Path(candidate).is_file():
            return candidate
    found = shutil.which("freecadcmd")
    if found:
        return found
    raise FileNotFoundError(
        "FreeCAD headless Python not found. Install FreeCAD 1.0+ or set FREECAD_PYTHON."
    )


def resolve_isaacsim7_python() -> str:
    """Locate the Python 3.12 environment containing Isaac Sim 7.0.0a1 modular wheels + OpenUSD."""
    explicit = os.environ.get("ISAACSIM7_PYTHON")
    if explicit and Path(explicit).is_file():
        return explicit
    candidates = [
        r"H:\isaacsim7-venv\Scripts\python.exe",
        r"H:\IsaacSim-ros_workspaces\jazzy_ws\.pixi\envs\default\python.exe",
        str(REPO_ROOT / ".venv" / "Scripts" / "python.exe"),
        str(REPO_ROOT / ".venv" / "bin" / "python"),
    ]
    for candidate in candidates:
        if Path(candidate).is_file():
            return candidate
    return sys.executable


def _resolve_kit_rpc_url() -> str:
    override = os.environ.get("ISAAC_ASSIST_URL")
    if override:
        return override.rstrip("/")
    explicit_port = os.environ.get("KIT_RPC_PORT")
    if explicit_port:
        return f"http://127.0.0.1:{explicit_port}"
    for candidate in (
        Path(tempfile.gettempdir()) / "isaac_assist_rpc_port",
        Path("/tmp/isaac_assist_rpc_port"),
    ):
        if candidate.is_file():
            text = candidate.read_text(encoding="utf-8").strip()
            if text.isdigit():
                return f"http://127.0.0.1:{text}"
    return "http://127.0.0.1:8001"


# ---------------------------------------------------------------------------
# Stage 0: Parametric B-Rep CAD Authoring & STEP Reader (FreeCAD 1.0 headless)
# ---------------------------------------------------------------------------

_FREECAD_WORKER_SCRIPT = r"""
import json
import math
import sys
from pathlib import Path

import FreeCAD
import Mesh
import MeshPart
import Part

cfg = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
mode = cfg["mode"]
out_dir = Path(cfg["output_dir"])
out_dir.mkdir(parents=True, exist_ok=True)
part_id = cfg["part_id"]
density = float(cfg["density_kg_m3"])
linear_deflection = float(cfg.get("linear_deflection_mm", 0.2))
angular_deflection = float(cfg.get("angular_deflection_rad", 0.25))


def build_template(template: str, params: dict):
    if template == "slotted_fixture":
        base_w = float(params.get("base_width_mm", 90.0))
        base_d = float(params.get("base_depth_mm", 90.0))
        base_h = float(params.get("base_height_mm", 14.0))
        boss_od = float(params.get("boss_outer_diameter_mm", 56.0))
        boss_h = float(params.get("boss_height_mm", 34.0))
        bore_d = float(params.get("bore_diameter_mm", 32.0))
        slot_w = float(params.get("keyway_width_mm", 8.0))
        base = Part.makeBox(base_w, base_d, base_h, FreeCAD.Vector(-base_w / 2.0, -base_d / 2.0, 0.0))
        boss = Part.makeCylinder(boss_od / 2.0, boss_h, FreeCAD.Vector(0.0, 0.0, base_h))
        solid = base.fuse(boss)
        bore = Part.makeCylinder(bore_d / 2.0, base_h + boss_h - 6.0, FreeCAD.Vector(0.0, 0.0, 6.0))
        keyway = Part.makeBox(
            slot_w,
            bore_d / 2.0 + 4.5,
            base_h + boss_h - 6.0,
            FreeCAD.Vector(-slot_w / 2.0, 0.0, 6.0),
        )
        solid = solid.cut(bore).cut(keyway)
        for sx in (-1.0, 1.0):
            for sy in (-1.0, 1.0):
                hole = Part.makeCylinder(
                    3.3,
                    base_h + 2.0,
                    FreeCAD.Vector(sx * (base_w / 2.0 - 11.0), sy * (base_d / 2.0 - 11.0), -1.0),
                )
                solid = solid.cut(hole)
        solid = solid.removeSplitter()
        return [("FixtureBody", solid)]

    if template == "machined_shaft":
        shaft_d = float(params.get("shaft_diameter_mm", 30.0))
        shaft_l = float(params.get("shaft_length_mm", 52.0))
        flange_d = float(params.get("flange_diameter_mm", 44.0))
        flange_h = float(params.get("flange_height_mm", 10.0))
        grip_w = float(params.get("grip_flats_width_mm", 26.0))
        grip_h = float(params.get("grip_height_mm", 22.0))
        key_w = float(params.get("key_width_mm", 7.0))
        lower = Part.makeCylinder(shaft_d / 2.0, shaft_l, FreeCAD.Vector(0.0, 0.0, 0.0))
        key_tab = Part.makeBox(
            key_w,
            shaft_d / 2.0 + 3.5,
            shaft_l - 8.0,
            FreeCAD.Vector(-key_w / 2.0, 0.0, 4.0),
        )
        flange = Part.makeCylinder(flange_d / 2.0, flange_h, FreeCAD.Vector(0.0, 0.0, shaft_l))
        top_post = Part.makeBox(
            grip_w,
            grip_w,
            grip_h,
            FreeCAD.Vector(-grip_w / 2.0, -grip_w / 2.0, shaft_l + flange_h),
        )
        solid = lower.fuse(key_tab).fuse(flange).fuse(top_post).removeSplitter()
        return [("MachinedShaft", solid)]

    if template == "parallel_gripper_finger":
        length = float(params.get("finger_length_mm", 68.0))
        width = float(params.get("finger_width_mm", 22.0))
        thick = float(params.get("finger_thickness_mm", 12.0))
        groove_d = float(params.get("v_groove_depth_mm", 3.5))
        base_tab = Part.makeBox(width, thick + 6.0, 18.0, FreeCAD.Vector(-width / 2.0, 0.0, 0.0))
        blade = Part.makeBox(width, thick, length - 18.0, FreeCAD.Vector(-width / 2.0, 0.0, 18.0))
        solid = base_tab.fuse(blade)
        v_cut = Part.makeCylinder(groove_d * 1.8, width + 2.0, FreeCAD.Vector(-width / 2.0 - 1.0, 0.0, length - 16.0), FreeCAD.Vector(1, 0, 0))
        solid = solid.cut(v_cut)
        for z_hole in (6.0, 13.0):
            bolt = Part.makeCylinder(2.2, thick + 10.0, FreeCAD.Vector(0.0, -1.0, z_hole), FreeCAD.Vector(0, 1, 0))
            solid = solid.cut(bolt)
        solid = solid.removeSplitter()
        return [("GripperFinger", solid)]

    if template == "part_tray_bin":
        outer_x = float(params.get("outer_length_mm", 160.0))
        outer_y = float(params.get("outer_width_mm", 120.0))
        outer_z = float(params.get("outer_height_mm", 36.0))
        wall = float(params.get("wall_mm", 4.0))
        outer = Part.makeBox(outer_x, outer_y, outer_z, FreeCAD.Vector(-outer_x / 2.0, -outer_y / 2.0, 0.0))
        cavity = Part.makeBox(
            outer_x - 2.0 * wall,
            outer_y - 2.0 * wall,
            outer_z - wall + 1.0,
            FreeCAD.Vector(-(outer_x - 2.0 * wall) / 2.0, -(outer_y - 2.0 * wall) / 2.0, wall),
        )
        divider = Part.makeBox(
            wall,
            outer_y - 2.0 * wall,
            outer_z - wall - 4.0,
            FreeCAD.Vector(-wall / 2.0, -(outer_y - 2.0 * wall) / 2.0, wall),
        )
        solid = outer.cut(cavity).fuse(divider).removeSplitter()
        return [("PartTrayBin", solid)]

    raise ValueError(f"Unsupported CAD template: {template}")


if mode == "generate":
    named_solids = build_template(cfg["template"], cfg.get("parameters", {}))
    compound = Part.makeCompound([s for _, s in named_solids]) if len(named_solids) > 1 else named_solids[0][1]
    step_path = out_dir / f"{part_id}.step"
    stl_path = out_dir / f"{part_id}.stl"
    compound.exportStep(str(step_path))
    mesh_all = MeshPart.meshFromShape(
        Shape=compound, LinearDeflection=linear_deflection, AngularDeflection=angular_deflection
    )
    mesh_all.write(str(stl_path))
elif mode == "import_cad":
    input_path = Path(cfg["input_cad_path"])
    ext = input_path.suffix.lower()
    step_path = input_path
    stl_path = out_dir / f"{part_id}.stl"
    if ext in {".step", ".stp", ".iges", ".igs", ".brep"}:
        shape = Part.Shape()
        shape.read(str(input_path))
        solids = list(shape.Solids)
        if not solids:
            named_solids = [(part_id, shape)]
        else:
            named_solids = [(f"Part_{idx:02d}", solid) for idx, solid in enumerate(solids)]
        compound = shape
        mesh_all = MeshPart.meshFromShape(
            Shape=compound, LinearDeflection=linear_deflection, AngularDeflection=angular_deflection
        )
        mesh_all.write(str(stl_path))
    elif ext == ".stl":
        m = Mesh.Mesh(str(input_path))
        stl_path = input_path
        verts_m = [[p.x * 1e-3, p.y * 1e-3, p.z * 1e-3] for p in m.Points]
        faces = [list(f.PointIndices) for f in m.Facets]
        vol_m3 = max(abs(float(m.Volume)) * 1e-9, 1e-7)
        bb = m.BoundBox
        com_m = [(bb.XMin + bb.XMax) * 0.5e-3, (bb.YMin + bb.YMax) * 0.5e-3, (bb.ZMin + bb.ZMax) * 0.5e-3]
        mass_kg = vol_m3 * density
        dx, dy, dz = bb.XLength * 1e-3, bb.YLength * 1e-3, bb.ZLength * 1e-3
        inertia = [
            mass_kg * (dy * dy + dz * dz) / 12.0,
            mass_kg * (dx * dx + dz * dz) / 12.0,
            mass_kg * (dx * dx + dy * dy) / 12.0,
        ]
        meta = {
            "part_id": part_id,
            "source_cad_path": str(input_path),
            "step_path": None,
            "stl_path": str(stl_path),
            "volume_m3": vol_m3,
            "mass_kg": mass_kg,
            "center_of_mass_m": com_m,
            "diagonal_inertia_kg_m2": inertia,
            "bounds_min_m": [bb.XMin * 1e-3, bb.YMin * 1e-3, bb.ZMin * 1e-3],
            "bounds_max_m": [bb.XMax * 1e-3, bb.YMax * 1e-3, bb.ZMax * 1e-3],
            "subparts": [{"name": "MeshBody", "vertices_m": verts_m, "faces": faces}],
        }
        Path(cfg["metadata_out"]).write_text(json.dumps(meta), encoding="utf-8")
        sys.exit(0)
    else:
        raise ValueError(f"Unsupported CAD file extension: {ext}")
else:
    raise ValueError(f"Unknown worker mode: {mode}")

total_vol_mm3 = sum(float(s.Volume) for _, s in named_solids)
vol_m3 = max(total_vol_mm3 * 1e-9, 1e-8)
mass_kg = vol_m3 * density

weighted_com = [0.0, 0.0, 0.0]
diag_inertia = [0.0, 0.0, 0.0]
for _, s in named_solids:
    solids_list = list(s.Solids) if hasattr(s, "Solids") and len(s.Solids) > 0 else [s]
    for sub_s in solids_list:
        v = float(sub_s.Volume)
        c = sub_s.CenterOfMass
        weighted_com[0] += v * c.x
        weighted_com[1] += v * c.y
        weighted_com[2] += v * c.z
        mat = sub_s.MatrixOfInertia
        diag_inertia[0] += max(float(mat.A11), 1e-3) * 1e-15 * density
        diag_inertia[1] += max(float(mat.A22), 1e-3) * 1e-15 * density
        diag_inertia[2] += max(float(mat.A33), 1e-3) * 1e-15 * density

if total_vol_mm3 > 0:
    com_m = [
        (weighted_com[0] / total_vol_mm3) * 1e-3,
        (weighted_com[1] / total_vol_mm3) * 1e-3,
        (weighted_com[2] / total_vol_mm3) * 1e-3,
    ]
else:
    com_m = [0.0, 0.0, 0.0]

bb = compound.BoundBox
subparts = []
for sub_name, solid in named_solids:
    mesh = MeshPart.meshFromShape(
        Shape=solid, LinearDeflection=linear_deflection, AngularDeflection=angular_deflection
    )
    verts_m = [[float(p.x) * 1e-3, float(p.y) * 1e-3, float(p.z) * 1e-3] for p in mesh.Points]
    faces = [[int(i) for i in f.PointIndices] for f in mesh.Facets]
    subparts.append(
        {
            "name": sub_name,
            "volume_m3": float(solid.Volume) * 1e-9,
            "mass_kg": float(solid.Volume) * 1e-9 * density,
            "vertices_m": verts_m,
            "faces": faces,
        }
    )

meta = {
    "part_id": part_id,
    "source_cad_path": str(step_path),
    "step_path": str(step_path),
    "stl_path": str(stl_path),
    "volume_m3": vol_m3,
    "mass_kg": mass_kg,
    "center_of_mass_m": com_m,
    "diagonal_inertia_kg_m2": diag_inertia,
    "bounds_min_m": [float(bb.XMin) * 1e-3, float(bb.YMin) * 1e-3, float(bb.ZMin) * 1e-3],
    "bounds_max_m": [float(bb.XMax) * 1e-3, float(bb.YMax) * 1e-3, float(bb.ZMax) * 1e-3],
    "extents_m": [float(bb.XLength) * 1e-3, float(bb.YLength) * 1e-3, float(bb.ZLength) * 1e-3],
    "subparts": subparts,
}
Path(cfg["metadata_out"]).write_text(json.dumps(meta), encoding="utf-8")
"""


def design_parametric_cad(
    part_id: str,
    template: str = "slotted_fixture",
    material: str = "6061_t6_aluminum",
    parameters: Optional[Dict[str, Any]] = None,
    output_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """Generate a parametric B-Rep CAD model (.step + .stl + mass/inertia metadata) via FreeCAD 1.0."""
    if material not in MATERIAL_PRESETS:
        raise ValueError(f"Unknown material '{material}'. Supported: {sorted(MATERIAL_PRESETS)}")
    preset = MATERIAL_PRESETS[material]
    target_dir = Path(output_dir) if output_dir else (REPO_ROOT / "workspace" / "cad_pipeline" / part_id)
    target_dir.mkdir(parents=True, exist_ok=True)
    metadata_out = target_dir / f"{part_id}_cad_metadata.json"

    freecad_py = resolve_freecad_python()
    with tempfile.TemporaryDirectory(prefix="isaac_cad_") as tmp:
        tmp_path = Path(tmp)
        worker_py = tmp_path / "freecad_worker.py"
        cfg_json = tmp_path / "config.json"
        worker_py.write_text(_FREECAD_WORKER_SCRIPT, encoding="utf-8")
        cfg_json.write_text(
            json.dumps(
                {
                    "mode": "generate",
                    "part_id": part_id,
                    "template": template,
                    "parameters": parameters or {},
                    "density_kg_m3": preset["density_kg_m3"],
                    "output_dir": str(target_dir),
                    "metadata_out": str(metadata_out),
                }
            ),
            encoding="utf-8",
        )
        proc = subprocess.run(
            [freecad_py, str(worker_py), str(cfg_json)],
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )
        if proc.returncode != 0 or not metadata_out.is_file():
            raise RuntimeError(
                f"FreeCAD parametric generation failed (exit {proc.returncode}):\n{proc.stderr or proc.stdout}"
            )

    meta = json.loads(metadata_out.read_text(encoding="utf-8"))
    meta["material"] = material
    meta["material_preset"] = preset
    meta["template"] = template
    meta["parameters"] = parameters or {}
    metadata_out.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return {
        "part_id": part_id,
        "template": template,
        "material": material,
        "step_path": meta["step_path"],
        "stl_path": meta["stl_path"],
        "cad_metadata_path": str(metadata_out),
        "volume_cm3": round(meta["volume_m3"] * 1e6, 3),
        "mass_kg": round(meta["mass_kg"], 5),
        "center_of_mass_m": [round(v, 5) for v in meta["center_of_mass_m"]],
        "diagonal_inertia_kg_m2": [round(v, 8) for v in meta["diagonal_inertia_kg_m2"]],
        "extents_m": [round(v, 5) for v in meta.get("extents_m", [])],
        "subpart_count": len(meta.get("subparts", [])),
        "total_triangles": sum(len(sp["faces"]) for sp in meta.get("subparts", [])),
    }


def extract_cad_metadata_from_file(
    cad_path: Path,
    part_id: Optional[str] = None,
    material: str = "6061_t6_aluminum",
    output_dir: Optional[Path] = None,
) -> Path:
    """Extract B-Rep solids, exact mass properties, and watertight meshes from a .step/.iges/.stl file."""
    cad_path = Path(cad_path).resolve()
    if not cad_path.is_file():
        raise FileNotFoundError(f"CAD file not found: {cad_path}")
    if material not in MATERIAL_PRESETS:
        raise ValueError(f"Unknown material '{material}'. Supported: {sorted(MATERIAL_PRESETS)}")
    resolved_id = part_id or cad_path.stem
    target_dir = Path(output_dir) if output_dir else (REPO_ROOT / "workspace" / "cad_pipeline" / resolved_id)
    target_dir.mkdir(parents=True, exist_ok=True)
    metadata_out = target_dir / f"{resolved_id}_cad_metadata.json"

    preset = MATERIAL_PRESETS[material]
    freecad_py = resolve_freecad_python()
    with tempfile.TemporaryDirectory(prefix="isaac_cad_import_") as tmp:
        tmp_path = Path(tmp)
        worker_py = tmp_path / "freecad_worker.py"
        cfg_json = tmp_path / "config.json"
        worker_py.write_text(_FREECAD_WORKER_SCRIPT, encoding="utf-8")
        cfg_json.write_text(
            json.dumps(
                {
                    "mode": "import_cad",
                    "part_id": resolved_id,
                    "input_cad_path": str(cad_path),
                    "density_kg_m3": preset["density_kg_m3"],
                    "output_dir": str(target_dir),
                    "metadata_out": str(metadata_out),
                }
            ),
            encoding="utf-8",
        )
        proc = subprocess.run(
            [freecad_py, str(worker_py), str(cfg_json)],
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )
        if proc.returncode != 0 or not metadata_out.is_file():
            raise RuntimeError(
                f"FreeCAD CAD import failed (exit {proc.returncode}):\n{proc.stderr or proc.stdout}"
            )
    meta = json.loads(metadata_out.read_text(encoding="utf-8"))
    meta["material"] = material
    meta["material_preset"] = preset
    metadata_out.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return metadata_out


# ---------------------------------------------------------------------------
# Stage 1: Layered OpenUSD Authoring (.usdc + .usda + VariantSets + Validation)
# ---------------------------------------------------------------------------

def _author_layered_simready_inprocess(
    metadata_path: Path,
    output_dir: Path,
    default_physics_variant: str = "rigid_convex",
    collision_approximation: str = "convexDecomposition",
) -> Dict[str, Any]:
    preload_msvc_1444_runtime()
    from pxr import Gf, Kind, Sdf, Usd, UsdGeom, UsdPhysics, UsdShade, Vt

    meta = json.loads(Path(metadata_path).read_text(encoding="utf-8"))
    part_id = meta["part_id"]
    material_name = meta.get("material", "6061_t6_aluminum")
    preset = MATERIAL_PRESETS.get(material_name, MATERIAL_PRESETS["6061_t6_aluminum"])

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    geom_layer_path = output_dir / "01_cad_geometry.usdc"
    phys_layer_path = output_dir / "02_simready_physics.usda"
    root_usd_path = output_dir / f"{part_id}_simready.usda"

    for stale in (geom_layer_path, phys_layer_path, root_usd_path):
        if stale.exists():
            stale.unlink()

    # 1. Immutable Binary Crate Geometry Layer (01_cad_geometry.usdc)
    geom_stage = Usd.Stage.CreateNew(str(geom_layer_path))
    UsdGeom.SetStageUpAxis(geom_stage, UsdGeom.Tokens.z)
    UsdGeom.SetStageMetersPerUnit(geom_stage, 1.0)
    asset_xform = UsdGeom.Xform.Define(geom_stage, "/Asset")
    Usd.ModelAPI(asset_xform).SetKind(Kind.Tokens.component)
    geom_stage.SetDefaultPrim(asset_xform.GetPrim())

    visuals_scope = UsdGeom.Scope.Define(geom_stage, "/Asset/Visuals")
    colliders_scope = UsdGeom.Scope.Define(geom_stage, "/Asset/Collisions")
    _ = visuals_scope, colliders_scope

    collider_paths: List[str] = []
    for sp in meta["subparts"]:
        safe_name = "".join(ch if (ch.isalnum() or ch == "_") else "_" for ch in sp["name"])
        points = Vt.Vec3fArray([Gf.Vec3f(float(v[0]), float(v[1]), float(v[2])) for v in sp["vertices_m"]])
        counts = Vt.IntArray([len(f) for f in sp["faces"]])
        indices = Vt.IntArray([int(idx) for f in sp["faces"] for idx in f])

        vis_mesh = UsdGeom.Mesh.Define(geom_stage, f"/Asset/Visuals/{safe_name}")
        vis_mesh.CreatePointsAttr(points)
        vis_mesh.CreateFaceVertexCountsAttr(counts)
        vis_mesh.CreateFaceVertexIndicesAttr(indices)
        vis_mesh.CreateSubdivisionSchemeAttr(UsdGeom.Tokens.none)
        extent = UsdGeom.Boundable.ComputeExtentFromPlugins(vis_mesh, Usd.TimeCode.Default())
        if extent:
            vis_mesh.CreateExtentAttr(extent)

        col_path = f"/Asset/Collisions/{safe_name}_Collider"
        col_mesh = UsdGeom.Mesh.Define(geom_stage, col_path)
        col_mesh.CreatePointsAttr(points)
        col_mesh.CreateFaceVertexCountsAttr(counts)
        col_mesh.CreateFaceVertexIndicesAttr(indices)
        col_mesh.CreateSubdivisionSchemeAttr(UsdGeom.Tokens.none)
        col_mesh.CreatePurposeAttr(UsdGeom.Tokens.guide)
        if extent:
            col_mesh.CreateExtentAttr(extent)
        collider_paths.append(col_path)

    geom_stage.GetRootLayer().Save()

    # 2. Physics & Material Layer (02_simready_physics.usda)
    phys_stage = Usd.Stage.CreateNew(str(phys_layer_path))
    UsdGeom.SetStageUpAxis(phys_stage, UsdGeom.Tokens.z)
    UsdGeom.SetStageMetersPerUnit(phys_stage, 1.0)
    phys_stage.GetRootLayer().subLayerPaths.append("./01_cad_geometry.usdc")
    root_prim = phys_stage.OverridePrim("/Asset")
    phys_stage.SetDefaultPrim(root_prim)

    # PBR + Physics Material
    looks_scope = UsdGeom.Scope.Define(phys_stage, "/Asset/Looks")
    _ = looks_scope
    mat_path = "/Asset/Looks/SimReadyMaterial"
    usd_mat = UsdShade.Material.Define(phys_stage, mat_path)
    shader = UsdShade.Shader.Define(phys_stage, f"{mat_path}/PreviewSurface")
    shader.CreateIdAttr("UsdPreviewSurface")
    rgb = preset["diffuse_rgb"]
    shader.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(*rgb))
    shader.CreateInput("metallic", Sdf.ValueTypeNames.Float).Set(float(preset["metallic"]))
    shader.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(float(preset["roughness"]))
    usd_mat.CreateSurfaceOutput().ConnectToSource(shader.ConnectableAPI(), "surface")

    phys_mat_api = UsdPhysics.MaterialAPI.Apply(usd_mat.GetPrim())
    phys_mat_api.CreateStaticFrictionAttr(float(preset["static_friction"]))
    phys_mat_api.CreateDynamicFrictionAttr(float(preset["dynamic_friction"]))
    phys_mat_api.CreateRestitutionAttr(float(preset["restitution"]))
    phys_mat_api.CreateDensityAttr(float(preset["density_kg_m3"]))

    UsdShade.MaterialBindingAPI.Apply(root_prim).Bind(usd_mat)

    rb_api = UsdPhysics.RigidBodyAPI.Apply(root_prim)
    rb_api.CreateRigidBodyEnabledAttr(True)
    root_prim.AddAppliedSchema("PhysxRigidBodyAPI")

    mass_api = UsdPhysics.MassAPI.Apply(root_prim)
    mass_api.CreateMassAttr(float(meta["mass_kg"]))
    com = meta["center_of_mass_m"]
    mass_api.CreateCenterOfMassAttr(Gf.Vec3f(float(com[0]), float(com[1]), float(com[2])))
    inert = meta["diagonal_inertia_kg_m2"]
    mass_api.CreateDiagonalInertiaAttr(Gf.Vec3f(float(inert[0]), float(inert[1]), float(inert[2])))
    mass_api.CreatePrincipalAxesAttr(Gf.Quatf(1.0, 0.0, 0.0, 0.0))

    for col_path in collider_paths:
        col_over = phys_stage.OverridePrim(col_path)
        col_api = UsdPhysics.CollisionAPI.Apply(col_over)
        col_api.CreateCollisionEnabledAttr(True)
        mesh_col_api = UsdPhysics.MeshCollisionAPI.Apply(col_over)
        mesh_col_api.CreateApproximationAttr(collision_approximation)
        UsdShade.MaterialBindingAPI.Apply(col_over).Bind(
            usd_mat, UsdShade.Tokens.weakerThanDescendants, "physics"
        )

    phys_stage.GetRootLayer().Save()

    # 3. Composed Root Layer with OpenUSD VariantSets (<part_id>_simready.usda)
    comp_stage = Usd.Stage.CreateNew(str(root_usd_path))
    UsdGeom.SetStageUpAxis(comp_stage, UsdGeom.Tokens.z)
    UsdGeom.SetStageMetersPerUnit(comp_stage, 1.0)
    comp_stage.GetRootLayer().subLayerPaths.append("./02_simready_physics.usda")
    comp_root = comp_stage.OverridePrim("/Asset")
    comp_stage.SetDefaultPrim(comp_root)

    # VariantSet 1: PhysicsFidelity
    vset_phys = comp_root.GetVariantSets().AddVariantSet("PhysicsFidelity")
    for variant_name in ("rigid_convex", "sdf_hydroelastic", "static_fixture"):
        vset_phys.AddVariant(variant_name)
    vset_phys.SetVariantSelection("rigid_convex")
    with vset_phys.GetVariantEditContext():
        UsdPhysics.RigidBodyAPI(comp_root).GetKinematicEnabledAttr().Set(False)
        for col_path in collider_paths:
            UsdPhysics.MeshCollisionAPI(comp_stage.OverridePrim(col_path)).GetApproximationAttr().Set(
                collision_approximation
            )

    vset_phys.SetVariantSelection("sdf_hydroelastic")
    with vset_phys.GetVariantEditContext():
        UsdPhysics.RigidBodyAPI(comp_root).GetKinematicEnabledAttr().Set(False)
        for col_path in collider_paths:
            col_over = comp_stage.OverridePrim(col_path)
            UsdPhysics.MeshCollisionAPI(col_over).GetApproximationAttr().Set("sdf")
            col_over.AddAppliedSchema("PhysxSDFMeshCollisionAPI")
            col_over.CreateAttribute("physxSDFMeshCollision:sdfResolution", Sdf.ValueTypeNames.Int).Set(256)

    vset_phys.SetVariantSelection("static_fixture")
    with vset_phys.GetVariantEditContext():
        UsdPhysics.RigidBodyAPI(comp_root).GetKinematicEnabledAttr().Set(True)

    vset_phys.SetVariantSelection(default_physics_variant)

    # VariantSet 2: MaterialFinish
    vset_mat = comp_root.GetVariantSets().AddVariantSet("MaterialFinish")
    finishes = {
        "anodized_aluminum": ((0.76, 0.78, 0.80), 0.88, 0.28),
        "machined_steel": ((0.68, 0.70, 0.72), 0.92, 0.22),
        "matte_polymer": ((0.18, 0.52, 0.86), 0.05, 0.48),
    }
    for fname, (frgb, fmet, frough) in finishes.items():
        vset_mat.AddVariant(fname)
        vset_mat.SetVariantSelection(fname)
        with vset_mat.GetVariantEditContext():
            sh_over = UsdShade.Shader(comp_stage.OverridePrim(f"{mat_path}/PreviewSurface"))
            sh_over.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(*frgb))
            sh_over.CreateInput("metallic", Sdf.ValueTypeNames.Float).Set(fmet)
            sh_over.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(frough)
    vset_mat.SetVariantSelection(preset["finish_variant"])

    comp_stage.GetRootLayer().Save()

    # 4. Run NVIDIA OpenUSD Validation (usd_validation_nvidia robotics ruleset)
    validation_summary: Dict[str, Any] = {"backend": "basic_pxr", "errors": 0, "warnings": 0, "issues": []}
    try:
        from usd_validation_nvidia import CategoryRuleRegistry, ValidationEngine

        engine = ValidationEngine(
            init_rules=False,
            variants=False,
            instance_prototypes=False,
            processes=0,
        )
        registry = CategoryRuleRegistry()
        for rule_name in (
            "StageMetadataChecker",
            "DefaultPrimChecker",
            "LayerSpecChecker",
            "UsdAsciiPerformanceChecker",
            "UnicodeNameChecker",
            "MaterialPathChecker",
            "UsdDanglingMaterialBinding",
            "UsdMaterialBindingApi",
            "RigidBodyChecker",
            "ColliderChecker",
            "MassChecker",
        ):
            rule = registry.find_rule(rule_name)
            if rule is not None:
                engine.enable_rule(rule)
        val_res = engine.validate(str(root_usd_path))
        raw_issues = val_res.issues() if callable(getattr(val_res, "issues", None)) else getattr(val_res, "issues", [])
        errs = [
            str(i)
            for i in raw_issues
            if any(tok in str(getattr(i, "severity", i)).lower() for tok in ("error", "failure"))
        ]
        warns = [str(i) for i in raw_issues if str(i) not in errs]
        validation_summary = {
            "backend": "usd-validation-nvidia",
            "ruleset": "robotics",
            "errors": len(errs),
            "warnings": len(warns),
            "issues": (errs + warns)[:15],
        }
    except Exception as exc:
        # Verify stage composes cleanly with pxr
        verify_stage = Usd.Stage.Open(str(root_usd_path))
        has_default = bool(verify_stage and verify_stage.GetDefaultPrim())
        validation_summary = {
            "backend": "pxr_stage_verify",
            "stage_composed": has_default,
            "errors": 0 if has_default else 1,
            "warnings": 0,
            "note": str(exc),
        }

    report = {
        "part_id": part_id,
        "root_usd_path": str(root_usd_path),
        "geometry_usdc_path": str(geom_layer_path),
        "physics_usda_path": str(phys_layer_path),
        "default_physics_variant": default_physics_variant,
        "available_variants": {
            "PhysicsFidelity": ["rigid_convex", "sdf_hydroelastic", "static_fixture"],
            "MaterialFinish": list(finishes.keys()),
        },
        "mass_kg": round(float(meta["mass_kg"]), 5),
        "extents_m": [round(float(x), 5) for x in meta.get("extents_m", [])],
        "validation": validation_summary,
    }
    report_path = output_dir / f"{part_id}_simready_report.json"
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    report["report_path"] = str(report_path)
    return report


def convert_cad_to_simready_usd(
    cad_or_metadata_path: str | Path,
    part_id: Optional[str] = None,
    material: str = "6061_t6_aluminum",
    default_physics_variant: str = "rigid_convex",
    collision_approximation: str = "convexDecomposition",
    output_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """Convert a .step/.iges/.stl CAD file (or Stage-0 metadata JSON) into a layered SimReady OpenUSD asset."""
    src = Path(cad_or_metadata_path).resolve()
    if src.suffix.lower() == ".json":
        meta_path = src
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        resolved_id = part_id or meta["part_id"]
    else:
        resolved_id = part_id or src.stem
        meta_path = extract_cad_metadata_from_file(
            src, part_id=resolved_id, material=material, output_dir=output_dir
        )

    target_dir = Path(output_dir) if output_dir else meta_path.parent / "simready_usd"
    try:
        import pxr  # noqa: F401

        return _author_layered_simready_inprocess(
            meta_path,
            target_dir,
            default_physics_variant=default_physics_variant,
            collision_approximation=collision_approximation,
        )
    except ImportError:
        py = resolve_isaacsim7_python()
        proc = subprocess.run(
            [
                py,
                str(Path(__file__).resolve()),
                "convert",
                "--input",
                str(meta_path),
                "--output-dir",
                str(target_dir),
                "--physics-variant",
                default_physics_variant,
                "--collision-approx",
                collision_approximation,
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"USD conversion subprocess failed:\n{proc.stderr or proc.stdout}")
        return json.loads(proc.stdout.strip().splitlines()[-1])


# ---------------------------------------------------------------------------
# Stage 2: Zero-Kit Isaac Sim 7.0.0a1 Dual-Engine Verification (ovphysx + newton)
# ---------------------------------------------------------------------------

def _verify_dual_7a_inprocess(
    usd_path: Path,
    drop_height_m: float = 0.20,
    steps: int = 180,
    dt: float = 1.0 / 60.0,
    promote_to_registry: bool = False,
) -> Dict[str, Any]:
    preload_msvc_1444_runtime()
    import isaacsim.physics_engines.ovnewton as ovn
    import isaacsim.physics_engines.ovphysx  # noqa: F401
    from isaacsim.foundation.objects import Stage
    from isaacsim.physics.entities import RigidBodyEntity
    from isaacsim.physics.manager import PhysicsManager
    from isaacsim.physics_engines.ovstage import get_native_handle, setup as ovstage_setup
    from pxr import Gf, Sdf, Usd, UsdGeom, UsdPhysics, UsdUtils

    usd_path = Path(usd_path).resolve()
    if not usd_path.is_file():
        raise FileNotFoundError(f"USD file not found: {usd_path}")

    # Build a self-contained drop-test USDA referencing the SimReady asset with dynamic rigid_convex variant
    with tempfile.TemporaryDirectory(prefix="isaac7a_drop_") as tmp:
        harness_path = Path(tmp) / "drop_harness.usda"
        h_stage = Usd.Stage.CreateNew(str(harness_path))
        UsdGeom.SetStageUpAxis(h_stage, UsdGeom.Tokens.z)
        UsdGeom.SetStageMetersPerUnit(h_stage, 1.0)
        world = UsdGeom.Xform.Define(h_stage, "/World")
        h_stage.SetDefaultPrim(world.GetPrim())

        scene = UsdPhysics.Scene.Define(h_stage, "/World/PhysicsScene")
        scene.CreateGravityDirectionAttr(Gf.Vec3f(0.0, 0.0, -1.0))
        scene.CreateGravityMagnitudeAttr(9.81)

        # Ground box collider (z in [-0.10, 0.0]) so both OvPhysX and Newton collide identically
        ground = UsdGeom.Cube.Define(h_stage, "/World/GroundBox")
        ground.CreateSizeAttr(1.0)
        gx = UsdGeom.Xformable(ground)
        gx.AddTranslateOp().Set(Gf.Vec3d(0.0, 0.0, -0.05))
        gx.AddScaleOp().Set(Gf.Vec3f(4.0, 4.0, 0.10))
        UsdPhysics.CollisionAPI.Apply(ground.GetPrim())

        part_xf = UsdGeom.Xform.Define(h_stage, "/World/TestPart")
        part_prim = part_xf.GetPrim()
        part_prim.GetReferences().AddReference(usd_path.as_posix())
        vsets = part_prim.GetVariantSets()
        if vsets.HasVariantSet("PhysicsFidelity"):
            vsets.GetVariantSet("PhysicsFidelity").SetVariantSelection("rigid_convex")
        UsdGeom.Xformable(part_xf).AddTranslateOp().Set(Gf.Vec3d(0.0, 0.0, float(drop_height_m)))

        # Flatten so both standalone OVStage importers receive an identical self-contained stage string
        flat_layer = h_stage.Flatten()
        flattened_usda = flat_layer.ExportToString()
        flat_stage = Usd.Stage.Open(flat_layer)
        cache_stage_id = int(UsdUtils.StageCache.Get().Insert(flat_stage).ToLongInt())

    results: Dict[str, Any] = {}
    t_start = time.perf_counter()

    sim_id = ovn.register()
    newton_name = ovn.get_registry(sim_id).simulation_name
    ovstage_setup()
    import ovstage as py_ovstage_mod

    ctypes.pythonapi.PyCapsule_New.restype = ctypes.py_object
    ctypes.pythonapi.PyCapsule_New.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_void_p]
    try:
        for engine_label, engine_name in (("ovphysx", "ovphysx"), ("ovnewton", newton_name)):
            t0 = time.perf_counter()
            ov_stage = None
            py_ov = None
            pm = PhysicsManager.get_instance()
            try:
                if not pm.switch_physics_engine(engine_name):
                    raise RuntimeError(f"Physics engine '{engine_name}' unavailable")
                pm.setup(dt=dt)
                if engine_label == "ovphysx":
                    ov_stage = Stage("ovstage").import_stage_from_string(flattened_usda, make_default=False)
                    stage_ptr = ov_stage.get_stage_ptr()
                    stage_id = ov_stage.get_stage_id()
                else:
                    py_ov = py_ovstage_mod.Stage("verify-7a-newton")
                    py_ovstage_mod.population.open_usd_from_string(
                        py_ov,
                        flattened_usda,
                        domains=int(py_ovstage_mod.PopulationDomain.PHYSICS | py_ovstage_mod.PopulationDomain.RENDERING),
                    )
                    stage_ptr = get_native_handle(py_ov)
                    stage_id = cache_stage_id
                if not pm.initialize(stage_ptr, stage_id):
                    raise RuntimeError(f"Failed to initialize '{engine_name}'")
                entity = RigidBodyEntity(engine_name, "/World/TestPart")
                if engine_label == "ovphysx":
                    pos0, _ = entity.get_world_poses()
                    z_initial = float(pos0.numpy()[0, 2])
                    pm.step(steps=steps - 1)
                    pos_prev, _ = entity.get_world_poses()
                    z_prev = float(pos_prev.numpy()[0, 2])
                    pm.step(steps=1)
                    pos_final, _ = entity.get_world_poses()
                    p_np = pos_final.numpy()[0]
                else:
                    n_stage = ovn.get_registry(sim_id).stage
                    z_initial = float(n_stage.state_0.body_q.numpy()[0, 2])
                    pm.step(steps=steps - 1)
                    z_prev = float(n_stage.state_0.body_q.numpy()[0, 2])
                    pm.step(steps=1)
                    p_np = n_stage.state_0.body_q.numpy()[0, :3]
                z_final = float(p_np[2])
                vz_settled = abs(z_final - z_prev) / dt
                lateral_drift = math.hypot(float(p_np[0]), float(p_np[1]))
                results[engine_label] = {
                    "engine": engine_name,
                    "initial_z_m": round(z_initial, 5),
                    "final_z_m": round(z_final, 5),
                    "settled_vz_m_s": round(vz_settled, 5),
                    "lateral_drift_m": round(lateral_drift, 5),
                    "elapsed_s": round(time.perf_counter() - t0, 3),
                    "fell_and_caught": (z_final < z_initial - 0.02) and (z_final > -0.02),
                    "settled": vz_settled < 0.05,
                }
            finally:
                if pm.is_initialized():
                    pm.invalidate()
                if ov_stage is not None:
                    ov_stage.close_stage()
                if py_ov is not None:
                    py_ov.destroy()
    finally:
        ovn.unregister(sim_id)

    z_delta = abs(results["ovphysx"]["final_z_m"] - results["ovnewton"]["final_z_m"])
    passed = (
        results["ovphysx"]["fell_and_caught"]
        and results["ovnewton"]["fell_and_caught"]
        and results["ovphysx"]["settled"]
        and results["ovnewton"]["settled"]
        and z_delta < 0.025
    )

    promoted_dir: Optional[str] = None
    if promote_to_registry and passed:
        part_id = usd_path.stem.replace("_simready", "")
        dest = REPO_ROOT / "workspace" / "asset_library" / part_id
        dest.mkdir(parents=True, exist_ok=True)
        for item in usd_path.parent.iterdir():
            if item.is_file():
                shutil.copy2(item, dest / item.name)
        promoted_dir = str(dest)

    summary = {
        "usd_path": str(usd_path),
        "runtime": "isaacsim-7.0.0a1-zero-kit",
        "steps": steps,
        "dt_s": round(dt, 5),
        "drop_height_m": drop_height_m,
        "engines": results,
        "cross_engine_z_delta_m": round(z_delta, 5),
        "passed": passed,
        "total_elapsed_s": round(time.perf_counter() - t_start, 3),
        "promoted_library_dir": promoted_dir,
    }
    evidence_path = usd_path.parent / f"{usd_path.stem}_7a_dual_verification.json"
    evidence_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    summary["evidence_path"] = str(evidence_path)
    return summary


def verify_asset_dual_7a(
    usd_path: str | Path,
    drop_height_m: float = 0.20,
    steps: int = 180,
    promote_to_registry: bool = False,
) -> Dict[str, Any]:
    """Run zero-Kit Isaac Sim 7.0.0a1 dual-engine (ovphysx + ovnewton) verification in a clean process."""
    py = resolve_isaacsim7_python()
    cmd = [
        py,
        str(Path(__file__).resolve()),
        "verify7a",
        "--usd",
        str(Path(usd_path).resolve()),
        "--drop-height",
        str(drop_height_m),
        "--steps",
        str(steps),
    ]
    if promote_to_registry:
        cmd.append("--promote")
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=180)
    if proc.returncode != 0:
        raise RuntimeError(f"7.0.0a1 dual-engine verification failed:\n{proc.stderr or proc.stdout}")
    lines = [ln.strip() for ln in proc.stdout.splitlines() if ln.strip().startswith("{")]
    if not lines:
        raise RuntimeError(f"Unexpected verifier output:\n{proc.stdout}\n{proc.stderr}")
    return json.loads(lines[-1])


# ---------------------------------------------------------------------------
# Stage 3 & 4: Governed OpenUSD Workcell Composition + Live Kit / ROS 2 Deploy
# ---------------------------------------------------------------------------

def _compose_workcell_inprocess(
    workcell_id: str,
    fixture_usd_path: Path,
    workpiece_usd_path: Path,
    output_dir: Path,
    gsplat_backdrop_usd: Optional[str] = None,
) -> Dict[str, Any]:
    preload_msvc_1444_runtime()
    from pxr import Gf, Sdf, Usd, UsdGeom, UsdLux, UsdPhysics, UsdShade

    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    env_layer = output_dir / "01_environment.usda"
    cad_layer = output_dir / "02_cad_assets.usda"
    patch_layer = output_dir / "03_agent_governance_patch.usda"
    root_layer = output_dir / f"{workcell_id}_workcell.usda"

    for f in (env_layer, cad_layer, patch_layer, root_layer):
        if f.exists():
            f.unlink()

    # Layer 1: Environment + Lighting + Workbench Table (+ optional 3DGS scan backdrop)
    s_env = Usd.Stage.CreateNew(str(env_layer))
    UsdGeom.SetStageUpAxis(s_env, UsdGeom.Tokens.z)
    UsdGeom.SetStageMetersPerUnit(s_env, 1.0)
    world = UsdGeom.Xform.Define(s_env, "/World")
    s_env.SetDefaultPrim(world.GetPrim())

    phys = UsdPhysics.Scene.Define(s_env, "/World/PhysicsScene")
    phys.CreateGravityDirectionAttr(Gf.Vec3f(0.0, 0.0, -1.0))
    phys.CreateGravityMagnitudeAttr(9.81)

    sun = UsdLux.DistantLight.Define(s_env, "/World/Lights/KeyLight")
    sun.CreateIntensityAttr(2500.0)
    sun.CreateAngleAttr(1.0)
    UsdGeom.Xformable(sun).AddRotateXYZOp().Set(Gf.Vec3f(-42.0, 28.0, 0.0))

    dome = UsdLux.DomeLight.Define(s_env, "/World/Lights/DomeLight")
    dome.CreateIntensityAttr(650.0)

    floor = UsdGeom.Cube.Define(s_env, "/World/Environment/Floor")
    floor.CreateSizeAttr(1.0)
    fxf = UsdGeom.Xformable(floor)
    fxf.AddTranslateOp().Set(Gf.Vec3d(0.0, 0.0, -0.025))
    fxf.AddScaleOp().Set(Gf.Vec3f(6.0, 6.0, 0.05))
    UsdPhysics.CollisionAPI.Apply(floor.GetPrim())

    # Industrial Workbench Pedestal (top at z = 0.75 m)
    table = UsdGeom.Cube.Define(s_env, "/World/Environment/Workbench")
    table.CreateSizeAttr(1.0)
    txf = UsdGeom.Xformable(table)
    txf.AddTranslateOp().Set(Gf.Vec3d(0.45, 0.0, 0.375))
    txf.AddScaleOp().Set(Gf.Vec3f(0.90, 1.10, 0.75))
    UsdPhysics.CollisionAPI.Apply(table.GetPrim())

    tmat = UsdShade.Material.Define(s_env, "/World/Environment/Looks/WorkbenchMat")
    tsh = UsdShade.Shader.Define(s_env, "/World/Environment/Looks/WorkbenchMat/Shader")
    tsh.CreateIdAttr("UsdPreviewSurface")
    tsh.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(0.19, 0.21, 0.24))
    tsh.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(0.6)
    tsh.CreateInput("metallic", Sdf.ValueTypeNames.Float).Set(0.2)
    tmat.CreateSurfaceOutput().ConnectToSource(tsh.ConnectableAPI(), "surface")
    UsdShade.MaterialBindingAPI.Apply(table.GetPrim()).Bind(tmat)

    if gsplat_backdrop_usd and Path(gsplat_backdrop_usd).is_file():
        gs_xf = UsdGeom.Xform.Define(s_env, "/World/Environment/GSplatBackdrop")
        gs_xf.GetPrim().GetReferences().AddReference(Path(gsplat_backdrop_usd).resolve().as_posix())

    s_env.GetRootLayer().Save()

    # Layer 2: SimReady CAD Assembly Assets (Fixture + Machined Workpiece)
    s_cad = Usd.Stage.CreateNew(str(cad_layer))
    UsdGeom.SetStageUpAxis(s_cad, UsdGeom.Tokens.z)
    UsdGeom.SetStageMetersPerUnit(s_cad, 1.0)
    s_cad.SetDefaultPrim(UsdGeom.Xform.Define(s_cad, "/World").GetPrim())

    fix_xf = UsdGeom.Xform.Define(s_cad, "/World/CADWorkcell/SlottedFixture")
    fix_prim = fix_xf.GetPrim()
    fix_prim.GetReferences().AddReference(Path(fixture_usd_path).resolve().as_posix())
    if fix_prim.GetVariantSets().HasVariantSet("PhysicsFidelity"):
        fix_prim.GetVariantSets().GetVariantSet("PhysicsFidelity").SetVariantSelection("static_fixture")
    UsdGeom.Xformable(fix_xf).AddTranslateOp().Set(Gf.Vec3d(0.45, -0.10, 0.7505))

    wp_xf = UsdGeom.Xform.Define(s_cad, "/World/CADWorkcell/MachinedShaft")
    wp_prim = wp_xf.GetPrim()
    wp_prim.GetReferences().AddReference(Path(workpiece_usd_path).resolve().as_posix())
    if wp_prim.GetVariantSets().HasVariantSet("PhysicsFidelity"):
        wp_prim.GetVariantSets().GetVariantSet("PhysicsFidelity").SetVariantSelection("rigid_convex")
    if wp_prim.GetVariantSets().HasVariantSet("MaterialFinish"):
        wp_prim.GetVariantSets().GetVariantSet("MaterialFinish").SetVariantSelection("matte_polymer")
    # Place workpiece poised slightly above the workbench staging zone so physics settles it cleanly
    UsdGeom.Xformable(wp_xf).AddTranslateOp().Set(Gf.Vec3d(0.45, 0.14, 0.79))

    s_cad.GetRootLayer().Save()

    # Layer 3: Mutable Agent Governance Patch Sublayer (zero-ms rollback via sublayer mute/remove)
    s_patch = Usd.Stage.CreateNew(str(patch_layer))
    UsdGeom.SetStageUpAxis(s_patch, UsdGeom.Tokens.z)
    UsdGeom.SetStageMetersPerUnit(s_patch, 1.0)
    patch_world = s_patch.OverridePrim("/World")
    patch_world.SetCustomDataByKey("isaac_assist_governance_layer", True)
    patch_world.SetCustomDataByKey("workcell_id", workcell_id)
    s_patch.SetDefaultPrim(patch_world)
    s_patch.GetRootLayer().Save()

    # Composed Root Stage (LIVRPS sublayer stack: strongest -> weakest)
    s_root = Usd.Stage.CreateNew(str(root_layer))
    UsdGeom.SetStageUpAxis(s_root, UsdGeom.Tokens.z)
    UsdGeom.SetStageMetersPerUnit(s_root, 1.0)
    s_root.GetRootLayer().subLayerPaths.append("./03_agent_governance_patch.usda")
    s_root.GetRootLayer().subLayerPaths.append("./02_cad_assets.usda")
    s_root.GetRootLayer().subLayerPaths.append("./01_environment.usda")
    s_root.SetDefaultPrim(s_root.OverridePrim("/World"))
    s_root.GetRootLayer().Save()

    return {
        "workcell_id": workcell_id,
        "workcell_usd_path": str(root_layer),
        "sublayers": [
            str(patch_layer),
            str(cad_layer),
            str(env_layer),
        ],
        "tracked_prims": [
            "/World/CADWorkcell/SlottedFixture",
            "/World/CADWorkcell/MachinedShaft",
        ],
    }


def compose_omniverse_workcell_stage(
    workcell_id: str,
    fixture_usd_path: str | Path,
    workpiece_usd_path: str | Path,
    output_dir: Optional[Path] = None,
    gsplat_backdrop_usd: Optional[str] = None,
    deploy_live: bool = False,
) -> Dict[str, Any]:
    """Compose a multi-sublayer OpenUSD CAD workcell and optionally deploy it to live Isaac Sim + ROS 2."""
    target_dir = Path(output_dir) if output_dir else (REPO_ROOT / "workspace" / "cad_workcells" / workcell_id)
    try:
        import pxr  # noqa: F401

        composed = _compose_workcell_inprocess(
            workcell_id,
            Path(fixture_usd_path),
            Path(workpiece_usd_path),
            target_dir,
            gsplat_backdrop_usd=gsplat_backdrop_usd,
        )
    except ImportError:
        py = resolve_isaacsim7_python()
        cmd = [
            py,
            str(Path(__file__).resolve()),
            "compose",
            "--workcell-id",
            workcell_id,
            "--fixture-usd",
            str(Path(fixture_usd_path).resolve()),
            "--workpiece-usd",
            str(Path(workpiece_usd_path).resolve()),
            "--output-dir",
            str(target_dir),
        ]
        if gsplat_backdrop_usd:
            cmd.extend(["--gsplat-usd", gsplat_backdrop_usd])
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=120)
        if proc.returncode != 0:
            raise RuntimeError(f"Workcell composition failed:\n{proc.stderr or proc.stdout}")
        composed = json.loads(proc.stdout.strip().splitlines()[-1])

    if deploy_live:
        composed["live_deployment"] = deploy_workcell_to_live_kit(composed["workcell_usd_path"])
    return composed


def deploy_workcell_to_live_kit(workcell_usd_path: str | Path) -> Dict[str, Any]:
    """Open the composed workcell in the running Isaac Sim Kit RPC instance and attach a ROS 2 /clock + /tf graph."""
    rpc_url = _resolve_kit_rpc_url()
    usd_posix = Path(workcell_usd_path).resolve().as_posix()

    kit_code = f"""
import json
import omni.usd
import omni.timeline
import omni.graph.core as og
from pxr import Sdf

ctx = omni.usd.get_context()
ctx.open_stage("{usd_posix}")
stage = ctx.get_stage()

# Build ROS 2 Jazzy ActionGraph publishing /clock and /tf for the CAD workcell prims
keys = og.Controller.Keys
graph_path = "/World/ROS2WorkcellGraph"
if not stage.GetPrimAtPath(graph_path).IsValid():
    (graph, nodes, _, _) = og.Controller.edit(
        {{"graph_path": graph_path, "evaluator_name": "execution"}},
        {{
            keys.CREATE_NODES: [
                ("OnPlaybackTick", "omni.graph.action.OnPlaybackTick"),
                ("ReadSimTime", "isaacsim.core.nodes.IsaacReadSimulationTime"),
                ("ROS2Context", "isaacsim.ros2.bridge.ROS2Context"),
                ("PublishClock", "isaacsim.ros2.bridge.ROS2PublishClock"),
                ("PublishTF", "isaacsim.ros2.bridge.ROS2PublishTransformTree"),
            ],
            keys.CONNECT: [
                ("OnPlaybackTick.outputs:tick", "PublishClock.inputs:execIn"),
                ("OnPlaybackTick.outputs:tick", "PublishTF.inputs:execIn"),
                ("ROS2Context.outputs:context", "PublishClock.inputs:context"),
                ("ROS2Context.outputs:context", "PublishTF.inputs:context"),
                ("ReadSimTime.outputs:simulationTime", "PublishClock.inputs:timeStamp"),
                ("ReadSimTime.outputs:simulationTime", "PublishTF.inputs:timeStamp"),
            ],
        }},
    )
    tf_prim = stage.GetPrimAtPath(graph_path + "/PublishTF")
    if tf_prim.IsValid():
        rel = tf_prim.GetRelationship("inputs:targetPrims")
        if not rel:
            rel = tf_prim.CreateRelationship("inputs:targetPrims")
        rel.SetTargets([
            Sdf.Path("/World/CADWorkcell/SlottedFixture"),
            Sdf.Path("/World/CADWorkcell/MachinedShaft"),
        ])

omni.timeline.get_timeline_interface().play()
print(json.dumps({{
    "stage_url": ctx.get_stage_url(),
    "ros2_graph": graph_path,
    "playing": omni.timeline.get_timeline_interface().is_playing(),
}}))
"""
    req = urllib.request.Request(
        f"{rpc_url}/exec_sync",
        data=json.dumps({"code": kit_code, "timeout": 45}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=50) as resp:
        exec_res = json.loads(resp.read().decode("utf-8"))

    # Position the viewport camera cleanly onto the workbench CAD assembly
    cam_req = urllib.request.Request(
        f"{rpc_url}/set_camera_pose",
        data=json.dumps({"eye": [0.95, 0.62, 1.12], "target": [0.45, 0.02, 0.78]}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(cam_req, timeout=10) as c_resp:
            cam_res = json.loads(c_resp.read().decode("utf-8"))
    except Exception as exc:
        cam_res = {"warning": str(exc)}

    play_req = urllib.request.Request(
        f"{rpc_url}/sim_control",
        data=json.dumps({"action": "play"}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(play_req, timeout=10) as p_resp:
            play_res = json.loads(p_resp.read().decode("utf-8"))
    except Exception as exc:
        play_res = {"warning": str(exc)}

    return {"kit_rpc_url": rpc_url, "exec_result": exec_res, "camera": cam_res, "play": play_res}


# ---------------------------------------------------------------------------
# Stage 5: Cross-DCC Blender 5.2.1 LTS + ovrtx / OVPhysX Sidecar Verification
# ---------------------------------------------------------------------------

def resolve_blender_ovrtx_runtime() -> Dict[str, Optional[str]]:
    """Locate Blender 5.2+ executable and optional isolated ovrtx settings profile."""
    explicit_blender = os.environ.get("BLENDER_OVRTX_EXE")
    blender_candidates = [
        c
        for c in (
            explicit_blender,
            r"H:\blender-5.2.1\blender.exe",
            r"C:\Program Files\Blender Foundation\Blender 5.2\blender.exe",
            "/opt/blender-5.2.1/blender",
            shutil.which("blender"),
        )
        if c
    ]
    blender_exe = next((str(Path(c)) for c in blender_candidates if Path(c).is_file()), None)
    if not blender_exe:
        raise FileNotFoundError(
            "Blender 5.2+ executable not found. Install Blender 5.2.1 LTS (e.g. H:\\blender-5.2.1\\blender.exe) "
            "or set BLENDER_OVRTX_EXE."
        )

    explicit_profile = os.environ.get("BLENDER_OVRTX_PROFILE")
    profile_candidates = [
        p
        for p in (
            explicit_profile,
            r"H:\ov-blender-settings",
            "/opt/ov-blender-settings",
        )
        if p
    ]
    profile_dir: Optional[str] = None
    for cand in profile_candidates:
        root = Path(cand)
        if (root / "ovrtx-sdk" / "ready.json").is_file() or (root / "ovrtx-sdk" / "native-root.json").is_file():
            profile_dir = str(root)
            break

    launcher_candidates = [
        os.environ.get("BLENDER_OVRTX_LAUNCH"),
        r"H:\ov-blender-example\launch_blender_ovrtx.bat",
        r"H:\ov-blender-example\launch.bat",
        "/opt/ov-blender-example/launch.sh",
    ]
    launcher = next((str(Path(c)) for c in launcher_candidates if c and Path(c).is_file()), None)
    return {
        "blender_exe": blender_exe,
        "profile_dir": profile_dir,
        "launcher": launcher,
    }


def _build_clean_blender_env(profile_dir: Optional[str]) -> Dict[str, str]:
    """Construct a clean environment for Blender + ovrtx without parent venv/Kit contamination."""
    env = dict(os.environ)
    for key in tuple(env):
        if key.startswith(("OV_BLENDER", "OVRTX_", "OVPHYSX_", "OVSTREAM_", "OVSTAGE_", "OMNI_")) or key in (
            "PYTHONHOME",
            "PYTHONPATH",
            "VIRTUAL_ENV",
            "PXR_PLUGINPATH_NAME",
        ):
            env.pop(key, None)
        elif profile_dir is None and key.startswith("BLENDER_USER_"):
            env.pop(key, None)

    if profile_dir:
        prof = Path(profile_dir)
        for env_key, sub in (
            ("BLENDER_USER_CONFIG", "config"),
            ("BLENDER_USER_SCRIPTS", "scripts"),
            ("BLENDER_USER_EXTENSIONS", "extensions"),
            ("BLENDER_USER_DATAFILES", "datafiles"),
        ):
            target = prof / sub
            target.mkdir(parents=True, exist_ok=True)
            env[env_key] = str(target)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


_BLENDER_OVRTX_WORKER_SCRIPT = r"""
from __future__ import annotations

import json
import math
from pathlib import Path
import shutil
import sys
import tempfile
import time
import traceback

import bpy
import mathutils
from pxr import Gf, Usd, UsdPhysics, UsdShade

MODULE = "bl_ext.user_default.ovrtx_blender_example"
ENGINE = "OVRTX_EXAMPLE"


def _user_args() -> list[str]:
    return sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []


def aim(obj: bpy.types.Object, target: tuple[float, float, float]) -> None:
    direction = mathutils.Vector(target) - obj.location
    obj.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()


def read_usd_simready_specs(usd_path: Path) -> dict:
    stage = Usd.Stage.Open(str(usd_path))
    if stage is None:
        raise RuntimeError(f"Failed to open USD stage: {usd_path}")
    default_prim = stage.GetDefaultPrim()
    if not default_prim or not default_prim.IsValid():
        for p in stage.GetPseudoRoot().GetChildren():
            if p.IsValid():
                default_prim = p
                break
    if not default_prim or not default_prim.IsValid():
        raise RuntimeError(f"No valid root prim in {usd_path}")

    mass_api = UsdPhysics.MassAPI(default_prim)
    mass = float(mass_api.GetMassAttr().Get() or 0.1)
    com_gf = mass_api.GetCenterOfMassAttr().Get() or Gf.Vec3f(0.0, 0.0, 0.0)
    inertia_gf = mass_api.GetDiagonalInertiaAttr().Get() or Gf.Vec3f(1e-4, 1e-4, 1e-4)
    axes_attr = mass_api.GetPrincipalAxesAttr()
    axes_gf = axes_attr.Get() if axes_attr and axes_attr.HasAuthoredValue() else None
    if axes_gf is not None:
        imag = axes_gf.GetImaginary()
        q = (float(axes_gf.GetReal()), float(imag[0]), float(imag[1]), float(imag[2]))
        norm = math.sqrt(sum(v * v for v in q))
        principal_axes = tuple(v / norm for v in q) if norm > 1e-6 else (1.0, 0.0, 0.0, 0.0)
    else:
        principal_axes = (1.0, 0.0, 0.0, 0.0)

    density = 1000.0
    static_friction = 0.5
    dynamic_friction = 0.4
    restitution = 0.1
    diffuse = (0.72, 0.75, 0.78)
    metallic = 0.2
    roughness = 0.35

    for prim in stage.Traverse():
        if prim.HasAPI(UsdPhysics.MaterialAPI):
            mat_api = UsdPhysics.MaterialAPI(prim)
            if mat_api.GetDensityAttr().HasAuthoredValue():
                density = float(mat_api.GetDensityAttr().Get())
            if mat_api.GetStaticFrictionAttr().HasAuthoredValue():
                static_friction = float(mat_api.GetStaticFrictionAttr().Get())
            if mat_api.GetDynamicFrictionAttr().HasAuthoredValue():
                dynamic_friction = float(mat_api.GetDynamicFrictionAttr().Get())
            if mat_api.GetRestitutionAttr().HasAuthoredValue():
                restitution = float(mat_api.GetRestitutionAttr().Get())
        if prim.IsA(UsdShade.Shader):
            shader = UsdShade.Shader(prim)
            if shader.GetIdAttr().Get() == "UsdPreviewSurface":
                c_in = shader.GetInput("diffuseColor")
                m_in = shader.GetInput("metallic")
                r_in = shader.GetInput("roughness")
                if c_in and c_in.Get() is not None:
                    c = c_in.Get()
                    diffuse = (float(c[0]), float(c[1]), float(c[2]))
                if m_in and m_in.Get() is not None:
                    metallic = float(m_in.Get())
                if r_in and r_in.Get() is not None:
                    roughness = float(r_in.Get())

    return {
        "usd_path": str(usd_path),
        "mass_kg": mass if mass > 0.0 else 0.1,
        "center_of_mass": (float(com_gf[0]), float(com_gf[1]), float(com_gf[2])),
        "diagonal_inertia": (float(inertia_gf[0]), float(inertia_gf[1]), float(inertia_gf[2])),
        "principal_axes": principal_axes,
        "density": density if density > 0.0 else 1000.0,
        "static_friction": static_friction,
        "dynamic_friction": dynamic_friction,
        "restitution": restitution,
        "diffuse_rgb": diffuse,
        "metallic": metallic,
        "roughness": roughness,
    }


def make_principled_material(
    name: str,
    *,
    color: tuple[float, float, float],
    roughness: float,
    metallic: float = 0.0,
    texture: bpy.types.Image | None = None,
) -> bpy.types.Material:
    mat = bpy.data.materials.new(name)
    if mat.node_tree is None:
        mat.use_nodes = True
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    bsdf = next(n for n in nodes if n.type == "BSDF_PRINCIPLED")
    bsdf.inputs["Base Color"].default_value = (*color, 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    if texture is not None:
        tex_node = nodes.new("ShaderNodeTexImage")
        tex_node.image = texture
        links.new(tex_node.outputs["Color"], bsdf.inputs["Base Color"])
    return mat


def create_workbench_grid_texture(path: Path, size: int = 256, cells: int = 16) -> bpy.types.Image:
    path.parent.mkdir(parents=True, exist_ok=True)
    image = bpy.data.images.new("nemotron_workbench_grid", size, size, alpha=False)
    step = size // cells
    pixels = []
    for y in range(size):
        for x in range(size):
            border = (x % step == 0) or (y % step == 0)
            checker = ((x // step) + (y // step)) % 2 == 0
            if border:
                rgba = (0.26, 0.30, 0.36, 1.0)
            elif checker:
                rgba = (0.14, 0.16, 0.19, 1.0)
            else:
                rgba = (0.11, 0.13, 0.16, 1.0)
            pixels.extend(rgba)
    image.pixels.foreach_set(pixels)
    image.filepath_raw = str(path)
    image.file_format = "PNG"
    image.save()
    image.source = "FILE"
    image.reload()
    return image


def ensure_collection(parent: bpy.types.Collection, name: str) -> bpy.types.Collection:
    existing = parent.children.get(name)
    if existing is not None:
        return existing
    col = bpy.data.collections.new(name)
    parent.children.link(col)
    return col


def move_exclusively_to_collection(obj: bpy.types.Object, target: bpy.types.Collection) -> None:
    for col in list(obj.users_collection):
        col.objects.unlink(obj)
    target.objects.link(obj)


def import_and_convert_asset(
    scene: bpy.types.Scene,
    asset_cfg: dict,
    workcell_col: bpy.types.Collection,
) -> tuple[bpy.types.Object, dict]:
    usd_path = Path(asset_cfg["usd_path"])
    body_name = str(asset_cfg["name"])
    loc = tuple(float(v) for v in asset_cfg.get("location", [0.0, 0.0, 0.05]))
    rot_deg = tuple(float(v) for v in asset_cfg.get("rotation_euler_deg", [0.0, 0.0, 0.0]))
    role = str(asset_cfg.get("role", "DYNAMIC")).upper()

    specs = read_usd_simready_specs(usd_path)
    before_objs = set(bpy.data.objects)
    res = bpy.ops.wm.usd_import(
        filepath=str(usd_path),
        import_guide=False,
        import_proxy=False,
        import_render=True,
    )
    if "FINISHED" not in res:
        raise RuntimeError(f"USD import failed for {usd_path}: {res}")

    new_objs = [o for o in bpy.data.objects if o not in before_objs]
    meshes = [o for o in new_objs if o.type == "MESH"]
    if not meshes:
        raise RuntimeError(f"No render mesh found in {usd_path}")

    if len(meshes) > 1:
        bpy.ops.object.select_all(action="DESELECT")
        for m in meshes:
            m.select_set(True)
        bpy.context.view_layer.objects.active = meshes[0]
        bpy.ops.object.parent_clear(type="CLEAR_KEEP_TRANSFORM")
        bpy.ops.object.join()
        mesh_obj = bpy.context.active_object
    else:
        mesh_obj = meshes[0]
        bpy.ops.object.select_all(action="DESELECT")
        mesh_obj.select_set(True)
        bpy.context.view_layer.objects.active = mesh_obj
        bpy.ops.object.parent_clear(type="CLEAR_KEEP_TRANSFORM")

    for other in list(new_objs):
        if other is not mesh_obj and other.name in bpy.data.objects:
            bpy.data.objects.remove(other, do_unlink=True)

    mesh_obj.name = body_name
    mesh_obj.data.name = f"{body_name}Mesh"
    mesh_obj.location = loc
    mesh_obj.rotation_euler = tuple(math.radians(d) for d in rot_deg)
    bpy.context.view_layer.update()

    mat = make_principled_material(
        f"{body_name}_SimReadyMat",
        color=specs["diffuse_rgb"],
        roughness=specs["roughness"],
        metallic=specs["metallic"],
    )
    mesh_obj.data.materials.clear()
    mesh_obj.data.materials.append(mat)

    export_col = ensure_collection(scene.collection, "Export")
    geom_col = ensure_collection(export_col, "Geometry")
    ref_col = ensure_collection(export_col, "ReferencePrims")
    ensure_collection(export_col, "Colliders")

    ref_empty = bpy.data.objects.new(f"{body_name}_RefPrim", None)
    ref_empty.location = loc
    ref_empty.rotation_euler = mesh_obj.rotation_euler
    ref_col.objects.link(ref_empty)
    move_exclusively_to_collection(mesh_obj, geom_col)
    bpy.context.view_layer.update()

    child_of = mesh_obj.constraints.new(type="CHILD_OF")
    child_of.target = ref_empty
    child_of.inverse_matrix = ref_empty.matrix_world.inverted()

    mesh_obj["pxr:usd:physics_mass"] = float(specs["mass_kg"])
    mesh_obj["pxr:usd:physics_centerofmass"] = list(specs["center_of_mass"])
    mesh_obj["pxr:usd:physics_inertia"] = list(specs["diagonal_inertia"])
    mesh_obj["pxr:physics:principalAxes"] = list(specs["principal_axes"])

    mat["pxr:usd:physics_density"] = float(specs["density"])
    mat["pxr:usd:physics_dynamicFriction"] = float(specs["dynamic_friction"])
    mat["pxr:usd:physics_staticFriction"] = float(specs["static_friction"])
    mat["pxr:usd:physics_restitution"] = float(specs["restitution"])

    bpy.ops.object.select_all(action="DESELECT")
    mesh_obj.select_set(True)
    bpy.context.view_layer.objects.active = mesh_obj
    bpy.context.view_layer.update()

    conv_res = bpy.ops.ov.physics_convert_simready()
    if "FINISHED" not in conv_res or mesh_obj.ov.physics.simready_version != 1:
        raise RuntimeError(f"ov.physics_convert_simready failed on {body_name}: {conv_res}")

    child_of.mute = True
    mesh_obj.location = loc
    mesh_obj.rotation_euler = tuple(math.radians(d) for d in rot_deg)
    mesh_obj.ov.physics.rigid_body.role = role
    move_exclusively_to_collection(mesh_obj, workcell_col)
    bpy.data.objects.remove(ref_empty, do_unlink=True)
    bpy.context.view_layer.update()

    specs["name"] = body_name
    specs["role"] = role
    specs["initial_location"] = list(loc)
    specs["converted_simready_version"] = int(mesh_obj.ov.physics.simready_version)
    specs["collider_shape"] = str(mesh_obj.ov.physics.colliders[0].shape)
    return mesh_obj, specs


def run_ovphysx_bake(scene: bpy.types.Scene, frames: int, substeps: int) -> dict:
    from bl_ext.user_default.ovrtx_blender_example.ovrtx_blender_example.physics_bake_capture import (
        BakeCaptureSession,
        BakeCaptureSettings,
    )
    from bl_ext.user_default.ovrtx_blender_example.ovrtx_blender_example.physics_bake_conversion import (
        convert_capture,
    )
    from bl_ext.user_default.ovrtx_blender_example.ovrtx_blender_example.physics_bake_actions import (
        ActionBuilder,
        Activation,
    )

    scene.frame_start = 1
    scene.frame_end = frames
    scene.frame_set(1)
    bake_dir = Path(tempfile.mkdtemp(prefix="isaac-assist-ovphysx-"))
    t0 = time.perf_counter()
    session = None
    try:
        settings = BakeCaptureSettings(
            initial_frame=1,
            start_frame=1,
            end_frame=frames,
            fps=scene.render.fps,
            fps_base=scene.render.fps_base,
            interval=1,
            substeps=substeps,
        )
        session = BakeCaptureSession(scene, settings, bake_dir / "capture")
        while not session.poll():
            time.sleep(0.01)
        artifact = session.artifact
        session.close()
        session = None

        converted = convert_capture(scene, artifact, bake_dir / "converted")
        builder = ActionBuilder(scene, converted, name="IsaacAssist_OVPhysX")
        while not builder.step():
            time.sleep(0.005)

        activation = Activation(scene)
        deadline = time.monotonic() + 30.0
        while not activation.step():
            if time.monotonic() > deadline:
                activation.cancel()
                raise RuntimeError("Timed out activating OVPhysX baked playback")
            time.sleep(0.01)

        return {
            "frames_simulated": frames,
            "substeps": substeps,
            "simulation_wall_s": round(time.perf_counter() - t0, 3),
            "bake_active": bool(scene.ov_physics_bake.active),
        }
    finally:
        if session is not None:
            try:
                session.cancel()
            except Exception:
                pass
            session.close()
        shutil.rmtree(bake_dir, ignore_errors=True)


def main() -> None:
    cfg_path = Path(_user_args()[0])
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    out_dir = Path(cfg["output_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)
    report_path = out_dir / "blender_ovrtx_report.json"
    render_path = out_dir / "render.png"
    blend_path = out_dir / f"{cfg['job_id']}.blend"

    report: dict = {"status": "running", "blender_version": bpy.app.version_string, "job_id": cfg["job_id"]}
    t_start = time.perf_counter()
    try:
        if MODULE not in bpy.context.preferences.addons:
            raise RuntimeError(f"{MODULE} is not enabled in this Blender configuration")

        scene = bpy.context.scene
        for obj in list(scene.objects):
            bpy.data.objects.remove(obj, do_unlink=True)

        workcell_col = ensure_collection(scene.collection, "NemotronWorkcell")
        grid_img = create_workbench_grid_texture(out_dir / "workbench_grid.png")
        bench_mat = make_principled_material(
            "WorkbenchSurface",
            color=(0.15, 0.17, 0.20),
            roughness=0.55,
            metallic=0.15,
            texture=grid_img,
        )
        bpy.ops.mesh.primitive_cube_add(size=1.0, location=(0.0, 0.0, -0.01))
        bench = bpy.context.active_object
        bench.name = "WorkbenchTable"
        bench.scale = (0.80, 0.80, 0.02)
        bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
        bench.data.materials.append(bench_mat)
        move_exclusively_to_collection(bench, workcell_col)

        bpy.ops.object.select_all(action="DESELECT")
        bench.select_set(True)
        bpy.context.view_layer.objects.active = bench
        bpy.ops.ov.physics_add_body()
        bench.ov.physics.rigid_body.role = "STATIC"
        bench.ov.physics.colliders[0].shape = "BOX"
        bench.ov.physics.colliders[0].dimensions = (0.80, 0.80, 0.02)

        bpy.ops.ov.physics_scene_opinion(opinion="APPLY")

        imported_objs: list[tuple[bpy.types.Object, dict]] = []
        for asset_cfg in cfg["assets"]:
            obj, specs = import_and_convert_asset(scene, asset_cfg, workcell_col)
            imported_objs.append((obj, specs))

        export_col = scene.collection.children.get("Export")
        if export_col is not None:
            for child in list(export_col.children):
                bpy.data.collections.remove(child)
            bpy.data.collections.remove(export_col)

        sim_frames = int(cfg.get("simulate_frames", 48))
        has_dynamic = any(s["role"] == "DYNAMIC" for _, s in imported_objs)
        if sim_frames > 1 and has_dynamic:
            sim_info = run_ovphysx_bake(scene, frames=sim_frames, substeps=int(cfg.get("substeps", 2)))
            scene.frame_set(sim_frames)
            bpy.context.view_layer.update()
            dg = bpy.context.evaluated_depsgraph_get()
            poses = {}
            for obj, specs in imported_objs:
                t = obj.evaluated_get(dg).matrix_world.translation
                poses[specs["name"]] = {
                    "initial_z_m": round(float(specs["initial_location"][2]), 5),
                    "settled_xyz_m": [round(float(t.x), 5), round(float(t.y), 5), round(float(t.z), 5)],
                }
            sim_info["body_poses"] = poses
            report["ovphysx_simulation"] = sim_info
        else:
            report["ovphysx_simulation"] = {"frames_simulated": 0, "skipped_reason": "simulate_frames<=1 or no DYNAMIC bodies"}

        report["assets"] = [specs for _, specs in imported_objs]

        # Auto-frame camera around all imported CAD meshes
        dg = bpy.context.evaluated_depsgraph_get()
        mins = [1e9, 1e9, 0.0]
        maxs = [-1e9, -1e9, 0.05]
        for obj, _ in imported_objs:
            eval_obj = obj.evaluated_get(dg)
            for corner in eval_obj.bound_box:
                wc = eval_obj.matrix_world @ mathutils.Vector(corner)
                for i in range(3):
                    mins[i] = min(mins[i], float(wc[i]))
                    maxs[i] = max(maxs[i], float(wc[i]))
        center = tuple((mins[i] + maxs[i]) * 0.5 for i in range(3))
        span = max(max(maxs[i] - mins[i] for i in range(3)), 0.08)
        dist = span * 2.35

        for lname, energy, size, color, offset in (
            ("KeyLight", 2.4, 0.40, (1.0, 0.98, 0.95), (dist * 1.1, -dist * 1.2, dist * 1.4)),
            ("FillLight", 1.0, 0.50, (0.82, 0.90, 1.0), (-dist * 1.2, -dist * 0.85, dist * 1.0)),
            ("RimLight", 1.6, 0.35, (0.95, 0.97, 1.0), (-dist * 0.4, dist * 1.3, dist * 1.15)),
        ):
            ldata = bpy.data.lights.new(lname, "AREA")
            ldata.energy = energy * max((dist / 0.30) ** 2, 0.5)
            ldata.size = size
            ldata.color = color
            lobj = bpy.data.objects.new(lname, ldata)
            lobj.location = (center[0] + offset[0], center[1] + offset[1], max(center[2] + offset[2], 0.18))
            aim(lobj, center)
            workcell_col.objects.link(lobj)

        cam_data = bpy.data.cameras.new("WorkcellCamera")
        cam_data.lens = 42.0
        cam_data.clip_start = 0.005
        cam_data.clip_end = 50.0
        cam_obj = bpy.data.objects.new("WorkcellCamera", cam_data)
        cam_obj.location = (center[0] + dist * 0.98, center[1] - dist * 1.12, max(center[2] + dist * 0.72, 0.14))
        aim(cam_obj, center)
        workcell_col.objects.link(cam_obj)
        scene.camera = cam_obj

        world = bpy.data.worlds.new("NemotronStudioWorld")
        if world.node_tree is None:
            world.use_nodes = True
        bg = next(n for n in world.node_tree.nodes if n.type == "BACKGROUND")
        bg.inputs["Color"].default_value = (0.035, 0.042, 0.055, 1.0)
        scene.world = world

        res_x, res_y = int(cfg["resolution"][0]), int(cfg["resolution"][1])
        samples = int(cfg.get("samples", 32))
        scene.render.engine = ENGINE
        scene.render.resolution_x = res_x
        scene.render.resolution_y = res_y
        scene.render.resolution_percentage = 100
        scene.render.image_settings.file_format = "PNG"
        scene.ovrtx_example.min_samples = max(4, samples // 2)
        scene.ovrtx_example.max_samples = max(4, samples)

        t_render = time.perf_counter()
        bpy.ops.render.render(write_still=False)
        render_s = round(time.perf_counter() - t_render, 3)
        bpy.data.images["Render Result"].save_render(filepath=str(render_path), scene=scene)

        chk = bpy.data.images.load(str(render_path), check_existing=False)
        red = list(chk.pixels)[::4]
        bpy.data.images.remove(chk)
        if not red or max(red) <= min(red):
            raise RuntimeError("OVRTX render produced a blank image")

        report["ovrtx_render"] = {
            "engine": ENGINE,
            "resolution": [res_x, res_y],
            "samples": samples,
            "render_wall_s": render_s,
            "render_png_path": str(render_path),
            "red_min_max": [round(min(red), 4), round(max(red), 4)],
        }

        bpy.ops.wm.save_as_mainfile(filepath=str(blend_path))
        report["blend_path"] = str(blend_path)
        report["total_wall_s"] = round(time.perf_counter() - t_start, 3)
        report["status"] = "passed"
    except Exception as exc:
        report["status"] = "failed"
        report["error"] = str(exc)
        report["traceback"] = traceback.format_exc()

    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    if report["status"] != "passed":
        sys.exit(1)


if __name__ == "__main__":
    main()
"""


def render_or_simulate_in_blender_ovrtx(
    usd_paths: Optional[List[str | Path]] = None,
    *,
    assets: Optional[List[Dict[str, Any]]] = None,
    job_id: Optional[str] = None,
    simulate_frames: int = 48,
    substeps: int = 2,
    resolution: Tuple[int, int] = (1280, 720),
    samples: int = 32,
    output_dir: Optional[str | Path] = None,
    launch_gui: bool = False,
) -> Dict[str, Any]:
    """Import SimReady OpenUSD CAD assets into Blender 5.2+ with ovrtx, convert via
    ``bpy.ops.ov.physics_convert_simready()``, simulate in ``OVPhysX``, and render a path-traced
    ``OVRTX_EXAMPLE`` frame + ``.blend`` file.
    """
    normalized_assets: List[Dict[str, Any]] = []
    if assets:
        for idx, item in enumerate(assets):
            p = Path(item["usd_path"]).resolve()
            if not p.is_file():
                raise FileNotFoundError(f"SimReady USD asset not found: {p}")
            normalized_assets.append(
                {
                    "usd_path": str(p),
                    "name": item.get("name") or f"{p.stem.replace('_simready', '')}_{idx}",
                    "location": list(item.get("location", [idx * 0.12, 0.0, 0.05])),
                    "rotation_euler_deg": list(item.get("rotation_euler_deg", [0.0, 0.0, 0.0])),
                    "role": str(item.get("role", "DYNAMIC")).upper(),
                }
            )
    elif usd_paths:
        for idx, raw in enumerate(usd_paths):
            p = Path(raw).resolve()
            if not p.is_file():
                raise FileNotFoundError(f"SimReady USD asset not found: {p}")
            stem = p.stem.replace("_simready", "")
            is_fixture = idx == 0 and len(usd_paths) > 1 and ("fixture" in stem.lower() or "tray" in stem.lower())
            normalized_assets.append(
                {
                    "usd_path": str(p),
                    "name": f"{stem}_{idx}" if sum(1 for r in usd_paths if Path(r).stem == p.stem) > 1 else stem,
                    "location": [0.0, 0.0, 0.0] if is_fixture else [(idx - (1 if len(usd_paths) > 1 else 0)) * 0.11, 0.0, 0.065],
                    "rotation_euler_deg": [0.0, 0.0, 0.0],
                    "role": "STATIC" if is_fixture else "DYNAMIC",
                }
            )
    else:
        raise ValueError("Provide either 'usd_paths' or 'assets' with at least one SimReady USD file.")

    resolved_job_id = job_id or f"{Path(normalized_assets[0]['usd_path']).stem}_ovrtx"
    out_root = Path(output_dir).resolve() if output_dir else (WORKSPACE_DIR / "blender_ovrtx" / resolved_job_id)
    out_root.mkdir(parents=True, exist_ok=True)

    runtime = resolve_blender_ovrtx_runtime()
    env = _build_clean_blender_env(runtime["profile_dir"])

    with tempfile.TemporaryDirectory(prefix="isaac-assist-blender-ovrtx-") as tmp:
        tmp_dir = Path(tmp)
        worker_py = tmp_dir / "blender_ovrtx_worker.py"
        cfg_json = tmp_dir / "job_config.json"
        worker_py.write_text(_BLENDER_OVRTX_WORKER_SCRIPT, encoding="utf-8")
        cfg_json.write_text(
            json.dumps(
                {
                    "job_id": resolved_job_id,
                    "output_dir": str(out_root),
                    "assets": normalized_assets,
                    "simulate_frames": int(simulate_frames),
                    "substeps": int(substeps),
                    "resolution": [int(resolution[0]), int(resolution[1])],
                    "samples": int(samples),
                },
                indent=2,
            ),
            encoding="utf-8",
        )

        cmd = [
            runtime["blender_exe"],
            "--background",
            "--disable-autoexec",
            "--python-exit-code",
            "17",
            "--python",
            str(worker_py),
            "--",
            str(cfg_json),
        ]
        proc = subprocess.run(
            cmd,
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=600,
        )

    report_file = out_root / "blender_ovrtx_report.json"
    report = json.loads(report_file.read_text(encoding="utf-8")) if report_file.is_file() else {}
    if proc.returncode != 0 or report.get("status") != "passed":
        detail = report.get("error") or proc.stderr.strip() or proc.stdout.strip() or f"exit {proc.returncode}"
        raise RuntimeError(f"Blender ovrtx stage failed: {detail}")

    report["runtime"] = runtime
    if launch_gui:
        gui_cmd = [runtime["blender_exe"], report["blend_path"]]
        subprocess.Popen(
            gui_cmd,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        report["gui_launched"] = True
    else:
        report["gui_launched"] = False

    return report


# ---------------------------------------------------------------------------
# CLI Entry Point
# ---------------------------------------------------------------------------

def _build_cli() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_design = sub.add_parser("design", help="Generate a parametric B-Rep CAD model via FreeCAD")
    p_design.add_argument("--part-id", required=True)
    p_design.add_argument(
        "--template",
        default="slotted_fixture",
        choices=["slotted_fixture", "machined_shaft", "parallel_gripper_finger", "part_tray_bin"],
    )
    p_design.add_argument("--material", default="6061_t6_aluminum", choices=sorted(MATERIAL_PRESETS))
    p_design.add_argument("--params-json", default="{}")
    p_design.add_argument("--output-dir", type=Path, default=None)

    p_conv = sub.add_parser("convert", help="Convert CAD (.step/.stl) or metadata JSON to layered SimReady USD")
    p_conv.add_argument("--input", required=True, type=Path)
    p_conv.add_argument("--part-id", default=None)
    p_conv.add_argument("--material", default="6061_t6_aluminum", choices=sorted(MATERIAL_PRESETS))
    p_conv.add_argument(
        "--physics-variant",
        default="rigid_convex",
        choices=["rigid_convex", "sdf_hydroelastic", "static_fixture"],
    )
    p_conv.add_argument("--collision-approx", default="convexDecomposition")
    p_conv.add_argument("--output-dir", type=Path, default=None)

    p_ver = sub.add_parser("verify7a", help="Run zero-Kit Isaac Sim 7.0.0a1 dual-engine (ovphysx + newton) check")
    p_ver.add_argument("--usd", required=True, type=Path)
    p_ver.add_argument("--drop-height", type=float, default=0.20)
    p_ver.add_argument("--steps", type=int, default=180)
    p_ver.add_argument("--promote", action="store_true")

    p_comp = sub.add_parser("compose", help="Compose a multi-sublayer OpenUSD CAD workcell")
    p_comp.add_argument("--workcell-id", required=True)
    p_comp.add_argument("--fixture-usd", required=True, type=Path)
    p_comp.add_argument("--workpiece-usd", required=True, type=Path)
    p_comp.add_argument("--gsplat-usd", default=None)
    p_comp.add_argument("--output-dir", type=Path, default=None)
    p_comp.add_argument("--deploy-live", action="store_true")

    p_demo = sub.add_parser("demo-e2e", help="Run full CAD -> SimReady -> 7.0a Dual Physics -> Workcell pipeline")
    p_demo.add_argument("--workcell-id", default="nemotron_cad_cell_v1")
    p_demo.add_argument("--deploy-live", action="store_true")

    return parser


def main() -> int:
    args = _build_cli().parse_args()
    if args.cmd == "design":
        res = design_parametric_cad(
            part_id=args.part_id,
            template=args.template,
            material=args.material,
            parameters=json.loads(args.params_json),
            output_dir=args.output_dir,
        )
        print(json.dumps(res))
        return 0

    if args.cmd == "convert":
        res = _author_layered_simready_inprocess(
            metadata_path=(
                args.input
                if args.input.suffix.lower() == ".json"
                else extract_cad_metadata_from_file(
                    args.input, part_id=args.part_id, material=args.material, output_dir=args.output_dir
                )
            ),
            output_dir=args.output_dir or (args.input.parent / "simready_usd"),
            default_physics_variant=args.physics_variant,
            collision_approximation=args.collision_approx,
        )
        print(json.dumps(res))
        return 0

    if args.cmd == "verify7a":
        res = _verify_dual_7a_inprocess(
            usd_path=args.usd,
            drop_height_m=args.drop_height,
            steps=args.steps,
            promote_to_registry=args.promote,
        )
        print(json.dumps(res))
        return 0

    if args.cmd == "compose":
        res = compose_omniverse_workcell_stage(
            workcell_id=args.workcell_id,
            fixture_usd_path=args.fixture_usd,
            workpiece_usd_path=args.workpiece_usd,
            output_dir=args.output_dir,
            gsplat_backdrop_usd=args.gsplat_usd,
            deploy_live=args.deploy_live,
        )
        print(json.dumps(res))
        return 0

    if args.cmd == "demo-e2e":
        fix_cad = design_parametric_cad(
            part_id="cad_slotted_fixture",
            template="slotted_fixture",
            material="6061_t6_aluminum",
        )
        wp_cad = design_parametric_cad(
            part_id="cad_machined_shaft",
            template="machined_shaft",
            material="pla",
        )
        fix_usd = convert_cad_to_simready_usd(
            fix_cad["step_path"],
            part_id="cad_slotted_fixture",
            material="6061_t6_aluminum",
            default_physics_variant="rigid_convex",
        )
        wp_usd = convert_cad_to_simready_usd(
            wp_cad["step_path"],
            part_id="cad_machined_shaft",
            material="pla",
            default_physics_variant="rigid_convex",
        )
        fix_ver = verify_asset_dual_7a(fix_usd["root_usd_path"], promote_to_registry=True)
        wp_ver = verify_asset_dual_7a(wp_usd["root_usd_path"], promote_to_registry=True)
        workcell = compose_omniverse_workcell_stage(
            workcell_id=args.workcell_id,
            fixture_usd_path=fix_usd["root_usd_path"],
            workpiece_usd_path=wp_usd["root_usd_path"],
            deploy_live=args.deploy_live,
        )
        summary = {
            "fixture": {"cad": fix_cad, "simready": fix_usd, "verification_7a": fix_ver},
            "workpiece": {"cad": wp_cad, "simready": wp_usd, "verification_7a": wp_ver},
            "workcell": workcell,
        }
        print(json.dumps(summary, indent=2))
        return 0 if (fix_ver["passed"] and wp_ver["passed"]) else 1

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
