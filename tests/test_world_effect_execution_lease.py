from pathlib import Path

import pytest

from scripts.world_effect_execution_lease import (
    WORLD_EFFECT_EXECUTION_LEASE_SCHEMA_VERSION,
    ShadowExecutionLeaseGate,
    WorldEffectExecutionLeaseError,
    build_shadow_execution_lease_candidates,
    build_shadow_execution_lease_prompt,
)
from scripts.world_effect_operation_plan import (
    PlanningToolActivation,
    PlanningWorldEffectProviderInstance,
    WorldEffectOperationCandidate,
    WorldEffectOperationCandidateSet,
    WorldEffectOperationDecision,
)


def inventory():
    return {
        "schema_version": "semantic-scene-inventory.v1",
        "available": True,
        "source": "fresh_rgbd",
        "frame": "robot_root",
        "entities": [
            {
                "entity_id": "red_block",
                "label": "red block",
                "observation_status": "visible_rgbd",
                "geometry": {
                    "center_base_m": [0.50, 0.20, 0.04],
                    "visible_extent_base_m": [0.04, 0.04, 0.04],
                },
            },
            {
                "entity_id": "grey_bin",
                "label": "grey bin",
                "observation_status": "visible_rgbd",
                "geometry": {
                    "center_base_m": [0.40, -0.20, 0.08],
                    "visible_extent_base_m": [0.35, 0.25, 0.15],
                },
            },
        ],
        "role_bindings": [],
        "limitations": [],
    }


def operation_fixture(*, tool_id="spatial_motion"):
    configuration_schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "position_tolerance_m": {
                "type": "number",
                "minimum": 0.001,
                "maximum": 0.05,
            },
            "minimum_progress_m": {
                "type": "number",
                "minimum": 0.0001,
                "maximum": 0.01,
            },
            "maximum_stalled_observations": {
                "type": "integer",
                "minimum": 2,
                "maximum": 20,
            },
            "maximum_tracked_pose_error_m": {
                "type": "number",
                "minimum": 0.001,
                "maximum": 0.30,
            },
            "require_contact": {"type": "boolean"},
        },
    }
    instance = PlanningWorldEffectProviderInstance(
        instance_id="planning-provider:test",
        session_observation_id="effect-session-observation:test",
        session_candidate_id="effect-session:test",
        provider_id="transport.reversible_attachment",
        graph_id="clean-table",
        membership_lease_id="membership:test",
        goal_id="red-in-bin",
        world_capability_id="world_relation.realize_inside",
        desired_state=(
            {
                "subject_id": "red_block",
                "attribute": "inside",
                "operator": "==",
                "value": True,
                "reference_id": "grey_bin",
            },
        ),
        tool_activations=(
            PlanningToolActivation(
                requirement_id="observation_bound_spatial_motion",
                source_tool_id="factory.spatial",
                activated_tool_id=tool_id,
                tool_family="motion",
                capability_tags=(
                    "spatial.pose_target",
                    "motion.observation_bound",
                    "motion.invalidation_feedback",
                ),
                tool_advertisement={
                    "executor_id": tool_id,
                    "tool_name": "execute_spatial_motion",
                    "tool_family": "motion",
                    "capability_tags": [
                        "spatial.pose_target",
                        "motion.observation_bound",
                        "motion.invalidation_feedback",
                    ],
                    "configuration_schema": configuration_schema,
                },
                factory_instantiated=True,
            ),
        ),
        activation_blockers=(),
    )
    operation = WorldEffectOperationCandidate(
        operation_candidate_id="effect-operation:test",
        provider_instance_id=instance.instance_id,
        requirement_id="observation_bound_spatial_motion",
        tool_id=tool_id,
        tool_family="motion",
        capability_tags=(
            "spatial.pose_target",
            "motion.observation_bound",
            "motion.invalidation_feedback",
        ),
    )
    candidate_set = WorldEffectOperationCandidateSet(
        observation_id="effect-operation-observation:test",
        provider_instance_id=instance.instance_id,
        related_entity_ids=("grey_bin", "red_block"),
        candidates=(operation,),
    )
    decision = WorldEffectOperationDecision(
        observation_id=candidate_set.observation_id,
        decision="propose_operation",
        operation_candidate_id=operation.operation_candidate_id,
        requirement_id=operation.requirement_id,
        tool_id=operation.tool_id,
        purpose="establish_precondition",
        target_entity_ids=("red_block",),
        desired_outcome="Interaction geometry is ready for acquisition.",
        stop_condition="Stop at the observable interaction geometry.",
        confidence=0.9,
        reason="The target is visible.",
    )
    lease_candidates = build_shadow_execution_lease_candidates(
        instance,
        candidate_set,
        decision,
        inventory(),
    )
    return instance, candidate_set, decision, lease_candidates


def invalidations(*, include_tracked_pose=False):
    values = [
        {
            "condition_id": "scene.target_visibility_lost",
            "target_entity_ids": ["red_block"],
            "parameters": {},
        },
        {
            "condition_id": "scene.target_geometry_drift",
            "target_entity_ids": ["red_block"],
            "parameters": {
                "maximum_center_shift_m": 0.02,
                "maximum_extent_change_fraction": 0.25,
            },
        },
        {
            "condition_id": "lease.membership_changed",
            "target_entity_ids": [],
            "parameters": {},
        },
        {
            "condition_id": "provider.instance_changed",
            "target_entity_ids": [],
            "parameters": {},
        },
    ]
    if include_tracked_pose:
        values.append(
            {
                "condition_id": "scene.tracked_pose_error_exceeded",
                "target_entity_ids": ["red_block"],
                "parameters": {},
            }
        )
    return values


def proposal(candidate_set, **overrides):
    candidate = candidate_set.candidates[0]
    payload = {
        "schema_version": WORLD_EFFECT_EXECUTION_LEASE_SCHEMA_VERSION,
        "observation_id": candidate_set.observation_id,
        "decision": "propose_lease",
        "candidate_id": candidate.candidate_id,
        "provider_instance_id": candidate.provider_instance_id,
        "operation_candidate_id": candidate.operation_candidate_id,
        "tool_id": candidate.tool_id,
        "grounding_entity_ids": ["red_block", "grey_bin"],
        "tool_configuration": {"position_tolerance_m": 0.02},
        "invalidation_conditions": invalidations(),
        "confidence": 0.91,
        "reason": "Fresh target and goal geometry are bound to the lease.",
    }
    payload.update(overrides)
    return payload


def test_candidate_binds_operation_tool_to_fresh_goal_geometry():
    instance, _, decision, candidate_set = operation_fixture()
    candidate = candidate_set.candidates[0]

    assert candidate.provider_instance_id == instance.instance_id
    assert candidate.operation_observation_id == decision.observation_id
    assert candidate.tool_id == "spatial_motion"
    assert {item.entity_id for item in candidate.geometry_bindings} == {
        "red_block",
        "grey_bin",
    }
    assert all(item.geometry_digest.startswith("geometry:") for item in candidate.geometry_bindings)
    assert set(candidate.mandatory_condition_ids()) == {
        "scene.target_visibility_lost",
        "scene.target_geometry_drift",
        "lease.membership_changed",
        "provider.instance_changed",
    }
    geometry_drift = next(
        item
        for item in candidate.invalidation_candidates
        if item.condition_id == "scene.target_geometry_drift"
    )
    assert geometry_drift.parameter_schema["properties"][
        "maximum_center_shift_m"
    ]["maximum"] == pytest.approx(0.04)
    serialized = candidate_set.to_dict()
    assert not serialized["execution_lease_issued"]
    assert not serialized["handler_bound"]
    assert not serialized["dispatch_enabled"]


def test_runtime_tool_schema_can_change_without_changing_lease_contract():
    instance, _, _, candidate_set = operation_fixture(tool_id="whole_body_reach")
    candidate = candidate_set.candidates[0]

    assert instance.goal_id == "red-in-bin"
    assert candidate.tool_id == "whole_body_reach"
    assert candidate.tool_configuration_schema["properties"]["position_tolerance_m"]


def test_gate_validates_configuration_and_issues_no_execution_lease():
    _, _, _, candidate_set = operation_fixture()
    accepted = ShadowExecutionLeaseGate(candidate_set).dispatch(proposal(candidate_set))
    serialized = accepted.to_dict()

    assert accepted.lease_id.startswith("shadow-execution-lease:")
    assert accepted.tool_configuration == {"position_tolerance_m": 0.02}
    assert serialized["configuration_validated"]
    assert not serialized["execution_lease_issued"]
    assert not serialized["tool_called"]
    assert not serialized["handler_bound"]
    assert not serialized["dispatch_enabled"]
    assert not serialized["motion_authority"]
    assert not serialized["execution_authority"]


def test_gate_rejects_stale_invented_unbound_and_unsafe_proposals():
    _, _, _, candidate_set = operation_fixture()
    gate = ShadowExecutionLeaseGate(candidate_set)

    with pytest.raises(WorldEffectExecutionLeaseError, match="stale"):
        gate.dispatch(proposal(candidate_set, observation_id="stale:lease"))

    missing = invalidations()[1:]
    with pytest.raises(WorldEffectExecutionLeaseError, match="mandatory"):
        gate.dispatch(proposal(candidate_set, invalidation_conditions=missing))

    with pytest.raises(WorldEffectExecutionLeaseError, match="triple"):
        gate.dispatch(proposal(candidate_set, tool_id="invented_tool"))

    with pytest.raises(WorldEffectExecutionLeaseError, match="fresh geometry"):
        gate.dispatch(
            proposal(
                candidate_set,
                grounding_entity_ids=["red_block", "invented_entity"],
            )
        )

    with pytest.raises(WorldEffectExecutionLeaseError, match="maximum"):
        gate.dispatch(
            proposal(
                candidate_set,
                tool_configuration={"position_tolerance_m": 2.0},
            )
        )


def test_configured_sensor_threshold_requires_matching_invalidation():
    _, _, _, candidate_set = operation_fixture()
    gate = ShadowExecutionLeaseGate(candidate_set)
    configuration = {
        "position_tolerance_m": 0.02,
        "maximum_tracked_pose_error_m": 0.04,
    }

    with pytest.raises(WorldEffectExecutionLeaseError, match="requires invalidation"):
        gate.dispatch(
            proposal(candidate_set, tool_configuration=configuration)
        )

    accepted = gate.dispatch(
        proposal(
            candidate_set,
            tool_configuration=configuration,
            invalidation_conditions=invalidations(include_tracked_pose=True),
        )
    )
    assert "scene.tracked_pose_error_exceeded" in {
        item.condition_id for item in accepted.invalidation_conditions
    }


def test_prompt_exposes_configuration_but_forbids_dispatch_arguments():
    _, _, _, candidate_set = operation_fixture()
    prompt = build_shadow_execution_lease_prompt(
        instruction="Clean the table",
        candidate_set=candidate_set,
    )
    lowered = prompt.lower()

    assert "geometry-grounded shadow execution lease" in lowered
    assert "event-or-completion" in lowered
    assert "can cover many local runtime steps" in lowered
    assert "does not issue a lease" in lowered
    assert "does not issue a lease, bind a\nhandler, call a tool, or dispatch" in lowered
    assert "do not provide a target pose, pose\ndelta, trajectory" in lowered
    assert "omit it when require_contact is\nfalse or absent" in lowered
    assert '"execution_authority": false' in lowered


def test_runner_wires_lease_after_operation_and_before_shadow_boundary():
    source = (
        Path(__file__).parents[1]
        / "scripts"
        / "run_gemini_robotics_robolab.py"
    ).read_text()
    operation_gate = source.index("WorldEffectOperationGate(")
    lease_candidates = source.index(
        "build_shadow_execution_lease_candidates(", operation_gate
    )
    lease_prompt = source.index(
        "build_shadow_execution_lease_prompt(", lease_candidates
    )
    lease_gate = source.index("ShadowExecutionLeaseGate(", lease_prompt)
    lease_trace = source.index(
        'episode_trace["world_effect_execution_lease_shadow"]', lease_gate
    )
    hard_boundary = source.index("if args_cli.shadow_plan_only:", lease_trace)

    assert (
        operation_gate
        < lease_candidates
        < lease_prompt
        < lease_gate
        < lease_trace
        < hard_boundary
    )
    block = source[lease_candidates:hard_boundary]
    assert '"execution_lease_issued": False' in block
    assert '"handler_bound": False' in block
    assert '"dispatch_enabled": False' in block
    assert '"motion_authority": False' in block
    assert '"execution_authority": False' in block
    assert "_execute_adaptive_stage(" not in block
    assert "actuator_transition_handler(" not in block
