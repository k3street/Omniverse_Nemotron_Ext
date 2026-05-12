"""Scene blueprint handlers — target scope: scene-from-blueprint
construction, scene-template load/export/import.

Phase 6 wave 11 — scene blueprint code generators move out of
tool_executor.py. Same migration pattern as Phase 3 / Phase 5 /
Phase 6 waves 1-10.

Per specs/IA_FULL_SPEC_2026-05-10.md Phases 2 + 6.
"""
from __future__ import annotations

from typing import Any, Callable, Dict


# ---------------------------------------------------------------------------
# Phase 6 wave 11 — scene blueprints + scene templates


def _gen_build_scene_from_blueprint(args: Dict) -> str:
    """Generate code to build a scene from a structured blueprint."""
    from ..tool_executor import _SAFE_XFORM_SNIPPET
    blueprint = args.get("blueprint", {})
    objects = blueprint.get("objects", [])
    dry_run = args.get("dry_run", False)

    if not objects:
        return "print('Empty blueprint — nothing to build')\n"

    # Build the per-object placement as a helper function so we can use
    # early-return semantics (`return` instead of the old unwrapped code
    # that would need `continue` without a loop to skip bad objects).
    lines = [
        "import os",
        "import omni.usd",
        "from pxr import UsdGeom, Gf, Sdf",
        _SAFE_XFORM_SNIPPET,
        "stage = omni.usd.get_context().get_stage()",
        "_placed = 0",
        "_missing_assets = []",
        "_ref_not_authored = []",
        "",
        "def _place(name, asset_path, prim_path, prim_type, pos, rot, scale):",
        "    global _placed",
        "    if asset_path:",
        "        if not any(asset_path.startswith(p) for p in ('omniverse://','http://','https://','file://','anon:')):",
        "            if not os.path.exists(asset_path):",
        "                _missing_assets.append(name)",
        "                print(f'build_scene_from_blueprint: skipping {name} — asset not found: {asset_path!r}')",
        "                return",
        "        prim = stage.DefinePrim(prim_path, 'Xform')",
        "        prim.GetReferences().AddReference(asset_path)",
        "        if not prim.HasAuthoredReferences():",
        "            _ref_not_authored.append(name)",
        "            print(f'build_scene_from_blueprint: reference not authored on {prim.GetPath()} for {asset_path!r}')",
        "            return",
        "    elif prim_type:",
        "        prim = stage.DefinePrim(prim_path, prim_type)",
        "    else:",
        "        prim = stage.DefinePrim(prim_path, 'Xform')",
        "    _safe_set_translate(prim, (pos[0], pos[1], pos[2]))",
        "    if rot != [0, 0, 0]:",
        "        _safe_set_rotate_xyz(prim, (rot[0], rot[1], rot[2]))",
        "    if scale != [1, 1, 1]:",
        "        _safe_set_scale(prim, (scale[0], scale[1], scale[2]))",
        "    _placed += 1",
        "",
    ]

    for i, obj in enumerate(objects):
        name = obj.get("name", f"object_{i}")
        asset_path = obj.get("asset_path", "")
        prim_path = obj.get("prim_path", f"/World/{name}")
        pos = obj.get("position", [0, 0, 0])
        rot = obj.get("rotation", [0, 0, 0])
        scale = obj.get("scale", [1, 1, 1])
        prim_type = obj.get("prim_type")

        lines.append(f"# --- {name} ---")
        lines.append(
            f"_place({name!r}, {asset_path!r}, {prim_path!r}, "
            f"{prim_type!r}, {pos!r}, {rot!r}, {scale!r})"
        )

    n = len(objects)
    lines.append("")
    lines.append(
        f"print(f'build_scene_from_blueprint: placed={{_placed}}/{n} requested, "
        f"missing_assets={{len(_missing_assets)}}, ref_not_authored={{len(_ref_not_authored)}}')"
    )
    lines.append(
        f"if _placed == 0 and {n} > 0:\n"
        f"    raise RuntimeError("
        f"        f'build_scene_from_blueprint: 0 of {n} objects placed — '\n"
        f"        f'missing_assets={{_missing_assets}}, ref_not_authored={{_ref_not_authored}}')"
    )

    if dry_run:
        return f"# DRY RUN — code preview only\n" + "\n".join(lines)
    return "\n".join(lines)


def _gen_load_scene_template(args: Dict) -> str:
    """Generate code to build a quick-start scene template."""
    template = args["template_name"]

    if template == "pick_and_place":
        return """\
import omni.usd
from pxr import UsdGeom, UsdPhysics, Gf, Sdf, PhysxSchema

stage = omni.usd.get_context().get_stage()

# Physics scene
if not stage.GetPrimAtPath('/World/PhysicsScene').IsValid():
    scene = UsdPhysics.Scene.Define(stage, '/World/PhysicsScene')
    scene.GetGravityDirectionAttr().Set(Gf.Vec3f(0, 0, -1))
    scene.GetGravityMagnitudeAttr().Set(9.81)

# Ground plane
ground = stage.DefinePrim('/World/GroundPlane', 'Xform')
ground_mesh = UsdGeom.Mesh.Define(stage, '/World/GroundPlane/Mesh')
UsdPhysics.CollisionAPI.Apply(ground_mesh.GetPrim())
plane = stage.DefinePrim('/World/GroundPlane/Mesh', 'Plane')

# Table
table = stage.DefinePrim('/World/Table', 'Cube')
xf = UsdGeom.Xformable(table)
xf.AddTranslateOp().Set(Gf.Vec3d(0.5, 0, 0.4))
xf.AddScaleOp().Set(Gf.Vec3d(0.6, 0.8, 0.02))
UsdPhysics.CollisionAPI.Apply(table)

# 3 cubes on the table
colors = [(0.8, 0.1, 0.1), (0.1, 0.8, 0.1), (0.1, 0.1, 0.8)]
for i, color in enumerate(colors):
    cube_path = f'/World/Cube_{i}'
    cube = stage.DefinePrim(cube_path, 'Cube')
    xf = UsdGeom.Xformable(cube)
    xf.AddTranslateOp().Set(Gf.Vec3d(0.4 + i * 0.1, 0, 0.45))
    xf.AddScaleOp().Set(Gf.Vec3d(0.025, 0.025, 0.025))
    UsdPhysics.RigidBodyAPI.Apply(cube)
    UsdPhysics.CollisionAPI.Apply(cube)

print('Template pick_and_place loaded: table + 3 cubes + physics. Add a Franka robot with: import_robot(file_path="franka", format="asset_library")')
"""

    if template == "mobile_nav":
        return """\
import omni.usd
from pxr import UsdGeom, UsdPhysics, Gf

stage = omni.usd.get_context().get_stage()

# Physics scene
if not stage.GetPrimAtPath('/World/PhysicsScene').IsValid():
    scene = UsdPhysics.Scene.Define(stage, '/World/PhysicsScene')
    scene.GetGravityDirectionAttr().Set(Gf.Vec3f(0, 0, -1))
    scene.GetGravityMagnitudeAttr().Set(9.81)

# Ground plane
ground = stage.DefinePrim('/World/Ground', 'Plane')
UsdPhysics.CollisionAPI.Apply(ground)

# Obstacles (walls)
for i, (pos, scale) in enumerate([
    ((2, 0, 0.5), (0.1, 2, 0.5)),
    ((-2, 0, 0.5), (0.1, 2, 0.5)),
    ((0, 2, 0.5), (2, 0.1, 0.5)),
    ((0, -2, 0.5), (2, 0.1, 0.5)),
]):
    wall = stage.DefinePrim(f'/World/Wall_{i}', 'Cube')
    xf = UsdGeom.Xformable(wall)
    xf.AddTranslateOp().Set(Gf.Vec3d(*pos))
    xf.AddScaleOp().Set(Gf.Vec3d(*scale))
    UsdPhysics.CollisionAPI.Apply(wall)

print('Template mobile_nav loaded: ground + walls. Add a Jetbot with: import_robot(file_path="jetbot", format="asset_library")')
"""

    if template == "sdg_basic":
        return """\
import omni.usd
from pxr import UsdGeom, Gf, Sdf

stage = omni.usd.get_context().get_stage()

# Camera
cam = UsdGeom.Camera.Define(stage, '/World/SDG_Camera')
xf = UsdGeom.Xformable(cam.GetPrim())
xf.AddTranslateOp().Set(Gf.Vec3d(2, 2, 2))
xf.AddRotateXYZOp().Set(Gf.Vec3d(-35, 0, 45))

# Ground
ground = stage.DefinePrim('/World/Ground', 'Plane')

# 5 objects with semantic labels
shapes = ['Cube', 'Sphere', 'Cylinder', 'Cone', 'Cube']
for i, shape in enumerate(shapes):
    prim = stage.DefinePrim(f'/World/Object_{i}', shape)
    xf = UsdGeom.Xformable(prim)
    xf.AddTranslateOp().Set(Gf.Vec3d(i * 0.3 - 0.6, 0, 0.15))
    xf.AddScaleOp().Set(Gf.Vec3d(0.1, 0.1, 0.1))
    # Add semantic label for SDG
    prim.CreateAttribute('semantic:Semantics:params:semanticType', Sdf.ValueTypeNames.String).Set('class')
    prim.CreateAttribute('semantic:Semantics:params:semanticData', Sdf.ValueTypeNames.String).Set(shape.lower())

print('Template sdg_basic loaded: camera + 5 labeled objects. Configure SDG with: configure_sdg(num_frames=10, output_dir="/tmp/sdg_output")')
"""

    if template == "empty_robot":
        return """\
import omni.usd
from pxr import UsdGeom, UsdPhysics, Gf

stage = omni.usd.get_context().get_stage()

# Physics scene
if not stage.GetPrimAtPath('/World/PhysicsScene').IsValid():
    scene = UsdPhysics.Scene.Define(stage, '/World/PhysicsScene')
    scene.GetGravityDirectionAttr().Set(Gf.Vec3f(0, 0, -1))
    scene.GetGravityMagnitudeAttr().Set(9.81)

# Ground plane
ground = stage.DefinePrim('/World/Ground', 'Plane')
UsdPhysics.CollisionAPI.Apply(ground)

print('Template empty_robot loaded: physics + ground. Add a Franka with: import_robot(file_path="franka", format="asset_library")')
"""

    return (
        "raise ValueError("
        + repr(
            f"load_scene_template: unknown template {template!r}. "
            f"Valid: pick_and_place, mobile_nav, warehouse, empty_robot, drop_test."
        )
        + ")"
    )


def _gen_export_template(args: Dict) -> str:
    """Generate code that bundles the live stage + config + metadata into .isaa.

    Runs inside Kit so it can use omni.usd to flatten the open stage when the
    caller doesn't supply scene_path.  The .isaa file is a zip with this
    layout:

        manifest.json
        scene.usd          (or .usda)
        config/<files>     (optional; copied from CONFIG_DIR if present)

    """
    from ..tool_executor import _TEMPLATE_EXPORT_DIR, _ISAA_MANIFEST_VERSION
    from datetime import datetime as _dt
    name = args["name"]
    safe_name = "".join(c if c.isalnum() or c in "_-" else "_" for c in name)
    description = args.get("description", "")
    scene_path = args.get("scene_path")  # may be None → flatten open stage
    output_dir = args.get("output_dir") or str(_TEMPLATE_EXPORT_DIR)
    min_vram_gb = args.get("min_vram_gb")
    recommended_vram_gb = args.get("recommended_vram_gb")
    tags = args.get("tags", []) or []
    timestamp = _dt.utcnow().strftime("%Y%m%dT%H%M%SZ")

    # Build the manifest dict literal we want serialized inside Kit.
    manifest = {
        "manifest_version": _ISAA_MANIFEST_VERSION,
        "name": name,
        "template": safe_name,
        "description": description,
        "exported_at": timestamp,
        "min_vram_gb": min_vram_gb,
        "recommended_vram_gb": recommended_vram_gb,
        "tags": list(tags),
        "scene_file": "scene.usda",
    }

    return f"""\
import json
import os
import shutil
import tempfile
import zipfile
from pathlib import Path

import omni.usd

manifest = {json.dumps(manifest, indent=2)}
output_dir = Path({output_dir!r})
output_dir.mkdir(parents=True, exist_ok=True)
isaa_path = output_dir / ({safe_name!r} + '.isaa')

scene_path = {scene_path!r}
with tempfile.TemporaryDirectory() as _tmp:
    tmp = Path(_tmp)
    scene_dst = tmp / 'scene.usda'
    if scene_path:
        # Copy the supplied .usd/.usda directly into the bundle.
        shutil.copyfile(scene_path, scene_dst)
        manifest['scene_file'] = Path(scene_path).name
    else:
        # Flatten the currently open stage to a single .usda file.
        ctx = omni.usd.get_context()
        stage = ctx.get_stage()
        if stage is None:
            raise RuntimeError('No open stage to export — supply scene_path or open a scene.')
        stage.Export(str(scene_dst))

    (tmp / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')

    with zipfile.ZipFile(isaa_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        zf.write(tmp / 'manifest.json', arcname='manifest.json')
        zf.write(scene_dst, arcname=manifest['scene_file'])

print(f'[export_template] wrote {{isaa_path}} ({{isaa_path.stat().st_size}} bytes)')
"""


def _gen_import_template(args: Dict) -> str:
    """Generate code that extracts an .isaa file into the local library."""
    from ..tool_executor import _TEMPLATE_LIBRARY_DIR
    file_path = args["file_path"]
    library_dir = args.get("library_dir") or str(_TEMPLATE_LIBRARY_DIR)
    overwrite = bool(args.get("overwrite", False))

    return f"""\
import json
import shutil
import zipfile
from pathlib import Path

src = Path({file_path!r})
library = Path({library_dir!r})
library.mkdir(parents=True, exist_ok=True)

if not src.exists():
    raise FileNotFoundError(f'.isaa file not found: {{src}}')

with zipfile.ZipFile(src, 'r') as zf:
    names = zf.namelist()
    if 'manifest.json' not in names:
        raise ValueError(f'{{src}} is not a valid .isaa template (missing manifest.json)')
    manifest = json.loads(zf.read('manifest.json').decode('utf-8'))

template_id = manifest.get('template') or manifest.get('name')
if not template_id:
    raise ValueError('manifest.json missing template/name field')
safe_id = ''.join(c if c.isalnum() or c in '_-' else '_' for c in template_id)

dest = library / safe_id
if dest.exists():
    if {overwrite!r}:
        shutil.rmtree(dest)
    else:
        raise FileExistsError(f'Template {{template_id!r}} already in library — pass overwrite=True to replace.')

dest.mkdir(parents=True)
with zipfile.ZipFile(src, 'r') as zf:
    zf.extractall(dest)

print(f'[import_template] installed {{template_id}} -> {{dest}}')
"""


# ---------------------------------------------------------------------------
# Registration


def register(
    data: Dict[str, Callable[..., Any]],
    codegen: Dict[str, Callable[..., Any]],
) -> None:
    """Phase 6 wave 11 — dispatch lines in tool_executor.py still
    reference these names via re-import. Phase 9 swaps to register()
    being authoritative; until then this is intentionally a no-op.
    """
    return None
