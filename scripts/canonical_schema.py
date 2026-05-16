"""
canonical_schema.py — Canonical template schema definition for workspace/templates/*.json

Two tiers:
  T1 (CP-*): scene-build canonicals — require verify_args, simulate_args, diagnose_args, verified_status
  T2 (non-CP): dialogue canonicals — core-6 only

Used by lint_canonical_templates.py and importable for testing.
"""

# ── Core-6: mandatory for ALL templates ──────────────────────────────────────

CORE_FIELDS = ["task_id", "goal", "tools_used", "thoughts", "code", "failure_modes"]

# ── T1-mandatory: required for every CP-* template ──────────────────────────

T1_FIELDS = ["verify_args", "simulate_args", "diagnose_args", "verified_status"]

# ── T1 recommended: absence is WARN (not ERROR) ──────────────────────────────

T1_RECOMMENDED = ["settle_state"]

# ── Deprecated fields: presence is ERROR for any template ────────────────────
# Source: Q4 §2 — confirmed one-off fields that are not read by any production code.
# `blocked` is deliberately excluded: it is a legitimate infra-pause marker on CP-06.

DEPRECATED_FIELDS = {
    "benchmark_vs_alternatives",       # CP-01 only; belongs in docs
    "verified_date",                   # CP-01/02 only; superseded by verified_status string
    "verified_metrics",                # CP-01/02 only; superseded by verified_status string
    "delivery",                        # CP-06/07 only; experiment field
    "cube_path",                       # CP-06/07 only; duplicates simulate_args.cube_path
    "extends_notes",                   # CP-NEW-multi-amr-corridor; typo of extension_notes
}

# Deprecated fields whose key matches a prefix (for compute_stack_placement_*)
DEPRECATED_FIELD_PREFIXES = (
    "compute_stack_placement_verified_",
)

# ── Role-based fields: absence is INFO (migration not yet complete) ──────────

ROLE_FIELDS = ["intent", "roles", "role_defaults", "code_template"]

# ── Motion-controller compatibility tag ──────────────────────────────────────
# Records which motion controllers (planners/control modes) this canonical has
# been verified-runnable, observed-failed, or untested with. Versioning via
# `name@version` suffix is allowed (e.g. "curobo@1.8.2"). Honesty rule: a
# controller listed under `verified` means an actual successful run exists;
# absent means untested; under `failed` means we have a reproducible failure.

VALID_MOTION_CONTROLLER_NAMES = {
    "curobo",           # cuRobo CUDA motion planner
    "rmpflow",          # Lula RMPflow (Cortex)
    "moveit2",          # MoveIt2 (ROS2)
    "isaac_ros_cumotion",  # NVIDIA Isaac ROS cuMotion wrapper
    "admittance",       # ros2_control admittance controller (compliance)
    "impedance",        # ros2_control impedance controller (compliance)
    "ros2_control",     # generic ros2_control trajectory controller
    "isaac_lcm",        # Isaac LULA controller manager
    "pinocchio",        # Pinocchio dynamics-based control
    "direct_joint",     # direct articulation joint targets (no planner)
    "cortex",           # Cortex behavior runtime
}

# Tools that imply this canonical performs motion planning / control — when
# any of these appear in tools_used, the canonical SHOULD declare its
# motion_controllers compatibility (WARN if absent).
MOTION_PLANNING_TOOL_PREFIXES = (
    "plan_trajectory",
    "move_to_pose",
    "solve_ik",
    "interpolate_trajectory",
    "follow_trajectory",
    "setup_admittance_controller",
    "setup_impedance_controller",
    "set_compliance_params",
    "release_compliance",
    "setup_isaac_ros_cumotion",
    "setup_cortex_behavior",
    "setup_pick_place_controller",
    "set_motion_policy",
)


def template_uses_motion_planning(data: dict) -> bool:
    """Return True if the template's tools_used contains any motion-planning tool."""
    tools = data.get("tools_used") or []
    if not isinstance(tools, list):
        return False
    for t in tools:
        if not isinstance(t, str):
            continue
        for prefix in MOTION_PLANNING_TOOL_PREFIXES:
            if t.startswith(prefix):
                return True
    return False


def parse_motion_controller_name(value: str) -> tuple[str, str | None]:
    """Split 'curobo@1.8.2' into ('curobo', '1.8.2'). Returns (name, None) if no version."""
    if "@" in value:
        name, version = value.split("@", 1)
        return name, version
    return value, None

# Role-based fields that must co-occur with the core role fields when present.
# All three of roles/role_defaults/code_template must appear together.
ROLE_CORE_TRIO = ["roles", "role_defaults", "code_template"]

# ── Value validators ─────────────────────────────────────────────────────────

# intent.pattern_hint valid values (from types.py PatternHint enum)
#
# Round 12 additions (2026-05-16):
#   "insert"    — force/compliance-based peg-in-hole or assembly-constraint insertion;
#                 success criterion: peg seated in hole (force threshold / constraint active)
#   "train"     — RL training scaffold, SDG pipeline, or sim-to-real measurement;
#                 success criterion: training loop running / dataset exported / gap measured
#   "other"     — catch-all for long-tail novel patterns (≤2 templates per cluster);
#                 structural_tags provide the discriminating signal at retrieval time
#
# When a future round accumulates ≥3 "other" templates sharing a clear success-criterion
# shape, promote them to a named enum value (minor version bump, extractor update required).
VALID_PATTERN_HINTS = {
    "pick_place",  # success: workpiece in destination, at rest
    "sort",        # success: workpiece in correct-class destination
    "reorient",    # success: workpiece in destination AND oriented
    "navigate",    # success: mobile platform at goal pose
    "insert",      # success: peg/part seated in hole (force-threshold or assembly-constraint)
    "train",       # success: training loop running / dataset exported / gap metric produced
    "other",       # long-tail; structural_tags discriminate at retrieval time
}

# intent.structural_features.destination_kind valid values
VALID_DESTINATION_KINDS = {"single_bin", "n_bins_routed", "shelf", "fixture"}

# intent.structural_features.routing_axis valid values
# Added 2026-05-15: semantic_class for inspect-and-reject patterns where
# routing decision is based on a classifier output (good vs defective,
# defect-type categories, etc.) rather than a raw geometric property.
VALID_ROUTING_AXES = {"color", "size", "shape", "label", "semantic_class"}

# verified_status is free-text (not enum-constrained) — the values are too diverse.
# We do not validate its content, only its presence (for T1).

# structural_tags must match this pattern: "namespace:segment.subsegment"
import re
from pathlib import Path
STRUCTURAL_TAG_PATTERN = re.compile(r"^(isaac|cad|user):[a-z0-9_]+(\.[a-z0-9_]+)*$")

# ── verify_args subkey requirements ─────────────────────────────────────────

# Each stage in verify_args.stages must have these keys.
VERIFY_ARGS_STAGE_KEYS = ["robot_path", "pick_path", "place_path"]

# ── simulate_args subkey requirements ───────────────────────────────────────

# Some templates use `cube_paths` (list, multi-cube) and some use `cube_path` (string, single).
# Both are valid — at least one of the two must be present.
SIMULATE_ARGS_REQUIRED_KEYS = ["target_path", "duration_s"]
SIMULATE_ARGS_CUBE_KEY_VARIANTS = ["cube_path", "cube_paths"]  # at least one must be present


def is_cp_template(task_id: str) -> bool:
    """Return True if task_id indicates a T1 (CP-*) template."""
    return str(task_id).startswith("CP-")


def is_deprecated_field(field_name: str) -> bool:
    """Return True if field_name is a deprecated one-off field."""
    if field_name in DEPRECATED_FIELDS:
        return True
    for prefix in DEPRECATED_FIELD_PREFIXES:
        if field_name.startswith(prefix):
            return True
    return False


# ── Tool-call validation helpers ─────────────────────────────────────────────
# Used by lint_canonical_templates.py --validate-tool-calls pass.
# Kept here so tests can import discovery logic independently.

def _camel_to_snake(name: str) -> str:
    """Convert 'SetDriveGainsArgs' → 'set_drive_gains_args'."""
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def build_tool_model_map() -> "dict[str, type]":
    """
    Return a mapping of tool_name (snake_case) → Pydantic model class.

    Discovery strategy:
      (a) Dynamic import of service.isaac_assist_service.chat.tools.handlers._models
          via sys.path manipulation — zero subprocess overhead.
      (b) On ImportError, return an empty dict so the caller can emit a single
          WARN and skip tool-call validation rather than crashing lint.

    Naming convention: ``SetDriveGainsArgs`` → ``set_drive_gains``
    (strip trailing ``Args``, convert CamelCase → snake_case).
    """
    import importlib
    import inspect
    import sys as _sys

    # Locate repo root relative to this file (scripts/canonical_schema.py)
    _scripts_dir = Path(__file__).parent
    _repo_root = _scripts_dir.parent
    _service_root = str(_repo_root / "service")

    if _service_root not in _sys.path:
        _sys.path.insert(0, _service_root)

    try:
        mod = importlib.import_module(
            "isaac_assist_service.chat.tools.handlers._models"
        )
    except ImportError:
        return {}

    tool_map: dict[str, type] = {}
    for attr_name, cls in inspect.getmembers(mod, inspect.isclass):
        if not attr_name.endswith("Args"):
            continue
        if not hasattr(cls, "model_fields"):
            continue
        # Strip trailing "Args" then convert to snake_case
        base = attr_name[: -len("Args")]
        tool_name = _camel_to_snake(base)
        tool_map[tool_name] = cls
    return tool_map


# Lazily cached at module level; populated on first call to build_tool_model_map()
_TOOL_MODEL_MAP: "dict[str, type] | None" = None


def get_tool_model_map() -> "dict[str, type]":
    """Return (and cache) the tool-name → Pydantic model map."""
    global _TOOL_MODEL_MAP
    if _TOOL_MODEL_MAP is None:
        _TOOL_MODEL_MAP = build_tool_model_map()
    return _TOOL_MODEL_MAP


# ── JSONSchema tool map ───────────────────────────────────────────────────────
# Provides per-parameter enum lists, nested-object property names, and
# nested-array per-item required fields from tool_schemas.py ISAAC_SIM_TOOLS.

def build_tool_jsonschema_map() -> "dict[str, dict]":
    """
    Return a mapping of tool_name → {param_name: param_schema_dict}.

    Loaded from ISAAC_SIM_TOOLS in tool_schemas.py.  Each value is the
    JSON Schema ``properties`` dict for that tool's parameters object.
    Returns an empty dict if the module cannot be imported.
    """
    import importlib
    import sys as _sys

    _scripts_dir = Path(__file__).parent
    _repo_root = _scripts_dir.parent
    _service_root = str(_repo_root / "service")

    if _service_root not in _sys.path:
        _sys.path.insert(0, _service_root)

    try:
        mod = importlib.import_module(
            "isaac_assist_service.chat.tools.tool_schemas"
        )
    except ImportError:
        return {}

    tool_map: dict[str, dict] = {}
    for entry in getattr(mod, "ISAAC_SIM_TOOLS", []):
        fn = entry.get("function", {})
        name = fn.get("name")
        params = fn.get("parameters", {})
        props = params.get("properties", {})
        if name and props:
            tool_map[name] = props
    return tool_map


_TOOL_JSONSCHEMA_MAP: "dict[str, dict] | None" = None


def get_tool_jsonschema_map() -> "dict[str, dict]":
    """Return (and cache) the tool-name → JSONSchema properties map."""
    global _TOOL_JSONSCHEMA_MAP
    if _TOOL_JSONSCHEMA_MAP is None:
        _TOOL_JSONSCHEMA_MAP = build_tool_jsonschema_map()
    return _TOOL_JSONSCHEMA_MAP
