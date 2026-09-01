from dataclasses import replace
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
    summarize_world_effect_operation_history,
)
from scripts.world_effect_provider_registry import RuntimeToolCapability
from scripts.world_effect_composed_sequence import (
    WORLD_EFFECT_COMPOSED_SEQUENCE_SCHEMA_VERSION,
    ComposedToolSequenceGate,
    WorldEffectComposedSequenceError,
    build_composed_tool_sequence_candidates,
    build_composed_tool_sequence_prompt,
)
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
                        "properties": {
                            "state": {
                                "type": "string",
                                "enum": ["engage", "disengage"],
                            }
                        },
                        "required": ["state"],
                    },
                    "configuration_schema": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {},
                    },
                    "semantic_command_bindings": {
                        "entity_attachment.acquire": {"state": "engage"},
                        "entity_attachment.release": {"state": "disengage"},
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


def composed_sequence_candidates(
    *,
    maximum_tool_calls=6,
    grasp_corridor_aligned=True,
    retained_contact_supported=False,
    alignment_object_runtime_id="red_block",
):
    _, _, instance = planning_instance()
    scene = inventory()
    scene["entities"][0]["geometry"] = {
        "center_base_m": [0.50, 0.20, 0.04],
        "visible_aabb_min_base_m": [0.47, 0.17, 0.01],
        "visible_aabb_max_base_m": [0.53, 0.23, 0.07],
        "visible_extent_base_m": [0.06, 0.06, 0.06],
        "oriented_footprint_axes_base": [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]],
    }
    scene["entities"][1]["geometry"] = {
        "center_base_m": [0.42, -0.24, 0.05],
        "visible_aabb_min_base_m": [0.30, -0.36, 0.0],
        "visible_aabb_max_base_m": [0.54, -0.12, 0.10],
        "visible_extent_base_m": [0.24, 0.24, 0.10],
        "oriented_footprint_axes_base": [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]],
    }
    operation_candidates = build_world_effect_operation_candidates(
        instance, scene
    )
    candidates = build_composed_tool_sequence_candidates(
        instance=instance,
        operation_candidates=operation_candidates,
        inventory=scene,
        execution_context={
            "controlled_frame": {
                "position_m": [0.3, 0.0, 0.4],
                "quaternion_wxyz": [1.0, 0.0, 0.0, 0.0],
            },
            "interaction_frame": {
                "contact_center_local_m": [0.0, 0.0, 0.1],
                "closing_axis_local": [1.0, 0.0, 0.0],
            },
            "current_contact": {"touch": False},
            "retained_contact_supported": retained_contact_supported,
            "two_pad_grasp_alignment": {
                "available": True,
                "object_center_inside_full_grasp_corridor": (
                    grasp_corridor_aligned
                ),
                "corrective_motion_grounding_contract": {
                    "entity_id": alignment_object_runtime_id,
                    "required_terminal_position_anchor_id": (
                        f"{alignment_object_runtime_id}.center"
                    ),
                    "required_terminal_interaction_offset_from_anchor_m": [
                        0.0,
                        0.0,
                        0.0,
                    ],
                    "maximum_terminal_position_error_m": 0.004,
                },
            },
        },
        maximum_tool_calls=maximum_tool_calls,
    )
    return scene, candidates


def composed_call(
    *,
    call_id,
    requirement_id,
    tool_id,
    tool_family,
    semantic_effect_id,
    invocation_arguments,
    position_anchor_id=None,
    interaction_offset_from_anchor_m=(),
    orientation_alignment_id=None,
    tool_configuration=None,
):
    return {
        "call_id": call_id,
        "requirement_id": requirement_id,
        "tool_id": tool_id,
        "tool_family": tool_family,
        "semantic_effect_id": semantic_effect_id,
        "purpose": "establish_precondition",
        "target_entity_ids": ["red_block"],
        "desired_outcome": f"{call_id} completed",
        "stop_condition": f"{call_id} observed",
        "tool_configuration": dict(tool_configuration or {}),
        "geometry_drift_tolerance": {
            "maximum_center_shift_m": 0.01,
            "maximum_extent_change_fraction": 0.2,
        },
        "position_anchor_id": position_anchor_id,
        "interaction_offset_from_anchor_m": list(
            interaction_offset_from_anchor_m
        ),
        "orientation_alignment_id": orientation_alignment_id,
        "invocation_arguments": invocation_arguments,
        "expected_state_change": f"expected {call_id} state",
        "reason": f"{call_id} advances the selected goal",
    }


def test_composed_sequence_accepts_many_tool_calls_from_one_model_response():
    _, candidates = composed_sequence_candidates(maximum_tool_calls=6)
    payload = {
        "schema_version": WORLD_EFFECT_COMPOSED_SEQUENCE_SCHEMA_VERSION,
        "observation_id": candidates.observation_id,
        "decision": "propose_sequence",
        "goal_id": candidates.goal_id,
        "tool_calls": [
            composed_call(
                call_id="align",
                requirement_id="observation_bound_spatial_motion",
                tool_id="spatial_motion",
                tool_family="motion",
                semantic_effect_id=None,
                invocation_arguments={},
                position_anchor_id="red_block.center",
                interaction_offset_from_anchor_m=[0.0, 0.0, 0.05],
                orientation_alignment_id=(
                    "red_block.oriented_footprint_axes_base.0"
                ),
            ),
            composed_call(
                call_id="acquire",
                requirement_id="reversible_entity_attachment",
                tool_id="reversible_attachment",
                tool_family="actuator",
                semantic_effect_id="entity_attachment.acquire",
                invocation_arguments={"state": "engage"},
            ),
            composed_call(
                call_id="release",
                requirement_id="reversible_entity_attachment",
                tool_id="reversible_attachment",
                tool_family="actuator",
                semantic_effect_id="entity_attachment.release",
                invocation_arguments={"state": "disengage"},
            ),
        ],
        "confidence": 0.9,
        "reason": "The full foreseeable composition is grounded.",
    }

    decision = ComposedToolSequenceGate(candidates).dispatch(payload)

    assert len(decision.tool_calls) == 3
    assert decision.tool_calls[0].tool_family == "motion"
    assert decision.tool_calls[1].invocation_arguments == {"state": "engage"}
    assert decision.to_dict()["queue_authority"] == (
        "pending_fresh_evidence_per_call"
    )
    assert not decision.to_dict()["execution_authority"]


def test_composed_sequence_materializes_contact_guard_after_acquire():
    _, candidates = composed_sequence_candidates(maximum_tool_calls=3)
    acquire = composed_call(
        call_id="acquire",
        requirement_id="reversible_entity_attachment",
        tool_id="reversible_attachment",
        tool_family="actuator",
        semantic_effect_id="entity_attachment.acquire",
        invocation_arguments={"state": "engage"},
    )
    transport = composed_call(
        call_id="transport",
        requirement_id="observation_bound_spatial_motion",
        tool_id="spatial_motion",
        tool_family="motion",
        semantic_effect_id=None,
        invocation_arguments={},
        position_anchor_id="red_block.center",
        interaction_offset_from_anchor_m=[0.0, 0.0, 0.05],
        orientation_alignment_id=(
            "red_block.oriented_footprint_axes_base.0"
        ),
    )
    payload = {
        "schema_version": WORLD_EFFECT_COMPOSED_SEQUENCE_SCHEMA_VERSION,
        "observation_id": candidates.observation_id,
        "decision": "propose_sequence",
        "goal_id": candidates.goal_id,
        "tool_calls": [acquire, transport],
        "confidence": 0.9,
        "reason": "Acquire and retain the entity during transport.",
    }

    accepted = ComposedToolSequenceGate(candidates).dispatch(payload)
    assert accepted.tool_calls[1].tool_configuration["require_contact"] is True


def test_composed_sequence_materializes_contact_guard_for_fresh_loaded_queue():
    _, candidates = composed_sequence_candidates(
        maximum_tool_calls=1,
        retained_contact_supported=True,
    )
    transport = composed_call(
        call_id="transport",
        requirement_id="observation_bound_spatial_motion",
        tool_id="spatial_motion",
        tool_family="motion",
        semantic_effect_id=None,
        invocation_arguments={},
        position_anchor_id="grey_bin.center",
        interaction_offset_from_anchor_m=[0.0, 0.0, 0.15],
        orientation_alignment_id="grey_bin.oriented_footprint_axes_base.0",
    )
    payload = {
        "schema_version": WORLD_EFFECT_COMPOSED_SEQUENCE_SCHEMA_VERSION,
        "observation_id": candidates.observation_id,
        "decision": "propose_sequence",
        "goal_id": candidates.goal_id,
        "tool_calls": [transport],
        "confidence": 0.9,
        "reason": "Continue a sensor-supported loaded transport.",
    }

    accepted = ComposedToolSequenceGate(candidates).dispatch(payload)
    assert accepted.tool_calls[0].tool_configuration["require_contact"] is True


def test_composed_sequence_removes_impossible_contact_guard_before_acquire():
    _, candidates = composed_sequence_candidates(maximum_tool_calls=2)
    approach = composed_call(
        call_id="approach",
        requirement_id="observation_bound_spatial_motion",
        tool_id="spatial_motion",
        tool_family="motion",
        semantic_effect_id=None,
        invocation_arguments={},
        position_anchor_id="red_block.center",
        interaction_offset_from_anchor_m=[0.0, 0.0, 0.0],
        orientation_alignment_id=(
            "red_block.oriented_footprint_axes_base.0"
        ),
        tool_configuration={"require_contact": True},
    )
    payload = {
        "schema_version": WORLD_EFFECT_COMPOSED_SEQUENCE_SCHEMA_VERSION,
        "observation_id": candidates.observation_id,
        "decision": "propose_sequence",
        "goal_id": candidates.goal_id,
        "tool_calls": [approach],
        "confidence": 0.9,
        "reason": "Approach without claiming an existing attachment.",
    }

    accepted = ComposedToolSequenceGate(candidates).dispatch(payload)
    assert "require_contact" not in accepted.tool_calls[0].tool_configuration


def test_composed_sequence_allows_clearance_before_guarded_grasp_relation():
    _, candidates = composed_sequence_candidates(
        maximum_tool_calls=3,
        grasp_corridor_aligned=False,
    )
    clearance = composed_call(
        call_id="clearance",
        requirement_id="observation_bound_spatial_motion",
        tool_id="spatial_motion",
        tool_family="motion",
        semantic_effect_id=None,
        invocation_arguments={},
        position_anchor_id="red_block.center",
        interaction_offset_from_anchor_m=[0.0, 0.0, 0.10],
        orientation_alignment_id=(
            "red_block.oriented_footprint_axes_base.0"
        ),
    )
    centered = composed_call(
        call_id="centered",
        requirement_id="observation_bound_spatial_motion",
        tool_id="spatial_motion",
        tool_family="motion",
        semantic_effect_id=None,
        invocation_arguments={},
        position_anchor_id="red_block.center",
        interaction_offset_from_anchor_m=[0.0, 0.0, 0.0],
        orientation_alignment_id=(
            "red_block.oriented_footprint_axes_base.0"
        ),
    )
    acquire = composed_call(
        call_id="acquire",
        requirement_id="reversible_entity_attachment",
        tool_id="reversible_attachment",
        tool_family="actuator",
        semantic_effect_id="entity_attachment.acquire",
        invocation_arguments={"state": "engage"},
    )
    payload = {
        "schema_version": WORLD_EFFECT_COMPOSED_SEQUENCE_SCHEMA_VERSION,
        "observation_id": candidates.observation_id,
        "decision": "propose_sequence",
        "goal_id": candidates.goal_id,
        "tool_calls": [clearance, centered, acquire],
        "confidence": 0.9,
        "reason": "Use clearance, establish the relation, then acquire.",
    }

    accepted = ComposedToolSequenceGate(candidates).dispatch(payload)
    assert "require_interaction_relation" not in (
        accepted.tool_calls[0].tool_configuration
    )
    assert accepted.tool_calls[1].tool_configuration[
        "require_interaction_relation"
    ] is True
    assert accepted.tool_calls[1].tool_configuration[
        "position_tolerance_m"
    ] == pytest.approx(0.004)


def test_composed_sequence_materializes_fresh_grasp_relation_before_acquire():
    _, candidates = composed_sequence_candidates(
        maximum_tool_calls=2,
        grasp_corridor_aligned=False,
    )
    imprecise_approach = composed_call(
        call_id="approach",
        requirement_id="observation_bound_spatial_motion",
        tool_id="spatial_motion",
        tool_family="motion",
        semantic_effect_id=None,
        invocation_arguments={},
        position_anchor_id="red_block.center",
        interaction_offset_from_anchor_m=[0.0, 0.0, 0.10],
        orientation_alignment_id=(
            "red_block.oriented_footprint_axes_base.0"
        ),
    )
    acquire = composed_call(
        call_id="acquire",
        requirement_id="reversible_entity_attachment",
        tool_id="reversible_attachment",
        tool_family="actuator",
        semantic_effect_id="entity_attachment.acquire",
        invocation_arguments={"state": "engage"},
    )
    payload = {
        "schema_version": WORLD_EFFECT_COMPOSED_SEQUENCE_SCHEMA_VERSION,
        "observation_id": candidates.observation_id,
        "decision": "propose_sequence",
        "goal_id": candidates.goal_id,
        "tool_calls": [imprecise_approach, acquire],
        "confidence": 0.9,
        "reason": "Approach and acquire using the sensed interaction relation.",
    }

    accepted = ComposedToolSequenceGate(candidates).dispatch(payload)
    approach = accepted.tool_calls[0]
    assert approach.position_anchor_id == "red_block.center"
    assert approach.interaction_offset_from_anchor_m == pytest.approx(
        (0.0, 0.0, 0.0)
    )
    assert approach.tool_configuration["require_interaction_relation"] is True
    assert approach.tool_configuration["position_tolerance_m"] == pytest.approx(
        0.004
    )


def test_composed_sequence_materializes_ordered_terminal_grasp_relation():
    _, candidates = composed_sequence_candidates(
        maximum_tool_calls=2,
        grasp_corridor_aligned=False,
    )
    approach = composed_call(
        call_id="approach_path",
        requirement_id="observation_bound_spatial_motion",
        tool_id="spatial_motion",
        tool_family="motion",
        semantic_effect_id=None,
        invocation_arguments={
            "ordered_waypoints": [
                {
                    "position_anchor_id": "red_block.center",
                    "interaction_offset_from_anchor_m": [0.0, 0.0, 0.15],
                    "orientation_alignment_id": (
                        "red_block.oriented_footprint_axes_base.0"
                    ),
                },
                {
                    "position_anchor_id": "red_block.center",
                    "interaction_offset_from_anchor_m": [0.0, 0.0, 0.10],
                    "orientation_alignment_id": (
                        "red_block.oriented_footprint_axes_base.0"
                    ),
                },
            ]
        },
    )
    acquire = composed_call(
        call_id="acquire",
        requirement_id="reversible_entity_attachment",
        tool_id="reversible_attachment",
        tool_family="actuator",
        semantic_effect_id="entity_attachment.acquire",
        invocation_arguments={"state": "engage"},
    )
    payload = {
        "schema_version": WORLD_EFFECT_COMPOSED_SEQUENCE_SCHEMA_VERSION,
        "observation_id": candidates.observation_id,
        "decision": "propose_sequence",
        "goal_id": candidates.goal_id,
        "tool_calls": [approach, acquire],
        "confidence": 0.9,
        "reason": "Follow a clearance path and acquire.",
    }

    accepted = ComposedToolSequenceGate(candidates).dispatch(payload)
    waypoints = accepted.tool_calls[0].invocation_arguments["ordered_waypoints"]
    assert waypoints[0]["interaction_offset_from_anchor_m"] == [0.0, 0.0, 0.15]
    assert waypoints[-1]["position_anchor_id"] == "red_block.center"
    assert waypoints[-1]["interaction_offset_from_anchor_m"] == [0.0, 0.0, 0.0]
    assert accepted.tool_calls[0].tool_configuration[
        "require_interaction_relation"
    ] is True


def test_composed_sequence_ignores_alignment_for_an_unrelated_scene_role():
    _, candidates = composed_sequence_candidates(
        maximum_tool_calls=2,
        grasp_corridor_aligned=False,
        alignment_object_runtime_id="configured_but_unselected_object",
    )
    approach = composed_call(
        call_id="approach",
        requirement_id="observation_bound_spatial_motion",
        tool_id="spatial_motion",
        tool_family="motion",
        semantic_effect_id=None,
        invocation_arguments={},
        position_anchor_id="red_block.center",
        interaction_offset_from_anchor_m=[0.0, 0.0, 0.10],
        orientation_alignment_id=(
            "red_block.oriented_footprint_axes_base.0"
        ),
    )
    acquire = composed_call(
        call_id="acquire",
        requirement_id="reversible_entity_attachment",
        tool_id="reversible_attachment",
        tool_family="actuator",
        semantic_effect_id="entity_attachment.acquire",
        invocation_arguments={"state": "engage"},
    )
    payload = {
        "schema_version": WORLD_EFFECT_COMPOSED_SEQUENCE_SCHEMA_VERSION,
        "observation_id": candidates.observation_id,
        "decision": "propose_sequence",
        "goal_id": candidates.goal_id,
        "tool_calls": [approach, acquire],
        "confidence": 0.9,
        "reason": "Use only evidence for the selected goal subject.",
    }

    accepted = ComposedToolSequenceGate(candidates).dispatch(payload)
    assert accepted.tool_calls[0].interaction_offset_from_anchor_m == pytest.approx(
        (0.0, 0.0, 0.10)
    )
    assert "require_interaction_relation" not in (
        accepted.tool_calls[0].tool_configuration
    )


def test_composed_sequence_rejects_budget_overflow_and_semantic_mismatch():
    _, candidates = composed_sequence_candidates(maximum_tool_calls=1)
    call = composed_call(
        call_id="acquire",
        requirement_id="reversible_entity_attachment",
        tool_id="reversible_attachment",
        tool_family="actuator",
        semantic_effect_id="entity_attachment.acquire",
        invocation_arguments={"state": "engage"},
    )
    payload = {
        "schema_version": WORLD_EFFECT_COMPOSED_SEQUENCE_SCHEMA_VERSION,
        "observation_id": candidates.observation_id,
        "decision": "propose_sequence",
        "goal_id": candidates.goal_id,
        "tool_calls": [call, {**call, "call_id": "second"}],
        "confidence": 0.9,
        "reason": "Too many calls.",
    }
    with pytest.raises(WorldEffectComposedSequenceError, match="budget"):
        ComposedToolSequenceGate(candidates).dispatch(payload)

    payload["tool_calls"] = [
        {
            **call,
            "invocation_arguments": {"state": "disengage"},
        }
    ]
    with pytest.raises(WorldEffectComposedSequenceError, match="contradicts"):
        ComposedToolSequenceGate(candidates).dispatch(payload)


def test_composed_prompt_requires_longest_queue_and_sensor_invalidated_suffix():
    scene, candidates = composed_sequence_candidates(maximum_tool_calls=6)

    prompt = build_composed_tool_sequence_prompt(
        instruction="Put the red block in the grey bin",
        inventory=scene,
        candidate_set=candidates,
        recent_operation_history={"entries": []},
    )

    assert "in ONE response" in prompt
    assert "Do not return only the first call" in prompt
    assert "discard the unexecuted suffix" in prompt
    assert "without asking the model again" in prompt
    assert "just-in-time validation" in prompt


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


def test_planning_provider_activates_all_compatible_motion_alternatives():
    candidate_set, decision = session()
    candidate = candidate_set.candidates[0]
    bindings = []
    for binding in candidate.requirement_bindings:
        if binding["requirement_id"] != "observation_bound_spatial_motion":
            bindings.append(binding)
            continue
        bindings.append(
            {
                **binding,
                "tool_id": "bounded_dls_ik",
                "activation_status": "active",
                "compatible_tools": [
                    {
                        "tool_id": "bounded_dls_ik",
                        "activation_status": "active",
                    },
                    {
                        "tool_id": "bounded_dls_waypoint_path",
                        "activation_status": "active",
                    },
                ],
            }
        )
    candidate = replace(candidate, requirement_bindings=tuple(bindings))
    candidate_set = replace(candidate_set, candidates=(candidate,))
    active_motion_tools = [
        RuntimeToolCapability(
            tool_id=tool_id,
            tool_family="motion",
            capability_tags=MOTION_TAGS,
            activation_status="active",
            source="active_test_registry",
        )
        for tool_id in ("bounded_dls_ik", "bounded_dls_waypoint_path")
    ]
    tools = [runtime_tools()[0], *active_motion_tools, runtime_tools()[2]]

    instance = build_planning_world_effect_provider_instance(
        candidate_set,
        decision,
        tools,
        catalog(),
    )

    assert {
        item.activated_tool_id
        for item in instance.tool_activations
        if item.requirement_id == "observation_bound_spatial_motion"
    } == {"bounded_dls_ik", "bounded_dls_waypoint_path"}


def test_first_operation_candidates_are_goal_and_observation_bound():
    _, _, instance = planning_instance()
    candidates = build_world_effect_operation_candidates(instance, inventory())

    assert len(candidates.candidates) == 4
    assert candidates.provider_instance_id == instance.instance_id
    assert candidates.related_entity_ids == ("grey_bin", "red_block")
    assert all(not item.to_dict()["dispatch_enabled"] for item in candidates.candidates)

    actuator_candidates = [
        item for item in candidates.candidates if item.tool_family == "actuator"
    ]
    assert {
        item.semantic_effect_id: item.required_invocation_arguments
        for item in actuator_candidates
    } == {
        "entity_attachment.acquire": {"state": "engage"},
        "entity_attachment.release": {"state": "disengage"},
    }


def test_continuation_prompt_treats_retained_occlusion_as_planning_only():
    _, _, instance = planning_instance()
    candidate_set = build_world_effect_operation_candidates(instance, inventory())
    prompt = build_world_effect_operation_prompt(
        instruction="Clean the table",
        inventory=inventory(),
        instance=instance,
        candidate_set=candidate_set,
        execution_context={
            "retained_attachment": {
                "entity_ids": ["red_block"],
                "temporarily_occluded": True,
            }
        },
    )

    assert "planning evidence for continued attachment" in prompt
    assert "not\ngoal-completion evidence" in prompt
    assert "visible destination or support entity" in prompt
    assert "spatial.ordered_waypoints" in prompt
    assert "both a retained source entity\nand a visible destination" in prompt


def test_unretained_engaged_attempt_advertises_only_actuator_recovery():
    _, _, instance = planning_instance()
    scene = inventory()
    scene["world_effect_continuation_evidence"] = {
        "schema_version": "world-effect-continuation-evidence.v1",
        "selected_goal_id": "red-in-bin",
        "attachment_entity_ids": ["red_block"],
        "tracked_present_entity_ids": ["red_block"],
        "tracked_entity_positions_m": {"red_block": [0.5, 0.2, 0.05]},
        "planning_continuation_allowed": True,
        "gripper_engaged": True,
        "retained_contact_supported": False,
        "recovery_actuator_only": True,
        "completion_evidence": False,
        "task_completion_allowed": False,
        "dispatch_enabled": False,
        "motion_authority": False,
        "execution_authority": False,
        "authority_scope": [],
    }

    candidate_set = build_world_effect_operation_candidates(instance, scene)
    prompt = build_world_effect_operation_prompt(
        instruction="Clean the table",
        inventory=scene,
        instance=instance,
        candidate_set=candidate_set,
        execution_context={"current_contact": {"touch": True}},
    )

    assert [item.tool_family for item in candidate_set.candidates] == [
        "actuator"
    ]
    assert candidate_set.candidates[0].semantic_effect_id == (
        "entity_attachment.release"
    )
    assert candidate_set.candidates[0].required_invocation_arguments == {
        "state": "disengage"
    }
    assert "Use the sole advertised reversible actuator" in prompt
    assert "never transport it" in prompt


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


def test_operation_prompt_includes_fresh_post_effect_context_without_authority():
    _, _, instance = planning_instance()
    candidates = build_world_effect_operation_candidates(instance, inventory())
    prompt = build_world_effect_operation_prompt(
        instruction="Clean the table",
        inventory=inventory(),
        instance=instance,
        candidate_set=candidates,
        execution_context={
            "gripper_closed_fraction": 1.0,
            "current_contact": {"touch": True, "net_force_n": 2.5},
            "recent_operation_history": {
                "entries": [
                    {
                        "operation_index": 2,
                        "tool_family": "motion",
                        "purpose": "establish_precondition",
                        "result": {"converged": True},
                    }
                ],
                "consecutive_same_semantic_selection_count": 1,
            },
        },
    )

    assert '"gripper_closed_fraction": 1.0' in prompt
    assert '"touch": true' in prompt
    assert '"consecutive_same_semantic_selection_count": 1' in prompt
    assert "do not repeat a completed precondition" in prompt.lower()
    assert "contact may be created by the advertised actuator" in prompt.lower()
    assert "fully\nbetween both advertised pad planes" in prompt
    assert "inside both transverse\npad-face bounds" in prompt
    assert "corrective_motion_grounding_contract" in prompt
    assert "destination or an obstacle cannot be the\nterminal anchor" in prompt
    assert "grasp-corridor\ncenter" in prompt
    assert "does not call the named tool" in prompt.lower()


def test_operation_history_summarizes_motion_and_actuator_feedback():
    history = summarize_world_effect_operation_history(
        [
            {
                "operation_index": 2,
                "tool_family": "motion",
                "tool_id": "bounded_motion",
                "purpose": "establish_precondition",
                "target_entity_ids": ["red_block"],
                "dispatch": {
                    "runtime_lease_after": {
                        "revocation_reason": None,
                    },
                    "outcome": {
                        "final_lease_state": "consumed",
                        "handler_result": {
                            "execution_report": {
                                "converged": True,
                                "target_error_after_m": 0.004,
                                "target_xyz": [0.5, 0.2, 0.1],
                                "target_quaternion_wxyz": [1.0, 0.0, 0.0, 0.0],
                                "grounding": {
                                    "position_anchor_id": "red_block.center",
                                    "interaction_offset_from_anchor_m": [0, 0, 0.03],
                                },
                            }
                        },
                    }
                },
            },
            {
                "operation_index": 3,
                "tool_family": "actuator",
                "tool_id": "reversible_attachment",
                "purpose": "realize_effect",
                "target_entity_ids": ["red_block"],
                "dispatch": {
                    "outcome": {
                        "final_lease_state": "consumed",
                        "handler_result": {
                            "actuator_report": {
                                "requested_state": "engage",
                                "engaged_before": False,
                                "engaged_after": True,
                                "state_after": {
                                    "current_contact": {
                                        "touch": True,
                                        "net_force_n": 1.2,
                                        "contact_bodies": {"active_body_count": 2},
                                    }
                                },
                            }
                        },
                    }
                },
            },
        ]
    )

    assert len(history["entries"]) == 2
    assert history["entries"][0]["result"]["converged"] is True
    assert history["entries"][0]["result"]["position_anchor_id"] == (
        "red_block.center"
    )
    assert history["entries"][0]["result"]["terminal_target_position_m"] == [
        0.5,
        0.2,
        0.1,
    ]
    assert history["entries"][1]["result"]["requested_state"] == "engage"
    assert history["entries"][1]["result"]["active_contact_body_count_after"] == 2
    assert history["consecutive_same_semantic_selection_count"] == 1
    assert history["execution_authority"] is False


def test_operation_history_exposes_unmaterializable_operation_for_replan():
    history = summarize_world_effect_operation_history(
        [
            {
                "operation_index": 3,
                "planning_status": "operation_replan_required",
                "planning": {
                    "operation_plan": {
                        "candidate_set": {
                            "candidates": [
                                {
                                    "operation_candidate_id": "motion-noop",
                                    "tool_family": "motion",
                                }
                            ]
                        },
                        "decision": {
                            "operation_candidate_id": "motion-noop",
                            "tool_id": "bounded_dls_ik",
                            "purpose": "establish_precondition",
                            "target_entity_ids": ["red_block"],
                            "desired_outcome": "already reached pose",
                        },
                    },
                    "tool_invocation": {
                        "attempts": [
                            {
                                "status": "rejected",
                                "rejection": {
                                    "error_type": "WorldEffectToolInvocationError",
                                    "error": "materialized motion target is already within tolerance",
                                    "evidence": {
                                        "segment_displacement_m": 0.52,
                                        "maximum_segment_displacement_m": 0.35,
                                        "required_segment_count": 2,
                                    },
                                },
                            }
                        ]
                    },
                },
            }
        ]
    )

    entry = history["entries"][0]
    assert entry["planning_status"] == "operation_replan_required"
    assert entry["tool_family"] == "motion"
    assert entry["purpose"] == "establish_precondition"
    assert entry["target_entity_ids"] == ["red_block"]
    assert entry["result"]["invocation_rejection"]["attempts_exhausted"] is True
    assert entry["result"]["invocation_rejection"]["evidence"][
        "required_segment_count"
    ] == 2

    _, _, instance = planning_instance()
    prompt = build_world_effect_operation_prompt(
        instruction="Move the red block",
        inventory=inventory(),
        instance=instance,
        candidate_set=build_world_effect_operation_candidates(
            instance, inventory()
        ),
        execution_context={"recent_operation_history": history},
    )
    assert "choose a different advertised operation" in prompt
    assert '"required_segment_count": 2' in prompt


def test_operation_history_preserves_failed_terminal_pose_for_retry_admission():
    history = summarize_world_effect_operation_history(
        [
            {
                "operation_index": 6,
                "tool_family": "motion",
                "target_entity_ids": ["red_block"],
                "dispatch": {
                    "runtime_lease_after": {
                        "revocation_reason": "dispatch.motion_not_converged",
                    },
                    "outcome": {
                        "final_lease_state": "revoked",
                        "handler_result": {
                            "motion_report": {
                                "converged": False,
                                "target_xyz": [0.459, -0.003, 0.211],
                                "target_quaternion_wxyz": [
                                    0.7071,
                                    -0.0236,
                                    0.7063,
                                    0.0258,
                                ],
                                "target_error_after_m": 0.055,
                            }
                        },
                    },
                },
            }
        ]
    )

    result = history["entries"][0]["result"]
    assert result["converged"] is False
    assert result["revocation_reason"] == "dispatch.motion_not_converged"
    assert result["terminal_target_position_m"] == [0.459, -0.003, 0.211]


def test_runner_checks_failed_motion_retry_before_issuing_continuation_lease():
    source = (
        Path(__file__).parents[1]
        / "scripts"
        / "run_gemini_robotics_robolab.py"
    ).read_text()
    start = source.index("def _plan_guarded_world_effect_continuation(")
    end = source.index(
        "def _dispatch_guarded_world_effect_continuation(", start
    )
    planner = source[start:end]

    retry_gate = planner.index(
        "compare_motion_invocation_to_recent_failures("
    )
    lease_issue = planner.index("issue_world_effect_runtime_lease(")
    assert retry_gate < lease_issue
    assert '"error_type": "RepeatedFailedMotionTarget"' in planner
    assert '"execution_authority": False' in planner[retry_gate:lease_issue]


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
