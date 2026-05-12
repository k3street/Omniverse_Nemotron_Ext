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
    from ..tool_executor import _RENDER_QUALITY_PRESETS
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
    from ..tool_executor import _QUICK_DEMO_TEMPLATES, _SCENE_STYLE_PRESETS
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
# Registration


def register(
    data: Dict[str, Callable[..., Any]],
    codegen: Dict[str, Callable[..., Any]],
) -> None:
    """Phase 6 wave 15 — dispatch lines in tool_executor.py still
    reference these names via re-import. Phase 9 swaps to register()
    being authoritative; until then this is intentionally a no-op.
    """
    return None
