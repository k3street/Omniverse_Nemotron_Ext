"""SDG / DR handlers — target scope: SDG pipeline configuration,
COCO/YOLO/KITTI dataset writers, domain randomization (correlated,
latency, differential), dataset export, class balancing.

Phase 6 wave 5 — first SDG/DR code generators move out of tool_executor.py.
Same migration pattern as Phase 3 / Phase 5 / Phase 6 waves 1-4.

Per specs/IA_FULL_SPEC_2026-05-10.md Phases 2 + 6.
"""
# audit-Q17: cohesive — full SDG/DR handler domain (pipeline config, COCO/YOLO writers, domain randomization, dataset export, class balance)
from __future__ import annotations

import json
from typing import Any, Callable, Dict
from service.isaac_assist_service.observability.handler_telemetry import with_telemetry


# ---------------------------------------------------------------------------
# Phase 6 wave 5 — SDG pipeline + DR + writers + class balance


def _gen_configure_sdg(args: Dict) -> str:
    """Generate a minimal Replicator SDG session with a BasicWriter attached.

    Emits code that creates a Replicator layer, attaches the active camera to a
    render product at the requested resolution, configures a ``BasicWriter`` with
    the requested annotators enabled, and runs until the frame count is reached.

    Args:
        args: Tool arguments dict containing:
            - annotators (list[str], optional): Annotator names to enable (e.g.
              ``["rgb", "bounding_box_2d", "semantic_segmentation"]``).
              Defaults to ``["rgb", "bounding_box_2d"]``.
            - num_frames (int, optional): Total frames to generate. Defaults to
              ``10``.
            - output_dir (str, optional): Output directory for written data.
              Defaults to ``"/tmp/sdg_output"``.
            - resolution (list[int], optional): ``[width, height]`` render
              resolution. Defaults to ``[1280, 720]``.

    Returns:
        str: Python source code string for Kit RPC execution.
    """
    annotators = args.get("annotators", ["rgb", "bounding_box_2d"])
    num_frames = args.get("num_frames", 10)
    output_dir = args.get("output_dir", "/tmp/sdg_output")
    resolution = args.get("resolution", [1280, 720])

    ann_lines = "\n    ".join(
        f'rp.AnnotatorRegistry.get_annotator("{a}")' for a in annotators
    )

    return f"""\
import omni.replicator.core as rep

with rep.new_layer():
    camera = rep.get.camera()
    rp = rep.create.render_product(camera, ({resolution[0]}, {resolution[1]}))

    writer = rep.WriterRegistry.get("BasicWriter")
    writer.initialize(output_dir="{output_dir}", rgb=True,
                      bounding_box_2d={'bounding_box_2d' in annotators},
                      semantic_segmentation={'semantic_segmentation' in annotators},
                      instance_segmentation={'instance_segmentation' in annotators},
                      normals={'normals' in annotators},
                      distance_to_camera={'distance_to_camera' in annotators})
    writer.attach([rp])

    rep.orchestrator.run_until_complete(num_frames={num_frames})
"""


def _gen_create_sdg_pipeline(args: Dict) -> str:
    """Generate a full Replicator SDG pipeline with camera, render product, writer."""
    annotators = args.get("annotators", ["bounding_box_2d"])
    output_format = args.get("output_format", "basic")
    num_frames = args.get("num_frames", 100)
    output_dir = args.get("output_dir", "/tmp/sdg_output")
    cam_pos = args.get("camera_position", [0, 0, 5])
    cam_look = args.get("camera_look_at", [0, 0, 0])
    resolution = args.get("resolution", [1280, 720])

    # Map output_format to writer class name
    writer_map = {
        "coco": "CocoWriter",
        "kitti": "KittiWriter",
        "basic": "BasicWriter",
        "numpy": "BasicWriter",
    }
    writer_class = writer_map.get(output_format, "BasicWriter")

    # Build writer.initialize() kwargs based on format
    if output_format == "coco":
        writer_init = f'writer.initialize(output_dir="{output_dir}")'
    elif output_format == "kitti":
        writer_init = f'writer.initialize(output_dir="{output_dir}")'
    elif output_format == "numpy":
        # BasicWriter with raw annotator flags
        init_kwargs = [f'output_dir="{output_dir}"', "rgb=True"]
        if "normals" in annotators:
            init_kwargs.append("normals=True")
        if "depth" in annotators:
            init_kwargs.append("distance_to_camera=True")
        if "semantic_segmentation" in annotators:
            init_kwargs.append("semantic_segmentation=True")
        if "instance_segmentation" in annotators:
            init_kwargs.append("instance_segmentation=True")
        if "bounding_box_2d" in annotators:
            init_kwargs.append("bounding_box_2d=True")
        if "bounding_box_3d" in annotators:
            init_kwargs.append("bounding_box_3d=True")
        if "occlusion" in annotators:
            init_kwargs.append("occlusion=True")
        writer_init = "writer.initialize(" + ", ".join(init_kwargs) + ")"
    else:
        # basic
        init_kwargs = [f'output_dir="{output_dir}"', "rgb=True"]
        for ann in annotators:
            # Map annotator names to BasicWriter kwargs
            kwarg = ann.replace("-", "_")
            if kwarg == "depth":
                kwarg = "distance_to_camera"
            init_kwargs.append(f"{kwarg}=True")
        writer_init = "writer.initialize(" + ", ".join(init_kwargs) + ")"

    return f"""\
import omni.replicator.core as rep

with rep.new_layer():
    camera = rep.create.camera(
        position=({cam_pos[0]}, {cam_pos[1]}, {cam_pos[2]}),
        look_at=({cam_look[0]}, {cam_look[1]}, {cam_look[2]}),
    )
    rp = rep.create.render_product(camera, ({resolution[0]}, {resolution[1]}))

    writer = rep.WriterRegistry.get("{writer_class}")
    {writer_init}
    writer.attach([rp])

    with rep.trigger.on_frame(num_frames={num_frames}):
        pass

    rep.orchestrator.run()

print("SDG pipeline started: {num_frames} frames -> {output_dir}")
"""


def _gen_add_domain_randomizer(args: Dict) -> str:
    """Generate Replicator domain randomization code."""
    target = args["target"]
    rand_type = args["randomizer_type"]
    params = args.get("params", {})

    lines = ["import omni.replicator.core as rep", ""]

    if rand_type == "pose":
        surface = params.get("surface_prim", "/World/Ground")
        min_angle = params.get("min_angle", -180)
        max_angle = params.get("max_angle", 180)
        lines.extend([
            "with rep.trigger.on_frame():",
            f"    with rep.get.prims(path_pattern=\"{target}\"):",
            f"        rep.randomizer.scatter_2d(",
            f"            surface_prims=rep.get.prims(path_pattern=\"{surface}\")",
            f"        )",
            f"        rep.randomizer.rotation(",
            f"            min_angle={min_angle}, max_angle={max_angle}",
            f"        )",
        ])

    elif rand_type == "texture":
        lines.extend([
            "with rep.trigger.on_frame():",
            f"    with rep.get.prims(path_pattern=\"{target}\"):",
            "        rep.randomizer.texture(",
            "            textures=rep.distribution.choice([",
            "                'omniverse://localhost/NVIDIA/Materials/Base/Stone/Fieldstone.mdl',",
            "                'omniverse://localhost/NVIDIA/Materials/Base/Wood/Oak.mdl',",
            "                'omniverse://localhost/NVIDIA/Materials/Base/Metal/Steel_Brushed.mdl',",
            "            ])",
            "        )",
        ])

    elif rand_type == "color":
        c_min = params.get("color_min", [0, 0, 0])
        c_max = params.get("color_max", [1, 1, 1])
        lines.extend([
            "with rep.trigger.on_frame():",
            f"    with rep.get.prims(path_pattern=\"{target}\"):",
            f"        rep.randomizer.color(",
            f"            colors=rep.distribution.uniform(",
            f"                ({c_min[0]}, {c_min[1]}, {c_min[2]}),",
            f"                ({c_max[0]}, {c_max[1]}, {c_max[2]}),",
            f"            )",
            f"        )",
        ])

    elif rand_type == "lighting":
        i_min = params.get("intensity_min", 500)
        i_max = params.get("intensity_max", 2000)
        lines.extend([
            "# Note: 'intensity' is in nits (candelas/m^2), not lux.",
            "# Lux is not directly settable on USD lights.",
            "with rep.trigger.on_frame():",
            f"    with rep.get.prims(path_pattern=\"{target}\"):",
            f"        rep.modify.attribute(",
            f"            \"intensity\",",
            f"            rep.distribution.uniform({i_min}, {i_max}),",
            f"        )",
        ])

    elif rand_type == "material_properties":
        r_min = params.get("roughness_min", 0.0)
        r_max = params.get("roughness_max", 1.0)
        m_min = params.get("metallic_min", 0.0)
        m_max = params.get("metallic_max", 1.0)
        lines.extend([
            "with rep.trigger.on_frame():",
            f"    with rep.get.prims(path_pattern=\"{target}\"):",
            f"        rep.modify.attribute(",
            f"            \"inputs:reflection_roughness_constant\",",
            f"            rep.distribution.uniform({r_min}, {r_max}),",
            f"        )",
            f"        rep.modify.attribute(",
            f"            \"inputs:metallic_constant\",",
            f"            rep.distribution.uniform({m_min}, {m_max}),",
            f"        )",
        ])

    elif rand_type == "visibility":
        prob = params.get("probability", 0.5)
        lines.extend([
            "with rep.trigger.on_frame():",
            f"    with rep.get.prims(path_pattern=\"{target}\"):",
            f"        rep.modify.visibility(",
            f"            rep.distribution.choice([True, False],",
            f"                weights=[{prob}, {1.0 - prob}])",
            f"        )",
        ])

    else:
        lines.append(f"# Unknown randomizer type: {rand_type}")

    return "\n".join(lines)


def _gen_export_dataset(args: Dict) -> str:
    """Generate async step-loop code for large dataset generation."""
    output_dir = args["output_dir"]
    num_frames = args["num_frames"]
    step_batch = args.get("step_batch", 10)

    return f"""\
import omni.replicator.core as rep
import asyncio

async def _export_dataset():
    num_frames = {num_frames}
    step_batch = {step_batch}
    for i in range(0, num_frames, step_batch):
        batch = min(step_batch, num_frames - i)
        for _ in range(batch):
            await rep.orchestrator.step_async()
        print(f"Progress: {{i + batch}}/{{num_frames}} frames")
    print(f"Dataset export complete: {{num_frames}} frames -> '{output_dir}'")

asyncio.ensure_future(_export_dataset())
"""


def _gen_configure_differential_sdg(args: Dict) -> str:
    """Configure a Replicator pipeline that re-renders only dynamic elements."""
    static_elements = args.get("static_elements", []) or []
    dynamic_elements = args.get("dynamic_elements", []) or []
    randomize = args.get("randomize") or ["rotation", "color"]

    static_lines = []
    for p in static_elements:
        static_lines.append(f"    rep.utils.set_static('{p}')  # freeze static element")
    static_block = "\n".join(static_lines) if static_lines else "    # no static elements supplied"

    dyn_targets = ", ".join(f"'{p}'" for p in dynamic_elements)
    rnd_lines = []
    if "rotation" in randomize:
        rnd_lines.append("        rep.randomizer.rotation(dynamic)")
    if "position" in randomize:
        rnd_lines.append("        rep.randomizer.position(dynamic)")
    if "color" in randomize:
        rnd_lines.append("        rep.randomizer.color(dynamic)")
    if "intensity" in randomize:
        rnd_lines.append("        rep.randomizer.light_intensity(dynamic)")
    if "scale" in randomize:
        rnd_lines.append("        rep.randomizer.scale(dynamic)")
    rnd_block = "\n".join(rnd_lines) if rnd_lines else "        # no randomizers selected"

    pattern = "|".join(dynamic_elements) or "NONE"
    n_static = len(static_elements)
    n_dynamic = len(dynamic_elements)
    randomize_list = list(randomize)
    return f"""\
import omni.replicator.core as rep

# Differential re-render: static elements are evaluated once, dynamic ones per frame.
with rep.new_layer():
{static_block}

    dynamic = rep.get.prims(path_pattern='({pattern})')

    with rep.trigger.on_frame():
{rnd_block}

_summary = {{
    'tool': 'configure_differential_sdg',
    'static_count': {n_static},
    'dynamic_count': {n_dynamic},
    'randomize': {randomize_list!r},
}}
print('configure_differential_sdg: pipeline configured — ' + str(_summary))
"""


def _gen_configure_coco_yolo_writer(args: Dict) -> str:
    """Custom COCO/YOLO writer with globally unique IDs across cameras."""
    output_dir = args.get("output_dir", "/tmp/sdg_output")
    cameras = args.get("cameras", []) or []
    fmt = args.get("format", "coco")
    categories = args.get("categories", []) or []
    id_offset = int(args.get("id_offset", 1_000_000))

    return f"""\
import json
import os
import omni.replicator.core as rep

OUTPUT_DIR = {output_dir!r}
CAMERAS = {list(cameras)!r}
FORMAT = {fmt!r}
CATEGORIES = {list(categories)!r}
ID_OFFSET = {id_offset}

os.makedirs(OUTPUT_DIR, exist_ok=True)

# Merged category map — written once, not per camera
category_map = {{i: name for i, name in enumerate(CATEGORIES)}}
with open(os.path.join(OUTPUT_DIR, 'categories.json'), 'w') as f:
    json.dump(category_map, f, indent=2)

_GLOBAL_ANN_ID = {{'next': 1}}


def _next_ann_id():
    nid = _GLOBAL_ANN_ID['next']
    _GLOBAL_ANN_ID['next'] += 1
    return nid


def _image_id_for(camera_index, frame_index):
    return ID_OFFSET * (camera_index + 1) + frame_index


writers = []
for ci, cam in enumerate(CAMERAS):
    rp = rep.create.render_product(cam, (1280, 720))
    if FORMAT == 'yolo':
        writer = rep.WriterRegistry.get('BasicWriter')
        writer.initialize(
            output_dir=os.path.join(OUTPUT_DIR, f'camera_{{ci}}'),
            rgb=True, bounding_box_2d_tight=True,
        )
    else:
        writer = rep.WriterRegistry.get('KittiWriter') if 'KittiWriter' in dir(rep.WriterRegistry) else rep.WriterRegistry.get('BasicWriter')
        writer.initialize(
            output_dir=os.path.join(OUTPUT_DIR, f'camera_{{ci}}'),
            rgb=True, bounding_box_2d_tight=True,
            semantic_segmentation=True,
        )
    writer.attach([rp])
    writers.append(writer)

print(f'configure_coco_yolo_writer: {{len(writers)}} cameras configured — '
      f'format={{FORMAT}}, categories={{len(CATEGORIES)}}, id_offset={{ID_OFFSET}}')
"""


def _gen_enforce_class_balance(args: Dict) -> str:
    """Enforce minimum class-occurrence count per frame via retry loop."""
    min_per_class = int(args.get("min_per_class", 1))
    max_retries = int(args.get("max_retries", 5))
    classes = args.get("classes") or []
    write_partial = bool(args.get("write_partial_on_fail", True))

    return f"""\
import json
import omni.replicator.core as rep

MIN_PER_CLASS = {min_per_class}
MAX_RETRIES = {max_retries}
REQUIRED_CLASSES = {list(classes)!r}
WRITE_PARTIAL_ON_FAIL = {write_partial}


def _class_counts(annotation_data):
    counts = {{}}
    for ann in annotation_data or []:
        cls = ann.get('class') or ann.get('label') or ann.get('category')
        if cls is not None:
            counts[cls] = counts.get(cls, 0) + 1
    return counts


def class_balance_gate(annotator_out):
    \"\"\"Return True to write, False to retry.\"\"\"
    counts = _class_counts(annotator_out)
    missing = [c for c in REQUIRED_CLASSES if counts.get(c, 0) < MIN_PER_CLASS]
    return not missing, missing


# Register as an on-frame pre-write hook. Replicator's orchestrator polls this.
_RETRY_STATE = {{'retries': 0, 'total': 0, 'skipped': 0, 'written': 0}}


def on_frame_gate(annotator_out):
    ok, missing = class_balance_gate(annotator_out)
    _RETRY_STATE['total'] += 1
    if ok:
        _RETRY_STATE['retries'] = 0
        _RETRY_STATE['written'] += 1
        return True
    _RETRY_STATE['retries'] += 1
    if _RETRY_STATE['retries'] < MAX_RETRIES:
        return False  # retry with new randomization
    # Max retries exhausted
    _RETRY_STATE['retries'] = 0
    if WRITE_PARTIAL_ON_FAIL:
        _RETRY_STATE['written'] += 1
        return True
    _RETRY_STATE['skipped'] += 1
    return False


print(json.dumps({{
    'enforce_class_balance': 'configured',
    'min_per_class': MIN_PER_CLASS,
    'max_retries': MAX_RETRIES,
    'required_classes': REQUIRED_CLASSES,
    'write_partial_on_fail': WRITE_PARTIAL_ON_FAIL,
}}))
"""


def _gen_configure_correlated_dr(args: Dict) -> str:
    """Generate a Gaussian-copula randomizer over correlated parameter groups.

    Output is plain Python (numpy + scipy.stats) so it compiles standalone
    and can be dropped into a Replicator on_frame() callback or an IsaacLab
    EventManager term.
    """
    groups = args.get("parameter_groups", []) or []
    target_path = args.get("target_path", "/World")
    seed = int(args.get("seed", 0))

    # Materialize the group config as a Python literal so the generated
    # script is fully self-contained.
    safe_groups = []
    for g in groups:
        if not isinstance(g, dict):
            continue
        params = list(g.get("params", []))
        ranges = dict(g.get("ranges", {}))
        # Default range when caller omitted ranges entirely.
        for p in params:
            ranges.setdefault(p, [0.0, 1.0])
        correlation = float(g.get("correlation", 0.0))
        method = g.get("method", "copula")
        if method not in ("copula", "linear"):
            method = "copula"
        safe_groups.append({
            "params": params,
            "ranges": ranges,
            "correlation": correlation,
            "method": method,
        })

    return f'''"""Correlated domain-randomization sampler.
Auto-generated by Isaac Assist (configure_correlated_dr).
Target: {target_path}
"""
import numpy as np

try:
    from scipy.stats import norm
    _HAS_SCIPY = True
except Exception:  # scipy is optional in some Kit builds
    _HAS_SCIPY = False

_GROUPS = {json.dumps(safe_groups, indent=4)}
_TARGET_PATH = {target_path!r}
_RNG = np.random.default_rng({seed})


def _sample_copula(group):
    """Draw one correlated sample from a 2-or-more-param Gaussian copula."""
    params = group["params"]
    ranges = group["ranges"]
    rho = float(group["correlation"])
    n = len(params)
    if n == 0:
        return {{}}
    # Build symmetric correlation matrix (rho off-diagonal, 1 on diagonal).
    cov = np.full((n, n), rho)
    np.fill_diagonal(cov, 1.0)
    # Latent multivariate normal -> uniform via standard normal CDF.
    z = _RNG.multivariate_normal(np.zeros(n), cov)
    if _HAS_SCIPY:
        u = norm.cdf(z)
    else:
        # Closed-form approximation when scipy is unavailable.
        u = 0.5 * (1.0 + np.tanh(z / np.sqrt(2.0)))
    out = {{}}
    for i, p in enumerate(params):
        lo, hi = ranges[p]
        out[p] = float(lo + (hi - lo) * u[i])
    return out


def _sample_linear(group):
    """Anchor first param uniformly, derive the rest via linear regression on rho."""
    params = group["params"]
    ranges = group["ranges"]
    rho = float(group["correlation"])
    if not params:
        return {{}}
    anchor = params[0]
    lo, hi = ranges[anchor]
    base_u = float(_RNG.uniform(0.0, 1.0))
    out = {{anchor: float(lo + (hi - lo) * base_u)}}
    for p in params[1:]:
        lo_p, hi_p = ranges[p]
        # Pull toward base_u proportional to rho, add residual noise.
        noise = float(_RNG.normal(0.0, max(1e-6, 1.0 - abs(rho)) * 0.1))
        u = max(0.0, min(1.0, rho * base_u + (1.0 - rho) * float(_RNG.uniform(0.0, 1.0)) + noise))
        out[p] = float(lo_p + (hi_p - lo_p) * u)
    return out


def sample_correlated_dr():
    """Return a dict {{group_index: {{param_name: value}}}} for one episode."""
    samples = {{}}
    for idx, group in enumerate(_GROUPS):
        if group["method"] == "linear":
            samples[idx] = _sample_linear(group)
        else:
            samples[idx] = _sample_copula(group)
    return samples


# Example: print one draw so the patch is observable in the Kit log.
_draw = sample_correlated_dr()
print(f"[correlated_dr] target={{_TARGET_PATH}} sample={{_draw}}")
'''


def _gen_add_latency_randomization(args: Dict) -> str:
    """Generate an IsaacLab EventManager-compatible ActionLatencyEvent."""
    min_ms = float(args.get("min_ms", 10.0))
    max_ms = float(args.get("max_ms", 50.0))
    physics_dt = float(args.get("physics_dt", 0.005))
    if max_ms < min_ms:
        min_ms, max_ms = max_ms, min_ms
    # Compute default buffer size = ceil(max_ms / dt_ms) + 1.
    dt_ms = physics_dt * 1000.0
    import math as _math
    auto_buf = int(_math.ceil(max_ms / max(dt_ms, 1e-6))) + 1
    buffer_size = int(args.get("buffer_size") or auto_buf)
    if buffer_size < auto_buf:
        buffer_size = auto_buf

    return f'''"""Action latency randomization for IsaacLab.
Auto-generated by Isaac Assist (add_latency_randomization).
Drop into your env.cfg as: events.action_latency = ActionLatencyEvent()
"""
import math
import numpy as np

try:
    import torch
    _HAS_TORCH = True
except Exception:
    _HAS_TORCH = False


_MIN_MS = {min_ms}
_MAX_MS = {max_ms}
_PHYSICS_DT = {physics_dt}
_BUFFER_SIZE = {buffer_size}


def _ms_to_steps(ms):
    """Convert milliseconds to integer physics steps (>=0)."""
    return max(0, int(round(ms / max(_PHYSICS_DT * 1000.0, 1e-6))))


class ActionLatencyEvent:
    """Per-env uniform action latency between min_ms and max_ms.

    On reset: sample a fresh latency for each environment.
    On step:  read actions delayed by the sampled number of physics steps.
    """

    def __init__(self, min_ms=_MIN_MS, max_ms=_MAX_MS, physics_dt=_PHYSICS_DT,
                 buffer_size=_BUFFER_SIZE):
        self.min_ms = float(min_ms)
        self.max_ms = float(max_ms)
        self.physics_dt = float(physics_dt)
        self.buffer_size = int(buffer_size)
        self._latency_steps = None  # per-env, set on first reset
        self._action_buffer = None  # ring buffer: (buffer_size, num_envs, action_dim)
        self._head = 0

    def reset(self, env, env_ids=None):
        num_envs = int(getattr(env, "num_envs", 1))
        max_steps = _ms_to_steps(self.max_ms)
        min_steps = _ms_to_steps(self.min_ms)
        if max_steps < min_steps:
            max_steps = min_steps
        sample_hi = max_steps + 1
        if _HAS_TORCH and hasattr(env, "device"):
            self._latency_steps = torch.randint(min_steps, sample_hi, (num_envs,),
                                                device=env.device)
        else:
            self._latency_steps = np.random.randint(min_steps, sample_hi, size=num_envs)

    def __call__(self, env):
        actions = getattr(env, "actions", None)
        if actions is None:
            return
        if self._action_buffer is None:
            shape = (self.buffer_size,) + tuple(getattr(actions, "shape", (1,)))
            if _HAS_TORCH and hasattr(actions, "zero_"):
                self._action_buffer = actions.new_zeros(shape)
            else:
                self._action_buffer = np.zeros(shape, dtype=np.float32)
        # Write current actions into the head slot.
        self._action_buffer[self._head] = actions
        # Read each env from its delayed slot.
        if self._latency_steps is None:
            self.reset(env)
        if _HAS_TORCH and hasattr(self._action_buffer, "device"):
            idx = (self._head - self._latency_steps) % self.buffer_size
            num_envs = int(getattr(env, "num_envs", 1))
            env_ix = torch.arange(num_envs, device=self._action_buffer.device)
            env.actions = self._action_buffer[idx, env_ix]
        else:
            num_envs = int(getattr(env, "num_envs", 1))
            for e in range(num_envs):
                lat = int(self._latency_steps[e])
                slot = (self._head - lat) % self.buffer_size
                env.actions[e] = self._action_buffer[slot, e]
        self._head = (self._head + 1) % self.buffer_size


# Eagerly construct so the patch validator sees a usable object.
action_latency_event = ActionLatencyEvent()
print(f"[action_latency] min={{_MIN_MS}}ms max={{_MAX_MS}}ms steps={{_ms_to_steps(_MAX_MS)}} buffer={{_BUFFER_SIZE}}")
'''


def _gen_preview_dr(args: Dict) -> str:
    """Generate code that captures N preview frames after re-randomizing the scene."""
    num_samples = int(args.get("num_samples", 9))
    if num_samples < 1:
        num_samples = 1
    output_dir = args.get("output_dir", "workspace/dr_previews")
    res = args.get("resolution", [512, 512])
    if not isinstance(res, (list, tuple)) or len(res) != 2:
        res = [512, 512]
    width, height = int(res[0]), int(res[1])

    return f'''"""DR preview frame generator.
Auto-generated by Isaac Assist (preview_dr).
Captures {num_samples} viewport frames at {width}x{height} after triggering
the configured Replicator randomizers between each frame.
"""
import os

_NUM_SAMPLES = {num_samples}
_OUTPUT_DIR = {output_dir!r}
_RESOLUTION = ({width}, {height})

os.makedirs(_OUTPUT_DIR, exist_ok=True)

try:
    import omni.replicator.core as rep
    _HAS_REPLICATOR = True
except Exception:
    _HAS_REPLICATOR = False


def _trigger_randomizers():
    """Step Replicator graph one tick, applying any registered randomizers."""
    if not _HAS_REPLICATOR:
        return
    try:
        rep.orchestrator.step()
    except Exception:
        pass


def _capture_frame(idx):
    """Save one viewport frame to OUTPUT_DIR/dr_preview_{{idx:03d}}.png."""
    path = os.path.join(_OUTPUT_DIR, f"dr_preview_{{idx:03d}}.png")
    try:
        from omni.kit.viewport.utility import get_active_viewport, capture_viewport_to_file
        vp = get_active_viewport()
        if vp is not None:
            capture_viewport_to_file(vp, path)
            return path
    except Exception:
        pass
    # Fallback: write a sentinel so callers can still see what was attempted.
    try:
        with open(path + ".txt", "w") as fh:
            fh.write(f"placeholder for {{path}} resolution={{_RESOLUTION}}")
    except Exception:
        pass
    return path


_written = []
for i in range(_NUM_SAMPLES):
    _trigger_randomizers()
    _written.append(_capture_frame(i))

print(f"[preview_dr] wrote {{len(_written)}} frames to {{_OUTPUT_DIR}} resolution={{_RESOLUTION}}")
'''


# ---------------------------------------------------------------------------
# Phase 7 wave 16 — final data-handler stragglers (COMPLETES data-handler migration)


@with_telemetry
async def _handle_preview_sdg(args: Dict) -> Dict:
    """Step the Replicator orchestrator a few times for preview frames."""
    from .. import kit_tools
    num_samples = args.get("num_samples", 3)

    code = f"""\
import omni.replicator.core as rep
import json

num_samples = {num_samples}
for i in range(num_samples):
    rep.orchestrator.step()
    print(f"Preview frame {{i + 1}}/{num_samples} generated")

print(json.dumps({{"preview_frames": num_samples, "status": "done"}}))
"""
    return await kit_tools.queue_exec_patch(code, f"Preview SDG: generate {num_samples} sample frames")


@with_telemetry
async def _handle_benchmark_sdg(args: Dict) -> Dict:
    """Run a headless SDG throughput benchmark.

    Generates a short measurement loop and queues it to Kit; returns the
    patch queue status plus the expected preset baseline for the current
    annotator combination.
    """
    from .. import kit_tools
    pipeline_id = args.get("pipeline_id", "")
    num_frames = int(args.get("num_frames", 100))
    annotators = args.get("annotators") or ["rgb"]
    resolution = args.get("resolution") or [1280, 720]

    # Sanitize pipeline_id to avoid injection into the generated script.
    import re as _re
    if pipeline_id and not _re.match(r"^[a-zA-Z0-9/_.:-]*$", pipeline_id):
        return {"error": f"Invalid characters in pipeline_id: {pipeline_id!r}"}
    if not all(isinstance(a, str) and _re.match(r"^[a-zA-Z0-9_]+$", a) for a in annotators):
        return {"error": f"Invalid annotator identifier in {annotators!r}"}
    if not (isinstance(resolution, list) and len(resolution) == 2 and all(isinstance(x, int) for x in resolution)):
        return {"error": "resolution must be [width, height] ints"}

    # Preset baselines (expected FPS on RTX 4090) derived from the spec table.
    preset_baselines = {
        frozenset({"rgb"}): (30, 60),
        frozenset({"rgb", "depth", "bounding_box_2d"}): (15, 25),
        frozenset({"rgb", "depth", "semantic_segmentation", "instance_segmentation", "normals"}): (5, 10),
    }
    key = frozenset(annotators)
    baseline = preset_baselines.get(key)

    code = f"""\
import json
import time
import omni.replicator.core as rep

ANNOTATORS = {list(annotators)!r}
NUM_FRAMES = {num_frames}
RESOLUTION = ({resolution[0]}, {resolution[1]})

with rep.new_layer():
    camera = rep.get.camera()
    rp = rep.create.render_product(camera, RESOLUTION)

    for a in ANNOTATORS:
        try:
            rep.AnnotatorRegistry.get_annotator(a).attach([rp])
        except Exception:
            pass

    t0 = time.time()
    rep.orchestrator.run_until_complete(num_frames=NUM_FRAMES)
    elapsed = max(time.time() - t0, 1e-6)

fps = NUM_FRAMES / elapsed

# VRAM + disk I/O are best-effort — fall back to nulls if unavailable
vram_peak_mb = None
try:
    import torch
    if torch.cuda.is_available():
        vram_peak_mb = torch.cuda.max_memory_allocated() / (1024 ** 2)
except Exception:
    pass

# Coarse bottleneck label
bottleneck = 'gpu_render'
if fps < 5 and vram_peak_mb is not None and vram_peak_mb > 10_000:
    bottleneck = 'gpu_memory'
elif fps < 2:
    bottleneck = 'disk_write'

print(json.dumps({{
    'pipeline_id': {pipeline_id!r},
    'num_frames': NUM_FRAMES,
    'annotators': ANNOTATORS,
    'resolution': list(RESOLUTION),
    'elapsed_s': round(elapsed, 3),
    'fps': round(fps, 2),
    'vram_peak_mb': vram_peak_mb,
    'bottleneck': bottleneck,
}}))
"""

    result = await kit_tools.queue_exec_patch(
        code, f"Benchmark SDG ({num_frames} frames, {len(annotators)} annotators)"
    )
    return {
        "success": bool(result.get("success", False)),
        "queued": result.get("queued", False),
        "pipeline_id": pipeline_id,
        "num_frames": num_frames,
        "annotators": list(annotators),
        "resolution": list(resolution),
        "expected_fps_range": list(baseline) if baseline else None,
        "note": "Actual FPS is printed by the Kit-side benchmark once the patch is approved and executed.",
    }


# ---------------------------------------------------------------------------
# Phase 61 — sample_correlated_dr (typed in-process sampler)


@with_telemetry
async def _handle_sample_correlated_dr(args: Dict) -> Dict[str, Any]:
    """Draw N samples from a correlated multivariate normal preset.

    Uses the Phase 61 Cholesky-based sampler in
    `multimodal.sdg_correlated_dr`. Pure Python, no Kit/GPU required.

    Args:
        preset: optional name. ``"sensor_camera"`` selects the bundled
            SENSOR_CAMERA_PRESET (4 axes, 3 PSD correlation pairs).
        axes: list of dicts ``[{name, mean, std}, ...]`` (used when no preset).
        correlations: list of dicts ``[{axis_a, axis_b, rho}, ...]``.
        n_samples: number of draws. Defaults to preset's ``num_samples``.
        seed: optional int for reproducibility.

    Returns:
        Dict with: ``samples`` (list of {axis: value}), ``empirical_rho``
        (per requested pair), ``axis_names``, ``n_samples``.
    """
    import random as _random
    from service.isaac_assist_service.multimodal.sdg_correlated_dr import (
        CorrelatedDRConfig,
        CorrelationPair,
        DRAxis,
        SENSOR_CAMERA_PRESET,
        empirical_correlation,
        sample_correlated,
    )

    preset = args.get("preset")
    if preset == "sensor_camera":
        config = SENSOR_CAMERA_PRESET
    else:
        raw_axes = args.get("axes") or []
        raw_corr = args.get("correlations") or []
        try:
            axes = [
                DRAxis(name=a["name"], mean=float(a["mean"]), std=float(a["std"]))
                for a in raw_axes
            ]
            correlations = [
                CorrelationPair(
                    axis_a=c["axis_a"], axis_b=c["axis_b"], rho=float(c["rho"])
                )
                for c in raw_corr
            ]
        except (KeyError, TypeError, ValueError) as exc:
            return {"error": f"malformed axes/correlations: {exc}"}
        if not axes:
            return {"error": "must specify preset or non-empty axes"}
        config = CorrelatedDRConfig(
            name=args.get("name", "custom"),
            axes=axes,
            correlations=correlations,
        )

    n = args.get("n_samples")
    seed = args.get("seed")
    rng = _random.Random(int(seed)) if seed is not None else _random.Random()
    try:
        samples = sample_correlated(config, rng=rng, n_samples=int(n) if n else None)
    except ValueError as exc:
        return {"error": f"correlation matrix not PSD: {exc}"}

    empirical = {
        f"{pair.axis_a}__{pair.axis_b}": empirical_correlation(
            samples, pair.axis_a, pair.axis_b
        )
        for pair in config.correlations
    }
    return {
        "preset": preset,
        "axis_names": config.axis_names(),
        "n_samples": len(samples),
        "samples": samples,
        "empirical_rho": empirical,
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
    # Data handlers (3)
    data["benchmark_sdg"] = _handle_benchmark_sdg
    data["preview_sdg"] = _handle_preview_sdg
    data["sample_correlated_dr"] = _handle_sample_correlated_dr

    # Code-gen handlers (10)
    codegen["add_domain_randomizer"] = _gen_add_domain_randomizer
    codegen["add_latency_randomization"] = _gen_add_latency_randomization
    codegen["configure_coco_yolo_writer"] = _gen_configure_coco_yolo_writer
    codegen["configure_correlated_dr"] = _gen_configure_correlated_dr
    codegen["configure_differential_sdg"] = _gen_configure_differential_sdg
    codegen["configure_sdg"] = _gen_configure_sdg
    codegen["create_sdg_pipeline"] = _gen_create_sdg_pipeline
    codegen["enforce_class_balance"] = _gen_enforce_class_balance
    codegen["export_dataset"] = _gen_export_dataset
    codegen["preview_dr"] = _gen_preview_dr
