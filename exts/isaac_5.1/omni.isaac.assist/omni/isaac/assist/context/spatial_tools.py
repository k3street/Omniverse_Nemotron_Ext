"""Spatial-awareness helpers for the Kit RPC server.

Purpose-built to remove the frame/orientation bug class from agent
workflows: every quaternion returned is explicitly labeled in BOTH
conventions, poses can be expressed relative to any reference prim, and
axes gizmos make a proposed frame visible before any motion executes.

Read-only USD access is safe from the RPC background thread (same
pattern as stage_reader); the debug-draw gizmo must run on Kit's main
thread — build_draw_axes_code() returns source for the sync-exec tick.
"""

from __future__ import annotations

import math


def _quat_wxyz_to_matrix(w, x, y, z):
    return [
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
        [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
        [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
    ]


def _rpy_from_matrix(m):
    sy = math.sqrt(m[0][0] ** 2 + m[1][0] ** 2)
    if sy > 1e-9:
        return [math.atan2(m[2][1], m[2][2]),
                math.atan2(-m[2][0], sy),
                math.atan2(m[1][0], m[0][0])]
    return [math.atan2(-m[1][2], m[1][1]), math.atan2(-m[2][0], sy), 0.0]


def _labeled_pose(translation, quat_wxyz):
    w, x, y, z = quat_wxyz
    matrix = _quat_wxyz_to_matrix(w, x, y, z)
    return {
        "position": [round(float(v), 6) for v in translation],
        "quaternion_wxyz": [round(float(v), 6) for v in (w, x, y, z)],
        "quaternion_xyzw": [round(float(v), 6) for v in (x, y, z, w)],
        "rpy_rad": [round(float(v), 6) for v in _rpy_from_matrix(matrix)],
        "rpy_deg": [round(math.degrees(float(v)), 3)
                    for v in _rpy_from_matrix(matrix)],
    }


def _world_transform(stage, prim_path):
    from pxr import Usd, UsdGeom

    prim = stage.GetPrimAtPath(prim_path)
    if not prim or not prim.IsValid():
        raise ValueError(f"prim not found: {prim_path}")
    xformable = UsdGeom.Xformable(prim)
    if not xformable:
        raise ValueError(f"prim is not Xformable: {prim_path}")
    return xformable.ComputeLocalToWorldTransform(Usd.TimeCode.Default())


# ── Fabric-aware live poses ──────────────────────────────────────────────
# Isaac Lab (and any omni.physx fabric pipeline) steps physics through
# Fabric and never writes back to the USD stage, so USD reads return the
# authoring-time transform all run.  usdrt reads the Fabric world
# transform PhysX actually produced this frame.


def _quat_from_row_matrix(m):
    """quat wxyz from a Gf row-vector-convention 4x4 (rows = basis images).

    A row-convention rotation is the transpose of the standard column
    convention, so the standard extraction is run on the transpose.
    """
    r = [[m[c][row] for c in range(3)] for row in range(3)]
    trace = r[0][0] + r[1][1] + r[2][2]
    if trace > 0.0:
        s = math.sqrt(trace + 1.0) * 2.0
        return [0.25 * s, (r[2][1] - r[1][2]) / s,
                (r[0][2] - r[2][0]) / s, (r[1][0] - r[0][1]) / s]
    if r[0][0] > r[1][1] and r[0][0] > r[2][2]:
        s = math.sqrt(1.0 + r[0][0] - r[1][1] - r[2][2]) * 2.0
        return [(r[2][1] - r[1][2]) / s, 0.25 * s,
                (r[0][1] + r[1][0]) / s, (r[0][2] + r[2][0]) / s]
    if r[1][1] > r[2][2]:
        s = math.sqrt(1.0 + r[1][1] - r[0][0] - r[2][2]) * 2.0
        return [(r[0][2] - r[2][0]) / s, (r[0][1] + r[1][0]) / s,
                0.25 * s, (r[1][2] + r[2][1]) / s]
    s = math.sqrt(1.0 + r[2][2] - r[0][0] - r[1][1]) * 2.0
    return [(r[1][0] - r[0][1]) / s, (r[0][2] + r[2][0]) / s,
            (r[1][2] + r[2][1]) / s, 0.25 * s]


def _fabric_world_pose(prim_path: str):
    """(position, quat_wxyz) from Fabric, or None if unavailable.

    Live-validated 2026-08-05: physics writes `omni:fabric:worldMatrix`
    (row-vector convention, translation in row 3); the Rt.Xformable
    world attrs are NOT populated in the Isaac Lab pipeline
    (HasWorldXform() is False) and remain only as a fallback.
    """
    try:
        import omni.usd
        import usdrt

        stage_id = omni.usd.get_context().get_stage_id()
        rt_stage = usdrt.Usd.Stage.Attach(stage_id)
        prim = rt_stage.GetPrimAtPath(usdrt.Sdf.Path(prim_path))
        if not prim or not prim.IsValid():
            return None
        attr = prim.GetAttribute("omni:fabric:worldMatrix")
        value = attr.Get() if attr and attr.IsValid() else None
        if value is not None:
            matrix = [[float(value[row][col]) for col in range(4)]
                      for row in range(4)]
            scale = [math.sqrt(sum(matrix[row][col] ** 2
                                   for col in range(3)))
                     for row in range(3)]
            rotation = [[matrix[row][col] / (scale[row] or 1.0)
                         for col in range(4)] for row in range(3)]
            return matrix[3][:3], _quat_from_row_matrix(rotation)
        xformable = usdrt.Rt.Xformable(prim)
        if not xformable.HasWorldXform():
            return None
        position = xformable.GetWorldPositionAttr().Get()
        orientation = xformable.GetWorldOrientationAttr().Get()
        imaginary = orientation.GetImaginary()
        return (
            [float(position[0]), float(position[1]), float(position[2])],
            [float(orientation.GetReal()), float(imaginary[0]),
             float(imaginary[1]), float(imaginary[2])],
        )
    except Exception:
        return None


def _usd_world_pose(prim_path: str):
    """(position, quat_wxyz) from the USD stage (authoring-time state)."""
    import omni.usd

    stage = omni.usd.get_context().get_stage()
    if stage is None:
        raise RuntimeError("no stage open")
    matrix = _world_transform(stage, prim_path)
    translation = matrix.ExtractTranslation()
    quat = matrix.ExtractRotationQuat()
    imaginary = quat.GetImaginary()
    return (
        [float(translation[0]), float(translation[1]), float(translation[2])],
        [float(quat.GetReal()), float(imaginary[0]), float(imaginary[1]),
         float(imaginary[2])],
    )


def _world_pose_any(prim_path: str, prefer: str = "fabric"):
    """(position, quat_wxyz, source) preferring live Fabric state."""
    if prefer != "usd":
        fabric = _fabric_world_pose(prim_path)
        if fabric is not None:
            return fabric[0], fabric[1], "fabric"
    position, quat = _usd_world_pose(prim_path)
    return position, quat, "usd_stage"


def _quat_mul(a, b):
    aw, ax, ay, az = a
    bw, bx, by, bz = b
    return [aw * bw - ax * bx - ay * by - az * bz,
            aw * bx + ax * bw + ay * bz - az * by,
            aw * by - ax * bz + ay * bw + az * bx,
            aw * bz + ax * by - ay * bx + az * bw]


def _quat_conj(q):
    return [q[0], -q[1], -q[2], -q[3]]


def _quat_rotate(q, v):
    return _quat_mul(_quat_mul(q, [0.0, v[0], v[1], v[2]]),
                     _quat_conj(q))[1:]


def _relative_pose(target_pos, target_quat, ref_pos, ref_quat):
    """Pose of target expressed in the reference prim's frame."""
    inverse = _quat_conj(ref_quat)
    delta = [target_pos[i] - ref_pos[i] for i in range(3)]
    return _quat_rotate(inverse, delta), _quat_mul(inverse, target_quat)


def get_prim_pose(prim_path: str, in_frame: str = "world",
                  prefer: str = "fabric") -> dict:
    """Pose of prim_path, optionally expressed in another prim's frame.

    Prefers the live Fabric world transform (what physics produced this
    frame) and falls back to the USD stage; the response's ``source``
    field states which one answered, so a stale authoring-time pose can
    never masquerade as live state again.
    """
    position, quat, source = _world_pose_any(prim_path, prefer)
    frame_label = "world"
    frame_source = None
    if in_frame and in_frame not in ("world", "/"):
        ref_pos, ref_quat, frame_source = _world_pose_any(in_frame, prefer)
        position, quat = _relative_pose(position, quat, ref_pos, ref_quat)
        frame_label = in_frame
    pose = _labeled_pose(position, quat)
    pose["prim_path"] = prim_path
    pose["in_frame"] = frame_label
    pose["source"] = source
    if frame_source is not None:
        pose["frame_source"] = frame_source
    return pose


def grasp_gap(object_path: str, hand_frame_path: str,
              digit_tip_paths: list, object_half_extents=None,
              prefer: str = "fabric") -> dict:
    """Live pre-grasp geometry report: everything an agent needs to aim.

    Returns the object's pose in the hand frame, each digit tip's vector
    to the object center (world and hand frame), the aperture center
    (tip centroid) offset to the object, and — when half extents are
    given — each tip's per-axis clearance to the object's box surface in
    the OBJECT frame (negative = inside the face band on that axis).
    """
    obj_pos, obj_quat, obj_src = _world_pose_any(object_path, prefer)
    hand_pos, hand_quat, hand_src = _world_pose_any(hand_frame_path, prefer)
    rel_pos, rel_quat = _relative_pose(obj_pos, obj_quat, hand_pos, hand_quat)
    object_in_hand = _labeled_pose(rel_pos, rel_quat)

    hand_inverse = _quat_conj(hand_quat)
    object_inverse = _quat_conj(obj_quat)
    tips = []
    centroid = [0.0, 0.0, 0.0]
    for path in digit_tip_paths:
        tip_pos, _tip_quat, tip_src = _world_pose_any(path, prefer)
        for axis in range(3):
            centroid[axis] += tip_pos[axis] / len(digit_tip_paths)
        to_object_world = [obj_pos[i] - tip_pos[i] for i in range(3)]
        record = {
            "prim_path": path,
            "source": tip_src,
            "tip_world": [round(v, 6) for v in tip_pos],
            "to_object_center_world": [round(v, 6) for v in to_object_world],
            "to_object_center_hand_frame": [
                round(v, 6) for v in _quat_rotate(hand_inverse, to_object_world)],
            "distance_to_object_center_m": round(
                math.sqrt(sum(v * v for v in to_object_world)), 6),
        }
        if object_half_extents is not None:
            tip_in_object = _quat_rotate(
                object_inverse, [tip_pos[i] - obj_pos[i] for i in range(3)])
            record["tip_in_object_frame"] = [
                round(v, 6) for v in tip_in_object]
            record["box_axis_clearance_m"] = [
                round(abs(tip_in_object[i]) - float(object_half_extents[i]), 6)
                for i in range(3)]
        tips.append(record)

    aperture_to_object_world = [obj_pos[i] - centroid[i] for i in range(3)]
    return {
        "object_path": object_path,
        "hand_frame_path": hand_frame_path,
        "sources": {"object": obj_src, "hand": hand_src},
        "object_in_hand_frame": object_in_hand,
        "aperture_center_world": [round(v, 6) for v in centroid],
        "aperture_to_object_world": [
            round(v, 6) for v in aperture_to_object_world],
        "aperture_to_object_hand_frame": [
            round(v, 6)
            for v in _quat_rotate(hand_inverse, aperture_to_object_world)],
        "aperture_to_object_distance_m": round(
            math.sqrt(sum(v * v for v in aperture_to_object_world)), 6),
        "digit_tips": tips,
    }


def build_draw_axes_code(position, quat_wxyz, scale: float = 0.1) -> str:
    """Main-thread code drawing an RGB axes triad at the given pose.

    Endpoints are precomputed here so the generated code is pure
    literals — nothing to get wrong on the other side.
    """
    matrix = _quat_wxyz_to_matrix(*quat_wxyz)
    origin = [float(v) for v in position]
    ends = []
    for axis in range(3):
        column = [matrix[row][axis] for row in range(3)]
        ends.append([origin[i] + scale * column[i] for i in range(3)])
    colors = [(1, 0, 0, 1), (0, 1, 0, 1), (0, 0, 1, 1)]
    starts = [origin, origin, origin]
    return (
        "from isaacsim.util.debug_draw import _debug_draw\n"
        "d = _debug_draw.acquire_debug_draw_interface()\n"
        f"d.draw_lines({starts!r}, {ends!r}, {colors!r}, [3.0, 3.0, 3.0])\n"
        "print('axes drawn')\n"
    )


def prim_bounds(prim_path: str, in_frame: str = "world",
                prefer: str = "fabric", include_proxy: bool = False) -> dict:
    """Geometric size of a prim, paired with its live pose.

    Size and pose come from different places on purpose.  Isaac Lab steps
    physics through Fabric and never writes back to the USD stage, so a USD
    world bound is the authoring-time box wherever the object *started*, not
    where it is now.  Extents, however, are rigid: a cup is the same size
    wherever physics has moved it.  So the half extents are read from USD
    geometry with every ancestor transform ignored, and the centre comes from
    the same Fabric-preferred pose ``get_prim_pose`` returns.  ``extent_source``
    and ``source`` label which answered, so a stale bound cannot masquerade as
    live state.

    The returned box is axis-aligned in the prim's OWN frame.  A caller that
    treats it as axis-aligned in the world silently ignores the prim's
    rotation; ``quaternion_wxyz`` in the reply is what it must apply, and
    ``world_aligned_half_extents`` is offered for the axis-aligned case with
    the enclosing-box inflation stated.
    """
    from pxr import Gf, Usd, UsdGeom
    import omni.usd

    stage = omni.usd.get_context().get_stage()
    if stage is None:
        raise ValueError("no stage open")
    prim = stage.GetPrimAtPath(prim_path)
    if not prim or not prim.IsValid():
        raise ValueError(f"prim not found: {prim_path}")

    purposes = [UsdGeom.Tokens.default_, UsdGeom.Tokens.render]
    if include_proxy:
        purposes.append(UsdGeom.Tokens.proxy)
    cache = UsdGeom.BBoxCache(Usd.TimeCode.Default(), purposes,
                              useExtentsHint=True)
    # Untransformed: the prim's own and every ancestor's transform ignored, so
    # this is geometry size rather than where the stage last placed it.
    local_range = cache.ComputeUntransformedBound(prim).ComputeAlignedRange()
    if local_range.IsEmpty():
        raise ValueError(
            f"prim has no computable extent (no boundable geometry): {prim_path}")
    minimum = local_range.GetMin()
    maximum = local_range.GetMax()
    half_extents = [float(maximum[i] - minimum[i]) / 2.0 for i in range(3)]
    local_centre = [float(maximum[i] + minimum[i]) / 2.0 for i in range(3)]

    pose = get_prim_pose(prim_path, in_frame, prefer)

    # An axis-aligned enclosing box for callers that cannot apply the rotation.
    rotation = _quat_wxyz_to_matrix(*pose["quaternion_wxyz"])
    enclosing = [
        float(sum(abs(rotation[row][col]) * half_extents[col]
                  for col in range(3)))
        for row in range(3)
    ]
    inflation = [round(enclosing[i] - half_extents[i], 6) for i in range(3)]

    return {
        "prim_path": prim_path,
        "in_frame": pose["in_frame"],
        "position": pose["position"],
        "quaternion_wxyz": pose["quaternion_wxyz"],
        "quaternion_xyzw": pose["quaternion_xyzw"],
        "half_extents": [round(v, 6) for v in half_extents],
        "size": [round(2.0 * v, 6) for v in half_extents],
        "local_centre_offset": [round(v, 6) for v in local_centre],
        "world_aligned_half_extents": [round(v, 6) for v in enclosing],
        "world_aligned_inflation": inflation,
        "extent_source": "usd_untransformed_bound",
        "source": pose["source"],
        "purposes": [str(p) for p in purposes],
        "note": (
            "half_extents are in the prim's own frame; apply quaternion_wxyz, "
            "or use world_aligned_half_extents to ignore rotation at the cost "
            "of world_aligned_inflation metres per axis"
        ),
    }
