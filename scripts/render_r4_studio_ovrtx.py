"""Render all 5 engineering qualification & capture-glove parity views of
Amber v0.5 `tool_gripper_r4` + docked `Wrist_Tool_Changer_R2` in Blender 5.2.1 LTS:

  1. `r4_open_with_changer.png`: Exact 3/4 ulnar-palmar camera framing matching the CSU Chico
     Capture Glove (`media_1795214756074_c806eaec.jpg`: Thumb bottom-left, Index middle-left,
     Wide Coupled 3-Finger Paddle top, Notched Grey Rotary Hubs & Framed Cheek Pod right, and
     docked `Wrist_Tool_Changer_R2` at the wrist base).
  2. `r4_palm_drives_cutaway.png`: Lower palm drive belly shell hidden (`hide_shell=True`),
     revealing the compact 2-layer, 3-deck (`floor_deck`, `mid_deck`, `top_deck`) 6x worm-locked
     SK78 spool drives + 7th thumb-opposition worm + `500 Hz` reflex PCB + `4x FSR07` palm pads.
  3. `r4_wrap_bottle_1l.png`: `1.04 kg` wet `Ø81 mm` 1 L bottle cradled horizontally in the
     C-mouth on the `Shore A 60` PU palmar saddle with Thumb at `+20°` and overhanging fingers.
  4. `r4_pinch_pen_table.png`: `Ø8.0 mm` pen resting against the tabletop plane (`~0.50 mm` flat
     bevel tip clearance, `GR3-PEN-01`) pinched between Thumb, Index, and Coupled Ulnar.
  5. `r4_bilateral_pair_ovrtx.png`: Right + Left Glove-Matched R4 grippers side-by-side on
     un-mirrored `Wrist_Tool_Changer_R2` couplings.
"""
from __future__ import annotations

import json
from pathlib import Path
import bpy
from mathutils import Vector

REPO_ROOT = Path(__file__).resolve().parents[1]
R4_DIR = REPO_ROOT / "workspace" / "amber_gripper_r4"
RENDERS_DIR = R4_DIR / "renders"


def _clear_scene() -> None:
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    for mesh in list(bpy.data.meshes):
        if mesh.users == 0:
            bpy.data.meshes.remove(mesh)


def _make_pbr_mat(name: str, rgb: list[float], material_tag: str) -> bpy.types.Material:
    mat = bpy.data.materials.get(name)
    if mat is not None:
        return mat
    mat = bpy.data.materials.new(name=name)
    if mat.node_tree is None:
        mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    if bsdf is not None:
        bsdf.inputs["Base Color"].default_value = (rgb[0], rgb[1], rgb[2], 1.0)
        if material_tag in ("7075-T6", "steel_303", "52100", "cusn12_bronze"):
            bsdf.inputs["Metallic"].default_value = 0.78
            bsdf.inputs["Roughness"].default_value = 0.28
        elif material_tag == "pu_shore_a60":
            bsdf.inputs["Metallic"].default_value = 0.02
            bsdf.inputs["Roughness"].default_value = 0.62
        else:
            bsdf.inputs["Metallic"].default_value = 0.03
            bsdf.inputs["Roughness"].default_value = 0.48
    return mat


def _load_records(records: list[dict], prefix: str = "", y_offset_m: float = 0.0, hide_shell: bool = False) -> None:
    for rec in records:
        if hide_shell and rec.get("is_shell", False):
            continue
        bpy.ops.wm.stl_import(filepath=rec["stl_path"], global_scale=0.001)
        obj = bpy.context.selected_objects[0]
        obj.name = f"{prefix}{rec['name']}"
        obj.location.y += y_offset_m
        mkey = f"{rec['material']}_{rec['rgb'][0]:.2f}_{rec['rgb'][1]:.2f}_{rec['rgb'][2]:.2f}"
        mat = _make_pbr_mat(mkey, rec["rgb"], rec["material"])
        obj.data.materials.clear()
        obj.data.materials.append(mat)


def _setup_lighting_and_backdrop(z_floor_m: float = -0.086, add_table_x_m: float | None = None) -> None:
    bpy.ops.mesh.primitive_plane_add(size=2.6, location=(0.0, 0.0, z_floor_m))
    floor = bpy.context.active_object
    floor.data.materials.append(_make_pbr_mat("StudioFloor", [0.10, 0.12, 0.16], "floor"))

    if add_table_x_m is not None:
        bpy.ops.mesh.primitive_cube_add(size=1.0, location=(add_table_x_m - 0.003, 0.0, 0.102))
        tbl = bpy.context.active_object
        tbl.scale = (0.006, 0.16, 0.15)
        tmat = _make_pbr_mat("PinchTableSlate", [0.42, 0.50, 0.62], "floor")
        tbl.data.materials.append(tmat)

    world = bpy.data.worlds.new("R4GloveStudio")
    if world.node_tree is None:
        world.use_nodes = True
    bg = next((n for n in world.node_tree.nodes if n.type == "BACKGROUND"), None)
    if bg:
        bg.inputs["Color"].default_value = (0.058, 0.070, 0.092, 1.0)
        bg.inputs["Strength"].default_value = 0.85
    bpy.context.scene.world = world

    # Balanced key-to-fill directional lighting so white planar chamfers NEVER blow out!
    lights = [
        ("Key", (-0.48, -0.52, 0.56), 22.0, (1.0, 0.98, 0.95)),
        ("Fill", (-0.36, 0.55, 0.34), 10.0, (0.82, 0.89, 1.0)),
        ("RimBack", (0.48, -0.32, 0.44), 16.0, (0.94, 0.97, 1.0)),
    ]
    for lname, loc, energy, col in lights:
        ldata = bpy.data.lights.new(name=lname, type="AREA")
        ldata.energy = energy
        ldata.size = 0.42
        ldata.color = col
        lobj = bpy.data.objects.new(name=lname, object_data=ldata)
        bpy.context.collection.objects.link(lobj)
        lobj.location = Vector(loc)
        lobj.rotation_euler = (Vector((-0.01, 0.0, 0.085)) - Vector(loc)).to_track_quat("-Z", "Y").to_euler()


def _aim_and_render(cam_loc: tuple[float, float, float], target: tuple[float, float, float], lens_mm: float, out_path: Path) -> None:
    RENDERS_DIR.mkdir(parents=True, exist_ok=True)
    cdata = bpy.data.cameras.new("Cam")
    cdata.lens = lens_mm
    cdata.clip_start = 0.005
    cdata.clip_end = 20.0
    cam = bpy.data.objects.new("Cam", cdata)
    bpy.context.collection.objects.link(cam)
    bpy.context.scene.camera = cam
    cam.location = Vector(cam_loc)
    cam.rotation_euler = (Vector(target) - Vector(cam_loc)).to_track_quat("-Z", "Y").to_euler()

    scn = bpy.context.scene
    scn.render.engine = "CYCLES"
    scn.cycles.samples = 64
    scn.render.use_freestyle = True
    scn.render.line_thickness = 0.55
    for vl in scn.view_layers:
        vl.use_freestyle = True
        if vl.freestyle_settings.linesets:
            ls = vl.freestyle_settings.linesets[0]
            ls.select_silhouette = True
            ls.select_crease = True
            ls.select_border = True
            ls.linestyle.color = (0.18, 0.21, 0.26)
            ls.linestyle.alpha = 0.45
    scn.render.resolution_x = 1280
    scn.render.resolution_y = 900
    scn.render.resolution_percentage = 100
    scn.render.image_settings.file_format = "PNG"
    scn.render.filepath = str(out_path)
    bpy.ops.render.render(write_still=True)


def main() -> None:
    right_recs = json.loads((R4_DIR / "cad" / "fcstd_parts_right_meshes.json").read_text(encoding="utf-8"))
    left_recs_path = R4_DIR / "cad" / "fcstd_parts_left_meshes.json"
    left_recs = json.loads(left_recs_path.read_text(encoding="utf-8")) if left_recs_path.exists() else right_recs

    # 1. Hero view on Wrist Tool Changer R2 — EXACT camera angle & orientation of `media_1795214756074_c806eaec.jpg`!
    #    (Thumb bottom-left, Index middle-left, Wide Coupled Paddle top, Notched Rotary Hubs & Cheek Pod right!)
    _clear_scene()
    _load_records(left_recs, prefix="hero_", hide_shell=False)
    _setup_lighting_and_backdrop()
    open_path = RENDERS_DIR / "r4_open_with_changer.png"
    _aim_and_render(cam_loc=(-0.275, -0.320, 0.195), target=(-0.014, 0.0, 0.070), lens_mm=45.0, out_path=open_path)

    # 2. Internal 2-Layer 3-Deck Spool Drives Cutaway (`hide_shell=True`, showing 6x worm spools + 7th thumb worm!)
    _clear_scene()
    _load_records([r for r in left_recs if not r["name"].startswith("wtc_")], prefix="drv_", hide_shell=True)
    _setup_lighting_and_backdrop(z_floor_m=0.012)
    drives_path = RENDERS_DIR / "r4_palm_drives_cutaway.png"
    _aim_and_render(cam_loc=(0.245, -0.255, 0.182), target=(0.004, 0.0, 0.078), lens_mm=45.0, out_path=drives_path)

    # 3. `wrap_bottle_1l` (Ø81 mm, 1.04 kg bottle cradled in the C-mouth on Shore A 60 PU saddle!)
    bottle_json = R4_DIR / "cad" / "pose_meshes_wrap_bottle_1l.json"
    bottle_path = RENDERS_DIR / "r4_wrap_bottle_1l.png"
    if bottle_json.exists():
        _clear_scene()
        _load_records(json.loads(bottle_json.read_text(encoding="utf-8")), prefix="wrap_")
        _setup_lighting_and_backdrop()
        _aim_and_render(cam_loc=(-0.31, -0.33, 0.205), target=(-0.016, 0.0, 0.072), lens_mm=43.0, out_path=bottle_path)

    # 4. `pinch_pen` (Ø8 mm pen pinched 0.50 mm clear of tabletop!)
    pen_json = R4_DIR / "cad" / "pose_meshes_pinch_pen.json"
    pen_path = RENDERS_DIR / "r4_pinch_pen_table.png"
    if pen_json.exists():
        recs_pen = json.loads(pen_json.read_text(encoding="utf-8"))
        table_x_m = -0.06735
        _clear_scene()
        _load_records(recs_pen, prefix="pen_")
        pen_objs = [o for o in bpy.data.objects if "benchmark_8mm_pen" in o.name]
        if pen_objs:
            pobj = pen_objs[0]
            xs = [(pobj.matrix_world @ Vector(c)).x for c in pobj.bound_box]
            table_x_m = min(xs)
        _setup_lighting_and_backdrop(add_table_x_m=table_x_m)
        _aim_and_render(cam_loc=(-0.035, -0.355, 0.135), target=(-0.048, 0.0, 0.088), lens_mm=48.0, out_path=pen_path)

    # 5. Bilateral Right + Left Pair on standardized Wrist Tool Changers!
    pair_path = RENDERS_DIR / "r4_bilateral_pair_ovrtx.png"
    _clear_scene()
    _load_records(left_recs, prefix="left_", y_offset_m=-0.095)
    _load_records(right_recs, prefix="right_", y_offset_m=0.095)
    _setup_lighting_and_backdrop()
    _aim_and_render(cam_loc=(-0.44, -0.20, 0.24), target=(-0.012, 0.0, 0.070), lens_mm=38.0, out_path=pair_path)

    print(
        json.dumps(
            {
                "open": str(open_path),
                "palm_drives": str(drives_path),
                "wrap_bottle_1l": str(bottle_path),
                "pinch_pen": str(pen_path),
                "bilateral_pair": str(pair_path),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
