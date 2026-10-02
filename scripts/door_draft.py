#!/usr/bin/env python3
"""Door tier of articulation drafting: leaf, frame, push bar, hinge, latch.

Geometry cannot say that a door opens, but once the class says "door" it can
say a lot: the leaf is the slab that fills the opening, a push (panic) bar is
a long horizontal part at hand height on one face, the hinge is on the side
the bar points away from, and the leaf swings away from the bar's face. The
class prior (asset_class_priors.json, "door" -> "mechanism_templates")
supplies what geometry cannot measure: travel, swing range, masses, and the
latch that couples the bar to the hinge.

Scan doors often model the leaf INTO its frame (shared vertices), so the
first step may be a split: the leaf's front and back faces are the large
faces that span most of the opening, and the leaf is every face lying in the
slab between them.

The result is a DRAFT for the reviewer, in the hub's articulation spec
format, plus the extra keys apply_articulation understands (link_masses,
no_collision, filtered_pairs, mechanisms).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).resolve().parent))

PRIORS = REPO / "workspace" / "knowledge" / "asset_class_priors.json"
BIG_FACE_SPAN = 0.6     # a leaf face spans this much of the part's width and height
FLAT_TOL_M = 0.001


def door_template() -> dict:
    door = json.loads(PRIORS.read_text())["classes"]["door"]
    return door.get("mechanism_templates", {}).get("panic_bar_latch", {})


def _world_faces(stage, mesh_path):
    from pxr import Gf, UsdGeom

    prim = stage.GetPrimAtPath(mesh_path)
    mesh = UsdGeom.Mesh(prim)
    to_world = UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(0)
    pts = [to_world.Transform(Gf.Vec3d(*p)) for p in mesh.GetPointsAttr().Get()]
    counts, idx = mesh.GetFaceVertexCountsAttr().Get(), mesh.GetFaceVertexIndicesAttr().Get()
    faces, off = [], 0
    for c in counts:
        faces.append([pts[idx[off + k]] for k in range(c)])
        off += c
    return faces


def leaf_slab(stage, mesh_path, width_axis, thick_axis):
    """The leaf's box inside a fused frame+leaf mesh, from its big faces."""
    faces = _world_faces(stage, mesh_path)
    lo = [min(p[k] for f in faces for p in f) for k in range(3)]
    hi = [max(p[k] for f in faces for p in f) for k in range(3)]
    big = []
    for f in faces:
        span_w = max(p[width_axis] for p in f) - min(p[width_axis] for p in f)
        span_h = max(p[2] for p in f) - min(p[2] for p in f)
        flat = max(p[thick_axis] for p in f) - min(p[thick_axis] for p in f) < FLAT_TOL_M
        if flat and span_w >= BIG_FACE_SPAN * (hi[width_axis] - lo[width_axis]) * 0.5 \
                and span_h >= BIG_FACE_SPAN * (hi[2] - lo[2]) * 0.5:
            big.append(f)
    planes = sorted({round(f[0][thick_axis], 4) for f in big})
    if len(planes) < 2:
        raise RuntimeError("could not find the leaf's front and back faces "
                           f"({len(planes)} large flat plane(s)) — split the leaf by hand")
    t0, t1 = planes[0], planes[-1]
    eps = 0.25 * (t1 - t0)
    w0 = min(p[width_axis] for f in big for p in f) - 0.005
    w1 = max(p[width_axis] for f in big for p in f) + 0.005
    top = max(p[2] for f in big for p in f) + 0.005
    box_lo, box_hi = [None, None, None], [None, None, None]
    box_lo[thick_axis], box_hi[thick_axis] = t0 - eps, t1 + eps
    box_lo[width_axis], box_hi[width_axis] = w0, w1
    box_hi[2] = top
    return box_lo, box_hi


def propose_door(stage, asset_root: str) -> tuple[dict, list[str]]:
    """Returns (spec draft, notes). May split a fused leaf out of its frame."""
    from articulation_draft import collect_parts
    from segment_mesh import split_mesh_by_box

    tpl = door_template()
    notes = []
    parts = collect_parts(stage, asset_root)
    if not parts:
        raise RuntimeError("no mesh parts")
    lo = [min(p["min"][k] for p in parts) for k in range(3)]
    hi = [max(p["max"][k] for p in parts) for k in range(3)]
    width_axis = 0 if (hi[0] - lo[0]) >= (hi[1] - lo[1]) else 1
    thick = 1 - width_axis
    W = "XYZ"[width_axis]
    T = "XYZ"[thick]

    # 1. leaf and frame: split a fused shell first
    biggest = max(parts, key=lambda p: p["volume"])
    others_like_leaf = [p for p in parts if p is not biggest
                        and p["size"][width_axis] > 0.6 * biggest["size"][width_axis]
                        and p["size"][2] > 0.6 * biggest["size"][2]]
    if not others_like_leaf:
        box_lo, box_hi = leaf_slab(stage, biggest["path"], width_axis, thick)
        split_mesh_by_box(stage, biggest["path"], box_lo, box_hi, "DoorLeaf", "DoorFrame")
        notes.append("split the leaf out of the frame by its front/back faces")
        parts = collect_parts(stage, asset_root)
    leaf = max((p for p in parts if p["path"].endswith("DoorLeaf")), key=lambda p: p["volume"], default=None) \
        or sorted(parts, key=lambda p: p["volume"])[-2]
    frame = max((p for p in parts if p["path"].endswith("DoorFrame")), key=lambda p: p["volume"], default=None) \
        or max(parts, key=lambda p: p["volume"])
    leaf_mid_t = leaf["centroid"][thick]
    leaf_mid_w = leaf["centroid"][width_axis]

    # 2. push bar: long, low, at hand height, standing proud of one face
    band = tpl.get("bar_height_m", [0.75, 1.3])
    bars = [p for p in parts if p not in (leaf, frame)
            and p["size"][width_axis] >= 0.35 * leaf["size"][width_axis]
            and p["size"][2] <= 0.12
            and band[0] <= p["centroid"][2] - leaf["min"][2] <= band[1]
            and (p["min"][thick] < leaf["min"][thick] or p["max"][thick] > leaf["max"][thick])]
    bar = max(bars, key=lambda p: p["size"][width_axis]) if bars else None
    if bar:
        push_sign = -1 if bar["centroid"][thick] < leaf_mid_t else 1
        # the bar reaches toward the latch edge
        latch_sign = 1 if (bar["max"][width_axis] - leaf_mid_w) > (leaf_mid_w - bar["min"][width_axis]) else -1
    else:
        push_sign, latch_sign = -1, 1
        notes.append("no push bar found: hinge side and swing direction are GUESSES — check them")
    swing_sign = -push_sign  # a door opens away from the side you push

    hinge_w = leaf["min"][width_axis] if latch_sign > 0 else leaf["max"][width_axis]
    swing_face = leaf["max"][thick] if swing_sign > 0 else leaf["min"][thick]
    anchor = [0.0, 0.0, round(0.5 * (leaf["min"][2] + leaf["max"][2]), 4)]
    anchor[width_axis], anchor[thick] = round(hinge_w, 4), round(swing_face, 4)
    # +angle about +Z moves the latch edge along Z x r; open toward swing_sign
    r_w = latch_sign  # direction from hinge to latch along the width axis
    z_cross_r_thick = r_w if (width_axis == 0) else -r_w
    swing_deg = float(tpl.get("swing_deg", 90))
    limits = [0.0, swing_deg] if z_cross_r_thick * swing_sign > 0 else [-swing_deg, 0.0]

    joints = [{"name": "door_hinge", "joint_type": "revolute", "parent_prim": frame["path"],
               "child_prim": leaf["path"], "axis": "Z", "lower_limit": limits[0],
               "upper_limit": limits[1], "anchor": anchor,
               "stiffness": 0.0, "damping": float(tpl.get("hinge_damping", 0.2)), "max_force": 1.0e6}]
    no_collision, filtered = [], []
    masses = {frame["path"]: float(tpl.get("frame_kg", 20)), leaf["path"]: float(tpl.get("leaf_kg", 35))}
    if bar:
        travel = float(tpl.get("bar_travel_m", 0.02))
        bar_limits = [0.0, travel] if -push_sign > 0 else [-travel, 0.0]
        b_anchor = list(bar["centroid"])
        b_anchor[thick] = leaf["min"][thick] if push_sign < 0 else leaf["max"][thick]
        joints.append({"name": "crash_bar_push", "joint_type": "prismatic", "parent_prim": leaf["path"],
                       "child_prim": bar["path"], "axis": T, "lower_limit": bar_limits[0],
                       "upper_limit": bar_limits[1], "anchor": [round(v, 4) for v in b_anchor],
                       "stiffness": float(tpl.get("bar_spring_n_per_m", 2500)),
                       "damping": float(tpl.get("bar_damping", 50)), "max_force": 1.0e4})
        masses[bar["path"]] = float(tpl.get("bar_kg", 2))
        filtered.append([frame["path"], bar["path"]])
    # 3. everything else inside the leaf's footprint rides on the leaf
    for p in parts:
        if p in (leaf, frame, bar):
            continue
        inside = all(leaf["min"][k] - 0.01 <= p["centroid"][k] <= leaf["max"][k] + 0.01 for k in (width_axis, 2))
        parent = leaf if inside else frame
        name = f"{Path(p['path']).name}_on_{'leaf' if inside else 'frame'}"
        joints.append({"name": name.replace("-", "_"), "joint_type": "fixed",
                       "parent_prim": parent["path"], "child_prim": p["path"]})
        flat = min(p["size"]) < FLAT_TOL_M
        if flat:
            no_collision.append(p["path"])  # a plane has no volume to collide with
        masses[p["path"]] = float(tpl.get("small_part_kg", 1.5))

    spec = {
        "prim_path": asset_root, "fixed_base": True, "approximation": "convexDecomposition",
        "joints": joints, "link_masses": masses, "no_collision": no_collision,
        "filtered_pairs": filtered,
        "mechanisms": ([{"type": "latch", "hinge_joint": "door_hinge", "actuator_joint": "crash_bar_push",
                         "leaf": leaf["path"], "frame": frame["path"]}] if bar else []),
        "_analysis": {"tier": "door", "leaf": leaf["path"], "frame": frame["path"],
                      "push_bar": bar["path"] if bar else None,
                      "push_side": f"{'-' if push_sign < 0 else '+'}{T}",
                      "hinge_edge": f"{W}={round(hinge_w, 3)}", "notes": notes},
        "_instructions": ("Door draft from geometry + the door class prior: hinge on the edge "
                          "the push bar points away from, swinging away from the bar's face; "
                          "bar sprung, coupled to a latch. CHECK the hinge side and swing "
                          "before applying; remove _analysis/_instructions when done."),
    }
    return spec, notes
