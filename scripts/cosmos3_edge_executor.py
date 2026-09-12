"""Cosmos 3 Edge motion-executor spec, importable without the Isaac runtime.

The runner registers this spec alongside the bounded DLS executors when
``--cosmos3-edge-policy`` is set; tests validate the advertised contract
without launching Isaac. Execution itself lives in the runner
(``_execute_cosmos3_edge_chunks``), transport in ``cosmos3_edge_client``.
"""

from __future__ import annotations

from typing import Sequence

try:
    from .observation_bound_motion_tools import MotionExecutorSpec
except ImportError:  # Script execution adds this directory directly to sys.path.
    from observation_bound_motion_tools import (  # type: ignore[no-redef]
        MotionExecutorSpec,
    )

COSMOS3_EDGE_EXECUTOR_ID = "cosmos3_edge_policy"
COSMOS3_EDGE_TOOL_NAME = "execute_cosmos3_edge_policy"
COSMOS3_EDGE_CAPABILITY_TAG = "policy.language_conditioned_action_chunks"
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
