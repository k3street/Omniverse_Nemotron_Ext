"""Cosmos 3 Edge motion-executor spec, importable without the Isaac runtime.

The runner registers this spec alongside the bounded DLS executors when
``--cosmos3-edge-policy`` is set; tests validate the advertised contract
without launching Isaac. Execution itself lives in the runner
(``_execute_cosmos3_edge_chunks``), transport in ``cosmos3_edge_client``.
"""

from __future__ import annotations

from typing import Any, Collection, Sequence

try:
    from .observation_bound_motion_tools import (
        ActuatorExecutorSpec,
        MotionExecutorSpec,
    )
except ImportError:  # Script execution adds this directory directly to sys.path.
    from observation_bound_motion_tools import (  # type: ignore[no-redef]
        ActuatorExecutorSpec,
        MotionExecutorSpec,
    )

COSMOS3_EDGE_EXECUTOR_ID = "cosmos3_edge_policy"
COSMOS3_EDGE_TOOL_NAME = "execute_cosmos3_edge_policy"
COSMOS3_EDGE_CAPABILITY_TAG = "policy.language_conditioned_action_chunks"
COSMOS3_EDGE_ACQUIRE_EXECUTOR_ID = "cosmos3_edge_acquire"
COSMOS3_EDGE_ACQUIRE_TOOL_NAME = "execute_cosmos3_edge_acquire"

# A policy acquisition rollout is a whole grasp attempt, not a settling delay.
DEFAULT_ACQUIRE_ACTION_CHUNKS = 6
MAXIMUM_ACQUIRE_ACTION_CHUNKS = 10
# Generic marker the operation-proposal prompt explains: a session-scoped
# operator preference among otherwise-qualifying candidates. Selection stages
# see only tool_id + capability_tags, so preference must travel as a tag.
OPERATOR_SESSION_PREFERRED_TAG = "operator.session_preferred"

MAXIMUM_ACTION_CHUNKS = 8
DEFAULT_ACTION_CHUNKS = 4

# A two-finger gripper closed past this fraction has nothing between its pads:
# the sensed touch is the pads pressing each other, not an object.
SELF_CLOSURE_FRACTION = 0.95
# Gripper contact that begins within this distance of an operation target is
# the intended interaction with that target, not a collision. Measured at the
# finger-pad center (the sensed bodies): pads touching or straddling a ~5 cm
# object sit within a few centimetres of its center.
PAD_CONTACT_ATTRIBUTION_RADIUS_M = 0.08
# Fallback when the finger bodies are unavailable: measured from the gripper
# base frame, ~0.15 m above the fingertips.
CONTACT_ATTRIBUTION_RADIUS_M = 0.25
# A learned policy stops at the object it was sent to, not at a standoff
# pre-grasp pose, so it ends roughly 0.10-0.15 m from a planner-grounded
# interaction pose (measured live: 0.09-0.13 m). A completion tolerance below
# this floor would be a contract the executor cannot honour.
MINIMUM_POSITION_TOLERANCE_M = 0.10
DEFAULT_POSITION_TOLERANCE_M = 0.15


def classify_gripper_contact(
    *,
    touch: bool | None,
    closed_fraction: float | None,
    retained_force_n: float | None,
    target_distance_m: float | None,
    attribution_radius_m: float = CONTACT_ATTRIBUTION_RADIUS_M,
) -> dict[str, object]:
    """Interpret one gripper contact sample for a learned-policy rollout.

    Returns ``contact_class`` in {"none", "self_closure", "external",
    "retained_object"} and ``attributed_to_target`` (external or retained
    contact that began within ``attribution_radius_m`` of an operation
    target). A learned policy closes its own gripper, so a fully closed empty
    gripper must not read as contact; the pose-servo executors never faced
    this because only the clamp actuator closed the gripper.
    """
    fraction = float(closed_fraction) if closed_fraction is not None else 0.0
    if not touch:
        contact_class = "none"
    elif fraction >= SELF_CLOSURE_FRACTION:
        contact_class = "self_closure"
    elif retained_force_n is not None and float(retained_force_n) > 0.0:
        contact_class = "retained_object"
    else:
        contact_class = "external"
    attributed = bool(
        contact_class in ("external", "retained_object")
        and target_distance_m is not None
        and float(target_distance_m) <= float(attribution_radius_m)
    )
    return {
        "contact_class": contact_class,
        "attributed_to_target": attributed,
        "touch": bool(touch),
        "closed_fraction": fraction,
        "retained_force_n": retained_force_n,
        "target_distance_m": target_distance_m,
    }


def _entity_label(entity_id: str) -> str:
    return " ".join(part for part in entity_id.replace("-", "_").split("_") if part)


def policy_instruction_for_operation(
    *,
    purpose: str | None,
    target_entity_ids: Sequence[str],
    receptacle_entity_id: str | None,
    fallback_instruction: str,
) -> tuple[str, str]:
    """Derive the language prompt for one policy rollout from the operation.

    The composed planner phrases a whole-task instruction; a DROID-style policy
    was trained on short imperative commands about the entities it should act
    on. The runtime owns this template so the prompt the policy receives is
    recorded verbatim in the execution report. Returns (instruction, source).
    """
    targets = [str(item) for item in target_entity_ids if isinstance(item, str) and item]
    receptacle = (
        receptacle_entity_id
        if isinstance(receptacle_entity_id, str) and receptacle_entity_id in targets
        else None
    )
    objects = [item for item in targets if item != receptacle]
    if not objects or purpose not in ("establish_precondition", "realize_effect"):
        return fallback_instruction, "session_instruction"
    subject = _entity_label(objects[0])
    if purpose == "establish_precondition":
        return f"move to the {subject}", "runtime_operation_template"
    if receptacle is not None:
        return (
            f"put the {subject} in the {_entity_label(receptacle)}",
            "runtime_operation_template",
        )
    return f"pick up the {subject}", "runtime_operation_template"


def build_cosmos3_edge_motion_executor_spec(
    *,
    capability_tags: Sequence[str],
    minimum_reachable_radius_m: float,
    maximum_reachable_radius_m: float,
    maximum_displacement_m: float,
    operator_preferred: bool = False,
) -> MotionExecutorSpec:
    """Build the advertised contract for the Cosmos 3 Edge chunk executor."""
    description = (
        "Execute the language-conditioned Cosmos 3 Edge manipulation "
        "policy for a bounded number of 32-step joint-position action "
        "chunks streamed from the local policy server. The policy acts "
        "from the live wrist and over-shoulder cameras toward the "
        "current instruction. The supplied target pose grounds and "
        "gates this invocation as the expected end-effector outcome but "
        "is not servoed to: completion reports the measured "
        "target_error_after_m without enforcing pose convergence. "
        "Prefer this executor for contact-rich, vision-guided motion "
        "that a direct pose target cannot express; prefer bounded DLS "
        "IK when precise pose attainment is the requirement."
    )
    tags = tuple(capability_tags)
    if operator_preferred:
        description += (
            " Operator preference for this session: when this executor and "
            "bounded DLS IK could both accomplish a motion, select this "
            "learned-policy executor so its live behavior is exercised and "
            "evaluated; all safety gates and lease conditions still apply."
        )
        if OPERATOR_SESSION_PREFERRED_TAG not in tags:
            tags = (*tags, OPERATOR_SESSION_PREFERRED_TAG)
    return MotionExecutorSpec(
        executor_id=COSMOS3_EDGE_EXECUTOR_ID,
        tool_name=COSMOS3_EDGE_TOOL_NAME,
        description=description,
        configuration_schema={
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "maximum_action_chunks": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": MAXIMUM_ACTION_CHUNKS,
                    "description": (
                        "Consecutive 32-step chunks (about 2.1 s each) "
                        "admitted under this one lease before a fresh "
                        "observation is returned; 4-8 for an acquisition or "
                        "transport rollout, 1-2 for a short adjustment."
                    ),
                },
                "chunk_execution_steps": {
                    "type": "integer", "minimum": 8, "maximum": 32,
                    "description": (
                        "Steps of each chunk executed before re-observing; "
                        "the chunk suffix is discarded."
                    ),
                },
                "completion_position_tolerance_m": {
                    "type": "number",
                    "minimum": MINIMUM_POSITION_TOLERANCE_M,
                    "maximum": 0.30,
                },
                # Shared lease-condition vocabulary the runtime rules teach
                # for motion executors; the chunk executor enforces each of
                # these at its monitor cadence or at completion.
                "position_tolerance_m": {
                    "type": "number",
                    "minimum": MINIMUM_POSITION_TOLERANCE_M,
                    "maximum": 0.20,
                    "description": (
                        "Completion tolerance around the grounded pose. The "
                        "policy stops at the object itself, roughly "
                        "0.10-0.15 m from a standoff pre-grasp pose; values "
                        "below 0.10 m are not achievable and are rejected. "
                        "Gripper contact attributed to an operation target "
                        "also counts as having reached it."
                    ),
                },
                "require_contact": {
                    "type": "boolean",
                    "description": (
                        "Completion requires an object retained between the "
                        "gripper pads (an opposing pinch); a fully closed "
                        "empty gripper does not count."
                    ),
                },
                "forbid_contact": {
                    "type": "boolean",
                    "description": (
                        "Revoke the rollout on gripper contact that is not "
                        "attributable to an operation target. Contact while "
                        "the finger pads are within 0.08 m of a target is "
                        "the intended interaction; a fully closed empty "
                        "gripper is not contact."
                    ),
                },
                "require_interaction_relation": {
                    "type": "boolean",
                    "description": (
                        "Completion requires having reached the grounded "
                        "expected-outcome pose: ending within position "
                        "tolerance of it, or gripper contact attributed to "
                        "an operation target."
                    ),
                },
                "minimum_contact_force_n": {
                    "type": "number", "minimum": 0.0, "maximum": 100.0,
                },
                "tracked_object_id": {
                    "type": "string",
                    "description": (
                        "Advisory context recorded in the execution report; "
                        "scene-level displacement monitoring already covers "
                        "tracked entities."
                    ),
                },
            },
        },
        capability_tags=tags,
        invocation_schema={
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "target_position_m": {
                    "type": "array",
                    "items": {"type": "number"},
                    "minItems": 3,
                    "maxItems": 3,
                },
                "target_quaternion_wxyz": {
                    "type": "array",
                    "items": {"type": "number"},
                    "minItems": 4,
                    "maxItems": 4,
                },
            },
            "required": [
                "target_position_m",
                "target_quaternion_wxyz",
            ],
            "x-runtime-constraints": {
                "coordinate_frame": "robot_root",
                "workspace_min_m": [-0.75, -0.75, 0.02],
                "workspace_max_m": [0.90, 0.90, 1.40],
                "minimum_reachable_radius_m": minimum_reachable_radius_m,
                "maximum_reachable_radius_m": maximum_reachable_radius_m,
                "maximum_displacement_m": maximum_displacement_m,
                "maximum_grounding_offset_m": 0.35,
                "maximum_alignment_error_deg": 15.0,
            },
        },
    )


def drop_redundant_pre_acquisition_motions(
    tool_calls: Sequence[Any],
    acquisition_tool_ids: Collection[str],
) -> list[Any]:
    """Remove approach motions that a policy acquisition performs itself.

    A composed sequence written for a gripper-only actuator reads
    "move to the object, then close". An acquisition executor that approaches
    and aligns inside its own rollout makes that leading motion redundant, and
    the runtime rejects it outright once the end-effector is already within
    the configured position tolerance — which discards the whole composition,
    acquisition included. Dropping the motion here keeps the plan executable
    without depending on the planner to omit it.

    Only motions that share a target entity with a later policy acquisition
    are dropped; motions toward anything else (a destination, a clearance
    waypoint) are preserved.
    """
    calls = list(tool_calls)
    acquisition_ids = set(acquisition_tool_ids)
    if not acquisition_ids:
        return calls
    first_acquisition_index: int | None = None
    acquisition_targets: set[str] = set()
    for index, call in enumerate(calls):
        effect_id = getattr(call, "semantic_effect_id", None)
        if (
            getattr(call, "tool_id", None) in acquisition_ids
            and isinstance(effect_id, str)
            and effect_id.endswith(".acquire")
        ):
            first_acquisition_index = index
            acquisition_targets = {
                str(item)
                for item in getattr(call, "target_entity_ids", ()) or ()
            }
            break
    if first_acquisition_index is None or not acquisition_targets:
        return calls
    kept: list[Any] = []
    for index, call in enumerate(calls):
        if index < first_acquisition_index and getattr(
            call, "tool_family", None
        ) == "motion":
            targets = {
                str(item)
                for item in getattr(call, "target_entity_ids", ()) or ()
            }
            if targets & acquisition_targets:
                continue
        kept.append(call)
    return kept


def build_cosmos3_edge_acquire_executor_spec(
    *,
    capability_tags: Sequence[str],
) -> "ActuatorExecutorSpec":
    """Advertise Cosmos 3 Edge as a reversible attachment actuator.

    The planner decomposes manipulation the way a pose-servo stack needs it:
    move to a standoff pose, then close a clamp. A DROID-trained policy's
    native unit is the whole acquisition — approach, align, and close on the
    object in one contact-rich rollout — so here the policy owns ``engage``.

    The command vocabulary stays the runtime's own ``engage``/``disengage``/
    ``maintain``, so retained-attachment bookkeeping and post-release
    geometry-change classification consume this executor unchanged.
    ``disengage`` and ``maintain`` remain the deterministic gripper commands:
    releasing needs no policy.
    """
    return ActuatorExecutorSpec(
        executor_id=COSMOS3_EDGE_ACQUIRE_EXECUTOR_ID,
        tool_name=COSMOS3_EDGE_ACQUIRE_TOOL_NAME,
        description=(
            "Acquire or release an entity with the language-conditioned "
            "Cosmos 3 Edge policy. Engage runs one acquisition rollout: the "
            "policy approaches, aligns, and closes the gripper on the "
            "operation target from the live cameras, and reports engagement "
            "only when an object is measurably retained between the pads. "
            "Use engage instead of a separate approach motion followed by a "
            "clamp: the whole grasp is one rollout, and gripper contact with "
            "the target is expected rather than a fault. Disengage opens the "
            "gripper and maintain preserves its current command, both "
            "directly. Select this for acquisition when the target is "
            "visible; a clamp engage still suits an already-aligned grasp."
        ),
        command_schema={
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "state": {
                    "type": "string",
                    "enum": ["engage", "disengage", "maintain"],
                }
            },
            "required": ["state"],
        },
        configuration_schema={
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "maximum_action_chunks": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": MAXIMUM_ACQUIRE_ACTION_CHUNKS,
                    "description": (
                        "Acquisition rollout budget in 32-step chunks (about "
                        "2.1 s each). Engage only; 4-8 suits a grasp from a "
                        "visible standoff."
                    ),
                },
                "settle_steps": {
                    "type": "integer",
                    "minimum": 8,
                    "maximum": 120,
                    "description": (
                        "Settling steps for the direct disengage and "
                        "maintain commands."
                    ),
                },
            },
        },
        capability_tags=tuple(capability_tags),
        semantic_command_bindings={
            "entity_attachment.acquire": {"state": "engage"},
            "entity_attachment.release": {"state": "disengage"},
        },
    )
