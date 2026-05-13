"""Scene blueprint handlers — target scope: scene-from-blueprint
construction, scene-template load/export/import.

Phase 6 wave 11 — scene blueprint code generators move out of
tool_executor.py. Same migration pattern as Phase 3 / Phase 5 /
Phase 6 waves 1-10.

Per specs/IA_FULL_SPEC_2026-05-10.md Phases 2 + 6.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from ....config import config

from typing import Any, Callable, Dict, List, Optional

# ---------------------------------------------------------------------------
# Theme-local asset-index unit (Phase 8 wave 23, 2026-05-13)
# Migrated from tool_executor.py — used only by handlers.scene_blueprints.

_asset_index: Optional[List[Dict]] = None

_CATALOG_ROBOTS = {
    "franka": "franka.usd",
    "panda": "franka.usd",
    "spot": "spot.usd",
    "spot_with_arm": "spot_with_arm.usd",
    "carter": "carter_v1.usd",
    "jetbot": "jetbot.usd",
    "kaya": "kaya.usd",
    "ur10": "ur10.usd",
    "ur5e": "ur5e.usd",
    "anymal_c": "anymal_c.usd",
    "anymal_d": "anymal_d.usd",
    "a1": "a1.usd",
    "go1": "go1.usd",
    "go2": "go2.usd",
    "g1": "g1.usd",
    "unitree_g1": "g1.usd",
    "g1_23dof": "g1_23dof_robot.usd",
    "h1": "h1.usd",
    "unitree_h1": "h1.usd",
    "h1_hand_left": "h1_hand_left.usd",
    "allegro_hand": "allegro_hand.usd",
    "ridgeback_franka": "ridgeback_franka.usd",
    "humanoid": "humanoid.usd",
    "humanoid_28": "humanoid_28.usd",
}

def _invalidate_asset_index() -> None:
    """Invalidate the cached asset index so the next search rebuilds it."""
    global _asset_index
    _asset_index = None

def _build_asset_index() -> List[Dict]:
    """Build searchable index from asset_catalog.json (fast) + known robots."""
    global _asset_index
    if _asset_index is not None:
        return _asset_index

    index = []
    assets_root = getattr(config, "assets_root_path", None) or ""
    robots_sub = getattr(config, "assets_robots_subdir", None) or "Collected_Robots"
    robots_dir = f"{assets_root}/{robots_sub}" if assets_root else ""

    # 1. Load asset_catalog.json (5,000+ entries with rich metadata)
    catalog_path = Path(assets_root) / "asset_catalog.json" if assets_root else None
    catalog_loaded = False
    if catalog_path and catalog_path.exists():
        try:
            catalog = json.loads(catalog_path.read_text())
            for entry in catalog.get("assets", []):
                tags = entry.get("tags", [])
                index.append({
                    "name": entry.get("name", ""),
                    "type": entry.get("category", "prop"),
                    "path": entry.get("usd_path", ""),
                    "rel_path": entry.get("relative_path", ""),
                    "tags": tags,
                    "source": "asset_catalog",
                })
            catalog_loaded = True
            logger.info(f"[AssetIndex] Loaded {len(index)} entries from asset_catalog.json")
        except Exception as e:
            logger.warning(f"[AssetIndex] Failed to load asset_catalog.json: {e}")

    # 2. Always add the known robot name map (canonical names → files)
    for name, filename in _CATALOG_ROBOTS.items():
        index.append({
            "name": name,
            "type": "robot",
            "path": f"{robots_dir}/{filename}" if robots_dir else filename,
            "source": "robot_library",
        })

    # 3. JSONL manifest (user-added entries)
    manifest_path = _WORKSPACE / "knowledge" / "asset_manifest.jsonl"
    if manifest_path.exists():
        for line in manifest_path.read_text().splitlines():
            line = line.strip()
            if line:
                try:
                    index.append(json.loads(line))
                except json.JSONDecodeError:
                    pass

    # 4. Filesystem walk only if catalog wasn't loaded (slow fallback)
    if not catalog_loaded and assets_root:
        search_dir = Path(assets_root)
        if search_dir.exists():
            try:
                for f in search_dir.rglob("*"):
                    if f.suffix.lower() in (".usd", ".usda", ".usdz"):
                        rel = f.relative_to(search_dir)
                        name_parts = rel.stem.replace("_", " ").replace("-", " ")
                        path_str = str(rel).lower()
                        if any(k in path_str for k in ("robot", "arm", "gripper", "manipulator")):
                            atype = "robot"
                        elif any(k in path_str for k in ("env", "room", "warehouse", "house", "kitchen")):
                            atype = "environment"
                        elif any(k in path_str for k in ("sensor", "camera", "lidar")):
                            atype = "sensor"
                        elif any(k in path_str for k in ("material", "mdl", "texture")):
                            atype = "material"
                        else:
                            atype = "prop"
                        index.append({
                            "name": name_parts,
                            "type": atype,
                            "path": str(f),
                            "source": "filesystem",
                            "rel_path": str(rel),
                        })
            except PermissionError:
                pass

    _asset_index = index
    return _asset_index

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Theme-local helpers (Phase 8 wave 18, 2026-05-13)

def _load_template_manifests(library_dir: Path) -> List[Dict]:
    """Load manifest.json from each template directory in library_dir.

    Each entry is augmented with `_template_dir` so the caller can resolve
    paths.  Missing or malformed manifests are skipped.
    """
    manifests: List[Dict] = []
    if not library_dir.exists():
        return manifests
    for entry in sorted(library_dir.iterdir()):
        if not entry.is_dir():
            continue
        manifest_path = entry / "manifest.json"
        if not manifest_path.exists():
            continue
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[filter_templates_by_hardware] bad manifest at {manifest_path}: {e}")
            continue
        if not isinstance(data, dict):
            continue
        data["_template_dir"] = str(entry)
        manifests.append(data)
    return manifests

# _handle_filter_templates_by_hardware moved to handlers/scene_blueprints.py (Phase 7 wave 12+13 redirect-stub stripped).

# _gen_export_template moved to handlers/scene_blueprints.py (Phase 6 wave 11).

# _gen_import_template moved to handlers/scene_blueprints.py (Phase 6 wave 11).


# _handle_check_vram_headroom moved to handlers/diagnostics.py (Phase 7 wave 12+13 redirect-stub stripped).

# ---------------------------------------------------------------------------
# Theme-local constants (Phase 8 wave 16, 2026-05-13)
# Migrated from tool_executor.py — used only by handlers.scene_blueprints.

_LIST_LOCAL_DEFAULT_ROOTS = [
    "/home/anton/projects/Omniverse_Nemotron_Ext/workspace",
    "/home/anton/projects/Omniverse_Nemotron_Ext/data",
    "/home/anton/Downloads",
    "/home/anton/Documents",
    "/home/anton/robots",
    "/home/anton/projects/myarm",
    "/home/anton/projects/sharp_football",
    "/tmp",
]

_LIST_LOCAL_MAX_RESULTS = 200

_LIST_LOCAL_MAX_DEPTH = 6

_LIST_LOCAL_ALLOWED_EXTS = {
    ".urdf", ".usd", ".usda", ".usdc", ".usdz",
    ".step", ".stp", ".iges", ".igs", ".stl", ".obj", ".fbx", ".gltf", ".glb",
    ".ifc", ".ifczip",
    ".yaml", ".yml", ".json",  # config, scene templates
    ".pcd", ".ply",  # point clouds
    ".png", ".jpg", ".jpeg", ".exr", ".hdr",  # textures (filtered by name pattern)
}

# ---------------------------------------------------------------------------
# Theme-local constants + helpers (Phase 8 wave 7, 2026-05-13)
# Migrated from tool_executor.py — used only by this module.

_WORKSPACE = Path(__file__).resolve().parents[5] / "workspace"

_ISAA_MANIFEST_VERSION = 1

_SCENE_TEMPLATES = {
    "tabletop_manipulation": {
        "description": "Table-top manipulation scene with a Franka robot arm, objects to grasp, and an overhead camera. Ideal for pick-and-place tasks.",
        "category": "manipulation",
        "room_dims": [4, 4, 3],
        "objects": [
            {"name": "GroundPlane", "prim_type": "Plane", "position": [0, 0, 0], "scale": [5, 5, 1]},
            {"name": "Table", "prim_type": "Cube", "position": [0, 0, 0.4], "scale": [0.8, 0.6, 0.4]},
            {"name": "Franka", "prim_path": "/World/Franka", "asset_name": "franka", "position": [0, -0.3, 0.8], "scale": [1, 1, 1]},
            {"name": "Cube_Red", "prim_type": "Cube", "position": [0.15, 0.1, 0.85], "scale": [0.03, 0.03, 0.03]},
            {"name": "Cube_Green", "prim_type": "Cube", "position": [-0.1, 0.15, 0.85], "scale": [0.03, 0.03, 0.03]},
            {"name": "Cylinder_Blue", "prim_type": "Cylinder", "position": [0.05, -0.1, 0.85], "scale": [0.02, 0.02, 0.04]},
            {"name": "OverheadCamera", "prim_type": "Camera", "position": [0, 0, 1.8], "rotation": [-90, 0, 0]},
        ],
        "suggested_sensors": ["camera (overhead, 1280x720)", "contact_sensor (gripper fingers)"],
        "physics_settings": {"gravity": -9.81, "time_step": 1.0 / 120.0, "solver_iterations": 32},
    },
    "warehouse_picking": {
        "description": "Warehouse bin-picking scene with shelving units, a mobile robot, bins with objects, and an overhead camera. Good for logistics and order-fulfillment tasks.",
        "category": "warehouse",
        "room_dims": [10, 8, 4],
        "objects": [
            {"name": "GroundPlane", "prim_type": "Plane", "position": [0, 0, 0], "scale": [12, 10, 1]},
            {"name": "Shelf_A", "prim_type": "Cube", "position": [-2, 2, 1.0], "scale": [1.2, 0.4, 2.0]},
            {"name": "Shelf_B", "prim_type": "Cube", "position": [2, 2, 1.0], "scale": [1.2, 0.4, 2.0]},
            {"name": "Bin_1", "prim_type": "Cube", "position": [-2, 2, 0.3], "scale": [0.4, 0.3, 0.25]},
            {"name": "Bin_2", "prim_type": "Cube", "position": [-2, 2, 0.8], "scale": [0.4, 0.3, 0.25]},
            {"name": "Bin_3", "prim_type": "Cube", "position": [2, 2, 0.3], "scale": [0.4, 0.3, 0.25]},
            {"name": "MobileRobot", "prim_path": "/World/Carter", "asset_name": "carter", "position": [0, -1, 0], "scale": [1, 1, 1]},
            {"name": "OverheadCamera", "prim_type": "Camera", "position": [0, 0, 3.5], "rotation": [-90, 0, 0]},
        ],
        "suggested_sensors": ["camera (overhead, 1920x1080)", "rtx_lidar (mobile robot)"],
        "physics_settings": {"gravity": -9.81, "time_step": 1.0 / 60.0, "solver_iterations": 16},
    },
    "mobile_navigation": {
        "description": "Indoor navigation scene with a ground plane, walls, obstacles, and a wheeled robot with lidar. Good for SLAM and path-planning tasks.",
        "category": "mobile",
        "room_dims": [8, 8, 3],
        "objects": [
            {"name": "GroundPlane", "prim_type": "Plane", "position": [0, 0, 0], "scale": [10, 10, 1]},
            {"name": "Wall_North", "prim_type": "Cube", "position": [0, 4, 1.0], "scale": [8, 0.1, 2.0]},
            {"name": "Wall_South", "prim_type": "Cube", "position": [0, -4, 1.0], "scale": [8, 0.1, 2.0]},
            {"name": "Wall_East", "prim_type": "Cube", "position": [4, 0, 1.0], "scale": [0.1, 8, 2.0]},
            {"name": "Wall_West", "prim_type": "Cube", "position": [-4, 0, 1.0], "scale": [0.1, 8, 2.0]},
            {"name": "Obstacle_1", "prim_type": "Cylinder", "position": [1.5, 1.0, 0.5], "scale": [0.3, 0.3, 1.0]},
            {"name": "Obstacle_2", "prim_type": "Cube", "position": [-1.0, -1.5, 0.4], "scale": [0.6, 0.6, 0.8]},
            {"name": "Obstacle_3", "prim_type": "Cylinder", "position": [-2.0, 2.0, 0.5], "scale": [0.25, 0.25, 1.0]},
            {"name": "Jetbot", "prim_path": "/World/Jetbot", "asset_name": "jetbot", "position": [0, 0, 0.05], "scale": [1, 1, 1]},
        ],
        "suggested_sensors": ["rtx_lidar (robot-mounted, 360 deg)", "camera (front-facing)"],
        "physics_settings": {"gravity": -9.81, "time_step": 1.0 / 60.0, "solver_iterations": 16},
    },
    "inspection_cell": {
        "description": "Automated inspection cell with a conveyor belt, inspection cameras, structured lighting, and sample objects. Good for quality-inspection and defect-detection tasks.",
        "category": "inspection",
        "room_dims": [6, 4, 3],
        "objects": [
            {"name": "GroundPlane", "prim_type": "Plane", "position": [0, 0, 0], "scale": [8, 6, 1]},
            {"name": "Conveyor", "prim_type": "Cube", "position": [0, 0, 0.45], "scale": [3.0, 0.5, 0.05]},
            {"name": "ConveyorLegs_L", "prim_type": "Cube", "position": [-1.2, 0, 0.2], "scale": [0.05, 0.4, 0.4]},
            {"name": "ConveyorLegs_R", "prim_type": "Cube", "position": [1.2, 0, 0.2], "scale": [0.05, 0.4, 0.4]},
            {"name": "InspectionCamera_Top", "prim_type": "Camera", "position": [0, 0, 1.5], "rotation": [-90, 0, 0]},
            {"name": "InspectionCamera_Side", "prim_type": "Camera", "position": [0, -1.2, 0.8], "rotation": [0, 0, 0]},
            {"name": "Light_Bar_1", "prim_type": "RectLight", "position": [-0.5, 0, 1.2], "scale": [0.8, 0.1, 0.05]},
            {"name": "Light_Bar_2", "prim_type": "RectLight", "position": [0.5, 0, 1.2], "scale": [0.8, 0.1, 0.05]},
            {"name": "SampleObject_1", "prim_type": "Cube", "position": [-0.3, 0, 0.5], "scale": [0.08, 0.08, 0.08]},
            {"name": "SampleObject_2", "prim_type": "Cylinder", "position": [0.1, 0, 0.5], "scale": [0.04, 0.04, 0.06]},
            {"name": "SampleObject_3", "prim_type": "Sphere", "position": [0.4, 0, 0.52], "scale": [0.03, 0.03, 0.03]},
        ],
        "suggested_sensors": ["camera (top-down, high-res 4K)", "camera (side-view, 1280x720)"],
        "physics_settings": {"gravity": -9.81, "time_step": 1.0 / 120.0, "solver_iterations": 32},
    },
}

_TEMPLATE_EXPORT_DIR = _WORKSPACE / "templates" / "exports"

_TEMPLATE_LIBRARY_DIR = _WORKSPACE / "templates" / "library"

_SENSOR_SPECS_PATH = _WORKSPACE / "knowledge" / "sensor_specs.jsonl"

_sensor_specs: Optional[List[Dict]] = None

def _load_sensor_specs() -> List[Dict]:
    global _sensor_specs
    if _sensor_specs is not None:
        return _sensor_specs
    specs = []
    if _SENSOR_SPECS_PATH.exists():
        for line in _SENSOR_SPECS_PATH.read_text().splitlines():
            line = line.strip()
            if line:
                specs.append(json.loads(line))
    _sensor_specs = specs
    return specs


# _load_deformable_presets migrated to handlers/physics.py (Phase 8 wave 6, 2026-05-13).


# ---------------------------------------------------------------------------
# Phase 6 wave 11 — scene blueprints + scene templates


def _gen_build_scene_from_blueprint(args: Dict) -> str:
    """Generate code to build a scene from a structured blueprint."""
    from ._shared import _SAFE_XFORM_SNIPPET
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
    # Phase 8 wave 7 — _TEMPLATE_EXPORT_DIR migrated to module body.
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
    # Phase 8 wave 7 — _TEMPLATE_LIBRARY_DIR migrated to module body.
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
# Phase 7 wave 13 — lookup + catalog + scene templates + scene packages


async def _handle_lookup_api_deprecation(args: Dict) -> Dict:
    """Deterministic keyword index over the 4.x→5.x deprecations corpus.

    Returns structured cite-facts so the agent can quote exact API
    names verbatim. See knowledge/deprecations_index.py for corpus +
    scoring semantics; this is an INDEX, not RAG — no LLM-side
    synthesis in the retrieval path.
    """
    from ....knowledge.deprecations_index import lookup as _deprec_lookup
    query = args.get("query", "") or ""
    top_k = int(args.get("top_k", 3))
    rows = _deprec_lookup(query, top_k=top_k)
    return {
        "query": query,
        "results": rows,
        "count": len(rows),
        "note": (
            "These rows are verbatim cite-facts. Use tool_5x names "
            "exactly as returned and flag deprecated_4x names as removed. "
            "Do not paraphrase the API names."
        ) if rows else (
            "No deprecations corpus match. Fall back to lookup_knowledge "
            "for general docs, or acknowledge that no cite-fact is on file."
        ),
    }


async def _handle_lookup_knowledge(args: Dict) -> Dict:
    """Search the version-specific knowledge base for code patterns and docs."""
    from ....retrieval.context_retriever import (
        retrieve_context,
        find_matching_patterns,
        detect_isaac_version,
    )
    query = args.get("query", "")
    version = detect_isaac_version()

    # Search FTS index
    fts_results = retrieve_context(query, version=version, limit=3)

    # Search code patterns
    patterns = find_matching_patterns(query, version=version, limit=3)

    results = []
    for r in fts_results:
        results.append({
            "source": r.get("source_id", "docs"),
            "section": r.get("section_path", ""),
            "content": r.get("content", "")[:600],
        })
    for p in patterns:
        results.append({
            "source": "code_patterns",
            "title": p.get("title", ""),
            "code": p.get("code", ""),
            "note": p.get("note", ""),
        })

    return {
        "version": version,
        "query": query,
        "results": results,
        "count": len(results),
    }


async def _handle_lookup_product_spec(args: Dict) -> Dict:
    """Fuzzy-match a product name against the sensor specs database."""
    # Phase 8 wave 7 — _load_sensor_specs migrated to module body.
    query = args.get("product_name", "").lower()
    specs = _load_sensor_specs()
    # Exact match first
    for s in specs:
        if s["product"].lower() == query:
            return {"found": True, "spec": s}
    # Substring match
    matches = [s for s in specs if query in s["product"].lower() or
               any(query in w.lower() for w in s["product"].split())]
    if matches:
        return {"found": True, "spec": matches[0], "alternatives": [m["product"] for m in matches[1:4]]}
    # Fuzzy by manufacturer or type
    by_type = [s for s in specs if query in s.get("type", "") or query in s.get("subtype", "")]
    if by_type:
        return {"found": False, "suggestions": [s["product"] for s in by_type[:5]],
                "message": f"No exact match for '{args['product_name']}'. Did you mean one of these?"}
    return {"found": False, "message": f"No sensor specs found for '{args['product_name']}'"}


async def _handle_catalog_search(args: Dict) -> Dict:
    """Fuzzy-match assets by name, type, and path."""
    # Phase 8 wave 23 — _build_asset_index migrated.
    query = args.get("query", "").lower()
    asset_type = args.get("asset_type", "any").lower()
    limit = args.get("limit", 10)

    index = _build_asset_index()
    scored = []
    query_words = query.split()

    for asset in index:
        if asset_type != "any" and asset.get("type", "any") != asset_type:
            continue

        name = asset.get("name", "").lower()
        path = asset.get("path", "").lower()
        rel_path = asset.get("rel_path", "").lower()
        tags = " ".join(asset.get("tags", [])).lower() if asset.get("tags") else ""
        searchable = f"{name} {path} {rel_path} {tags}"

        # Score: exact match > all words present > partial
        if query == name:
            score = 100
        elif all(w in searchable for w in query_words):
            score = 70 + sum(10 for w in query_words if w in name)
        elif any(w in searchable for w in query_words):
            score = sum(10 for w in query_words if w in searchable)
        else:
            continue

        scored.append((score, asset))

    scored.sort(key=lambda x: -x[0])
    results = [a for _, a in scored[:limit]]

    return {
        "query": args.get("query", ""),
        "results": results,
        "total_matches": len(scored),
        "index_size": len(index),
    }


async def _handle_nucleus_browse(args: Dict) -> Dict:
    """Browse a Nucleus server directory via Kit RPC (omni.client inside Isaac Sim)."""
    import json as _json
    import re as _re
    from .. import kit_tools
    nucleus_path = args.get("path", "/NVIDIA/Assets/Isaac/5.1")
    # Sanitize: strip shell metacharacters, only allow alphanumeric + / . _ -
    if not _re.match(r'^[a-zA-Z0-9/_. :-]+$', nucleus_path):
        return {"error": "Invalid path characters"}

    server = args.get("server", "omniverse://localhost")
    if not _re.match(r'^omniverse://[a-zA-Z0-9._-]+(:\d+)?$', server):
        return {"error": "Invalid Nucleus server URL. Expected format: omniverse://hostname"}

    full_path = f"{server}{nucleus_path}"
    limit = min(args.get("limit", 50), 200)

    code = f"""
import omni.client
import json

result, entries = omni.client.list("{full_path}")
items = []
payload = {{"status": str(result), "path": "{full_path}"}}
if result == omni.client.Result.OK:
    for entry in entries[:{limit}]:
        items.append({{
            "name": entry.relative_path,
            "size": entry.size,
            "is_folder": entry.flags & omni.client.ItemFlags.CAN_HAVE_CHILDREN != 0,
            "modified_time": str(entry.modified_time) if hasattr(entry, 'modified_time') else "",
        }})
    payload["items"] = items
    payload["count"] = len(items)
else:
    # Non-OK: surface as an explicit `error` key so the agent can't interpret
    # count=0 as an empty directory. The status string contains the
    # Result.ERROR_* variant (e.g. Result.ERROR_NOT_FOUND, Result.ERROR_ACCESS_DENIED).
    payload["error"] = (
        "omni.client.list(" + repr("{full_path}") + ") failed with " + str(result)
        + " — Nucleus server unreachable, path missing, or permission denied."
    )
    payload["items"] = []
    payload["count"] = 0
print(json.dumps(payload))
"""
    result = await kit_tools.exec_sync(code, timeout=15)
    if not result.get("success"):
        return {"error": f"Kit RPC failed: {result.get('output', 'unknown')}",
                "hint": "Is Isaac Sim running? Is a Nucleus server accessible?"}

    output = result.get("output", "").strip()
    # Parse the last line as JSON (exec_sync may include other prints)
    for line in reversed(output.splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                return _json.loads(line)
            except _json.JSONDecodeError:
                pass
    return {"error": "Failed to parse Nucleus response", "raw_output": output[:500]}


async def _handle_download_asset(args: Dict) -> Dict:
    """Download asset from Nucleus to local Desktop/assets and register in catalog."""
    import json as _json
    import logging as _logging
    import re as _re
    from pathlib import Path as _Path
    from .. import kit_tools
    from ....config import config

    _logger = _logging.getLogger(__name__)

    nucleus_url = args.get("nucleus_url", "")
    # Validate URL format
    if not nucleus_url.startswith("omniverse://"):
        return {"error": "nucleus_url must start with omniverse://"}
    if not _re.match(r'^omniverse://[a-zA-Z0-9._:-]+/[a-zA-Z0-9/_. -]+$', nucleus_url):
        return {"error": "Invalid nucleus_url format"}

    assets_root = getattr(config, "assets_root_path", "") or ""
    if not assets_root:
        return {"error": "ASSETS_ROOT_PATH not configured in .env"}

    # Determine local destination
    # omniverse://localhost/NVIDIA/Assets/Isaac/5.1/Robots/Franka/franka.usd
    # → Desktop/assets/Nucleus_Downloads/Robots/Franka/franka.usd
    subdir = args.get("local_subdir", "")
    if not subdir:
        # Auto-derive from Nucleus path: strip server + /NVIDIA/Assets/Isaac/X.X/
        path_part = nucleus_url.split("/", 3)[-1] if "/" in nucleus_url else ""
        # Remove common prefixes
        for prefix in ("NVIDIA/Assets/Isaac/5.1/", "NVIDIA/Assets/Isaac/", "NVIDIA/Assets/", "NVIDIA/"):
            if path_part.startswith(prefix):
                path_part = path_part[len(prefix):]
                break
        subdir = f"Nucleus_Downloads/{path_part}" if path_part else "Nucleus_Downloads"

    # Extract just the directory part (not filename)
    if subdir.endswith(".usd") or subdir.endswith(".usda") or subdir.endswith(".usdz"):
        subdir = str(_Path(subdir).parent)

    local_dir = _Path(assets_root) / subdir
    filename = nucleus_url.rsplit("/", 1)[-1]
    # Sanitize filename
    filename = _re.sub(r'[^a-zA-Z0-9._-]', '_', filename)
    local_path = local_dir / filename

    if local_path.exists():
        return {
            "status": "already_exists",
            "local_path": str(local_path),
            "message": f"Asset already downloaded at {local_path}",
        }

    # Escape paths for code injection safety
    safe_nucleus = nucleus_url.replace('"', '').replace("'", "").replace("\\", "")
    safe_local = str(local_path).replace('"', '').replace("'", "").replace("\\", "")
    safe_dir = str(local_dir).replace('"', '').replace("'", "").replace("\\", "")

    code = f"""
import omni.client
import os
import json

os.makedirs("{safe_dir}", exist_ok=True)
result = omni.client.copy("{safe_nucleus}", "{safe_local}")
if result == omni.client.Result.OK:
    size = os.path.getsize("{safe_local}") if os.path.exists("{safe_local}") else 0
    print(json.dumps({{"status": "ok", "local_path": "{safe_local}", "size": size}}))
else:
    print(json.dumps({{"status": "error", "result": str(result), "nucleus_url": "{safe_nucleus}"}}))
"""
    result = await kit_tools.exec_sync(code, timeout=60)
    if not result.get("success"):
        return {"error": f"Kit RPC download failed: {result.get('output', 'unknown')}"}

    output = result.get("output", "").strip()
    dl_result = None
    for line in reversed(output.splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                dl_result = _json.loads(line)
                break
            except _json.JSONDecodeError:
                pass

    if not dl_result or dl_result.get("status") != "ok":
        return {"error": "Download failed", "details": dl_result or output[:500]}

    # Register in asset_catalog.json
    catalog_path = _Path(assets_root) / "asset_catalog.json"
    asset_name = _Path(filename).stem.replace("_", " ").replace("-", " ")
    # Infer category from path
    path_lower = nucleus_url.lower()
    if any(k in path_lower for k in ("robot", "arm", "gripper", "manipulator")):
        category = "robot"
    elif any(k in path_lower for k in ("env", "room", "warehouse", "scene")):
        category = "scene"
    elif any(k in path_lower for k in ("sensor", "camera", "lidar")):
        category = "sensor"
    elif any(k in path_lower for k in ("prop", "object", "furniture")):
        category = "prop"
    else:
        category = args.get("category", "prop")

    new_entry = {
        "name": asset_name,
        "usd_path": str(local_path),
        "relative_path": str(local_path.relative_to(_Path(assets_root))),
        "category": category,
        "tags": [w for w in asset_name.lower().split() if len(w) > 1] + ["nucleus_download"],
        "nucleus_source": nucleus_url,
        "meters_per_unit": 1.0,
    }

    catalog_registered = False
    catalog_error = None
    if catalog_path.exists():
        try:
            catalog = _json.loads(catalog_path.read_text())
            catalog["assets"].append(new_entry)
            catalog["metadata"]["total_assets"] = len(catalog["assets"])
            catalog_path.write_text(_json.dumps(catalog, indent=2))
            catalog_registered = True
        except Exception as e:
            catalog_error = str(e)
            _logger.warning(f"[DownloadAsset] Failed to update catalog: {e}")
    else:
        catalog_error = f"catalog file not found at {catalog_path}"

    # Invalidate cached asset index so next search picks up the new entry
    # Phase 8 wave 23 — _invalidate_asset_index migrated.
    _invalidate_asset_index()

    size = dl_result.get("size", 0)
    if catalog_registered:
        msg = (
            f"Downloaded {filename} to {local_path} ({size} bytes). "
            f"Registered in asset catalog."
        )
    else:
        msg = (
            f"Downloaded {filename} to {local_path} ({size} bytes). "
            f"**Catalog registration FAILED** ({catalog_error}) — the file "
            f"is on disk but not searchable via catalog_search. "
            f"Fix the catalog issue and re-register manually."
        )
    return {
        "status": "downloaded" if catalog_registered else "downloaded_uncataloged",
        "local_path": str(local_path),
        "size": size,
        "category": category,
        "nucleus_source": nucleus_url,
        "catalog_registered": catalog_registered,
        "catalog_error": catalog_error,
        "message": msg,
    }


async def _handle_list_local_files(args: Dict) -> Dict:
    """Search the local filesystem under known asset roots for matching files.

    Use this before asking the user for a file path. The agent should call this
    with a name-pattern + extension hint and inspect results before falling back
    to "please give me the path".

    Args:
        pattern: glob-style pattern matched against basename. Example: "*ur10*"
        extensions: list of file extensions to limit results, e.g. [".urdf",".usd"]
        search_paths: optional override of the default asset roots
        max_results: cap (default 50, hard max 200)

    Returns:
        {"matches": [{"path": str, "size": int, "ext": str, "rel_root": str}, ...],
         "n_matches": int,
         "n_searched_roots": int,
         "truncated": bool,
         "skipped_dirs": [...]}  // dirs skipped due to depth cap or perm errors
    """
    import fnmatch as _fnmatch_files
    import os as _os_files
    # (Phase 8 wave 16) tool_executor imports migrated to module body:
    # _LIST_LOCAL_MAX_DEPTH migrated to module body (Phase 8 wave 16).
    pattern = (args.get("pattern") or "*").strip()
    extensions_raw = args.get("extensions") or []
    if isinstance(extensions_raw, str):
        extensions_raw = [extensions_raw]
    extensions = {("." + e.lstrip(".")).lower() for e in extensions_raw if e}
    if not extensions:
        # If caller didn't specify extensions, restrict to asset-relevant set
        extensions = set(_LIST_LOCAL_ALLOWED_EXTS)
    else:
        # Honor caller's choice but never widen beyond the safety set
        extensions = extensions & _LIST_LOCAL_ALLOWED_EXTS
        if not extensions:
            return {
                "matches": [],
                "n_matches": 0,
                "n_searched_roots": 0,
                "truncated": False,
                "skipped_dirs": [],
                "error": (
                    "list_local_files: requested extensions are outside the "
                    "allowed asset-discovery set. Allowed: "
                    + ", ".join(sorted(_LIST_LOCAL_ALLOWED_EXTS))
                ),
            }

    requested_paths = args.get("search_paths") or _LIST_LOCAL_DEFAULT_ROOTS
    if isinstance(requested_paths, str):
        requested_paths = [requested_paths]
    # Resolve + filter to existing directories. Refuse anything outside the
    # default roots unless explicitly listed by the agent (the agent runs as
    # a trusted-but-bounded process; this is a brake, not a wall).
    search_paths = []
    for p in requested_paths:
        p_abs = _os_files.path.abspath(_os_files.path.expanduser(p))
        if _os_files.path.isdir(p_abs):
            search_paths.append(p_abs)

    max_results = min(int(args.get("max_results") or 50), _LIST_LOCAL_MAX_RESULTS)

    matches: list[dict] = []
    skipped: list[str] = []
    truncated = False

    for root in search_paths:
        if len(matches) >= max_results:
            truncated = True
            break
        for dirpath, dirnames, filenames in _os_files.walk(root, followlinks=False):
            # Depth gate
            rel = _os_files.path.relpath(dirpath, root)
            depth = 0 if rel == "." else rel.count(_os_files.sep) + 1
            if depth >= _LIST_LOCAL_MAX_DEPTH:
                # Stop descending further from this dir
                dirnames[:] = []
                skipped.append(dirpath + " (depth-cap)")
                continue
            # Skip hidden directories — node_modules, .git, .cache, .venv etc
            dirnames[:] = [d for d in dirnames if not d.startswith(".") and d not in
                           ("node_modules", "__pycache__", "tool_index")]

            for fname in filenames:
                ext = _os_files.path.splitext(fname)[1].lower()
                if ext not in extensions:
                    continue
                if not _fnmatch_files.fnmatch(fname.lower(), pattern.lower()):
                    continue
                full = _os_files.path.join(dirpath, fname)
                try:
                    size = _os_files.path.getsize(full)
                except OSError:
                    continue
                matches.append({
                    "path": full,
                    "size": size,
                    "ext": ext,
                    "rel_root": root,
                })
                if len(matches) >= max_results:
                    truncated = True
                    break
            if truncated:
                break
        if truncated:
            break

    return {
        "matches": matches,
        "n_matches": len(matches),
        "n_searched_roots": len(search_paths),
        "truncated": truncated,
        "skipped_dirs": skipped[:20],
        "extensions_used": sorted(extensions),
    }


async def _handle_filter_templates_by_hardware(args: Dict) -> Dict:
    """Filter templates by GPU VRAM + tag/category."""
    from pathlib import Path as _Path
    from typing import List as _List
    from ._shared import _detect_local_vram_gb
    # _load_template_manifests, _TEMPLATE_LIBRARY_DIR are module-local
    # (Phase 8 waves 7 + 18).
    device_vram_gb = args.get("device_vram_gb")
    if device_vram_gb is None:
        device_vram_gb = _detect_local_vram_gb()

    category = args.get("category")
    tag = args.get("tag")
    use_recommended = bool(args.get("include_recommended_only"))

    library_dir_arg = args.get("library_dir") or str(_TEMPLATE_LIBRARY_DIR)
    library_dir = _Path(library_dir_arg)
    manifests = _load_template_manifests(library_dir)

    matched: _List[Dict] = []
    rejected: _List[Dict] = []
    for m in manifests:
        min_vram = float(m.get("min_vram_gb", 0) or 0)
        rec_vram = float(m.get("recommended_vram_gb", min_vram) or min_vram)
        threshold = rec_vram if use_recommended else min_vram

        # Hardware gate
        if device_vram_gb is not None and threshold > 0 and device_vram_gb < threshold:
            rejected.append({
                "template": m.get("template") or m.get("name"),
                "reason": f"requires {threshold} GB VRAM, you have {device_vram_gb} GB",
            })
            continue

        # Category filter
        if category and m.get("category") and m["category"] != category:
            continue

        # Tag filter
        if tag and tag not in (m.get("tags") or []):
            continue

        matched.append({
            "template": m.get("template") or m.get("name"),
            "description": m.get("description", ""),
            "min_vram_gb": m.get("min_vram_gb"),
            "recommended_vram_gb": m.get("recommended_vram_gb"),
            "estimated_fps": m.get("estimated_fps", {}),
            "tags": m.get("tags", []),
            "category": m.get("category"),
            "path": m.get("_template_dir"),
        })

    return {
        "device_vram_gb": device_vram_gb,
        "library_dir": str(library_dir),
        "matched_count": len(matched),
        "matched": matched,
        "rejected_count": len(rejected),
        "rejected": rejected,
    }


async def _handle_list_scene_templates(args: Dict) -> Dict:
    """List available scene templates, optionally filtered by category."""
    # Phase 8 wave 7 — _SCENE_TEMPLATES migrated to module body.
    category = args.get("category", "").lower()

    templates = []
    for name, tmpl in _SCENE_TEMPLATES.items():
        if category and tmpl.get("category", "") != category:
            continue
        templates.append({
            "name": name,
            "description": tmpl["description"],
            "category": tmpl.get("category", "general"),
            "object_count": len(tmpl["objects"]),
            "room_dims": tmpl["room_dims"],
        })

    return {
        "templates": templates,
        "count": len(templates),
        "total_available": len(_SCENE_TEMPLATES),
    }


async def _handle_load_scene_template(args: Dict) -> Dict:
    """Load a scene template by name. Returns a blueprint compatible with build_scene_from_blueprint."""
    # Phase 8 wave 7 — _SCENE_TEMPLATES migrated to module body.
    template_name = args.get("template_name", "").lower().replace(" ", "_").replace("-", "_")

    if template_name not in _SCENE_TEMPLATES:
        available = list(_SCENE_TEMPLATES.keys())
        return {
            "error": f"Template '{template_name}' not found.",
            "available_templates": available,
        }

    tmpl = _SCENE_TEMPLATES[template_name]

    # Build a blueprint dict compatible with build_scene_from_blueprint
    blueprint_objects = []
    for obj in tmpl["objects"]:
        bp_obj = {
            "name": obj["name"],
            "prim_path": obj.get("prim_path", f"/World/{obj['name']}"),
            "position": obj.get("position", [0, 0, 0]),
            "rotation": obj.get("rotation", [0, 0, 0]),
            "scale": obj.get("scale", [1, 1, 1]),
        }
        if obj.get("prim_type"):
            bp_obj["prim_type"] = obj["prim_type"]
        if obj.get("asset_name"):
            bp_obj["asset_name"] = obj["asset_name"]
        if obj.get("asset_path"):
            bp_obj["asset_path"] = obj["asset_path"]
        blueprint_objects.append(bp_obj)

    blueprint = {
        "description": tmpl["description"],
        "room_dimensions": tmpl["room_dims"],
        "objects": blueprint_objects,
        "suggested_sensors": tmpl.get("suggested_sensors", []),
        "physics_settings": tmpl.get("physics_settings", {}),
    }

    return {
        "template_name": template_name,
        "blueprint": blueprint,
        "object_count": len(blueprint_objects),
        "message": (
            f"Template '{template_name}' loaded with {len(blueprint_objects)} objects. "
            "Call build_scene_from_blueprint with the 'blueprint' field to create the scene."
        ),
    }


async def _handle_generate_scene_blueprint(args: Dict) -> Dict:
    """Generate a scene blueprint (data, not code). The LLM fills in the spatial layout."""
    description = args.get("description", "")
    room_dims = args.get("room_dimensions")
    available = args.get("available_assets")

    # If no assets provided, search the catalog
    if not available:
        catalog_result = await _handle_catalog_search({"query": description, "limit": 20})
        available = catalog_result.get("results", [])

    return {
        "type": "blueprint_request",
        "description": description,
        "room_dimensions": room_dims or [6, 6, 3],
        "available_assets": available,
        "instructions": (
            "Based on the description and available assets, generate a blueprint JSON with: "
            "objects: [{name, asset_path (from available_assets), prim_path (/World/Name), "
            "position [x,y,z], rotation [rx,ry,rz], scale [sx,sy,sz]}]. "
            "Ensure objects don't overlap, items sit ON surfaces (not floating), "
            "robots have 1m clearance. Then call build_scene_from_blueprint with the blueprint."
        ),
    }


async def _handle_export_scene_package(args: Dict) -> Dict:
    """Export the current session's scene setup as a reusable file package."""
    import re as _re
    from datetime import datetime as _dt
    from pathlib import Path as _Path
    from ...routes import _audit

    session_id = args.get("session_id", "default_session")
    scene_name = args.get("scene_name", "exported_scene")
    # Sanitize scene_name for filesystem
    safe_name = "".join(c if c.isalnum() or c in "_-" else "_" for c in scene_name)

    out_dir = _Path("workspace/scene_exports") / safe_name
    out_dir.mkdir(parents=True, exist_ok=True)

    # ── Collect approved patches from audit log ──────────────────────────
    entries = _audit.query_logs(limit=500, event_type="patch_executed")
    patches = []
    for e in entries:
        meta = e.metadata or {}
        if meta.get("success") and meta.get("session_id", "default_session") == session_id:
            code = meta.get("code", "")
            if code:
                patches.append({
                    "description": meta.get("user_message", ""),
                    "code": code,
                })

    if not patches:
        # Fallback: grab all successful patches regardless of session
        for e in entries:
            meta = e.metadata or {}
            if meta.get("success") and meta.get("code"):
                patches.append({
                    "description": meta.get("user_message", ""),
                    "code": meta["code"],
                })

    # ── scene_setup.py ───────────────────────────────────────────────────
    setup_lines = [
        '"""',
        f'Scene Setup: {scene_name}',
        f'Auto-exported by Isaac Assist on {_dt.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")}',
        f'Patches: {len(patches)}',
        '"""',
        'import omni.usd',
        'from pxr import Usd, UsdGeom, UsdPhysics, PhysxSchema, Gf, Sdf, UsdShade',
        '',
        'stage = omni.usd.get_context().get_stage()',
        '',
    ]
    for i, p in enumerate(patches):
        desc = p["description"] or f"Step {i+1}"
        setup_lines.append(f'# ── Step {i+1}: {desc}')
        setup_lines.append(p["code"].rstrip())
        setup_lines.append('')
    setup_lines.append('print("Scene setup complete.")')
    scene_py = "\n".join(setup_lines)
    (out_dir / "scene_setup.py").write_text(scene_py, encoding="utf-8")

    # ── Detect ROS2 topics from OmniGraph patterns in code ───────────────
    ros2_topics = set()
    og_node_types = set()
    robot_paths = set()
    for p in patches:
        code = p["code"]
        # topics: /joint_states, /joint_command, /clock, /tf, etc.
        ros2_topics.update(_re.findall(r"""['\"](/[a-zA-Z_][a-zA-Z0-9_/]*)['\"]\s*""", code))
        # OmniGraph node types
        og_node_types.update(_re.findall(r"""['\"](?:isaacsim|omni\.isaac)\.[a-zA-Z0-9_.]+['\"]""", code))
        # Robot paths
        robot_paths.update(_re.findall(r"""['\"](/World/[A-Z][a-zA-Z0-9_]*)['\"]\s*""", code))
    # Filter to ROS2-style topics only (not USD paths, not physics scene attrs)
    _NON_TOPIC_PREFIXES = ("/World", "/Physics", "/Collision", "/persistent", "/Render", "/OmniKit")
    ros2_topics = sorted(
        t for t in ros2_topics
        if not any(t.startswith(p) for p in _NON_TOPIC_PREFIXES)
        and len(t) > 2  # skip bare "/"
        and not t.endswith(".usd")
    )

    # ── ros2_topics.yaml ─────────────────────────────────────────────────
    if ros2_topics or og_node_types:
        topic_lines = [f"# ROS2 Topics detected in scene: {scene_name}", "topics:"]
        for t in sorted(ros2_topics):
            topic_lines.append(f"  - name: \"{t}\"")
        topic_lines.append("")
        topic_lines.append("omnigraph_node_types:")
        for nt in sorted(og_node_types):
            topic_lines.append(f"  - {nt}")
        (out_dir / "ros2_topics.yaml").write_text("\n".join(topic_lines) + "\n", encoding="utf-8")

    # ── ros2_launch.py (if ROS2 topics detected) ────────────────────────
    has_ros2 = bool(ros2_topics)
    if has_ros2:
        launch_lines = [
            '"""',
            f'ROS2 Launch File for scene: {scene_name}',
            f'Auto-generated by Isaac Assist on {_dt.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")}',
            '"""',
            'from launch import LaunchDescription',
            'from launch_ros.actions import Node',
            '',
            '',
            'def generate_launch_description():',
            '    return LaunchDescription([',
        ]
        # Add placeholder nodes for each topic
        for t in sorted(ros2_topics):
            node_name = t.strip("/").replace("/", "_")
            launch_lines.append(f'        # Topic: {t}')
            launch_lines.append(f'        # Node("{node_name}") — configure publisher/subscriber as needed')
        launch_lines.append('    ])')
        (out_dir / "ros2_launch.py").write_text("\n".join(launch_lines) + "\n", encoding="utf-8")

    # ── README.md ────────────────────────────────────────────────────────
    robot_list = ", ".join(f"`{r}`" for r in sorted(robot_paths)) or "None detected"
    topic_list = "\n".join(f"- `{t}`" for t in sorted(ros2_topics)) or "- None detected"
    readme = f"""# {scene_name}

Auto-exported by **Isaac Assist** on {_dt.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")}.

## Scene Summary

- **Patches applied:** {len(patches)}
- **Robots:** {robot_list}
- **ROS2 Topics:**
{topic_list}

## Files

| File | Description |
|------|-------------|
| `scene_setup.py` | All approved code patches as a single runnable script |
| `ros2_topics.yaml` | Detected ROS2 topics and OmniGraph node types |
{"| `ros2_launch.py` | ROS2 launch file template |" if has_ros2 else ""}
| `README.md` | This file |

## Usage

### Replay Scene in Isaac Sim
```python
# In Isaac Sim Script Editor or via Kit RPC:
exec(open("{out_dir}/scene_setup.py").read())
```

### ROS2 Topics
{"Launch the ROS2 nodes alongside Isaac Sim:" if has_ros2 else "No ROS2 topics detected in this scene."}
{"```bash" if has_ros2 else ""}
{"ros2 launch " + str(out_dir / "ros2_launch.py") if has_ros2 else ""}
{"```" if has_ros2 else ""}
"""
    (out_dir / "README.md").write_text(readme, encoding="utf-8")

    files_written = ["scene_setup.py", "README.md"]
    if ros2_topics or og_node_types:
        files_written.append("ros2_topics.yaml")
    if has_ros2:
        files_written.append("ros2_launch.py")

    return {
        "export_dir": str(out_dir),
        "files": files_written,
        "patch_count": len(patches),
        "ros2_topics": ros2_topics,
        "robots_detected": sorted(robot_paths),
        "message": f"Exported {len(patches)} patches to {out_dir}/ — files: {', '.join(files_written)}",
    }


# ---------------------------------------------------------------------------
# Registration


def register(
    data: Dict[str, Callable[..., Any]],
    codegen: Dict[str, Callable[..., Any]],
) -> None:
    """Phase 9 — populate dispatch dicts with this module's handlers.

    Called by `handlers/_dispatch.py:register_handlers()` which is the
    sole dispatch entry point from `tool_executor.py`.
    """
    # Data handlers (12)
    data["catalog_search"] = _handle_catalog_search
    data["download_asset"] = _handle_download_asset
    data["export_scene_package"] = _handle_export_scene_package
    data["filter_templates_by_hardware"] = _handle_filter_templates_by_hardware
    data["generate_scene_blueprint"] = _handle_generate_scene_blueprint
    data["list_local_files"] = _handle_list_local_files
    data["list_scene_templates"] = _handle_list_scene_templates
    data["load_scene_template"] = _handle_load_scene_template
    data["lookup_api_deprecation"] = _handle_lookup_api_deprecation
    data["lookup_knowledge"] = _handle_lookup_knowledge
    data["lookup_product_spec"] = _handle_lookup_product_spec
    data["nucleus_browse"] = _handle_nucleus_browse

    # Code-gen handlers (3)
    codegen["build_scene_from_blueprint"] = _gen_build_scene_from_blueprint
    codegen["export_template"] = _gen_export_template
    codegen["import_template"] = _gen_import_template

