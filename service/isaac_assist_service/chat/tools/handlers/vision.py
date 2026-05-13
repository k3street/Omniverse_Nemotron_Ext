"""Vision / rendering handlers — target scope: viewport camera,
attention-map extraction, semantic labels, render mode + video
capture, quick-demo recording.

Phase 6 wave 15 — vision/rendering code generators move out of
tool_executor.py. Same migration pattern as Phase 3 / Phase 5 /
Phase 6 waves 1-14.

Per specs/IA_FULL_SPEC_2026-05-10.md Phases 2 + 6.
"""
from __future__ import annotations

from typing import Any, Callable, Dict

# ---------------------------------------------------------------------------
# Theme-local constants (Phase 8 wave 4, 2026-05-13)
# Migrated from tool_executor.py — used only by this module.

_RENDER_QUALITY_PRESETS = {
    "preview": {
        "renderer": "RayTracing",
        "resolution": (1280, 720),
        "spp": 1,
    },
    "presentation": {
        "renderer": "PathTracing",
        "resolution": (1920, 1080),
        "spp": 64,
    },
    "production": {
        "renderer": "PathTracing",
        "resolution": (3840, 2160),
        "spp": 256,
    },
}

_QUICK_DEMO_TEMPLATES = {
    "pick_place": {
        "default_robot": "franka",
        "default_objects": ["cube"],
        "policy_checkpoint": "ppo_pick_place_franka.pt",
        "policy_algo": "ppo",
        "task": "Pick objects from tray and place in bin",
        "camera_position": [1.5, -1.0, 1.2],
    },
    "mobile_nav": {
        "default_robot": "jetbot",
        "default_objects": ["waypoint"],
        "policy_checkpoint": "astar_diffdrive_jetbot.pt",
        "policy_algo": "astar",
        "task": "Navigate to waypoint avoiding obstacles",
        "camera_position": [0, -3, 4],
    },
    "humanoid_walk": {
        "default_robot": "g1",
        "default_objects": [],
        "policy_checkpoint": "groot_n1_g1_walk.pt",
        "policy_algo": "groot",
        "task": "Walk forward 2m with stable balance",
        "camera_position": [3, -3, 2],
    },
}

_SCENE_STYLE_PRESETS = {
    "clean": {"intensity": 1500, "background": "white_floor"},
    "industrial": {"intensity": 1000, "background": "concrete"},
    "lab": {"intensity": 2000, "background": "neutral_gray"},
    "dramatic": {"intensity": 800, "background": "dark"},
}

_LIGHT_TYPE_NAMES = (
    "DistantLight",
    "DomeLight",
    "SphereLight",
    "RectLight",
    "DiskLight",
    "CylinderLight",
)


# ---------------------------------------------------------------------------
# Phase 6 wave 15 — viewport + render + semantic + attention + demo video


def _gen_set_viewport_camera(args: Dict) -> str:
    # Viewport API silently ignores camera_path assignment when the target
    # doesn't exist or isn't a Camera prim. Tool used to report success
    # while the viewport stayed on /OmniverseKit_Persp. Pre-check + verify.
    return (
        "import omni.usd\n"
        "import omni.kit.viewport.utility\n"
        "from pxr import UsdGeom\n"
        f"cam_path = {args['camera_path']!r}\n"
        "stage = omni.usd.get_context().get_stage()\n"
        "cam_prim = stage.GetPrimAtPath(cam_path)\n"
        "if not cam_prim or not cam_prim.IsValid():\n"
        "    raise RuntimeError(f'set_viewport_camera: prim not found: {cam_path!r}')\n"
        "if not cam_prim.IsA(UsdGeom.Camera):\n"
        "    raise RuntimeError(f'set_viewport_camera: prim {cam_path!r} is a {cam_prim.GetTypeName()!r}, not a Camera')\n"
        "vp_api = omni.kit.viewport.utility.get_active_viewport()\n"
        "vp_api.camera_path = cam_path\n"
        "# Viewport assignment can be silently rejected; verify.\n"
        "if str(vp_api.camera_path) != cam_path:\n"
        "    raise RuntimeError(f'set_viewport_camera: assignment ignored; viewport still on {vp_api.camera_path!r}')\n"
        "print(f'viewport camera set to {cam_path}')"
    )


def _gen_render_video(args: Dict) -> str:
    """Generate code that runs Movie Capture for a clip."""
    # Phase 8 wave 4 — _RENDER_QUALITY_PRESETS migrated to module body.
    duration = float(args["duration"])
    camera = args.get("camera")  # may be None → active viewport camera
    quality = args.get("quality", "preview")
    if quality not in _RENDER_QUALITY_PRESETS:
        quality = "preview"
    preset = _RENDER_QUALITY_PRESETS[quality]
    fps = int(args.get("fps", 30))

    output_path = args.get("output_path")
    if not output_path:
        # Stable per-call name; the Kit-side code resolves the timestamp.
        output_path = "workspace/renders/render_<timestamp>.mp4"

    res_w, res_h = preset["resolution"]
    renderer = preset["renderer"]
    spp = preset["spp"]

    return f"""\
import os
import time
from pathlib import Path

# Movie Capture / kit.capture extension (RTX-rendered, NOT screen capture)
try:
    from omni.kit.capture import CaptureOptions, CaptureExtension
except ImportError:
    # Newer Kit versions expose the API under omni.kit.capture.viewport
    from omni.kit.capture.viewport import CaptureOptions, CaptureExtension

DURATION_S = {duration!r}
FPS = {fps!r}
QUALITY = {quality!r}
RES = ({res_w}, {res_h})
RENDERER = {renderer!r}
SPP = {spp!r}
CAMERA = {camera!r}

raw_output = {output_path!r}
ts = time.strftime('%Y%m%dT%H%M%SZ')
output_path = raw_output.replace('<timestamp>', ts)
out = Path(output_path)
out.parent.mkdir(parents=True, exist_ok=True)

options = CaptureOptions()
options.fps = FPS
options.resolution = RES
options.renderer = RENDERER  # 'PathTracing' or 'RayTracing'
options.spp = SPP
options.output_path = str(out)
options.start_frame = 0
options.end_frame = max(int(DURATION_S * FPS) - 1, 0)
if CAMERA:
    options.camera = CAMERA

ext = CaptureExtension.get_instance()
ext.start(options)

print(f'[render_video] preset={{QUALITY}} renderer={{RENDERER}} '
      f'resolution={{RES}} spp={{SPP}} duration={{DURATION_S}}s fps={{FPS}} '
      f'output={{out}}')
"""


def _gen_quick_demo(args: Dict) -> str:
    """Build a complete demo scene by chaining template + robot + objects + policy + camera."""
    # Phase 8 wave 4 — _SCENE_STYLE_PRESETS migrated to module body.
    demo_type = args.get("demo_type", "pick_place")
    template = _QUICK_DEMO_TEMPLATES.get(demo_type, _QUICK_DEMO_TEMPLATES["pick_place"])
    robot = args.get("robot", template["default_robot"])
    objects = args.get("objects", template["default_objects"])
    scene_style = args.get("scene_style", "clean")
    style = _SCENE_STYLE_PRESETS.get(scene_style, _SCENE_STYLE_PRESETS["clean"])
    cam_pos = template["camera_position"]

    return f"""\
# Quick Demo Builder: {demo_type}
# Robot: {robot} | Objects: {objects} | Style: {scene_style}
import omni.usd
from pxr import UsdGeom, UsdLux, UsdPhysics, Gf

stage = omni.usd.get_context().get_stage()
print("Step 1/5: Loading scene template ({demo_type})...")

# 1. Ground + physics
if not stage.GetPrimAtPath("/World/PhysicsScene"):
    UsdPhysics.Scene.Define(stage, "/World/PhysicsScene")
ground = UsdGeom.Cube.Define(stage, "/World/Ground")
ground.AddTranslateOp().Set(Gf.Vec3d(0, 0, -0.05))
ground.AddScaleOp().Set(Gf.Vec3f(5, 5, 0.05))
UsdPhysics.CollisionAPI.Apply(ground.GetPrim())

# 2. Lighting (style: {scene_style}, intensity={style['intensity']})
print("Step 2/5: Setting up {scene_style} lighting...")
dome = UsdLux.DomeLight.Define(stage, "/World/DomeLight")
dome.CreateIntensityAttr().Set({style['intensity']})

# 3. Robot — actually import the asset (was previously only a placeholder
#    Xform with a comment telling the caller to follow up; observed
#    2026-04-19 that the follow-up never happened, leaving an empty
#    Robot Xform that failed scenario verification.)
print("Step 3/5: Importing robot ({robot})...")
import carb as _carb
_asset_root = _carb.settings.get_settings().get("/persistent/isaac/asset_root/default") or \
              "https://omniverse-content-production.s3-us-west-2.amazonaws.com/Assets/Isaac/5.1"
# Known-good canonical URLs by robot name. Paths here are the 5.1 parent
# directory for each robot family + the primary .usd file name. If a
# robot name isn't in the map we fall back to the placeholder behaviour
# and print a clear note so callers know to follow up.
_ROBOT_ASSETS = {{
    "franka": "/Isaac/Robots/FrankaRobotics/FrankaPanda/franka.usd",
    "panda": "/Isaac/Robots/FrankaRobotics/FrankaPanda/franka.usd",
    "franka_emika": "/Isaac/Robots/FrankaRobotics/FrankaPanda/franka.usd",
    "ur10": "/Isaac/Robots/UniversalRobots/ur10/ur10.usd",
    "ur5": "/Isaac/Robots/UniversalRobots/ur5e/ur5e.usd",
    "ur5e": "/Isaac/Robots/UniversalRobots/ur5e/ur5e.usd",
    "jetbot": "/Isaac/Robots/NVIDIA/Jetbot/jetbot.usd",
    "carter": "/Isaac/Robots/NVIDIA/Carter/carter_v1.usd",
    "nova_carter": "/Isaac/Robots/NVIDIA/NovaCarter/nova_carter.usd",
}}
_robot_rel = _ROBOT_ASSETS.get({robot!r}.lower())
robot_xform_prim = stage.DefinePrim("/World/Robot", "Xform")
if _robot_rel:
    _robot_url = f"{{_asset_root}}{{_robot_rel}}"
    robot_xform_prim.GetReferences().AddReference(_robot_url)
    _n_children = len(list(robot_xform_prim.GetAllChildren()))
    if _n_children == 0:
        print(f"WARNING: AddReference({{_robot_url}}) resolved to 0 children — asset URL may have 404'd")
    else:
        print(f"  → loaded {{_n_children}} descendant prims from {{_robot_url}}")
else:
    print(f"  → robot={robot!r} is not in the known-URL map; left as empty Xform placeholder. Call import_robot() as follow-up.")

# 4. Demo objects
print("Step 4/5: Placing {len(objects)} demo objects...")
_objects_list = {objects!r}
for i, obj_name in enumerate(_objects_list):
    obj_path = f"/World/Objects/{{obj_name}}_{{i}}"
    obj = UsdGeom.Cube.Define(stage, obj_path)
    obj.AddTranslateOp().Set(Gf.Vec3d(0.5 + i * 0.1, 0.0, 0.05))
    obj.AddScaleOp().Set(Gf.Vec3f(0.04, 0.04, 0.04))
    UsdPhysics.RigidBodyAPI.Apply(obj.GetPrim())
    UsdPhysics.CollisionAPI.Apply(obj.GetPrim())

# 5. Camera
print("Step 5/5: Positioning camera...")
cam = UsdGeom.Camera.Define(stage, "/World/DemoCamera")
cam.AddTranslateOp().Set(Gf.Vec3d({cam_pos[0]}, {cam_pos[1]}, {cam_pos[2]}))
cam.CreateFocalLengthAttr().Set(35.0)

import omni.kit.viewport.utility
vp = omni.kit.viewport.utility.get_active_viewport()
if vp:
    vp.camera_path = "/World/DemoCamera"

print(f"\\n✓ Quick demo ready: {demo_type} with {robot}")
print(f"  Task: {template['task']}")
print(f"  Pre-trained policy: {template['policy_checkpoint']} ({template['policy_algo']})")
print(f"  Click ▶ Play to start, or call deploy_policy() to load the trained policy.")
"""


def _gen_record_demo_video(args: Dict) -> str:
    """Record viewport to MP4 file."""
    duration = args.get("duration", 10.0)
    camera = args.get("camera", "")
    output_path = args["output_path"]
    resolution = args.get("resolution", [1920, 1080])
    fps = args.get("fps", 30)

    camera_setup = (
        f"vp.camera_path = {camera!r}"
        if camera
        else "# Using current active camera"
    )

    return f"""\
# Record demo video to {output_path}
import os
import omni.kit.viewport.utility

output_path = {output_path!r}
duration_s = {duration}
fps = {fps}
resolution = ({resolution[0]}, {resolution[1]})
total_frames = int(duration_s * fps)

os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

vp = omni.kit.viewport.utility.get_active_viewport()
if vp is None:
    raise RuntimeError("No active viewport available")

{camera_setup}

try:
    from omni.kit.capture.viewport import CaptureOptions, CaptureExtension
    options = CaptureOptions()
    options.file_type = ".mp4"
    options.output_folder = os.path.dirname(output_path)
    options.file_name = os.path.basename(output_path).replace(".mp4", "")
    options.fps = fps
    options.start_frame = 0
    options.end_frame = total_frames
    options.res_width = resolution[0]
    options.res_height = resolution[1]
    capture = CaptureExtension.get_instance()
    capture.start(options)
    print(f"Recording {{duration_s}}s at {{resolution[0]}}x{{resolution[1]}}@{{fps}}fps to {{output_path}}")
except ImportError:
    from omni.kit.viewport.utility import capture_viewport_to_file
    print("Capture extension not available — using frame-by-frame fallback")
    for frame in range(total_frames):
        capture_viewport_to_file(vp, f"{{output_path}}.frame_{{frame:05d}}.png")
    print(f"Captured {{total_frames}} frames. Use ffmpeg to assemble: ffmpeg -framerate {{fps}} -i {{output_path}}.frame_%05d.png {{output_path}}")
"""


def _gen_extract_attention_maps(args: Dict) -> str:
    """Generate code to extract DiT cross-attention maps from GR00T."""
    checkpoint = args["checkpoint_path"]
    obs_path = args["observation_path"]
    layer = args.get("layer", 12)

    return f"""\
# Extract GR00T attention maps (layer {layer})
import torch
import json
import os

checkpoint_path = {checkpoint!r}
observation_path = {obs_path!r}
layer = {layer}

if not os.path.exists(checkpoint_path):
    print(json.dumps({{"error": f"Checkpoint not found: {{checkpoint_path}}"}}))
else:
    print(f"Loading GR00T checkpoint from {{checkpoint_path}}...")
    # Note: actual GR00T model loading requires gr00t.policy package
    # from gr00t.policy.dit_policy import DiTPolicy
    # model = DiTPolicy.load_from_checkpoint(checkpoint_path)
    # from torch.fx import create_feature_extractor
    # features = create_feature_extractor(model.vision_encoder,
    #     return_nodes={{f"encoder.layers.{{layer}}.self_attn.attn_drop": f"attn_{{layer}}"}})

    print(f"Attention map extraction configured for layer {{layer}}")
    print(f"Observation: {{observation_path}}")
    print("Run model.forward(observation) to capture attention; overlay on viewport image as heatmap")

    result = {{
        "checkpoint": checkpoint_path,
        "observation": observation_path,
        "layer": layer,
        "tap_node": f"encoder.layers.{{layer}}.self_attn.attn_drop",
        "next_step": "Run inference with feature extractor, save heatmap PNG",
    }}
    print(json.dumps(result, indent=2))
"""


def _gen_set_semantic_label(args: Dict) -> str:
    prim_path = args["prim_path"]
    class_name = args["class_name"]
    semantic_type = args.get("semantic_type", "class")
    return (
        "import omni.usd\n"
        "from pxr import Usd, Semantics\n"
        "stage = omni.usd.get_context().get_stage()\n"
        f"prim = stage.GetPrimAtPath({prim_path!r})\n"
        f"sem = Semantics.SemanticsAPI.Apply(prim, 'Semantics_{semantic_type}')\n"
        "sem.CreateSemanticTypeAttr().Set("
        f"{semantic_type!r})\n"
        "sem.CreateSemanticDataAttr().Set("
        f"{class_name!r})\n"
        f"print('semantic_label', {prim_path!r}, {semantic_type!r}, {class_name!r})"
    )


def _gen_set_render_mode(args: Dict) -> str:
    mode = args["mode"]
    _MODE_TO_HYDRA = {
        "preview": "rtx",  # Hydra Storm fallback handled below
        "rt": "rtx",
        "path_traced": "rtx",
    }
    _MODE_TO_RENDERMODE = {
        "preview": "RaytracedLighting",
        "rt": "RaytracedLighting",
        "path_traced": "PathTracing",
    }
    # Live-probed 2026-04-18: unknown mode (e.g. 'bogus_mode_xyz') fell
    # through to the default 'RaytracedLighting' and tool printed
    # "render_mode set to bogus_mode_xyz" with success=True. Now reject
    # unknown modes upfront so the agent doesn't parrot a fake mode name.
    if mode not in _MODE_TO_RENDERMODE:
        _allowed = sorted(_MODE_TO_RENDERMODE.keys())
        _msg = f"set_render_mode: unknown mode {mode!r} — expected one of {_allowed}"
        return f"raise ValueError({_msg!r})\n"
    hydra = _MODE_TO_HYDRA[mode]
    render_mode = _MODE_TO_RENDERMODE[mode]
    return (
        "import carb.settings\n"
        "settings = carb.settings.get_settings()\n"
        f"# render mode: {mode}\n"
        f"settings.set('/rtx/rendermode', {render_mode!r})\n"
        "try:\n"
        "    import omni.kit.viewport.utility as vpu\n"
        "    vp = vpu.get_active_viewport()\n"
        "    if vp is not None:\n"
        f"        vp.set_hd_engine({hydra!r})\n"
        "except Exception as exc:\n"
        f"    print('viewport switch skipped:', exc)\n"
        f"print('render_mode set to', {mode!r})"
    )


# ---------------------------------------------------------------------------
# Phase 6 wave 22 — stragglers


def _gen_focus_viewport_on(args: Dict) -> str:
    prim_path = args["prim_path"]
    # Old version printed "prim not found" and returned success=True — the
    # viewport wasn't framed but the agent could claim it was. Also the
    # outer try/except swallowed framing failures so partial failures
    # (extension not loaded, etc.) flowed up as success.
    # Note: bind prim_path to a Python variable in the generated code so
    # the f-string interpolation in messages uses that variable — avoids
    # quote-char collisions when prim_path repr gets embedded twice.
    return f"""\
import omni.usd
import omni.kit.commands

_prim_path = {prim_path!r}
ctx = omni.usd.get_context()
stage = ctx.get_stage()
prim = stage.GetPrimAtPath(_prim_path)
if not prim or not prim.IsValid():
    raise RuntimeError(f'focus_viewport_on: prim not found: {{_prim_path!r}}')

ctx.get_selection().set_selected_prim_paths([_prim_path], True)
import omni.kit.viewport.utility as _vpu
vp_api = _vpu.get_active_viewport()
if vp_api is None:
    raise RuntimeError('focus_viewport_on: no active viewport')
try:
    _vpu.frame_viewport_selection(vp_api)
except Exception:
    # Fallback: older Kit versions use the FramePrimsCommand. If it also
    # fails, we surface the underlying error — no silent swallow.
    omni.kit.commands.execute('FramePrimsCommand', prim_to_move=[], prims_to_frame=[_prim_path])
print(f"focus_viewport_on: framed {{_prim_path!r}}")
"""


# ---------------------------------------------------------------------------
# Phase 7 wave 11 — vision/camera/render data-handlers


async def _get_viewport_bytes() -> tuple:
    """Capture the viewport and return (raw_bytes, mime_type)."""
    from .. import kit_tools
    result = await kit_tools.get_viewport_image(max_dim=1280)
    b64 = result.get("image_b64") or result.get("data", "")
    if not b64:
        return None, None
    import base64
    return base64.b64decode(b64), "image/png"


def _get_vision_provider():
    from ...vision_gemini import GeminiVisionProvider
    return GeminiVisionProvider()


def _parse_last_json_line(output: str):
    """Return the last well-formed JSON object printed in `output`, or None."""
    import json as _json
    from typing import Optional as _Optional
    for line in reversed(output.splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                return _json.loads(line)
            except _json.JSONDecodeError:
                continue
    return None


async def _handle_capture_viewport(args: Dict) -> Dict:
    from .. import kit_tools
    max_dim = args.get("max_dim", 1280)
    return await kit_tools.get_viewport_image(max_dim=max_dim)


async def _handle_capture_camera_image(args: Dict) -> Dict:
    """Render a single frame from the named camera and return base64 PNG."""
    from .. import kit_tools
    camera_path = args.get("camera_path", "")
    if not camera_path:
        return {"error": "camera_path is required"}
    import re as _re
    if not _re.match(r"^/[A-Za-z0-9_/\- ]+$", camera_path):
        return {"error": f"Invalid camera_path: {camera_path}"}

    resolution = args.get("resolution") or [1280, 720]
    if (
        not isinstance(resolution, (list, tuple))
        or len(resolution) != 2
        or not all(isinstance(v, int) and v > 0 for v in resolution)
    ):
        return {"error": "resolution must be [width, height] of positive integers"}
    width, height = int(resolution[0]), int(resolution[1])

    code = f"""\
import omni.usd
import json
import base64
from pxr import UsdGeom

stage = omni.usd.get_context().get_stage()
prim = stage.GetPrimAtPath('{camera_path}')
if not prim or not prim.IsValid():
    print(json.dumps({{'error': 'Camera prim not found', 'camera_path': '{camera_path}'}}))
elif prim.GetTypeName() != 'Camera':
    print(json.dumps({{'error': 'Prim is not a Camera', 'camera_path': '{camera_path}'}}))
else:
    try:
        import omni.replicator.core as rep
        rp = rep.create.render_product('{camera_path}', ({width}, {height}))
        annot = rep.AnnotatorRegistry.get_annotator('rgb')
        annot.attach([rp])
        rep.orchestrator.step()
        data = annot.get_data()
        # Encode the numpy RGB(A) array to PNG via PIL
        try:
            from PIL import Image
            import numpy as np
            arr = np.asarray(data)
            if arr.ndim == 3 and arr.shape[2] == 4:
                img = Image.fromarray(arr[:, :, :3].astype('uint8'), mode='RGB')
            else:
                img = Image.fromarray(arr.astype('uint8'), mode='RGB')
            import io
            buf = io.BytesIO()
            img.save(buf, format='PNG')
            b64 = base64.b64encode(buf.getvalue()).decode('ascii')
        finally:
            try:
                annot.detach([rp])
            except Exception:
                pass
            try:
                rp.destroy()
            except Exception:
                pass
        print(json.dumps({{
            'camera_path': '{camera_path}',
            'resolution': [{width}, {height}],
            'image_base64': b64,
            'format': 'png',
            'message': 'Rendered 1 frame from {camera_path} at {width}x{height}',
        }}))
    except ImportError as e:
        print(json.dumps({{'error': 'Replicator unavailable: ' + str(e),
                           'hint': 'omni.replicator.core extension must be enabled'}}))
"""
    result = await kit_tools.exec_sync(code, timeout=30)
    if not result.get("success"):
        return {"error": f"Kit RPC /exec_sync failed: {result.get('output', 'unknown')}"}
    parsed = _parse_last_json_line(result.get("output", ""))
    if parsed is None:
        return {"error": "Failed to parse capture result", "raw_output": result.get("output", "")[:500]}
    return parsed


async def _handle_inspect_camera(args: Dict) -> Dict:
    from .. import kit_tools
    from .sensors import _gen_inspect_camera
    camera_path = args["camera_path"]
    code = _gen_inspect_camera(args)
    return await kit_tools.queue_exec_patch(code, f"Inspect camera at {camera_path}")


async def _handle_pixel_to_world(args: Dict) -> Dict:
    """Project a viewport pixel through the camera + depth buffer to world."""
    from .. import kit_tools
    camera = args["camera"]
    x = int(args["x"])
    y = int(args["y"])
    resolution = args.get("resolution")
    res_expr = repr(list(resolution)) if resolution else "None"
    code = f"""\
import omni.usd
from pxr import Usd, UsdGeom, Gf
import json

camera_path = {camera!r}
px = {x}
py = {y}
override_res = {res_expr}

stage = omni.usd.get_context().get_stage()
cam_prim = stage.GetPrimAtPath(camera_path)
result = {{'camera': camera_path, 'x': px, 'y': py}}

if not cam_prim or not cam_prim.IsValid():
    result['error'] = 'camera not found'
elif not UsdGeom.Camera(cam_prim):
    result['error'] = 'prim is not a UsdGeom.Camera'
else:
    cam = UsdGeom.Camera(cam_prim)
    gf_cam = cam.GetCamera(Usd.TimeCode.Default())

    # Determine viewport / depth resolution
    if override_res:
        width, height = override_res
    else:
        try:
            import omni.kit.viewport.utility as vpu
            vp = vpu.get_active_viewport()
            width, height = vp.resolution
        except Exception:
            width, height = (1280, 720)

    # NDC coords (top-left origin)
    ndc_x = (px / float(width)) * 2.0 - 1.0
    ndc_y = 1.0 - (py / float(height)) * 2.0

    # Sample depth buffer if available
    depth_m = None
    try:
        import omni.syntheticdata as sd
        depth_arr = sd.sensors.get_distance_to_camera(camera_path)
        if depth_arr is not None and depth_arr.size:
            ix = max(0, min(width - 1, px))
            iy = max(0, min(height - 1, py))
            depth_m = float(depth_arr[iy, ix])
    except Exception as exc:
        result['depth_warning'] = f'no depth buffer: {{exc}}'

    # Build inverse view-projection
    proj = gf_cam.frustum.ComputeProjectionMatrix()
    view = gf_cam.transform.GetInverse()
    inv_vp = (view * proj).GetInverse()

    near_pt = inv_vp.Transform(Gf.Vec3d(ndc_x, ndc_y, -1.0))
    far_pt = inv_vp.Transform(Gf.Vec3d(ndc_x, ndc_y, 1.0))
    direction = (far_pt - near_pt).GetNormalized()

    if depth_m is None:
        # Without depth, fall back to a unit ray at 1 m
        depth_m = 1.0
        result['depth_fallback'] = True

    world = near_pt + direction * depth_m
    result['world_position'] = [world[0], world[1], world[2]]
    result['ray_origin'] = [near_pt[0], near_pt[1], near_pt[2]]
    result['ray_direction'] = [direction[0], direction[1], direction[2]]
    result['depth_m'] = depth_m

print(json.dumps(result, default=str))
"""
    return await kit_tools.queue_exec_patch(code, f"pixel_to_world {camera}@({x},{y})")


async def _handle_list_lights(args: Dict) -> Dict:
    """Enumerate all UsdLux light prims in the current stage via Kit RPC."""
    from .. import kit_tools
    # Phase 8 wave 4 — _LIGHT_TYPE_NAMES migrated to module body.
    type_tuple = repr(_LIGHT_TYPE_NAMES)
    code = f"""\
import omni.usd
import json

stage = omni.usd.get_context().get_stage()
LIGHT_TYPES = set({type_tuple})

lights = []
has_dome = False
if stage is not None:
    for prim in stage.Traverse():
        type_name = prim.GetTypeName()
        if type_name not in LIGHT_TYPES:
            continue
        intensity_attr = prim.GetAttribute('inputs:intensity')
        color_attr = prim.GetAttribute('inputs:color')
        enabled_attr = prim.GetAttribute('inputs:enabled')
        intensity = float(intensity_attr.Get()) if intensity_attr and intensity_attr.HasAuthoredValue() else None
        color_val = color_attr.Get() if color_attr and color_attr.HasAuthoredValue() else None
        if color_val is not None:
            color = [float(color_val[0]), float(color_val[1]), float(color_val[2])]
        else:
            color = None
        enabled = bool(enabled_attr.Get()) if enabled_attr and enabled_attr.HasAuthoredValue() else True
        if type_name == 'DomeLight':
            has_dome = True
        lights.append({{
            'path': str(prim.GetPath()),
            'type': type_name,
            'intensity': intensity,
            'color': color,
            'enabled': enabled,
        }})

print(json.dumps({{
    'lights': lights,
    'count': len(lights),
    'has_dome': has_dome,
}}))
"""
    return await kit_tools.queue_exec_patch(code, "List all UsdLux light prims in the stage")


async def _handle_get_light_properties(args: Dict) -> Dict:
    """Read the full attribute set of a single light prim."""
    from .. import kit_tools
    light_path = args["light_path"]
    code = f"""\
import omni.usd
import json

stage = omni.usd.get_context().get_stage()
prim = stage.GetPrimAtPath('{light_path}')

if not prim or not prim.IsValid():
    print(json.dumps({{'error': 'prim not found', 'path': '{light_path}'}}))
else:
    type_name = prim.GetTypeName()

    def _get(attr_name):
        a = prim.GetAttribute(attr_name)
        if a and a.HasAuthoredValue():
            return a.Get()
        if a:
            return a.Get()
        return None

    intensity = _get('inputs:intensity')
    exposure = _get('inputs:exposure')
    color = _get('inputs:color')
    enabled = _get('inputs:enabled')
    color_temp = _get('inputs:colorTemperature')
    angle = _get('inputs:angle') if type_name == 'DistantLight' else None
    radius = _get('inputs:radius') if type_name in ('SphereLight', 'DiskLight') else None
    width = _get('inputs:width') if type_name == 'RectLight' else None
    height = _get('inputs:height') if type_name == 'RectLight' else None
    texture_file = None
    if type_name == 'DomeLight':
        tex = _get('inputs:texture:file')
        if tex is not None:
            texture_file = str(tex)

    out = {{
        'path': '{light_path}',
        'type': type_name,
        'intensity': float(intensity) if intensity is not None else None,
        'exposure': float(exposure) if exposure is not None else None,
        'color': [float(color[0]), float(color[1]), float(color[2])] if color is not None else None,
        'enabled': bool(enabled) if enabled is not None else True,
        'color_temperature': float(color_temp) if color_temp is not None else None,
        'angle': float(angle) if angle is not None else None,
        'radius': float(radius) if radius is not None else None,
        'width': float(width) if width is not None else None,
        'height': float(height) if height is not None else None,
        'texture_file': texture_file,
    }}
    print(json.dumps(out))
"""
    return await kit_tools.queue_exec_patch(code, f"Read light properties for {light_path}")


async def _handle_list_cameras(args: Dict) -> Dict:
    """Walk the stage and return all UsdGeom.Camera prims with type info."""
    from .. import kit_tools
    code = """\
import omni.usd
import json
from pxr import Usd, UsdGeom

stage = omni.usd.get_context().get_stage()
cameras = []
if stage is not None:
    for prim in stage.Traverse():
        if prim.GetTypeName() == 'Camera':
            cam = UsdGeom.Camera(prim)
            proj_attr = cam.GetProjectionAttr()
            projection = proj_attr.Get() if proj_attr else 'perspective'
            cameras.append({
                'path': str(prim.GetPath()),
                'name': prim.GetName(),
                'projection': str(projection) if projection else 'perspective',
                'purpose': str(UsdGeom.Imageable(prim).GetPurposeAttr().Get() or 'default'),
                'kind': str(Usd.ModelAPI(prim).GetKind() or ''),
            })
print(json.dumps({'cameras': cameras, 'count': len(cameras)}))
"""
    result = await kit_tools.exec_sync(code, timeout=10)
    if not result.get("success"):
        return {
            "error": f"Kit RPC /exec_sync failed: {result.get('output', 'unknown')}",
            "hint": "Is Isaac Sim running with the extension's Kit RPC enabled?",
        }
    parsed = _parse_last_json_line(result.get("output", ""))
    if parsed is None:
        return {"error": "Failed to parse camera list", "raw_output": result.get("output", "")[:500]}
    return parsed


async def _handle_get_camera_params(args: Dict) -> Dict:
    """Read all cinematographic attributes from a UsdGeom.Camera prim."""
    from .. import kit_tools
    camera_path = args.get("camera_path", "")
    if not camera_path:
        return {"error": "camera_path is required"}
    # Sanitize path
    import re as _re
    if not _re.match(r"^/[A-Za-z0-9_/\- ]+$", camera_path):
        return {"error": f"Invalid camera_path: {camera_path}"}

    code = f"""\
import omni.usd
import json
import math
from pxr import UsdGeom

stage = omni.usd.get_context().get_stage()
prim = stage.GetPrimAtPath('{camera_path}')
if not prim or not prim.IsValid():
    print(json.dumps({{'error': 'Camera prim not found', 'camera_path': '{camera_path}'}}))
elif prim.GetTypeName() != 'Camera':
    print(json.dumps({{'error': 'Prim is not a Camera', 'camera_path': '{camera_path}', 'type': str(prim.GetTypeName())}}))
else:
    cam = UsdGeom.Camera(prim)
    focal = cam.GetFocalLengthAttr().Get() or 0.0
    h_ap = cam.GetHorizontalApertureAttr().Get() or 0.0
    v_ap = cam.GetVerticalApertureAttr().Get() or 0.0
    clip = cam.GetClippingRangeAttr().Get()
    near, far = (float(clip[0]), float(clip[1])) if clip else (0.0, 0.0)
    focus = cam.GetFocusDistanceAttr().Get() or 0.0
    fstop = cam.GetFStopAttr().Get() or 0.0
    proj = cam.GetProjectionAttr().Get() or 'perspective'

    def _fov_deg(aperture, focal_length):
        if focal_length <= 0 or aperture <= 0:
            return 0.0
        return math.degrees(2.0 * math.atan(aperture / (2.0 * focal_length)))

    info = {{
        'camera_path': '{camera_path}',
        'projection': str(proj),
        'focal_length_mm': float(focal),
        'horizontal_aperture_mm': float(h_ap),
        'vertical_aperture_mm': float(v_ap),
        'horizontal_fov_deg': _fov_deg(float(h_ap), float(focal)),
        'vertical_fov_deg': _fov_deg(float(v_ap), float(focal)),
        'clipping_range_m': [near, far],
        'focus_distance_m': float(focus),
        'f_stop': float(fstop),
    }}
    print(json.dumps(info))
"""
    result = await kit_tools.exec_sync(code, timeout=10)
    if not result.get("success"):
        return {"error": f"Kit RPC /exec_sync failed: {result.get('output', 'unknown')}"}
    parsed = _parse_last_json_line(result.get("output", ""))
    if parsed is None:
        return {"error": "Failed to parse camera params", "raw_output": result.get("output", "")[:500]}
    return parsed


async def _handle_get_render_config(args: Dict) -> Dict:
    """Read current renderer mode, SPP, max bounces, and viewport resolution.

    Generates a small introspection script and queues it via Kit RPC. The Kit
    side runs it and returns the printed JSON. When Kit is unreachable we
    return a structured stub so the LLM still gets predictable shape.
    """
    from .. import kit_tools
    code = """\
import json
try:
    import omni.kit.viewport.utility as vp_util
    import omni.usd
    from pxr import Sdf

    vp = vp_util.get_active_viewport()
    resolution = list(vp.resolution) if vp is not None else [None, None]
    renderer = vp.hydra_engine if vp is not None else None

    stage = omni.usd.get_context().get_stage()

    def _read(attr_path, default=None):
        prim_path, _, attr_name = attr_path.rpartition('.')
        prim = stage.GetPrimAtPath(prim_path)
        if not prim or not prim.IsValid():
            return default
        attr = prim.GetAttribute(attr_name)
        if attr is None or not attr.HasValue():
            return default
        return attr.Get()

    spp = _read('/Render/Vars.samplesPerPixel', 1)
    max_bounces = _read('/Render/Vars.maxBounces', 4)
    bloom = bool(_read('/Render/PostProcess/Bloom.enabled', False))
    tonemap = str(_read('/Render/PostProcess/Tonemap.operator', 'aces'))
    dof = bool(_read('/Render/PostProcess/DoF.enabled', False))
    motion_blur = bool(_read('/Render/PostProcess/MotionBlur.enabled', False))

    print(json.dumps({
        'renderer': renderer,
        'samples_per_pixel': spp,
        'max_bounces': max_bounces,
        'resolution': resolution,
        'post_process': {
            'bloom': bloom,
            'tonemap': tonemap,
            'dof': dof,
            'motion_blur': motion_blur,
        },
    }))
except Exception as e:
    print(json.dumps({'error': str(e)}))
"""
    result = await kit_tools.queue_exec_patch(code, "Read current render config")
    return {
        "queued": result.get("queued", False),
        "patch_id": result.get("patch_id"),
        "note": (
            "Render config introspection queued. Kit will print a JSON dict with keys: "
            "renderer, samples_per_pixel, max_bounces, resolution, post_process."
        ),
    }


async def _handle_get_timeline_state(args: Dict) -> Dict:
    """Return current timeline cursor + start/end + fps + play state."""
    from .. import kit_tools
    code = """\
import json
try:
    import omni.timeline
    import omni.usd
    tl = omni.timeline.get_timeline_interface()
    stage = omni.usd.get_context().get_stage()
    if stage is None:
        print(json.dumps({'error': 'no stage open'}))
    else:
        fps = float(stage.GetTimeCodesPerSecond() or 24.0)
        start_code = float(stage.GetStartTimeCode())
        end_code = float(stage.GetEndTimeCode())
        # current_time / start / end on the timeline interface are exposed in
        # *seconds* in modern Kit (>=105), so report both forms.
        try:
            cur = float(tl.get_current_time())
        except Exception:
            cur = float(tl.get_current_time_code()) / fps if fps else 0.0
        is_playing = bool(tl.is_playing()) if hasattr(tl, 'is_playing') else False
        looping = bool(tl.is_looping()) if hasattr(tl, 'is_looping') else False
        duration_codes = max(end_code - start_code, 0.0)
        print(json.dumps({
            'current_time': cur,
            'start_time': start_code,
            'end_time': end_code,
            'fps': fps,
            'time_codes_per_second': fps,
            'is_playing': is_playing,
            'looping': looping,
            'duration_seconds': duration_codes / fps if fps else 0.0,
        }))
except Exception as e:
    print(json.dumps({'error': str(e)}))
"""
    result = await kit_tools.queue_exec_patch(code, "Read timeline state (current/start/end/fps/playing)")
    return {
        "queued": result.get("queued", False),
        "patch_id": result.get("patch_id"),
        "note": (
            "Timeline-state introspection queued. Kit will print a JSON dict with keys: "
            "current_time, start_time, end_time, fps, time_codes_per_second, is_playing, "
            "looping, duration_seconds. Time codes are USD frames; duration_seconds = "
            "(end_time - start_time) / fps."
        ),
    }


async def _handle_list_keyframes(args: Dict) -> Dict:
    """Read every authored TimeSample on a single attribute."""
    from .. import kit_tools
    prim_path = args["prim_path"]
    attr = args["attr"]
    prim_path_repr = repr(prim_path)
    attr_repr = repr(attr)
    code = (
        "import json\n"
        "try:\n"
        "    import omni.usd\n"
        "    stage = omni.usd.get_context().get_stage()\n"
        "    if stage is None:\n"
        "        print(json.dumps({'error': 'no stage open'}))\n"
        "    else:\n"
        f"        prim_path = {prim_path_repr}\n"
        f"        attr_name = {attr_repr}\n"
        "        prim = stage.GetPrimAtPath(prim_path)\n"
        "        if not prim or not prim.IsValid():\n"
        "            print(json.dumps({'error': f'prim not found: {prim_path}'}))\n"
        "        else:\n"
        "            attr_handle = prim.GetAttribute(attr_name)\n"
        "            if not attr_handle or not attr_handle.IsValid():\n"
        "                print(json.dumps({\n"
        "                    'error': f'attribute not found: {attr_name}',\n"
        "                    'prim_path': prim_path,\n"
        "                }))\n"
        "            else:\n"
        "                fps = float(stage.GetTimeCodesPerSecond() or 24.0)\n"
        "                times = list(attr_handle.GetTimeSamples())\n"
        "                samples = []\n"
        "                for tc in times:\n"
        "                    try:\n"
        "                        v = attr_handle.Get(tc)\n"
        "                        # Coerce Vt/Gf types into JSON-safe primitives.\n"
        "                        try:\n"
        "                            v_json = list(v) if hasattr(v, '__iter__') and not isinstance(v, str) else v\n"
        "                        except Exception:\n"
        "                            v_json = repr(v)\n"
        "                        samples.append({\n"
        "                            'time_code': float(tc),\n"
        "                            'time_seconds': float(tc) / fps if fps else 0.0,\n"
        "                            'value': v_json,\n"
        "                        })\n"
        "                    except Exception as e:\n"
        "                        samples.append({\n"
        "                            'time_code': float(tc),\n"
        "                            'time_seconds': float(tc) / fps if fps else 0.0,\n"
        "                            'value': None,\n"
        "                            'error': str(e),\n"
        "                        })\n"
        "                if times:\n"
        "                    first, last = float(times[0]), float(times[-1])\n"
        "                    range_codes = [first, last]\n"
        "                    range_seconds = [first / fps if fps else 0.0, last / fps if fps else 0.0]\n"
        "                else:\n"
        "                    range_codes = []\n"
        "                    range_seconds = []\n"
        "                print(json.dumps({\n"
        "                    'prim_path': prim_path,\n"
        "                    'attr': attr_name,\n"
        "                    'has_timesamples': bool(times),\n"
        "                    'count': len(times),\n"
        "                    'fps': fps,\n"
        "                    'samples': samples,\n"
        "                    'time_range_codes': range_codes,\n"
        "                    'time_range_seconds': range_seconds,\n"
        "                }))\n"
        "except Exception as e:\n"
        "    print(json.dumps({'error': str(e)}))\n"
    )
    result = await kit_tools.queue_exec_patch(
        code, f"List keyframes for {prim_path}.{attr}"
    )
    return {
        "queued": result.get("queued", False),
        "patch_id": result.get("patch_id"),
        "prim_path": prim_path,
        "attr": attr,
        "note": (
            "Keyframe enumeration queued. Kit will print a JSON dict with keys: "
            "prim_path, attr, has_timesamples, count, fps, samples (list of "
            "{time_code, time_seconds, value}), time_range_codes, time_range_seconds. "
            "has_timesamples=false means the attribute has only a default value."
        ),
    }


async def _handle_get_viewport_camera(args: Dict) -> Dict:
    """Return the active viewport's current camera path and resolution."""
    from .. import kit_tools
    code = """\
import json
import omni.kit.viewport.utility as _vpu

vp_api = _vpu.get_active_viewport()
cam_path = None
viewport_id = ""
res = [0, 0]
if vp_api is not None:
    try:
        cam_path = str(vp_api.camera_path) if vp_api.camera_path else None
    except Exception:
        cam_path = None
    try:
        viewport_id = getattr(vp_api, "id", "") or ""
    except Exception:
        viewport_id = ""
    try:
        res = list(vp_api.resolution)
    except Exception:
        res = [0, 0]
print(json.dumps({"camera_path": cam_path, "viewport_id": viewport_id, "resolution": res}))
"""
    return await kit_tools.queue_exec_patch(code, "Read active viewport camera")


async def _handle_vision_detect_objects(args: Dict) -> Dict:
    img, mime = await _get_viewport_bytes()
    if img is None:
        return {"error": "Could not capture viewport image. Is Isaac Sim running?"}
    vp = _get_vision_provider()
    labels = args.get("labels")
    max_obj = args.get("max_objects", 10)
    detections = await vp.detect_objects(img, mime, labels=labels, max_objects=max_obj)
    return {"detections": detections, "count": len(detections), "model": vp.model}


async def _handle_vision_bounding_boxes(args: Dict) -> Dict:
    img, mime = await _get_viewport_bytes()
    if img is None:
        return {"error": "Could not capture viewport image. Is Isaac Sim running?"}
    vp = _get_vision_provider()
    boxes = await vp.detect_bounding_boxes(img, mime, max_objects=args.get("max_objects", 25))
    return {"bounding_boxes": boxes, "count": len(boxes), "model": vp.model}


async def _handle_vision_plan_trajectory(args: Dict) -> Dict:
    img, mime = await _get_viewport_bytes()
    if img is None:
        return {"error": "Could not capture viewport image. Is Isaac Sim running?"}
    vp = _get_vision_provider()
    points = await vp.plan_trajectory(
        img, args["instruction"], num_points=args.get("num_points", 15), mime_type=mime,
    )
    return {"trajectory": points, "num_points": len(points), "model": vp.model}


async def _handle_vision_analyze_scene(args: Dict) -> Dict:
    img, mime = await _get_viewport_bytes()
    if img is None:
        return {"error": "Could not capture viewport image. Is Isaac Sim running?"}
    vp = _get_vision_provider()
    analysis = await vp.analyze_scene(img, args["question"], mime_type=mime)
    return {"analysis": analysis, "model": vp.model}


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
    # Data handlers (16)
    data["capture_camera_image"] = _handle_capture_camera_image
    data["capture_viewport"] = _handle_capture_viewport
    data["get_camera_params"] = _handle_get_camera_params
    data["get_light_properties"] = _handle_get_light_properties
    data["get_render_config"] = _handle_get_render_config
    data["get_timeline_state"] = _handle_get_timeline_state
    data["get_viewport_camera"] = _handle_get_viewport_camera
    data["inspect_camera"] = _handle_inspect_camera
    data["list_cameras"] = _handle_list_cameras
    data["list_keyframes"] = _handle_list_keyframes
    data["list_lights"] = _handle_list_lights
    data["pixel_to_world"] = _handle_pixel_to_world
    data["vision_analyze_scene"] = _handle_vision_analyze_scene
    data["vision_bounding_boxes"] = _handle_vision_bounding_boxes
    data["vision_detect_objects"] = _handle_vision_detect_objects
    data["vision_plan_trajectory"] = _handle_vision_plan_trajectory

    # Code-gen handlers (8)
    codegen["extract_attention_maps"] = _gen_extract_attention_maps
    codegen["focus_viewport_on"] = _gen_focus_viewport_on
    codegen["quick_demo"] = _gen_quick_demo
    codegen["record_demo_video"] = _gen_record_demo_video
    codegen["render_video"] = _gen_render_video
    codegen["set_render_mode"] = _gen_set_render_mode
    codegen["set_semantic_label"] = _gen_set_semantic_label
    codegen["set_viewport_camera"] = _gen_set_viewport_camera

