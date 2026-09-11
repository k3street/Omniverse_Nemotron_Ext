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
                    "type": "integer", "minimum": 1, "maximum": 4,
                    "description": (
                        "Consecutive 32-step chunks admitted under this one "
                        "lease before control returns."
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
                    "type": "number", "minimum": 0.005, "maximum": 0.30,
                },
                # Shared lease-condition vocabulary the runtime rules teach
                # for motion executors; the chunk executor enforces each of
                # these at its monitor cadence or at completion.
                "position_tolerance_m": {
                    "type": "number", "minimum": 0.001, "maximum": 0.05,
                },
                "require_contact": {"type": "boolean"},
                "forbid_contact": {
                    "type": "boolean",
                    "description": (
                        "Revoke the motion immediately if gripper contact "
                        "is observed."
                    ),
                },
                "require_interaction_relation": {
                    "type": "boolean",
                    "description": (
                        "Completion requires ending within position "
                        "tolerance of the grounded expected-outcome pose."
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
