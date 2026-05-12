"""Sensor handlers — target scope: camera (RGB/depth), lidar (RTX/2D),
contact, force/torque, proximity, IMU, barcode, NIR material sensor.

Phase 6 wave 4 — first sensor code generators move out of `tool_executor.py`.
Same migration pattern as Phase 3 / Phase 5 / Phase 6 waves 1-3.

Per `specs/IA_FULL_SPEC_2026-05-10.md` Phases 2 + 6.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List


# ---------------------------------------------------------------------------
# Phase 6 wave 4 — camera + add_sensor + proximity


def _gen_add_sensor(args: Dict) -> str:
    """Generate code for adding a sensor based on type and optional product spec."""
    from ..tool_executor import _SAFE_XFORM_SNIPPET
    prim_path = args["prim_path"]
    sensor_type = args["sensor_type"]

    if sensor_type == "camera":
        fov = args.get("fov", 60)
        res = args.get("resolution", [1280, 720])
        return f"""\
import omni.usd
from pxr import UsdGeom, Sdf, Gf

stage = omni.usd.get_context().get_stage()
cam_path = '{prim_path}/Camera'
cam = UsdGeom.Camera.Define(stage, cam_path)
cam.GetHorizontalApertureAttr().Set(20.955)
cam.GetFocalLengthAttr().Set(10.0 * 20.955 / (2.0 * __import__('math').tan(__import__('math').radians({fov}/2))))
cam.GetClippingRangeAttr().Set(Gf.Vec2f(0.01, 1000.0))
"""
    if sensor_type == "rtx_lidar":
        return f"""\
import omni.usd
from pxr import UsdGeom, Gf
{_SAFE_XFORM_SNIPPET}
stage = omni.usd.get_context().get_stage()
lidar_path = '{prim_path}/RTXLidar'
lidar_prim = stage.DefinePrim(lidar_path, 'Camera')
_safe_set_translate(lidar_prim, (0, 0, 0.1))

# Configure RTX Lidar via Isaac Sim extension
from isaacsim.sensors.rtx import LidarRtx
lidar = LidarRtx(prim_path=lidar_path)
"""
    if sensor_type == "imu":
        return f"""\
from isaacsim.sensors.physics import IMUSensor
imu = IMUSensor(prim_path='{prim_path}/IMU')
"""
    if sensor_type == "contact_sensor":
        return f"""\
from isaacsim.sensors.physics import ContactSensor
contact = ContactSensor(prim_path='{prim_path}/ContactSensor')
"""
    return f"# Sensor type '{sensor_type}' not yet implemented"


def _gen_inspect_camera(args: Dict) -> str:
    camera_path = args["camera_path"]
    # UsdGeom.Camera(invalid_prim).GetFocalLengthAttr().Get() returns None
    # silently in some USD builds — JSON printed with every field=null but
    # success=True to the agent. Pre-check the prim exists AND is a Camera.
    return f"""\
import omni.usd
from pxr import UsdGeom
import json

stage = omni.usd.get_context().get_stage()
_cp = {camera_path!r}
_prim = stage.GetPrimAtPath(_cp)
if not _prim or not _prim.IsValid():
    raise RuntimeError('inspect_camera: prim not found: ' + repr(_cp))
if not _prim.IsA(UsdGeom.Camera):
    raise RuntimeError(
        'inspect_camera: prim at ' + repr(_cp) + ' is not a UsdGeom.Camera '
        '(type_name=' + str(_prim.GetTypeName()) + ')'
    )
cam = UsdGeom.Camera(_prim)
result = {{
    'camera_path': _cp,
    'focal_length': cam.GetFocalLengthAttr().Get(),
    'horizontal_aperture': cam.GetHorizontalApertureAttr().Get(),
    'vertical_aperture': cam.GetVerticalApertureAttr().Get(),
    'clipping_range': list(cam.GetClippingRangeAttr().Get() or ()),
    'focus_distance': cam.GetFocusDistanceAttr().Get(),
    'projection': cam.GetProjectionAttr().Get(),
}}
print(json.dumps(result))
"""

def _gen_configure_camera(args: Dict) -> str:
    camera_path = args["camera_path"]
    lines = [
        "import omni.usd",
        "from pxr import UsdGeom, Gf",
        "",
        "stage = omni.usd.get_context().get_stage()",
        f"cam = UsdGeom.Camera(stage.GetPrimAtPath('{camera_path}'))",
    ]
    if "focal_length" in args:
        lines.append(f"cam.GetFocalLengthAttr().Set({args['focal_length']})")
    if "horizontal_aperture" in args:
        lines.append(f"cam.GetHorizontalApertureAttr().Set({args['horizontal_aperture']})")
    if "vertical_aperture" in args:
        lines.append(f"cam.GetVerticalApertureAttr().Set({args['vertical_aperture']})")
    if "clipping_range" in args:
        cr = args["clipping_range"]
        lines.append(f"cam.GetClippingRangeAttr().Set(Gf.Vec2f({cr[0]}, {cr[1]}))")
    if "focus_distance" in args:
        lines.append(f"cam.GetFocusDistanceAttr().Set({args['focus_distance']})")
    lines.append(f"print(f'Camera {camera_path} configured')")
    return "\n".join(lines)


def _gen_set_camera_params(args: Dict) -> str:
    """Generate Python that mutates camera attributes. Each requested field becomes one .Set()."""
    camera_path = args["camera_path"]
    params = args.get("params", {}) or {}

    lines = [
        "import omni.usd",
        "from pxr import UsdGeom, Gf, Sdf",
        "",
        "stage = omni.usd.get_context().get_stage()",
        f"prim = stage.GetPrimAtPath('{camera_path}')",
        "if not prim or not prim.IsValid():",
        f"    raise RuntimeError('Camera prim not found: {camera_path}')",
        "if prim.GetTypeName() != 'Camera':",
        f"    raise RuntimeError('Prim is not a Camera: {camera_path}')",
        "cam = UsdGeom.Camera(prim)",
        "",
    ]

    if "focal_length" in params:
        lines.append(f"cam.GetFocalLengthAttr().Set({float(params['focal_length'])})")
    if "horizontal_aperture" in params:
        lines.append(f"cam.GetHorizontalApertureAttr().Set({float(params['horizontal_aperture'])})")
    if "vertical_aperture" in params:
        lines.append(f"cam.GetVerticalApertureAttr().Set({float(params['vertical_aperture'])})")
    if "clipping_range" in params:
        cr = params["clipping_range"]
        if isinstance(cr, (list, tuple)) and len(cr) == 2:
            near, far = float(cr[0]), float(cr[1])
            lines.append(
                f"cam.GetClippingRangeAttr().Set(Gf.Vec2f({near}, {far}))"
            )
    if "focus_distance" in params:
        lines.append(f"cam.GetFocusDistanceAttr().Set({float(params['focus_distance'])})")
    if "f_stop" in params:
        lines.append(f"cam.GetFStopAttr().Set({float(params['f_stop'])})")
    if "projection" in params:
        proj = str(params["projection"]).lower()
        if proj in ("perspective", "orthographic"):
            lines.append(f"cam.GetProjectionAttr().Set('{proj}')")
        else:
            lines.append(f"# WARNING: unsupported projection '{proj}' — skipped")

    lines.append("")
    lines.append(f"print('set_camera_params: updated {camera_path}')")
    return "\n".join(lines)


def _gen_set_camera_look_at(args: Dict) -> str:
    """Generate Python that orients a camera at a world-space target.

    Uses Gf.Matrix4d.SetLookAt — note that USD's Gf SetLookAt produces an
    *inverse* view matrix, so we extract its inverse and decompose into a
    rotation that the camera xform op can consume.
    """
    from ..tool_executor import _SAFE_XFORM_SNIPPET
    camera_path = args["camera_path"]
    target = args["target"]
    if not isinstance(target, (list, tuple)) or len(target) != 3:
        raise ValueError("target must be [x, y, z]")
    tx, ty, tz = float(target[0]), float(target[1]), float(target[2])

    up = args.get("up") or [0.0, 1.0, 0.0]
    if not isinstance(up, (list, tuple)) or len(up) != 3:
        raise ValueError("up must be [x, y, z]")
    ux, uy, uz = float(up[0]), float(up[1]), float(up[2])

    eye = args.get("eye")
    eye_block: List[str]
    if eye is not None:
        if not isinstance(eye, (list, tuple)) or len(eye) != 3:
            raise ValueError("eye must be [x, y, z] when provided")
        ex, ey, ez = float(eye[0]), float(eye[1]), float(eye[2])
        eye_block = [
            f"eye = Gf.Vec3d({ex}, {ey}, {ez})",
            "# Override translation to the supplied eye position",
            "_safe_set_translate(prim, (eye[0], eye[1], eye[2]))",
        ]
    else:
        eye_block = [
            "# Use camera's current world translation as the eye position",
            "world_xform = UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(Usd.TimeCode.Default())",
            "eye_v = world_xform.ExtractTranslation()",
            "eye = Gf.Vec3d(eye_v[0], eye_v[1], eye_v[2])",
        ]

    lines = [
        "import omni.usd",
        "from pxr import Usd, UsdGeom, Gf",
        _SAFE_XFORM_SNIPPET,
        "stage = omni.usd.get_context().get_stage()",
        f"prim = stage.GetPrimAtPath('{camera_path}')",
        "if not prim or not prim.IsValid():",
        f"    raise RuntimeError('Camera prim not found: {camera_path}')",
        "if prim.GetTypeName() != 'Camera':",
        f"    raise RuntimeError('Prim is not a Camera: {camera_path}')",
        "",
        f"target = Gf.Vec3d({tx}, {ty}, {tz})",
        f"up = Gf.Vec3d({ux}, {uy}, {uz})",
        *eye_block,
        "",
        "# Build a look-at view matrix and invert to a world-space camera transform.",
        "# Gf.Matrix4d.SetLookAt produces a view matrix (world->camera);",
        "# the camera's world transform is therefore its inverse.",
        "view = Gf.Matrix4d().SetLookAt(eye, target, up)",
        "world = view.GetInverse()",
        "rot = world.ExtractRotation()",
        "euler = rot.Decompose(Gf.Vec3d.ZAxis(), Gf.Vec3d.YAxis(), Gf.Vec3d.XAxis())",
        "# Decompose returns (Z, Y, X) — feed back as (X, Y, Z) for rotateXYZ op",
        "rx, ry, rz = float(euler[2]), float(euler[1]), float(euler[0])",
        "_safe_set_translate(prim, (eye[0], eye[1], eye[2]))",
        "_safe_set_rotate_xyz(prim, (rx, ry, rz))",
        f"print('set_camera_look_at: {camera_path} now looking at ({tx}, {ty}, {tz})')",
    ]
    return "\n".join(lines)


def _gen_add_proximity_sensor(args: Dict) -> str:
    """Create a physics trigger volume that sets a custom attribute when
    any prim matching a pattern enters the volume. The attribute is what
    controllers read to know whether a cube is at the pick station.

    Implementation:
      - Invisible Cube prim at position, scaled to detection_size
      - UsdPhysics.CollisionAPI + PhysxTriggerAPI → PhysX treats it as
        a sensing volume, no physical interaction with dynamic bodies
      - Custom attr `isaac_sensor:triggered` (bool) + `isaac_sensor:last_triggered_path` (string)
      - Python callback via omni.physx subscribe_physics_on_step_events that
        runs overlap_box each step, updates attrs

    This is sim2real-honest: the sensor is binary (in-zone or not), no
    ground-truth pose leakage. A real beam-break sensor has the same
    interface. Stationary pick stations use exactly this pattern in
    real cells.
    """
    sensor_path = args["sensor_path"]
    position = args["position"]
    size = args.get("size", [0.1, 0.1, 0.1])
    watched_pattern = args.get("watched_path_pattern", "/World/")

    return f"""\
import omni.usd
import omni.physx
from pxr import UsdGeom, UsdPhysics, PhysxSchema, Sdf, Gf

sensor_path = {sensor_path!r}
pos = {position}
size = {size}
pattern = {watched_pattern!r}

stage = omni.usd.get_context().get_stage()

# 1. Create invisible Cube as the sensing volume
sensor = UsdGeom.Cube.Define(stage, sensor_path)
sensor.CreateSizeAttr(1.0)
xf = UsdGeom.Xformable(sensor)
# Reuse existing translate op if present (safe pattern)
_t_op = None
_s_op = None
for op in xf.GetOrderedXformOps():
    if op.GetOpType() == UsdGeom.XformOp.TypeTranslate:
        _t_op = op
    elif op.GetOpType() == UsdGeom.XformOp.TypeScale:
        _s_op = op
if _t_op is None:
    _t_op = xf.AddTranslateOp()
_t_op.Set(Gf.Vec3d(*pos))
if _s_op is None:
    _s_op = xf.AddScaleOp()
_s_op.Set(Gf.Vec3f(size[0]*0.5, size[1]*0.5, size[2]*0.5))
# Make invisible
sensor.GetPurposeAttr().Set(UsdGeom.Tokens.guide)  # shows only in guide-view
sensor.GetPrim().GetAttribute("visibility").Set("invisible") if sensor.GetPrim().HasAttribute("visibility") else None

# 2. Trigger volume APIs — note PhysxTriggerAPI treats the prim as a
#    sensor, no collision response. Dynamic bodies pass through.
sensor_prim = sensor.GetPrim()
if not sensor_prim.HasAPI(UsdPhysics.CollisionAPI):
    UsdPhysics.CollisionAPI.Apply(sensor_prim)
if not sensor_prim.HasAPI(PhysxSchema.PhysxTriggerAPI):
    PhysxSchema.PhysxTriggerAPI.Apply(sensor_prim)

# 3. Custom attributes for triggered state
trig_attr = sensor_prim.GetAttribute("isaac_sensor:triggered")
if not trig_attr or not trig_attr.IsDefined():
    trig_attr = sensor_prim.CreateAttribute("isaac_sensor:triggered", Sdf.ValueTypeNames.Bool)
trig_attr.Set(False)
last_attr = sensor_prim.GetAttribute("isaac_sensor:last_triggered_path")
if not last_attr or not last_attr.IsDefined():
    last_attr = sensor_prim.CreateAttribute("isaac_sensor:last_triggered_path", Sdf.ValueTypeNames.String)
last_attr.Set("")

# 4. Physics-step callback: overlap-box query each step, update attrs
_physx = omni.physx.get_physx_scene_query_interface()

def _sensor_step(dt):
    # Guard: sensor prim may have been deleted by a scene reset while the
    # callback is still subscribed. Writing to an expired USD attribute
    # raises a loud RuntimeError every tick — catch and silently no-op.
    # Caller is expected to unregister the callback explicitly when
    # re-installing; this is belt-and-suspenders.
    try:
        if not sensor_prim.IsValid():
            return
        hits = []
        def _cb(hit):
            hp = str(hit.rigid_body)
            if hp.startswith(pattern):
                hits.append(hp)
            return True
        _physx.overlap_box(
            (size[0]*0.5, size[1]*0.5, size[2]*0.5),
            (pos[0], pos[1], pos[2]),
            (0.0, 0.0, 0.0, 1.0),
            _cb,
            False,
        )
        trig_attr.Set(bool(hits))
        last_attr.Set(hits[0] if hits else "")
    except Exception:
        pass

# Subscribe via omni.physx directly. World.add_physics_callback goes
# through SimulationContext._physics_context which is often None in
# exec_sync contexts (raises AttributeError at registration). The
# raw omni.physx subscription fires reliably under manual
# update_simulation stepping AND live timeline.play.
import builtins as _builtins
_sub_attr_name = "_sensor_" + sensor_path.replace("/", "_") + "_sub"
_old_sub = getattr(_builtins, _sub_attr_name, None)
if _old_sub is not None:
    try: _old_sub.unsubscribe()
    except Exception: pass
_sub = omni.physx.get_physx_interface().subscribe_physics_step_events(_sensor_step)
# Pin the subscription to a module-level attribute so Python's GC
# doesn't collect it and kill the callback. Storing on USD as
# str(id(_sub)) (the previous pattern) pinned only an integer string,
# not the Python object — the subscription died immediately.
setattr(_builtins, _sub_attr_name, _sub)

import json
print(json.dumps({{
    "ok": True,
    "sensor_path": sensor_path,
    "position": pos,
    "detection_size": size,
    "triggered_attr": f"{{sensor_path}}.isaac_sensor:triggered",
    "last_attr": f"{{sensor_path}}.isaac_sensor:last_triggered_path",
}}))
"""


# ---------------------------------------------------------------------------
# Registration


def register(
    data: Dict[str, Callable[..., Any]],
    codegen: Dict[str, Callable[..., Any]],
) -> None:
    """Phase 6 wave 4 — dispatch lines in `tool_executor.py` still
    reference these names via re-import. Phase 9 swaps to register()
    being authoritative; until then this is intentionally a no-op.
    """
    return None
