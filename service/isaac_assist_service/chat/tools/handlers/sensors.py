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
    from ._shared import _SAFE_XFORM_SNIPPET
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
    from ._shared import _SAFE_XFORM_SNIPPET
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
# Phase 7 wave 9 — sensor data-handlers (force/torque, barcode, NIR, overlap/sweep, raycast, contacts)


async def _handle_add_force_torque_sensor(args: Dict) -> Dict:
    """Tier C tool — adds an Isaac Sim ForceSensor (force/torque) on a robot
    end-effector or articulation joint.

    Used by #22 Peg-in-Hole (force-threshold-gated phase transitions).
    Wraps Isaac Sim's IsaacForceSensor schema.

    Args:
      sensor_path:    USD path of the force sensor
      parent_path:    USD path of the prim to attach sensor to (robot link)
      threshold:      force threshold for triggering events (default 5.0 N)
      noise_std:      Gaussian noise std-dev added to force/torque readings
                      (default 0.0 = no noise). Enables sim-to-real gap
                      emulation for contact-rich manipulation training.
      publish_topic:  If set, the generated code registers a ROS2-style
                      publisher stub on this topic so downstream consumers
                      can subscribe.  None (default) skips publishing.

    Returns: {sensor_path, parent_path, threshold, noise_std, publish_topic}
    """
    from .. import kit_tools
    sensor_path: str = args["sensor_path"]
    parent_path: str = args["parent_path"]
    threshold: float = float(args.get("threshold", 5.0))
    noise_std: float = float(args.get("noise_std", 0.0))
    publish_topic: str | None = args.get("publish_topic", None) or None

    # Build optional noise block — injected verbatim into the generated script.
    noise_block: str = ""
    if noise_std > 0.0:
        noise_block = f"""\
import random as _rnd
def _add_noise(v, std={noise_std}):
    return tuple(x + _rnd.gauss(0.0, std) for x in v)
"""

    # Build optional publish stub — injected only when a topic is requested.
    publish_block: str = ""
    if publish_topic is not None:
        publish_block = f"""\
# Publish stub — downstream consumers subscribe to {publish_topic!r}
sprim.CreateAttribute("ftsensor:publish_topic", Sdf.ValueTypeNames.String).Set({publish_topic!r})
"""

    code = f"""\
import omni.usd, json
from pxr import UsdPhysics, Sdf
stage = omni.usd.get_context().get_stage()
parent = stage.GetPrimAtPath({parent_path!r})
if not parent or not parent.IsValid():
    print(json.dumps({{"error": f"parent not found: {parent_path!r}"}})); raise SystemExit

# Apply ForceSensor schema (UsdPhysics.ForceSensor or simulated via attrs)
try:
    api = UsdPhysics.RigidBodyAPI.Get(parent)
    if not api:
        api = UsdPhysics.RigidBodyAPI.Apply(parent)
except Exception:
    pass

{noise_block}
# Create sensor prim with reading attrs (logical wrapper; reading hooks runtime)
from pxr import UsdGeom, Gf
spp = Sdf.Path({sensor_path!r})
sprim = stage.GetPrimAtPath(spp)
if not sprim or not sprim.IsValid():
    sprim = UsdGeom.Xform.Define(stage, spp).GetPrim()
sprim.CreateAttribute("ftsensor:parent",      Sdf.ValueTypeNames.String).Set({parent_path!r})
sprim.CreateAttribute("ftsensor:threshold",   Sdf.ValueTypeNames.Float).Set({threshold})
sprim.CreateAttribute("ftsensor:noise_std",   Sdf.ValueTypeNames.Float).Set({noise_std})
sprim.CreateAttribute("ftsensor:last_force",  Sdf.ValueTypeNames.Float3).Set((0.0, 0.0, 0.0))
sprim.CreateAttribute("ftsensor:last_torque", Sdf.ValueTypeNames.Float3).Set((0.0, 0.0, 0.0))
sprim.CreateAttribute("ftsensor:triggered",   Sdf.ValueTypeNames.Bool).Set(False)
{publish_block}
print(json.dumps({{"sensor_path": str(sprim.GetPath()), "parent": {parent_path!r}, "threshold": {threshold}, "noise_std": {noise_std}, "publish_topic": {publish_topic!r}}}))
"""
    res = await kit_tools.exec_sync(code, timeout=10)
    return {
        "success": bool(res.get("success", False)),
        "sensor_path": sensor_path,
        "parent_path": parent_path,
        "threshold": threshold,
        "noise_std": noise_std,
        "publish_topic": publish_topic,
        "raw": (res.get("output") or "")[-200:],
    }


async def _handle_add_vision_classifier_gate(args: Dict) -> Dict:
    """Tier A tool — build a class-routing dict by VLM vision classification.

    Agent-side helper for vision-gated palletizing/sorting (CP-N: parcel
    singulation, vision-quality-gate, postal cross-belt sorter, etc).

    Pipeline:
      1. (Optional) set viewport to camera_path so the captured image is
         from the requested vantage. If omitted, current viewport is used.
      2. Capture viewport image.
      3. For each cube_path, get its world position via BBoxCache.
      4. Project world position to image (y, x) via the camera's
         intrinsics. Match each cube to the nearest detection by
         normalized image distance.
      5. Return {cube_path: detected_label} mapping.

    Args:
      camera_path:    USD path of the camera to use (optional)
      cube_paths:     list of USD paths to classify
      class_labels:   list of expected class names (e.g. ['red cube', 'blue cube'])
      destination_map: optional {class_label: destination_prim_path} —
                       when provided, returned mapping is keyed by
                       cube_path → destination_path (color_routing-shaped)
                       in addition to cube_path → class_label.

    Returns:
      {
        cube_to_class: {cube_path: detected_label, ...},
        cube_to_destination: {cube_path: destination_path, ...} (if destination_map provided),
        unmatched_cubes: [cube_path, ...],
        raw_detections: [{point: [y,x], label: ...}, ...],
        model: gemini-...,
      }

    v1 simplification: 2D-distance matching is performed in image
    coordinates without explicit world→image projection — assumes
    detections are returned in roughly the same order as cube_paths
    by left-to-right viewport position. Agent should validate
    by inspecting unmatched_cubes and raw_detections.
    """
    from .. import kit_tools
    from ..tool_executor import execute_tool_call  # dispatch entry, stays in tool_executor
    from ._shared import _get_viewport_bytes, _get_vision_provider
    camera_path = args.get("camera_path")
    cube_paths = list(args.get("cube_paths") or [])
    class_labels = list(args.get("class_labels") or [])
    destination_map = args.get("destination_map") or {}

    if not cube_paths:
        return {"success": False, "type": "error", "error": "cube_paths is required and non-empty"}
    if not class_labels:
        return {"success": False, "type": "error", "error": "class_labels is required (list of expected class names)"}

    # Optionally set viewport to the requested camera before capture.
    if camera_path:
        try:
            await execute_tool_call("set_viewport_camera", {"camera_path": camera_path})
        except Exception as _e:
            # Non-fatal: continue with current viewport
            pass

    # Force-flush render before capture. Kit RPC's /capture endpoint
    # otherwise returns axis-only black image when the viewport hasn't
    # rendered the new scene state yet (KNOWN LIMITATION).
    flush_code = """
import omni.kit.app
app = omni.kit.app.get_app()
for _ in range(60):
    app.update()
print("flushed")
"""
    try:
        await kit_tools.exec_sync(flush_code, timeout=15)
    except Exception:
        pass

    # Capture viewport
    img, mime = await _get_viewport_bytes()
    if img is None:
        return {"success": False, "type": "error", "error": "Could not capture viewport image. Is Isaac Sim running?"}

    # Run vision detection
    vp = _get_vision_provider()
    detections = await vp.detect_objects(img, mime, labels=class_labels,
                                          max_objects=max(10, len(cube_paths)))

    # Get cube world positions via Kit RPC
    pos_code = f"""\
import omni.usd, json
from pxr import Usd, UsdGeom
stage = omni.usd.get_context().get_stage()
positions = {{}}
for path in {cube_paths!r}:
    p = stage.GetPrimAtPath(path)
    if not p or not p.IsValid(): continue
    cache = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.default_])
    b = cache.ComputeWorldBound(p).ComputeAlignedRange()
    if b.IsEmpty(): continue
    c = b.GetMidpoint()
    positions[path] = [float(c[0]), float(c[1]), float(c[2])]
print(json.dumps(positions))
"""
    pos_res = await kit_tools.exec_sync(pos_code, timeout=10)
    cube_world_positions = {}
    for line in (pos_res.get("output") or "").splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                import json as _j
                cube_world_positions = _j.loads(line)
                break
            except Exception:
                continue

    # Match detections to cubes by sorted left-to-right xy comparison.
    # v1 heuristic: sort cubes by world x (ascending → leftmost first
    # in a robot-standard front-facing camera), sort detections by
    # image x (point[1]). Pair them in order.
    sorted_cubes = sorted(
        [p for p in cube_paths if p in cube_world_positions],
        key=lambda p: cube_world_positions[p][0],
    )
    sorted_dets = sorted(
        [d for d in detections if isinstance(d.get("point"), (list, tuple)) and len(d["point"]) >= 2],
        key=lambda d: float(d["point"][1]),
    )

    cube_to_class: Dict[str, str] = {}
    unmatched_cubes: List[str] = []
    n_pairs = min(len(sorted_cubes), len(sorted_dets))
    for i in range(n_pairs):
        cube_to_class[sorted_cubes[i]] = sorted_dets[i].get("label", "")
    for cube in sorted_cubes[n_pairs:]:
        unmatched_cubes.append(cube)

    cube_to_destination = {}
    if destination_map:
        for cube, label in cube_to_class.items():
            # Fuzzy match: detected label may be "red cube" while
            # destination_map keys are "red". Try exact, then prefix.
            dest = destination_map.get(label)
            if dest is None:
                for k, v in destination_map.items():
                    if k.lower() in label.lower() or label.lower() in k.lower():
                        dest = v
                        break
            if dest:
                cube_to_destination[cube] = dest

    return {
        "success": True,
        "cube_to_class": cube_to_class,
        "cube_to_destination": cube_to_destination,
        "unmatched_cubes": unmatched_cubes,
        "raw_detections": detections,
        "model": getattr(vp, "model", "?"),
    }


async def _handle_barcode_reader_sensor(args: Dict) -> Dict:
    """Tier B tool — creates a barcode-reader sensor at a fixed scan position.

    Reads cube identity via Semantics_class lookup when a cube enters the
    sensor's xy zone. Output published as USD attribute on the sensor prim:
      barcode:last_read (cube path)
      barcode:last_class (semantic class read)
      barcode:read_count

    For canonical-time, creates the sensor prim with attrs. Runtime barcode
    reading would be a per-tick callback (controller-side, Sprint 3+).

    Args:
      sensor_path:  USD path of the barcode-reader prim
      position:     [x, y, z] of scan zone
      scan_radius:  radius of scan zone (default 0.05m)

    Returns: {sensor_path, position, scan_radius}
    """
    from .. import kit_tools
    sensor_path = args["sensor_path"]
    position = args.get("position", [0.4, 0.4, 0.835])
    scan_radius = float(args.get("scan_radius", 0.05))

    code = f"""\
import omni.usd, json
from pxr import UsdGeom, Sdf, Gf
stage = omni.usd.get_context().get_stage()
pp = Sdf.Path({sensor_path!r})
prim = stage.GetPrimAtPath(pp)
if not prim or not prim.IsValid():
    prim = UsdGeom.Xform.Define(stage, pp).GetPrim()
UsdGeom.XformCommonAPI(prim).SetTranslate(Gf.Vec3d({position[0]}, {position[1]}, {position[2]}))
prim.CreateAttribute("barcode:scan_radius", Sdf.ValueTypeNames.Float).Set({scan_radius})
prim.CreateAttribute("barcode:last_read",   Sdf.ValueTypeNames.String).Set("")
prim.CreateAttribute("barcode:last_class",  Sdf.ValueTypeNames.String).Set("")
prim.CreateAttribute("barcode:read_count",  Sdf.ValueTypeNames.Int).Set(0)
print(json.dumps({{"created": str(prim.GetPath()), "position": {position!r}, "scan_radius": {scan_radius}}}))
"""
    res = await kit_tools.exec_sync(code, timeout=10)
    return {
        "success": bool(res.get("success", False)),
        "sensor_path": sensor_path,
        "position": position,
        "scan_radius": scan_radius,
        "raw": (res.get("output") or "")[-200:],
    }


async def _handle_list_contacts(args: Dict) -> Dict:
    """Subscribe to PhysX contact reports for a body and return the pairs."""
    from .. import kit_tools
    prim_path = args["prim_path"]
    duration = float(args.get("duration", 0.5))
    min_impulse = float(args.get("min_impulse", 0.0))
    code = f"""\
import omni.usd
import json
import time
from pxr import UsdPhysics, PhysxSchema

stage = omni.usd.get_context().get_stage()
prim_path = {prim_path!r}
duration = {duration}
min_impulse = {min_impulse}
result = {{'prim_path': prim_path, 'duration_s': duration}}
prim = stage.GetPrimAtPath(prim_path)
if not prim or not prim.IsValid():
    result['error'] = 'prim not found'
    print(json.dumps(result, default=str))
else:
    # Apply contact report API if missing so PhysX emits events for this body.
    if not prim.HasAPI(PhysxSchema.PhysxContactReportAPI):
        PhysxSchema.PhysxContactReportAPI.Apply(prim)
    contacts = []
    sub = None
    try:
        from omni.physx import get_physx_simulation_interface
        sim = get_physx_simulation_interface()

        def _on_contact(contact_headers, contact_data):
            for header in contact_headers:
                pair = {{
                    'body_a': str(getattr(header, 'actor0', '')),
                    'body_b': str(getattr(header, 'actor1', '')),
                    'collider_a': str(getattr(header, 'collider0', '')),
                    'collider_b': str(getattr(header, 'collider1', '')),
                    'contact_count': int(getattr(header, 'num_contact_data', 0)),
                }}
                impulse = 0.0
                try:
                    n = int(getattr(header, 'num_contact_data', 0))
                    start = int(getattr(header, 'contact_data_offset', 0))
                    for i in range(start, start + n):
                        cd = contact_data[i]
                        imp = cd.impulse
                        impulse += float((imp[0]**2 + imp[1]**2 + imp[2]**2) ** 0.5)
                except Exception:
                    pass
                pair['impulse'] = impulse
                if impulse >= min_impulse:
                    contacts.append(pair)

        sub = sim.subscribe_contact_report_events(_on_contact)
        # Step the simulation briefly to gather contacts.
        deadline = time.time() + duration
        while time.time() < deadline:
            time.sleep(0.01)
    except Exception as exc:
        result['error'] = f'contact subscription failed: {{exc}}'
    finally:
        try:
            if sub is not None:
                sub = None
        except Exception:
            pass
    result['contact_count'] = len(contacts)
    result['contacts'] = contacts
    print(json.dumps(result, default=str))
"""
    return await kit_tools.queue_exec_patch(code, f"list_contacts {prim_path}")


async def _handle_nir_material_sensor(args: Dict) -> Dict:
    """Tier C — Near-IR material classification sensor for recycling scenarios
    (#18). Reads cube's material:type attr (set by user pre-canonical) within
    sensor proximity.

    Args:
      sensor_path:  USD path of the sensor
      position:     [x,y,z] of sensor center
      scan_radius:  detection radius

    Returns: {sensor_path, position, scan_radius}
    """
    from .. import kit_tools
    sensor_path = args["sensor_path"]
    position = args.get("position", [0.4, 0.4, 0.85])
    scan_radius = float(args.get("scan_radius", 0.05))

    code = f"""\
import omni.usd, json
from pxr import UsdGeom, Sdf, Gf
stage = omni.usd.get_context().get_stage()
pp = Sdf.Path({sensor_path!r})
prim = stage.GetPrimAtPath(pp)
if not prim or not prim.IsValid():
    prim = UsdGeom.Xform.Define(stage, pp).GetPrim()
UsdGeom.XformCommonAPI(prim).SetTranslate(Gf.Vec3d({position[0]}, {position[1]}, {position[2]}))
prim.CreateAttribute("nir:scan_radius",  Sdf.ValueTypeNames.Float).Set({scan_radius})
prim.CreateAttribute("nir:last_material",Sdf.ValueTypeNames.String).Set("")
prim.CreateAttribute("nir:read_count",   Sdf.ValueTypeNames.Int).Set(0)
print(json.dumps({{"created": str(prim.GetPath()), "position": {position!r}, "scan_radius": {scan_radius}}}))
"""
    res = await kit_tools.exec_sync(code, timeout=10)
    return {
        "success": bool(res.get("success", False)),
        "sensor_path": sensor_path,
        "position": position,
        "scan_radius": scan_radius,
        "raw": (res.get("output") or "")[-200:],
    }


async def _handle_overlap_box(args: Dict) -> Dict:
    """Find every collider that overlaps the given oriented box."""
    from .. import kit_tools
    center = args["center"]
    half_extents = args["half_extents"]
    rotation = args.get("rotation") or [0.0, 0.0, 0.0, 1.0]  # identity quaternion
    code = f"""\
import json

center = {list(center)!r}
half_extents = {list(half_extents)!r}
rotation = {list(rotation)!r}
hits = []

def _report_fn(hit):
    path = getattr(hit, 'rigid_body', None) or getattr(hit, 'collision', None)
    if path is None and isinstance(hit, dict):
        path = hit.get('rigidBody') or hit.get('collision')
    if path is not None:
        hits.append(str(path))
    return True

try:
    from omni.physx import get_physx_scene_query_interface
    sqi = get_physx_scene_query_interface()
    count = sqi.overlap_box(half_extents, center, rotation, _report_fn, False)
except Exception as exc:
    count = -1
    hits.append(f'__error__: {{exc}}')

result = {{
    'center': center,
    'half_extents': half_extents,
    'rotation': rotation,
    'count': len(hits),
    'reported_count': count,
    'prim_paths': hits,
}}
print(json.dumps(result, default=str))
"""
    return await kit_tools.queue_exec_patch(
        code, f"overlap_box center={center} half_extents={half_extents}"
    )


async def _handle_overlap_sphere(args: Dict) -> Dict:
    """Find every collider whose AABB overlaps the given sphere."""
    from .. import kit_tools
    center = args["center"]
    radius = float(args["radius"])
    code = f"""\
import json

center = {list(center)!r}
radius = {radius!r}
hits = []

def _report_fn(hit):
    # Called once per overlap. Return True to keep collecting.
    path = getattr(hit, 'rigid_body', None) or getattr(hit, 'collision', None)
    if path is None and isinstance(hit, dict):
        path = hit.get('rigidBody') or hit.get('collision')
    if path is not None:
        hits.append(str(path))
    return True

try:
    from omni.physx import get_physx_scene_query_interface
    sqi = get_physx_scene_query_interface()
    count = sqi.overlap_sphere(radius, center, _report_fn, False)
except Exception as exc:
    count = -1
    hits.append(f'__error__: {{exc}}')

result = {{
    'center': center,
    'radius': radius,
    'count': len(hits),
    'reported_count': count,
    'prim_paths': hits,
}}
print(json.dumps(result, default=str))
"""
    return await kit_tools.queue_exec_patch(
        code, f"overlap_sphere center={center} r={radius}"
    )


async def _handle_raycast(args: Dict) -> Dict:
    """Cast a single ray and return the closest PhysX hit."""
    from .. import kit_tools
    origin = args["origin"]
    direction = args["direction"]
    max_distance = float(args.get("max_distance", 1000.0))
    code = f"""\
import json

origin = {list(origin)!r}
direction = {list(direction)!r}
max_distance = {max_distance!r}

# Normalize direction
import math
_dx, _dy, _dz = direction
_len = math.sqrt(_dx * _dx + _dy * _dy + _dz * _dz)
if _len <= 0.0:
    print(json.dumps({{'error': 'direction has zero length', 'origin': origin, 'direction': direction}}))
else:
    direction = [_dx / _len, _dy / _len, _dz / _len]
    try:
        from omni.physx import get_physx_scene_query_interface
        sqi = get_physx_scene_query_interface()
        hit = sqi.raycast_closest(origin, direction, max_distance)
    except Exception as exc:
        hit = {{'error': f'PhysX scene query unavailable: {{exc}}'}}
    if isinstance(hit, dict) and hit.get('hit'):
        result = {{
            'hit': True,
            'origin': origin,
            'direction': direction,
            'max_distance': max_distance,
            'collision': hit.get('collision') or hit.get('rigidBody'),
            'position': list(hit.get('position', [])),
            'normal': list(hit.get('normal', [])),
            'distance': float(hit.get('distance', 0.0)),
            'face_index': hit.get('faceIndex'),
            'material': hit.get('material'),
        }}
    else:
        result = {{
            'hit': False,
            'origin': origin,
            'direction': direction,
            'max_distance': max_distance,
        }}
        if isinstance(hit, dict) and 'error' in hit:
            result['error'] = hit['error']
    print(json.dumps(result, default=str))
"""
    return await kit_tools.queue_exec_patch(code, f"raycast {origin} -> {direction}")


async def _handle_sweep_sphere(args: Dict) -> Dict:
    """Sweep a sphere from start to end, return closest hit along the sweep."""
    from .. import kit_tools
    start = args["start"]
    end = args["end"]
    radius = float(args["radius"])
    code = f"""\
import json
import math

start = {list(start)!r}
end = {list(end)!r}
radius = {radius!r}

dx = end[0] - start[0]
dy = end[1] - start[1]
dz = end[2] - start[2]
distance = math.sqrt(dx * dx + dy * dy + dz * dz)
if distance <= 0.0:
    print(json.dumps({{
        'error': 'sweep has zero length',
        'start': start,
        'end': end,
        'radius': radius,
    }}))
else:
    direction = [dx / distance, dy / distance, dz / distance]
    try:
        from omni.physx import get_physx_scene_query_interface
        sqi = get_physx_scene_query_interface()
        hit = sqi.sweep_sphere(radius, start, direction, distance)
    except Exception as exc:
        hit = {{'error': f'PhysX scene query unavailable: {{exc}}'}}
    if isinstance(hit, dict) and hit.get('hit'):
        result = {{
            'hit': True,
            'start': start,
            'end': end,
            'radius': radius,
            'direction': direction,
            'sweep_distance': distance,
            'collision': hit.get('collision') or hit.get('rigidBody'),
            'position': list(hit.get('position', [])),
            'normal': list(hit.get('normal', [])),
            'distance': float(hit.get('distance', 0.0)),
        }}
    else:
        result = {{
            'hit': False,
            'start': start,
            'end': end,
            'radius': radius,
            'direction': direction,
            'sweep_distance': distance,
        }}
        if isinstance(hit, dict) and 'error' in hit:
            result['error'] = hit['error']
    print(json.dumps(result, default=str))
"""
    return await kit_tools.queue_exec_patch(
        code, f"sweep_sphere {start} -> {end} r={radius}"
    )


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
    # Data handlers (9)
    data["add_force_torque_sensor"] = _handle_add_force_torque_sensor
    data["add_vision_classifier_gate"] = _handle_add_vision_classifier_gate
    data["barcode_reader_sensor"] = _handle_barcode_reader_sensor
    data["list_contacts"] = _handle_list_contacts
    data["nir_material_sensor"] = _handle_nir_material_sensor
    data["overlap_box"] = _handle_overlap_box
    data["overlap_sphere"] = _handle_overlap_sphere
    data["raycast"] = _handle_raycast
    data["sweep_sphere"] = _handle_sweep_sphere

    # Code-gen handlers (5)
    codegen["add_proximity_sensor"] = _gen_add_proximity_sensor
    codegen["add_sensor_to_prim"] = _gen_add_sensor
    codegen["configure_camera"] = _gen_configure_camera
    codegen["set_camera_look_at"] = _gen_set_camera_look_at
    codegen["set_camera_params"] = _gen_set_camera_params

