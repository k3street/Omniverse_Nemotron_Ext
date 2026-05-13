"""Resolve handlers — typed-variable resolvers for the agent layer:
count vagueness, robot class, material properties, constraint phrases,
sequence phrases, context references, coordinate references, relational
properties, success conditions, skill composition, size adjectives,
prim references.

Phase 7 wave 1 — first DATA-handler migration. Same pattern as the 25
prior codegen waves: async function bodies move, tool_executor.py
re-imports the names so the existing DATA_HANDLERS dispatch dict keeps
working.

Per specs/IA_FULL_SPEC_2026-05-10.md Phases 2 + 7.
"""
from __future__ import annotations

import re as _re
from typing import Any, Awaitable, Callable, Dict

# ---------------------------------------------------------------------------
# Phase 14 + 16 (2026-05-13): migrated from tool_executor.py.

_ROBOT_REACH_M = {
    "franka_panda": 0.855,  # Franka Panda — 855mm reach
    "ur5e":         0.850,
    "ur10":         1.300,
    "ur10e":        1.300,
    "kinova":       0.902,
    "h1":           0.580,  # H1 humanoid arm reach (one arm)
    "g1":           0.450,
    "default":      0.800,
}

_COORD_LANDMARKS = {
    # Named anchor points — return position relative to a reference prim
    # (or world origin when no reference). Ordered most-specific first.
    "origin": "world",
    "world origin": "world",
    "center of stage": "world",
    "stage center": "world",
}

_RELATIONAL_PATTERN_RE = __import__("re").compile(
    r"(?P<factor>\d+(?:\.\d+)?)\s*[xX×]?\s*(?P<rel>times|x|×|the size of|larger than|smaller than|bigger than)?",
    __import__("re").IGNORECASE,
)

from ._shared import _ROBOT_WIZARD_REGISTRY, _resolve_robot_asset

# ---------------------------------------------------------------------------
# Theme-local constants (Phase 8 wave 8, 2026-05-13)
# Migrated from tool_executor.py — used only by handlers.resolve.

_COUNT_BUCKETS = {
    "one": 1, "single": 1, "a": 1, "an": 1, "ett": 1, "en": 1,
    "two": 2, "pair": 2, "couple": 2, "two of them": 2, "ett par": 2, "två": 2,
    "few": 3, "a few": 3, "några": 3, "några få": 3,
    "several": 5, "some": 4, "handful": 5, "flera": 5,
    "many": 10, "lots": 10, "lots of": 10, "a lot": 10, "många": 10, "en massa": 10,
    "dozens": 24, "dozen": 12, "twenty": 20, "tjugo": 20, "ett dussin": 12, "dussintals": 24,
    "hundreds": 100, "a hundred": 100, "hundra": 100, "hundratals": 100,
}

_ROBOT_CLASS_DEFAULTS = {
    # Manipulator arms
    "manipulator": "franka_panda",
    "arm": "franka_panda",
    "robotic arm": "franka_panda",
    "robotarm": "franka_panda",
    # Wheeled / mobile bases
    "wheeled": "nova_carter",
    "wheeled robot": "nova_carter",
    "mobile": "nova_carter",
    "mobile robot": "nova_carter",
    "amr": "nova_carter",
    "agv": "nova_carter",
    "carter": "nova_carter",
    "nova carter": "nova_carter",
    # Humanoids
    "humanoid": "h1",
    "biped": "h1",
    "human-shaped": "h1",
    "h1": "h1",
    "g1": "g1",
    "unitree humanoid": "h1",
    # Quadrupeds
    "quadruped": "anymal_c",
    "dog": "spot",
    "spot": "spot",
    "anymal": "anymal_c",
    # Hands / grippers
    "hand": "allegro",
    "gripper": "allegro",
    "allegro": "allegro",
}

_MATERIAL_PROPERTIES = {
    # term: {density (kg/m^3), static_friction, dynamic_friction, restitution, body_type}
    "metal":     {"density": 7800, "static_friction": 0.6, "dynamic_friction": 0.4, "restitution": 0.3, "body_type": "rigid"},
    "steel":     {"density": 7850, "static_friction": 0.6, "dynamic_friction": 0.4, "restitution": 0.3, "body_type": "rigid"},
    "aluminum":  {"density": 2700, "static_friction": 0.5, "dynamic_friction": 0.3, "restitution": 0.3, "body_type": "rigid"},
    "wood":      {"density": 700,  "static_friction": 0.5, "dynamic_friction": 0.4, "restitution": 0.4, "body_type": "rigid"},
    "plastic":   {"density": 950,  "static_friction": 0.4, "dynamic_friction": 0.3, "restitution": 0.5, "body_type": "rigid"},
    "rubber":    {"density": 1200, "static_friction": 0.9, "dynamic_friction": 0.8, "restitution": 0.8, "body_type": "rigid"},
    "glass":     {"density": 2500, "static_friction": 0.4, "dynamic_friction": 0.3, "restitution": 0.2, "body_type": "rigid"},
    "concrete":  {"density": 2400, "static_friction": 0.7, "dynamic_friction": 0.6, "restitution": 0.2, "body_type": "rigid"},
    "rigid":     {"density": 1000, "static_friction": 0.5, "dynamic_friction": 0.4, "restitution": 0.3, "body_type": "rigid"},
    "soft":      {"density": 100,  "static_friction": 0.5, "dynamic_friction": 0.4, "restitution": 0.1, "body_type": "deformable"},
    "deformable":{"density": 100,  "static_friction": 0.5, "dynamic_friction": 0.4, "restitution": 0.1, "body_type": "deformable"},
    "fabric":    {"density": 100,  "static_friction": 0.6, "dynamic_friction": 0.5, "restitution": 0.0, "body_type": "deformable"},
    # Swedish aliases
    "metall":   "metal", "trä": "wood", "gummi": "rubber", "glas": "glass",
    "betong": "concrete", "stål": "steel", "plast": "plastic",
}

_CONSTRAINT_RE_NUMERIC = __import__("re").compile(
    r"(?P<value>-?\d+(?:\.\d+)?)\s*(?P<unit>mm|cm|m|meters?|metre|millimet(?:re|er)|centimet(?:re|er)|kg|kilo(?:gram)?|g|gram|s|sec|second|min|hr|hour|°|deg|rad)?",
    __import__("re").IGNORECASE,
)

_UNIT_TO_SI = {
    "mm": ("meters", 0.001), "millimeter": ("meters", 0.001), "millimetre": ("meters", 0.001),
    "cm": ("meters", 0.01), "centimeter": ("meters", 0.01), "centimetre": ("meters", 0.01),
    "m": ("meters", 1.0), "meter": ("meters", 1.0), "meters": ("meters", 1.0), "metre": ("meters", 1.0),
    "kg": ("kilograms", 1.0), "kilo": ("kilograms", 1.0), "kilogram": ("kilograms", 1.0),
    "g": ("kilograms", 0.001), "gram": ("kilograms", 0.001),
    "s": ("seconds", 1.0), "sec": ("seconds", 1.0), "second": ("seconds", 1.0),
    "min": ("seconds", 60.0), "hr": ("seconds", 3600.0), "hour": ("seconds", 3600.0),
    "°": ("degrees", 1.0), "deg": ("degrees", 1.0), "rad": ("radians", 1.0),
}

_SUCCESS_CONDITION_TEMPLATES = {
    # intent_kind: {fields it needs, structured form}
    "object_traversal": {
        "fields": ["object", "start_location", "end_location"],
        "verify_with": "verify_pickplace_pipeline",
        "rationale": "Object moves from start_location to end_location. Verifier checks reach + handoffs.",
    },
    "static_layout": {
        "fields": ["components"],
        "verify_with": None,  # snapshot diff is enough
        "rationale": "Specific components present at specific positions. Verifier optional — scene_summary suffices.",
    },
    "controller_setup": {
        "fields": ["robot", "controller_type"],
        "verify_with": None,
        "rationale": "A controller is configured on a robot. Verifier should test the controller responds.",
    },
    "data_pipeline": {
        "fields": ["source", "sink", "throughput"],
        "verify_with": None,
        "rationale": "Data flows from source to sink. Verifier should sample N frames and check format.",
    },
}

_SKILL_RECIPES = {
    "assembly_line": {
        "description": "Multi-station pick-place pipeline transporting an item from start bin through N stations to a final bin via robots and conveyors.",
        "tool_chain": [
            {"tool": "create_bin", "args_template": {"prim_path": "<INPUT_BIN>"}},
            {"tool": "create_conveyor", "args_template": {"prim_path": "<CONVEYOR_1>"}},
            {"tool": "robot_wizard", "args_template": {"robot_name": "<ROBOT>", "dest_path": "<ROBOT_PATH>"}},
            {"tool": "create_bin", "args_template": {"prim_path": "<OUTPUT_BIN>"}},
        ],
        "verify_step": {
            "tool": "verify_pickplace_pipeline",
            "args_template": {"stages": [{"robot_path": "<ROBOT_PATH>", "pick_path": "<PICK>", "place_path": "<PLACE>"}]},
            "rationale": "MANDATORY for multi-station builds. Confirms each robot can reach its pick AND place targets. Without this you might claim 'done' on a layout where robots can't physically perform the pipeline (caught VR-18 / VR-19).",
        },
        "success_condition": {
            "intent": "object_traversal",
            "start_state": "<ITEM> located at <INPUT_BIN>",
            "end_state": "<ITEM> located at <OUTPUT_BIN>",
        },
    },
    "pick_and_place": {
        "description": "Pick an object from one surface and place it on another using PickPlaceController.",
        "tool_chain": [
            {"tool": "setup_pick_place_controller", "args_template": {"robot_path": "<ROBOT>", "target_prim_path": "<OBJECT>", "destination": "<BIN>"}},
        ],
        "verify_step": {
            "tool": "verify_pickplace_pipeline",
            "args_template": {"stages": [{"robot_path": "<ROBOT>", "pick_path": "<OBJECT>", "place_path": "<BIN>"}]},
            "rationale": "Confirm the robot can reach both the pick and place targets before claiming the cell works.",
        },
        "success_condition": {
            "intent": "object_traversal",
            "start_state": "<OBJECT> located at <PICK_SURFACE>",
            "end_state": "<OBJECT> located at <PLACE_SURFACE>",
        },
    },
    "calibrate_camera": {
        "description": "Place a calibration board + camera and run the calibration routine.",
        "tool_chain": [
            {"tool": "create_calibration_experiment", "args_template": {"camera_path": "<CAMERA>"}},
            {"tool": "quick_calibrate", "args_template": {"camera_path": "<CAMERA>"}},
        ],
    },
    "rl_training_env": {
        "description": "Spin up an Isaac Lab env scaffold for RL training.",
        "tool_chain": [
            {"tool": "create_isaaclab_env", "args_template": {"task_type": "manipulation", "task_name": "<NAME>", "robot_path": "<ROBOT>"}},
        ],
    },
    "ros2_bridge": {
        "description": "Set up a ROS2 OmniGraph bridge for an articulation.",
        "tool_chain": [
            {"tool": "configure_ros2_bridge", "args_template": {"robot_path": "<ROBOT>"}},
        ],
    },
    "teleop_demo": {
        "description": "Set up teleop mapping + start a recording session.",
        "tool_chain": [
            {"tool": "configure_teleop_mapping", "args_template": {"robot_path": "<ROBOT>"}},
            {"tool": "start_teleop_session", "args_template": {}},
        ],
    },
    # English aliases
    "pick-and-place": "pick_and_place", "pickplace": "pick_and_place",
    "pick and place": "pick_and_place", "manipulation": "pick_and_place",
    "assembly line": "assembly_line", "assembly-line": "assembly_line",
    "production line": "assembly_line", "manufacturing line": "assembly_line",
    "multi-station": "assembly_line", "pipeline": "assembly_line",
    "calibration": "calibrate_camera", "camera calibration": "calibrate_camera",
    "rl env": "rl_training_env", "rl": "rl_training_env",
    "training env": "rl_training_env", "training environment": "rl_training_env",
    "ros2": "ros2_bridge", "ros": "ros2_bridge", "bridge": "ros2_bridge",
    "teleop": "teleop_demo", "teleoperation": "teleop_demo",
}

_SIZE_BUCKET_ALIASES = {
    "tiny": "tiny", "very small": "tiny", "minuscule": "tiny", "miniature": "tiny",
    "pyttig": "tiny", "pyttigt": "tiny", "pytteliten": "tiny",
    "small": "small", "little": "small", "compact": "small",
    "liten": "small", "litet": "small", "lilla": "small",
    "medium": "medium", "moderate": "medium", "mid-sized": "medium", "mid sized": "medium", "average": "medium",
    "mellan": "medium", "mellanstor": "medium", "lagom": "medium", "normalstor": "medium",
    "large": "large", "big": "large", "sizable": "large", "substantial": "large",
    "stor": "large", "stort": "large", "stora": "large",
    "huge": "huge", "very large": "huge", "massive": "huge", "enormous": "huge", "giant": "huge",
    "väldig": "huge", "väldigt stor": "huge", "enorm": "huge", "jätte": "huge", "jättestor": "huge",
}

_SIZE_BUCKETS = {
    "cube":     {"tiny": 0.02, "small": 0.05, "medium": 0.15, "large": 0.5,  "huge": 1.5},
    "box":      {"tiny": 0.05, "small": 0.10, "medium": 0.30, "large": 0.70, "huge": 1.5},
    "sphere":   {"tiny": 0.02, "small": 0.05, "medium": 0.15, "large": 0.5,  "huge": 1.5},
    "ball":     {"tiny": 0.02, "small": 0.05, "medium": 0.15, "large": 0.5,  "huge": 1.5},
    "cylinder": {"tiny": 0.02, "small": 0.05, "medium": 0.15, "large": 0.5,  "huge": 1.5},
    "bin":      {"tiny": 0.10, "small": 0.20, "medium": 0.30, "large": 0.6,  "huge": 1.0},
    "tray":     {"tiny": 0.10, "small": 0.20, "medium": 0.40, "large": 0.7,  "huge": 1.2},
    "table":    {"tiny": 0.50, "small": 0.80, "medium": 1.20, "large": 2.0,  "huge": 4.0},
    "desk":     {"tiny": 0.50, "small": 0.80, "medium": 1.20, "large": 2.0,  "huge": 4.0},
    "conveyor": {"tiny": 0.50, "small": 1.00, "medium": 1.60, "large": 3.0,  "huge": 5.0},
    "wall":     {"tiny": 1.00, "small": 2.00, "medium": 3.00, "large": 5.0,  "huge": 10.0},
    "room":     {"tiny": 2.00, "small": 4.00, "medium": 6.00, "large": 10.0, "huge": 20.0},
    "warehouse":{"tiny": 5.00, "small": 10.0, "medium": 20.0, "large": 40.0, "huge": 80.0},
    # Robots — "size" here means full standing/wingspan height in meters.
    # The buckets are tighter than for objects because real robot models
    # cluster around fixed dimensions; sizing across buckets typically
    # implies SELECTING a different model, not rescaling the asset.
    # Without those bucket values the default cube-scale was applied,
    # producing 0.1m "small humanoids" — agents would then scale H1
    # (1.8m natively) by 18× to match.
    "humanoid":   {"tiny": 1.10, "small": 1.50, "medium": 1.70, "large": 1.85, "huge": 2.00},
    "manipulator":{"tiny": 0.50, "small": 0.70, "medium": 0.85, "large": 1.10, "huge": 1.40},
    "robot":      {"tiny": 0.50, "small": 0.85, "medium": 1.10, "large": 1.50, "huge": 1.85},
    "quadruped":  {"tiny": 0.40, "small": 0.55, "medium": 0.70, "large": 0.90, "huge": 1.20},
    "mobile":     {"tiny": 0.30, "small": 0.45, "medium": 0.65, "large": 0.85, "huge": 1.20},
    # Default fallback when class is unknown.
    "default":  {"tiny": 0.05, "small": 0.10, "medium": 0.30, "large": 0.70, "huge": 1.50},
}



# ---------------------------------------------------------------------------
# Phase 7 wave 1 — resolve data-handlers (12 functions)


async def _handle_resolve_count_vagueness(args: Dict) -> Dict:
    """Map a vague count phrase ('a few', 'many', 'several') to a canonical
    integer. Pilot #4 of the typed-variable resolver pattern.

    Same shape as resolve_size_adjective: extract the count phrase from
    the prompt, get a stable integer, use it in the next tool call. The
    fallback for unknown phrases is 3 (smallest non-pair group) with a
    warning so the agent doesn't silently crash on an unrecognised term.

    Returns alternatives so the agent can pick a different count if the
    user pushes back ('not THAT many, more like a couple').
    """
    # Phase 8 wave 8 — _COUNT_BUCKETS migrated.

    term_raw = (args.get("term") or "").strip().lower()
    if not term_raw:
        return {"error": "resolve_count_vagueness requires a term ('few', 'many', etc)"}

    count = _COUNT_BUCKETS.get(term_raw)
    if count is None:
        # Try matching the first word of multi-word phrases.
        first = term_raw.split()[0]
        count = _COUNT_BUCKETS.get(first)
    if count is None:
        return {
            "term": term_raw,
            "count": 3,
            "warning": f"unknown count term {term_raw!r} — defaulted to 3",
            "known_terms": sorted(set(_COUNT_BUCKETS.keys()))[:30],
        }
    # Group the alternatives in increasing order — agent can pivot up/down.
    alternatives = {
        "one": 1, "couple": 2, "few": 3, "several": 5, "many": 10,
        "dozens": 24, "hundreds": 100,
    }
    return {
        "term": term_raw,
        "count": count,
        "alternatives": alternatives,
        "rationale": f"Canonical count for {term_raw!r}; tuned to common usage.",
    }


async def _handle_resolve_robot_class(args: Dict) -> Dict:
    """Map a generic robot class phrase ('a manipulator', 'a wheeled robot')
    to a concrete robot_name from the robot_wizard registry. Pilot #5.

    Use case: user asks for 'a manipulator' or 'a humanoid' without
    specifying which model. Without this resolver the LLM either invents
    a name + path (often wrong) or asks unnecessarily. With it, the
    agent gets a sane default + the registry's canonical asset URL.

    Returns the resolved robot_name + the registry entry's cloud URL so
    the agent can pass robot_name=... straight to robot_wizard or use
    the URL for import_robot. Includes alternatives the agent can pivot
    to if the user pushes back ('not Franka, give me a UR10').
    """
    # Phase 8 wave 8 — _resolve_robot_asset migrated.
    class_raw = (args.get("robot_class") or args.get("class") or "").strip().lower()
    if not class_raw:
        return {"error": "resolve_robot_class requires a robot_class (e.g. 'manipulator', 'humanoid')"}

    robot_name = _ROBOT_CLASS_DEFAULTS.get(class_raw)
    if robot_name is None:
        # Try matching the head noun.
        for word in reversed(class_raw.split()):
            if word in _ROBOT_CLASS_DEFAULTS:
                robot_name = _ROBOT_CLASS_DEFAULTS[word]
                break
    if robot_name is None:
        return {
            "robot_class": class_raw,
            "warning": f"unknown robot class {class_raw!r}",
            "known_classes": sorted(set(_ROBOT_CLASS_DEFAULTS.keys())),
        }

    entry = _ROBOT_WIZARD_REGISTRY.get(robot_name) or {}
    if isinstance(entry, str):
        entry = _ROBOT_WIZARD_REGISTRY.get(entry, {})
    asset_url = ""
    try:
        asset_url = _resolve_robot_asset(entry) if entry else ""
    except Exception:
        pass

    return {
        "robot_class": class_raw,
        "robot_name": robot_name,
        "asset_url": asset_url,
        "robot_type": entry.get("robot_type", "manipulator") if isinstance(entry, dict) else "manipulator",
        "alternatives": {
            "manipulator": "franka_panda",
            "wheeled": "nova_carter",
            "humanoid": "h1",
            "quadruped": "anymal_c",
            "hand": "allegro",
        },
        "rationale": f"Registry default for class {class_raw!r}; resolves to {robot_name!r}.",
    }


async def _handle_resolve_material_properties(args: Dict) -> Dict:
    """Map a material descriptor ('metal', 'rubber', 'soft', 'deformable')
    to physics properties (density, friction, restitution, body_type).

    Pilot #6. Replaces LLM-invented numbers for material properties with
    canonical defaults the user can refine. Body_type signals to the agent
    whether to reach for RigidBodyAPI or PhysxDeformableBodyAPI.
    """
    # Phase 8 wave 8 — _MATERIAL_PROPERTIES migrated.

    term = (args.get("material") or args.get("term") or "").strip().lower()
    if not term:
        return {"error": "resolve_material_properties requires a material term"}
    entry = _MATERIAL_PROPERTIES.get(term)
    while isinstance(entry, str):
        entry = _MATERIAL_PROPERTIES.get(entry)
    if not entry:
        return {
            "material": term,
            "warning": f"unknown material {term!r}",
            "known_materials": sorted(k for k, v in _MATERIAL_PROPERTIES.items() if isinstance(v, dict)),
        }
    return {
        "material": term,
        **entry,
        "rationale": f"Canonical physics properties for {term!r}; SI units (kg/m^3 for density, dimensionless for friction/restitution).",
    }


async def _handle_resolve_constraint_phrase(args: Dict) -> Dict:
    """Parse a constraint phrase ('with 5cm clearance', '10kg max weight',
    'within 2 minutes', 'no closer than 1m') into structured numeric data.

    Pilot #7. Returns the extracted value normalised to SI units plus the
    constraint kind heuristically classified from keyword presence
    (clearance / mass / time / distance / angular / collision-avoidance).
    """
    # Phase 8 wave 8 — _UNIT_TO_SI migrated.

    phrase = (args.get("phrase") or args.get("constraint") or "").strip().lower()
    if not phrase:
        return {"error": "resolve_constraint_phrase requires a phrase"}

    # Heuristic constraint-kind classification
    kind = "unknown"
    if any(k in phrase for k in ("clearance", "gap", "spacing", "avstånd", "mellanrum")):
        kind = "clearance"
    elif any(k in phrase for k in ("weight", "mass", "kg", "vikt", "tung", "lätt")):
        kind = "mass"
    elif any(k in phrase for k in ("time", "duration", "within", "tid", "minut", "second")):
        kind = "time"
    elif any(k in phrase for k in ("collide", "collision", "krock", "without hitting", "without colliding")):
        kind = "collision_avoidance"
    elif any(k in phrase for k in ("angle", "rotation", "vinkel", "rad", "deg")):
        kind = "angular"
    elif any(k in phrase for k in ("fit", "fits in", "passar", "max", "limit", "size")):
        kind = "size"

    # Parse first numeric+unit; ignore "no closer than" sign — the
    # numeric magnitude is what matters for the agent.
    m = _CONSTRAINT_RE_NUMERIC.search(phrase)
    parsed = None
    if m:
        try:
            value = float(m.group("value"))
            unit_raw = (m.group("unit") or "").lower()
            si_unit, mult = _UNIT_TO_SI.get(unit_raw, (None, 1.0))
            parsed = {
                "value": value * mult,
                "raw_value": value,
                "raw_unit": unit_raw or None,
                "si_unit": si_unit,
            }
        except Exception:
            pass

    return {
        "phrase": phrase,
        "kind": kind,
        "parsed": parsed,
        "rationale": (
            f"Constraint kind heuristically classified as {kind!r}; "
            f"value extracted via regex. Use parsed.value (SI) in tool args."
        ),
    }


async def _handle_resolve_sequence_phrase(args: Dict) -> Dict:
    """Split a sequence phrase ('first X, then Y', 'after X do Y') into an
    ordered list of intent fragments.

    Pilot #8. Pure text parsing — no scene access needed. Returns the
    fragments in order so the agent can issue tool calls in sequence.
    Detects common ordering markers in English + Swedish.
    """
    phrase = (args.get("phrase") or args.get("text") or "").strip()
    if not phrase:
        return {"error": "resolve_sequence_phrase requires a phrase"}

    import re as _re
    # Split on ordering markers; lower-cased copy used for boundary detection.
    pl = phrase.lower()
    # Replace separators with a unique split-token.
    split_pat = _re.compile(
        r"\b(?:then|after that|afterwards|next|finally|sen|sedan|därefter|sista|först|first)\b|;|\.|,",
        _re.IGNORECASE,
    )
    raw_parts = [p.strip() for p in split_pat.split(phrase) if p and p.strip()]
    # Discard empty fragments and strip leading conjunctions.
    fragments = []
    for p in raw_parts:
        p = _re.sub(r"^(and|och|sen|then)\s+", "", p, flags=_re.IGNORECASE).strip()
        if p:
            fragments.append(p)
    return {
        "phrase": phrase,
        "fragments": fragments,
        "count": len(fragments),
        "rationale": "Parsed sequence fragments in execution order; issue tool calls in this order.",
    }


async def _handle_resolve_context_reference(args: Dict) -> Dict:
    """Resolve an implicit context reference ('another one', 'the same as
    before', 'the last cube I made') by querying the stage.

    Pilot #9. Without conversation-history access we can still answer
    most cases by looking at what's currently in the stage and picking
    the most-recently-named prim of the requested class.

    Args: noun_class (cube/sphere/robot/...), recency ('last' default).
    """
    from .. import kit_tools
    noun_class = (args.get("noun_class") or args.get("class") or "").strip().lower()
    if not noun_class:
        return {"error": "resolve_context_reference requires a noun_class"}

    code = f"""\
import omni.usd, json
from pxr import UsdGeom, UsdPhysics

stage = omni.usd.get_context().get_stage()
noun = {noun_class!r}

TYPE_MAP = {{
    'cube':['Cube'], 'box':['Cube'],
    'sphere':['Sphere'], 'ball':['Sphere'],
    'cylinder':['Cylinder'], 'cone':['Cone'],
    'mesh':['Mesh'],
    'camera':['Camera'],
    'light':['DistantLight','DomeLight','SphereLight','RectLight','DiskLight','CylinderLight'],
}}
matches = []
for p in stage.Traverse():
    if not p.IsValid() or not p.IsActive():
        continue
    type_name = str(p.GetTypeName() or '')
    path = str(p.GetPath())
    if path.startswith('/Render') or path.startswith('/OmniverseKit') or '/HydraTextures' in path:
        continue
    is_match = False
    if noun in ('robot','articulation'):
        is_match = p.HasAPI(UsdPhysics.ArticulationRootAPI)
    elif noun in TYPE_MAP:
        is_match = type_name in TYPE_MAP[noun]
    elif type_name.lower() == noun:
        is_match = True
    if is_match:
        matches.append({{'prim_path': path, 'type': type_name}})

# Pick the LAST in stage-traversal order; that's a heuristic for "most
# recently created" since USD doesn't store timestamps and Omniverse adds
# new prims at the end of their parent's children.
result = {{
    'noun_class': noun,
    'matches': matches,
    'count': len(matches),
    'last': matches[-1] if matches else None,
    'first': matches[0] if matches else None,
}}
print(json.dumps(result))
"""
    return await kit_tools.queue_exec_patch(code, f"resolve_context_reference {noun_class!r}")


async def _handle_resolve_coordinate_reference(args: Dict) -> Dict:
    """Resolve a named coordinate reference ('origin', 'center of X',
    'top-left corner of Y', 'edge of Z') to world-space coordinates.

    Pilot of the coordinate-landmark resolver class. Eliminates LLM-
    invented coordinates for descriptors like 'corner of the table'
    (which the LLM otherwise just guesses, often badly).

    Args:
      landmark: the descriptor — one of 'origin', 'center', 'top',
                'bottom', 'top-left', 'top-right', 'bottom-left',
                'bottom-right', 'edge_+x', 'edge_-x', 'edge_+y', 'edge_-y'
      reference_prim: prim path the landmark is relative to. Empty/None
                      means world (origin/center is world origin).

    Returns: {position: [x,y,z], landmark, reference_prim, rationale}.
    """
    from .. import kit_tools
    landmark = (args.get("landmark") or "").strip().lower()
    ref = (args.get("reference_prim") or "").strip()
    if not landmark:
        return {"error": "resolve_coordinate_reference requires landmark (origin/center/corner/edge/...)"}

    # World-anchored landmarks
    if landmark in ("origin", "world origin", "center of stage", "stage center"):
        return {
            "position": [0.0, 0.0, 0.0],
            "landmark": landmark,
            "reference_prim": "world",
            "rationale": "World origin — the canonical (0,0,0) anchor.",
        }

    if not ref:
        return {
            "error": f"landmark {landmark!r} requires a reference_prim (e.g. 'top of /World/Cube')",
            "known_landmarks": ["origin", "center", "top", "bottom",
                                "top-left", "top-right", "bottom-left", "bottom-right",
                                "edge_+x", "edge_-x", "edge_+y", "edge_-y"],
        }

    # Prim-relative landmarks need a Kit RPC call to get the bbox.
    code = f"""\
import omni.usd, json
from pxr import UsdGeom, Usd

stage = omni.usd.get_context().get_stage()
ref = {ref!r}
landmark = {landmark!r}

p = stage.GetPrimAtPath(ref)
if not p or not p.IsValid():
    print(json.dumps({{'error': 'reference_prim not found: ' + ref}}))
else:
    cache = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.default_])
    b = cache.ComputeWorldBound(p).ComputeAlignedRange()
    if b.IsEmpty():
        print(json.dumps({{'error': 'reference_prim has empty bbox'}}))
    else:
        mn = b.GetMin(); mx = b.GetMax(); c = b.GetMidpoint()
        # Map landmark → point.
        candidates = {{
            'center':       [float(c[0]), float(c[1]), float(c[2])],
            'top':          [float(c[0]), float(c[1]), float(mx[2])],
            'bottom':       [float(c[0]), float(c[1]), float(mn[2])],
            'top-left':     [float(mn[0]), float(c[1]), float(mx[2])],
            'top-right':    [float(mx[0]), float(c[1]), float(mx[2])],
            'bottom-left':  [float(mn[0]), float(c[1]), float(mn[2])],
            'bottom-right': [float(mx[0]), float(c[1]), float(mn[2])],
            'edge_+x':      [float(mx[0]), float(c[1]), float(c[2])],
            'edge_-x':      [float(mn[0]), float(c[1]), float(c[2])],
            'edge_+y':      [float(c[0]), float(mx[1]), float(c[2])],
            'edge_-y':      [float(c[0]), float(mn[1]), float(c[2])],
            'left':         [float(mn[0]), float(c[1]), float(c[2])],
            'right':        [float(mx[0]), float(c[1]), float(c[2])],
            'front':        [float(c[0]), float(mn[1]), float(c[2])],
            'back':         [float(c[0]), float(mx[1]), float(c[2])],
        }}
        pos = candidates.get(landmark)
        if pos is None:
            print(json.dumps({{'error': 'unknown landmark', 'known': sorted(candidates.keys())}}))
        else:
            print(json.dumps({{
                'position': pos,
                'landmark': landmark,
                'reference_prim': ref,
                'bbox_min': [float(mn[0]),float(mn[1]),float(mn[2])],
                'bbox_max': [float(mx[0]),float(mx[1]),float(mx[2])],
                'rationale': 'Computed from world-space bbox; landmark mapped to bbox corner/face/center.',
            }}))
"""
    return await kit_tools.queue_exec_patch(code, f"resolve_coordinate_reference {landmark!r} of {ref!r}")


async def _handle_resolve_relational_property(args: Dict) -> Dict:
    """Resolve a relational property like 'twice the size of X' or 'same
    color as Y' or '50% of Z's height' to a concrete numeric value or
    attribute reference.

    Pilot for cross-prim relations. Tightly scoped to size/scale
    relations for now (most common); color/material/orientation can
    extend later.

    Args:
      relation: one of 'size_factor' (twice/half/N×), 'same_size_as',
                'opposite_facing', 'same_height_as', 'same_color_as'
      reference_prim: prim to base the relation on (Kit RPC reads it)
      factor: numeric multiplier when relation is size_factor (default 2.0)

    Returns: {relation, value (or reference), rationale}.
    """
    from .. import kit_tools
    relation = (args.get("relation") or "").strip().lower()
    ref = (args.get("reference_prim") or "").strip()
    factor = float(args.get("factor", 2.0))

    if not relation:
        return {"error": "resolve_relational_property requires relation"}
    if not ref:
        return {"error": f"relation {relation!r} requires reference_prim"}

    if relation in ("size_factor", "twice the size of", "half the size of",
                    "n times bigger", "n× larger", "scaled relative to",
                    "same_size_as", "same size as"):
        if relation in ("same_size_as", "same size as"):
            factor = 1.0
        if "half" in relation:
            factor = 0.5
        code = f"""\
import omni.usd, json
from pxr import UsdGeom, Usd

stage = omni.usd.get_context().get_stage()
ref = {ref!r}

p = stage.GetPrimAtPath(ref)
if not p or not p.IsValid():
    print(json.dumps({{'error': 'reference_prim not found: ' + ref}}))
else:
    cache = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.default_])
    b = cache.ComputeWorldBound(p).ComputeAlignedRange()
    if b.IsEmpty():
        print(json.dumps({{'error': 'reference_prim has empty bbox'}}))
    else:
        mn = b.GetMin(); mx = b.GetMax()
        size_xyz = [float(mx[0]-mn[0]), float(mx[1]-mn[1]), float(mx[2]-mn[2])]
        avg = sum(size_xyz) / 3.0
        scaled = [s * {factor!r} for s in size_xyz]
        scaled_avg = avg * {factor!r}
        out = {{
            'reference_prim': ref,
            'reference_size_xyz': size_xyz,
            'reference_size_avg': avg,
            'factor': {factor!r},
            'derived_size_xyz': scaled,
            'derived_size_avg': scaled_avg,
            'rationale': 'Reference size from world bbox; derived = reference × factor.',
        }}
        print(json.dumps(out))
"""
        return await kit_tools.queue_exec_patch(code, f"resolve_relational_property {relation!r} ref={ref}")

    return {
        "error": f"unsupported relation {relation!r}",
        "supported": ["size_factor", "same_size_as", "twice the size of", "half the size of"],
    }


async def _handle_resolve_success_condition(args: Dict) -> Dict:
    """Extract a structured success condition from a prompt's intent.

    The third resolver class — counterpart to input-resolvers and
    output-verifiers. This one extracts what 'done' means for the
    current intent so the agent can plan the verify step in advance.

    Args:
      intent_kind: one of {object_traversal, static_layout, controller_setup, data_pipeline}
      object: the thing that traverses (for traversal kind)
      start_location: where it begins (path or descriptor)
      end_location: where it must end up (path or descriptor)
      components: list of expected prims (for static_layout)

    Returns: {kind, success_condition: {start_state, end_state}, verify_with, rationale}.
    Agent uses this to know what verifier to call before declaring done.
    """
    # Phase 8 wave 8 — _SUCCESS_CONDITION_TEMPLATES migrated.

    kind = (args.get("intent_kind") or "").strip().lower()
    if not kind:
        return {"error": "resolve_success_condition requires intent_kind",
                "known_kinds": sorted(_SUCCESS_CONDITION_TEMPLATES.keys())}
    template = _SUCCESS_CONDITION_TEMPLATES.get(kind)
    if not template:
        return {"error": f"unknown intent_kind {kind!r}",
                "known_kinds": sorted(_SUCCESS_CONDITION_TEMPLATES.keys())}

    out = {
        "intent_kind": kind,
        "success_condition": {},
        "verify_with": template["verify_with"],
        "rationale": template["rationale"],
        "fields_required": template["fields"],
    }
    if kind == "object_traversal":
        obj = args.get("object", "")
        start = args.get("start_location", "")
        end = args.get("end_location", "")
        out["success_condition"] = {
            "start_state": f"{obj} located at {start}" if obj and start else "(unspecified — call again with object+start_location)",
            "end_state": f"{obj} located at {end}" if obj and end else "(unspecified — call again with object+end_location)",
            "object": obj, "start_location": start, "end_location": end,
        }
        # Surface ambiguity if any field empty — agent should ASK.
        missing = [f for f in template["fields"] if not args.get(f)]
        if missing:
            out["needs_clarification"] = True
            out["missing_fields"] = missing
            out["suggested_question"] = (
                f"To verify the assembly is complete I need: {', '.join(missing)}. "
                f"Could you specify?"
            )
    elif kind == "static_layout":
        components = args.get("components") or []
        out["success_condition"] = {"required_components": list(components)}
    elif kind == "controller_setup":
        out["success_condition"] = {
            "robot": args.get("robot", ""),
            "controller_type": args.get("controller_type", ""),
        }
    elif kind == "data_pipeline":
        out["success_condition"] = {
            "source": args.get("source", ""),
            "sink": args.get("sink", ""),
            "throughput": args.get("throughput", ""),
        }
    return out


async def _handle_resolve_skill_composition(args: Dict) -> Dict:
    """Map a skill-composition name ('pick-and-place', 'calibration', 'ros2')
    to a known tool chain. Pilot #10.

    Returns the recipe so the agent can issue the tool calls in order.
    Args_template fields like '<ROBOT>' tell the agent it needs to fill
    that in (typically by calling resolve_prim_reference first).
    """
    # Phase 8 wave 8 — _SKILL_RECIPES migrated.

    name = (args.get("skill") or args.get("name") or "").strip().lower()
    if not name:
        return {"error": "resolve_skill_composition requires a skill name"}
    entry = _SKILL_RECIPES.get(name)
    while isinstance(entry, str):
        entry = _SKILL_RECIPES.get(entry)
    if not entry:
        return {
            "skill": name,
            "warning": f"unknown skill {name!r}",
            "known_skills": sorted(k for k, v in _SKILL_RECIPES.items() if isinstance(v, dict)),
        }
    return {
        "skill": name,
        "description": entry["description"],
        "tool_chain": entry["tool_chain"],
        "rationale": "Canonical recipe; fill <ANGLE_BRACKET> placeholders with resolved prim paths before calling each tool in order.",
    }


async def _handle_resolve_size_adjective(args: Dict) -> Dict:
    """Map a size adjective ('small', 'large', 'tiny') for a given object
    class to a canonical numeric extent in meters.

    Pilot #3 of the typed-variable resolver pattern. The LLM extracts the
    adjective and the head noun (object class) from the user's prompt
    and calls this tool. Returns one canonical value plus the bucket
    map so the agent can pick a different bucket if the user pushes
    back ('not THAT small, more like medium').

    Examples:
      'a small cube'    → {value: 0.05, unit: 'meters', class: 'cube', bucket: 'small'}
      'a large table'   → {value: 2.0,  unit: 'meters', class: 'table', bucket: 'large'}
      'a tiny sphere'   → {value: 0.02, unit: 'meters', class: 'sphere', bucket: 'tiny'}

    Side benefits:
      - Eliminates LLM-invented numbers (each invocation gives different
        sizes for "a small cube"; this gives the same one).
      - Forces agreement across multi-prim prompts ("a small cube and
        a small sphere" become the same bucket → comparable sizes).
      - Future canary tests can pin specific values for regression checks.
    """
    # Phase 8 wave 8 — _SIZE_BUCKETS migrated.

    adjective_raw = (args.get("adjective") or "").strip().lower()
    object_class_raw = (args.get("object_class") or "").strip().lower()
    if not adjective_raw:
        return {"error": "resolve_size_adjective requires an adjective (e.g. 'small', 'tiny')"}

    bucket = _SIZE_BUCKET_ALIASES.get(adjective_raw)
    if bucket is None:
        # Unknown adjective — return medium as safe default + warn.
        return {
            "adjective": adjective_raw,
            "object_class": object_class_raw or "default",
            "bucket": "medium",
            "value": _SIZE_BUCKETS.get(object_class_raw, _SIZE_BUCKETS["default"])["medium"],
            "unit": "meters",
            "warning": f"unknown size adjective {adjective_raw!r} — defaulted to 'medium'",
            "known_adjectives": sorted(set(_SIZE_BUCKET_ALIASES.values())),
        }

    class_key = object_class_raw if object_class_raw in _SIZE_BUCKETS else "default"
    bucket_map = _SIZE_BUCKETS[class_key]
    value = bucket_map[bucket]
    return {
        "adjective": adjective_raw,
        "bucket": bucket,
        "object_class": class_key,
        "object_class_known": object_class_raw in _SIZE_BUCKETS,
        "value": value,
        "unit": "meters",
        "alternatives": bucket_map,  # full map so agent sees neighbour buckets
        "rationale": (
            f"Canonical {bucket}-bucket value for {class_key!r}; "
            "tuned to common Isaac Sim / industrial-robotics conventions."
        ),
    }


async def _handle_resolve_prim_reference(args: Dict) -> Dict:
    """Resolve a deictic noun phrase ('kuben', 'the cube', 'roboten') to one
    or more concrete prim paths in the current stage.

    Pilot #2 of the typed-variable-resolver pattern (after place_on_top_of).
    The LLM identifies a deictic reference in the user prompt, extracts a
    `name_hint` (the head noun, normalised) and optionally a `prim_type`
    (Cube/Sphere/Robot/Camera/Light/...), and calls this tool. The tool
    returns the matching candidates.

    The agent's protocol after the call:
      - 1 match → use the returned prim_path directly
      - >1 match → ask the user "which one?" with the candidate list
      - 0 match → tell the user nothing matches; offer to create or rename

    No numerical reasoning by the LLM; no prim-path hallucination. The
    "ambiguous → ask" path is the resolver-level clarification mechanism
    we want to prove out — it's strictly more focused than the
    whole-prompt negotiator.
    """
    from .. import kit_tools
    name_hint = (args.get("name_hint") or args.get("description") or "").strip().lower()
    prim_type = (args.get("prim_type") or "").strip()
    if not name_hint and not prim_type:
        return {"error": "resolve_prim_reference requires either name_hint or prim_type"}

    code = f"""\
import omni.usd
import json
from pxr import Usd, UsdGeom, UsdPhysics, UsdLux, Gf

stage = omni.usd.get_context().get_stage()
name_hint = {name_hint!r}
prim_type_filter = {prim_type!r}

# Map common natural-language hints to USD typeNames + schema APIs.
# Includes Swedish + English heads. Order matters: most specific first.
TYPE_HINTS = {{
    'cube': ['Cube'], 'kub': ['Cube'], 'kuben': ['Cube'], 'box': ['Cube'],
    'sphere': ['Sphere'], 'sfär': ['Sphere'], 'boll': ['Sphere'], 'ball': ['Sphere'],
    'cylinder': ['Cylinder'],
    'cone': ['Cone'], 'kon': ['Cone'],
    'capsule': ['Capsule'], 'kapsel': ['Capsule'],
    'mesh': ['Mesh'],
    'camera': ['Camera'], 'kamera': ['Camera'], 'kameran': ['Camera'],
    'light': ['DistantLight','DomeLight','SphereLight','RectLight','DiskLight','CylinderLight'],
    'ljus': ['DistantLight','DomeLight','SphereLight','RectLight','DiskLight','CylinderLight'],
    'ljuset': ['DistantLight','DomeLight','SphereLight','RectLight','DiskLight','CylinderLight'],
    'lampa': ['DistantLight','DomeLight','SphereLight','RectLight','DiskLight','CylinderLight'],
}}
hint_types = TYPE_HINTS.get(name_hint, [])

# Normalise the name hint for substring matching against prim paths.
# Strip common Swedish definite-article suffixes so 'kuben' also matches
# /World/Cube_3 (the agent's lookup hint is more useful than a literal
# substring search would be).
def _normalise(h):
    h = h.lower()
    for suf in ('en', 'er', 'et', 'na', 's'):
        if h.endswith(suf) and len(h) - len(suf) >= 3:
            return h[:-len(suf)]
    return h

stem = _normalise(name_hint) if name_hint else ''

def _is_robot(prim):
    return prim.HasAPI(UsdPhysics.ArticulationRootAPI)

def _is_light(prim):
    schemas = prim.GetTypeName()
    return schemas in TYPE_HINTS['light']

candidates = []
for p in stage.Traverse():
    if not p.IsValid() or not p.IsActive():
        continue
    type_name = str(p.GetTypeName() or '')
    path = str(p.GetPath())
    pl = path.lower()

    # Filter by explicit prim_type when supplied. 'Robot' / 'robot' is a
    # virtual class — match articulations regardless of typeName.
    if prim_type_filter:
        ptf = prim_type_filter.lower()
        if ptf in ('robot','robotar','articulation','manipulator','humanoid'):
            if not _is_robot(p): continue
        elif ptf in ('light','ljus','lamp'):
            if not _is_light(p): continue
        elif type_name.lower() != ptf:
            continue
    elif name_hint in ('robot','roboten','robotar'):
        if not _is_robot(p): continue
    elif hint_types:
        if type_name not in hint_types and not _is_light(p) and name_hint not in ('light','ljus','ljuset','lampa'):
            if type_name not in hint_types: continue
    elif stem:
        if stem not in pl and stem not in type_name.lower():
            continue

    # Skip Kit's internal scopes (render config, environment skydome etc)
    if path.startswith('/Render') or path.startswith('/OmniverseKit') or '/HydraTextures' in path:
        continue

    cand = {{'prim_path': path, 'type': type_name}}
    try:
        xf = UsdGeom.Xformable(p)
        if xf:
            t = xf.ComputeLocalToWorldTransform(0).ExtractTranslation()
            cand['position'] = [float(t[0]), float(t[1]), float(t[2])]
    except Exception:
        pass
    candidates.append(cand)

result = {{
    'name_hint': name_hint,
    'prim_type_filter': prim_type_filter,
    'candidates': candidates,
    'count': len(candidates),
    'exact_match': candidates[0]['prim_path'] if len(candidates) == 1 else None,
    'ambiguous': len(candidates) > 1,
    'no_match': len(candidates) == 0,
}}
print(json.dumps(result))
"""
    return await kit_tools.queue_exec_patch(
        code, f"resolve_prim_reference name_hint={name_hint!r} type={prim_type!r}"
    )


# ---------------------------------------------------------------------------
# Phase 20 — RoleRetriever-backed canonical-template lookup


async def _handle_retrieve_template_by_role(args: Dict[str, Any]) -> Dict[str, Any]:
    """Use the Phase 20 RoleRetriever to rank canonical templates against
    a user query and optional role hints.

    Pure-Python ranking — role-based matches ahead of legacy. No Kit
    invocation.

    Args:
        query: free-text user request (required).
        role_hints: optional list of role names to weight toward.
        max_results: cap on returned matches (default 10).

    Returns:
        Dict with ``matches`` (list of dicts) and ``count``.
    """
    from service.isaac_assist_service.chat.tools.role_retriever import RoleRetriever

    query = str(args.get("query") or "")
    if not query:
        return {"error": "query is required", "matches": [], "count": 0}
    role_hints = list(args.get("role_hints") or [])
    max_results = int(args.get("max_results", 10))

    retriever = RoleRetriever()
    matches = retriever.retrieve_with_roles(
        query=query, role_hints=role_hints, max_results=max_results
    )
    return {
        "matches": [
            {
                "template_id": m.template_id,
                "source": m.source,
                "match_score": m.match_score,
                "matched_role": m.matched_role,
                "matched_tags": list(m.matched_tags),
                "notes": m.notes,
            }
            for m in matches
        ],
        "count": len(matches),
    }


# ---------------------------------------------------------------------------
# Registration


def register(
    data: Dict[str, Callable[..., Awaitable[Any]]],
    codegen: Dict[str, Callable[..., Any]],
) -> None:
    """Phase 9 — populate dispatch dicts with this module's handlers.

    Called by `handlers/_dispatch.py:register_handlers()` which is the
    sole dispatch entry point from `tool_executor.py`.
    """
    # Data handlers (13)
    data["resolve_constraint_phrase"] = _handle_resolve_constraint_phrase
    data["retrieve_template_by_role"] = _handle_retrieve_template_by_role
    data["resolve_context_reference"] = _handle_resolve_context_reference
    data["resolve_coordinate_reference"] = _handle_resolve_coordinate_reference
    data["resolve_count_vagueness"] = _handle_resolve_count_vagueness
    data["resolve_material_properties"] = _handle_resolve_material_properties
    data["resolve_prim_reference"] = _handle_resolve_prim_reference
    data["resolve_relational_property"] = _handle_resolve_relational_property
    data["resolve_robot_class"] = _handle_resolve_robot_class
    data["resolve_sequence_phrase"] = _handle_resolve_sequence_phrase
    data["resolve_size_adjective"] = _handle_resolve_size_adjective
    data["resolve_skill_composition"] = _handle_resolve_skill_composition
    data["resolve_success_condition"] = _handle_resolve_success_condition
