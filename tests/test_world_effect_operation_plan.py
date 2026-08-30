from pathlib import Path

import pytest

from scripts.world_effect_operation_plan import (
    WORLD_EFFECT_OPERATION_SCHEMA_VERSION,
    PlanningToolFactory,
    PlanningToolFactoryCatalog,
    WorldEffectOperationGate,
    WorldEffectOperationPlanError,
    build_planning_world_effect_provider_instance,
    build_world_effect_operation_candidates,
    build_world_effect_operation_prompt,
)
from scripts.world_effect_provider_registry import RuntimeToolCapability
from scripts.world_effect_session import (
    WorldEffectSessionCandidate,
    WorldEffectSessionCandidateSet,
    WorldEffectSessionDecision,
)


MOTION_TAGS = (
    "spatial.pose_target",
    "motion.observation_bound",
    "motion.invalidation_feedback",
)
ATTACHMENT_TAGS = (
    "entity_attachment.acquire",
    "entity_attachment.release",
    "actuation.observation_bound",
)


def inventory():
    return {
        "schema_version": "semantic-scene-inventory.v1",
        "available": True,
        "source": "fresh_test_rgbd",
        "frame": "robot_root",
        "entities": [
            {
                "entity_id": "red_block",
                "label": "red block",
                "observation_status": "visible_rgbd",
                "geometry": {"visible_aabb_min_base_m": [0.5, 0.2, 0.0]},
            },
            {
                "entity_id": "grey_bin",
                "label": "grey bin",
                "observation_status": "visible_rgbd",
                "geometry": {"visible_aabb_min_base_m": [0.1, 0.1, 0.0]},
            },
        ],
        "role_bindings": [],
        "limitations": [],
    }


def session():
    candidate = WorldEffectSessionCandidate(
        candidate_id="effect-session:test",
        activation_observation_id="goal-activation:test",
        graph_id="clean-table",
        membership_lease_id="membership:test",
        goal_id="red-in-bin",
        world_capability_id="world_relation.realize_inside",
        provider_id="transport.reversible_attachment",
        desired_state=(
            {
                "subject_id": "red_block",
                "attribute": "inside",
                "operator": "==",
                "value": True,
                "reference_id": "grey_bin",
            },
        ),
        requirement_bindings=(
            {
                "requirement_id": "fresh_scene_geometry",
                "required_capability_tags": ["scene.geometry.rgbd"],
                "tool_id": "sensor.rgbd",
                "activation_status": "active",
            },
            {
                "requirement_id": "observation_bound_spatial_motion",
                "required_capability_tags": list(MOTION_TAGS),
                "tool_id": "factory.spatial",
                "activation_status": "factory_available",
            },
            {
                "requirement_id": "reversible_entity_attachment",
                "required_capability_tags": list(ATTACHMENT_TAGS),
                "tool_id": "factory.attach",
                "activation_status": "factory_available",
            },
        ),
        inactive_requirement_ids=(
            "observation_bound_spatial_motion",
            "reversible_entity_attachment",
        ),
        tool_binding_active=False,
    )
    candidate_set = WorldEffectSessionCandidateSet(
        observation_id="effect-session-observation:test",
        activation_observation_id="goal-activation:test",
        graph_id="clean-table",
        membership_lease_id="membership:test",
        goal_id="red-in-bin",
        world_capability_id="world_relation.realize_inside",
        candidates=(candidate,),
    )
    decision = WorldEffectSessionDecision(
        observation_id=candidate_set.observation_id,
        decision="select_provider",
        candidate_id=candidate.candidate_id,
        provider_id=candidate.provider_id,
        confidence=0.9,
        reason="The provider requirements have runtime factory bindings.",
    )
    return candidate_set, decision


def runtime_tools():
    return [
        RuntimeToolCapability(
            tool_id="sensor.rgbd",
            tool_family="sensor",
            capability_tags=("scene.geometry.rgbd",),
            activation_status="active",
            source="fresh_test_rgbd",
        ),
        RuntimeToolCapability(
            tool_id="factory.spatial",
            tool_family="motion",
            capability_tags=MOTION_TAGS,
            activation_status="factory_available",
            source="test_factory_catalog",
        ),
        RuntimeToolCapability(
            tool_id="factory.attach",
            tool_family="actuator",
            capability_tags=ATTACHMENT_TAGS,
            activation_status="factory_available",
            source="test_factory_catalog",
        ),
    ]


def catalog(*, motion_id="spatial_motion", actuator_id="reversible_attachment"):
    result = PlanningToolFactoryCatalog()
    result.register(
        PlanningToolFactory(
            factory_tool_id="factory.spatial",
            tool_family="motion",
            capability_tags=MOTION_TAGS,
            activator=lambda: (
                {
                    "executor_id": motion_id,
                    "tool_name": "execute_spatial_motion",
                    "tool_family": "motion",
                    "capability_tags": list(MOTION_TAGS),
                    "configuration_schema": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {},
                    },
                },
            ),
        )
    )
    result.register(
        PlanningToolFactory(
            factory_tool_id="factory.attach",
            tool_family="actuator",
            capability_tags=ATTACHMENT_TAGS,
            activator=lambda: (
                {
                    "executor_id": actuator_id,
                    "tool_name": "execute_reversible_attachment",
                    "tool_family": "actuator",
                    "capability_tags": list(ATTACHMENT_TAGS),
                    "command_schema": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {},
                    },
                    "configuration_schema": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {},
                    },
                },
            ),
        )
    )
    return result


def planning_instance(**catalog_kwargs):
    candidate_set, decision = session()
    instance = build_planning_world_effect_provider_instance(
        candidate_set,
        decision,
        runtime_tools(),
        catalog(**catalog_kwargs),
    )
    return candidate_set, decision, instance


def test_only_selected_provider_factories_publish_declarative_specs():
    _, _, instance = planning_instance()
    serialized = instance.to_dict()

    assert instance.planning_ready
    assert instance.provider_id == "transport.reversible_attachment"
    assert {item.activated_tool_id for item in instance.tool_activations} == {
        "sensor.rgbd",
        "spatial_motion",
        "reversible_attachment",
    }
    assert sum(item.factory_instantiated for item in instance.tool_activations) == 2
    assert serialized["planning_provider_instantiated"]
    assert not serialized["execution_provider_created"]
    assert not serialized["handler_bound"]
    assert not serialized["dispatch_enabled"]
    assert not serialized["motion_authority"]
    assert not serialized["execution_authority"]


def test_runtime_factory_outputs_change_without_changing_goal_contract():
    _, _, instance = planning_instance(
        motion_id="whole_body_reach",
        actuator_id="vacuum_acquire_release",
    )

    assert instance.goal_id == "red-in-bin"
    assert instance.related_entity_ids() == ("grey_bin", "red_block")
    assert {item.activated_tool_id for item in instance.tool_activations} == {
        "sensor.rgbd",
        "whole_body_reach",
        "vacuum_acquire_release",
    }


def test_first_operation_candidates_are_goal_and_observation_bound():
    _, _, instance = planning_instance()
    candidates = build_world_effect_operation_candidates(instance, inventory())

    assert len(candidates.candidates) == 3
    assert candidates.provider_instance_id == instance.instance_id
    assert candidates.related_entity_ids == ("grey_bin", "red_block")
    assert all(not item.to_dict()["dispatch_enabled"] for item in candidates.candidates)


def test_operation_gate_accepts_only_fresh_exact_semantic_proposal():
    _, _, instance = planning_instance()
    candidates = build_world_effect_operation_candidates(instance, inventory())
    candidate = next(
        item for item in candidates.candidates if item.tool_family == "motion"
    )
    payload = {
        "schema_version": WORLD_EFFECT_OPERATION_SCHEMA_VERSION,
        "observation_id": candidates.observation_id,
        "decision": "propose_operation",
        "operation_candidate_id": candidate.operation_candidate_id,
        "requirement_id": candidate.requirement_id,
        "tool_id": candidate.tool_id,
        "purpose": "establish_precondition",
        "target_entity_ids": ["red_block"],
        "desired_outcome": "Interaction geometry is observable and suitable.",
        "stop_condition": "Stop at the next state change and observe again.",
        "confidence": 0.86,
        "reason": "Fresh geometry is available.",
    }

    accepted = WorldEffectOperationGate(candidates).dispatch(payload)
    assert accepted.tool_id == "spatial_motion"
    assert not accepted.to_dict()["tool_called"]

    stale = dict(payload, observation_id="stale:observation")
    with pytest.raises(WorldEffectOperationPlanError, match="stale"):
        WorldEffectOperationGate(candidates).dispatch(stale)

    invented = dict(payload, tool_id="invented_tool")
    with pytest.raises(WorldEffectOperationPlanError, match="not advertised"):
        WorldEffectOperationGate(candidates).dispatch(invented)

    unrelated = dict(payload, target_entity_ids=["unrelated_object"])
    with pytest.raises(WorldEffectOperationPlanError, match="related entity"):
        WorldEffectOperationGate(candidates).dispatch(unrelated)


def test_operation_prompt_requests_outcomes_without_dispatch_details():
    _, _, instance = planning_instance()
    candidates = build_world_effect_operation_candidates(instance, inventory())
    prompt = build_world_effect_operation_prompt(
        instruction="Clean the table",
        inventory=inventory(),
        instance=instance,
        candidate_set=candidates,
    )
    lowered = prompt.lower()

    assert "red-in-bin" in prompt
    assert "red_block" in prompt
    assert "grey_bin" in prompt
    assert "first semantic operation" in lowered
    assert "no handler is bound" in lowered
    assert "dispatch is disabled" in lowered
    assert "do not\noutput tool arguments, poses, trajectories" in lowered
    assert '"execution_authority": false' in lowered


def test_runner_wires_operation_plan_after_session_and_before_hard_boundary():
    source = (
        Path(__file__).parents[1]
        / "scripts"
        / "run_gemini_robotics_robolab.py"
    ).read_text()
    session_gate = source.index("WorldEffectSessionGate(")
    provider_instance = source.index(
        "build_planning_world_effect_provider_instance(", session_gate
    )
    operation_candidates = source.index(
        "build_world_effect_operation_candidates(", provider_instance
    )
    operation_prompt = source.index(
        "build_world_effect_operation_prompt(", operation_candidates
    )
    operation_gate = source.index("WorldEffectOperationGate(", operation_prompt)
    operation_trace = source.index(
        'episode_trace["world_effect_operation_plan_shadow"]', operation_gate
    )
    hard_boundary = source.index("if args_cli.shadow_plan_only:", operation_trace)

    assert (
        session_gate
        < provider_instance
        < operation_candidates
        < operation_prompt
        < operation_gate
        < operation_trace
        < hard_boundary
    )
    block = source[provider_instance:hard_boundary]
    assert '"planning_provider_instantiated": True' in block
    assert '"execution_provider_created": False' in block
    assert '"handler_bound": False' in block
    assert '"dispatch_enabled": False' in block
    assert '"motion_authority": False' in block
    assert '"execution_authority": False' in block
    assert "_execute_adaptive_stage(" not in block
    assert "actuator_transition_handler(" not in block
