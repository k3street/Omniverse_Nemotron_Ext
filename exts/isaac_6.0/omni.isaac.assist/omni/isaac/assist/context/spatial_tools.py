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


def get_prim_pose(prim_path: str, in_frame: str = "world") -> dict:
    """Pose of prim_path, optionally expressed in another prim's frame.

    Returns explicitly labeled quaternion conventions.  Gf matrices are
    row-vector convention (world = local * M); the relative transform of
    a in frame b is therefore M_a * M_b.GetInverse().
    """
    import omni.usd

    stage = omni.usd.get_context().get_stage()
    if stage is None:
        raise RuntimeError("no stage open")
    matrix = _world_transform(stage, prim_path)
    frame_label = "world"
    if in_frame and in_frame not in ("world", "/"):
        reference = _world_transform(stage, in_frame)
        matrix = matrix * reference.GetInverse()
        frame_label = in_frame
    translation = matrix.ExtractTranslation()
    quat = matrix.ExtractRotationQuat()
    imaginary = quat.GetImaginary()
    pose = _labeled_pose(
        (translation[0], translation[1], translation[2]),
        (quat.GetReal(), imaginary[0], imaginary[1], imaginary[2]))
    pose["prim_path"] = prim_path
    pose["in_frame"] = frame_label
    return pose


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
