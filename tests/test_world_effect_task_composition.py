from pathlib import Path

import pytest

from scripts.world_effect_composed_sequence import (
    ComposedToolCallDraft,
    GeometryDriftTolerance,
    rebind_composed_tool_call_to_fresh_interaction_relation,
    rebind_loaded_motion_targets_to_fresh_attachment,
)

from scripts.world_effect_task_composition import (
    WORLD_EFFECT_TASK_COMPOSITION_SCHEMA_VERSION,
    WorldEffectTaskCompositionDraft,
    WorldEffectTaskCompositionError,
    bind_model_tool_calls_to_runtime_candidates,
    build_world_effect_task_composition_prompt,
    materialize_missing_task_composition_trace_reason,
    resolve_model_grounding_aliases,
    strip_model_materialized_motion_pose_fields,
    tighten_model_geometry_drift_tolerances,
    world_effect_task_composition_json_schema,
)


def payload():
    return {
        "schema_version": WORLD_EFFECT_TASK_COMPOSITION_SCHEMA_VERSION,
        "decision": "propose_task_plan",
        "world_intent": {"schema_version": "world-intent.v1"},
        "world_goal_graph": {"schema_version": "world-goal-graph.v1"},
        "scope_membership": {
            "instruction_scope": "specific",
            "feasibility_independent": True,
            "decisions": [],
            "confidence": 1.0,
            "reason": "Exact observed scope.",
        },
        "goal_activation": {
            "decision": "select_goal",
            "goal_id": "cube_in_bin",
            "capability_id": "world_relation.realize_inside",
            "confidence": 1.0,
            "reason": "Dependency-ready goal.",
        },
        "provider_selection": {
            "decision": "select_provider",
            "provider_id": "transport.reversible_attachment",
            "confidence": 1.0,
            "reason": "Compatible provider.",
        },
        "tool_sequence": {
            "decision": "propose_sequence",
            "tool_calls": [{"call_id": "approach"}],
            "confidence": 1.0,
            "reason": "Compose the full task.",
        },
        "confidence": 1.0,
        "reason": "One coherent task plan.",
    }


def test_task_composition_preserves_all_nested_drafts_without_authority():
    draft = WorldEffectTaskCompositionDraft.from_mapping(
        payload(), maximum_tool_calls=8
    )
    rendered = draft.to_dict()
    assert rendered["goal_activation"]["goal_id"] == "cube_in_bin"
    assert rendered["provider_selection"]["provider_id"] == (
        "transport.reversible_attachment"
    )
    assert len(rendered["tool_sequence"]["tool_calls"]) == 1
    assert not rendered["execution_authority"]


def test_task_composition_rejects_queue_over_runtime_budget():
    raw = payload()
    raw["tool_sequence"]["tool_calls"] = [{"call_id": str(i)} for i in range(3)]
    with pytest.raises(WorldEffectTaskCompositionError, match="operation budget"):
        WorldEffectTaskCompositionDraft.from_mapping(raw, maximum_tool_calls=2)


def test_task_composition_prompt_requires_one_call_and_local_gate_materialization():
    prompt = build_world_effect_task_composition_prompt(
        instruction="Put the cube in the bin.",
        inventory={"entities": []},
        predicate_advertisement={},
        capability_advertisement={},
        provider_advertisement={},
        runtime_effect_tools=[],
        grounding_catalog={},
        execution_context={},
        maximum_tool_calls=8,
    )
    assert "ONE JSON response" in prompt
    assert "inject fresh" in prompt
    assert "one\nsingle-use permit per call" in prompt
    assert "ask again only if sensor or execution" in prompt
    assert "Never echo target_position_m" in prompt


def test_task_composition_schema_bounds_tool_calls():
    schema = world_effect_task_composition_json_schema(5)
    assert schema["properties"]["tool_sequence"]["properties"]["tool_calls"]["maxItems"] == 5
    call_schema = schema["properties"]["tool_sequence"]["properties"][
        "tool_calls"
    ]["items"]
    assert call_schema["additionalProperties"] is False
    assert "geometry_drift_tolerance" in call_schema["required"]
    assert "orientation_alignment_id" in call_schema["required"]
    assert call_schema["allOf"][0]["then"]["oneOf"]


def test_guarded_runner_reuses_one_bundle_at_each_initial_planning_stage():
    source = (
        Path(__file__).parents[1]
        / "scripts"
        / "run_gemini_robotics_robolab.py"
    ).read_text()
    assert "_reason_and_materialize_world_effect_task_composition(" in source
    assert source.count('"bundled_task_composition"') >= 7
    assert '"model_call_count": 1' in source
    assert '"sequence_decision"' in source
    assert "composed_tool_sequence = (" in source


def test_absolute_motion_pose_fields_are_removed_before_anchored_gate():
    calls, removed = strip_model_materialized_motion_pose_fields(
        [
            {
                "tool_family": "motion",
                "invocation_arguments": {
                    "target_position_m": [1.0, 2.0, 3.0],
                    "target_quaternion_wxyz": [1.0, 0.0, 0.0, 0.0],
                    "ordered_waypoints": [
                        {
                            "position_anchor_id": "cube.center",
                            "target_quaternion_wxyz": [1.0, 0.0, 0.0, 0.0],
                        }
                    ],
                },
            },
            {
                "tool_family": "actuator",
                "invocation_arguments": {"target_position_m": 1.0},
            },
        ]
    )
    motion_arguments = calls[0]["invocation_arguments"]
    assert "target_position_m" not in motion_arguments
    assert "target_quaternion_wxyz" not in motion_arguments
    assert "target_quaternion_wxyz" not in motion_arguments["ordered_waypoints"][0]
    assert calls[1]["invocation_arguments"]["target_position_m"] == 1.0
    assert len(removed) == 3


def test_grounding_aliases_are_restricted_to_same_entity_advertisement():
    calls, replacements = resolve_model_grounding_aliases(
        [
            {
                "tool_family": "motion",
                "target_entity_ids": ["blue_block", "grey_bin"],
                "position_anchor_id": "blue_block.top_center",
                "orientation_alignment_id": "principal_axes_base.0",
                "invocation_arguments": {},
            }
        ],
        {
            "position_anchors": [
                {
                    "anchor_id": "blue_block.visible_aabb_top_center",
                    "entity_id": "blue_block",
                },
                {"anchor_id": "grey_bin.center", "entity_id": "grey_bin"},
            ],
            "orientation_axes": [
                {
                    "orientation_alignment_id": "blue_block.principal_axes_base.1",
                    "entity_id": "blue_block",
                },
                {
                    "orientation_alignment_id": "grey_bin.principal_axes_base.0",
                    "entity_id": "grey_bin",
                },
            ],
        },
    )
    assert calls[0]["position_anchor_id"] == (
        "blue_block.visible_aabb_top_center"
    )
    assert calls[0]["orientation_alignment_id"] == (
        "blue_block.principal_axes_base.1"
    )
    assert all(item["same_entity_only"] for item in replacements)


def test_only_missing_envelope_reason_is_materialized_from_nested_reason():
    raw = payload()
    del raw["reason"]
    normalized, changed = materialize_missing_task_composition_trace_reason(raw)
    assert changed
    assert normalized["reason"] == raw["tool_sequence"]["reason"]
    draft = WorldEffectTaskCompositionDraft.from_mapping(
        normalized,
        maximum_tool_calls=8,
    )
    assert draft.reason == raw["tool_sequence"]["reason"]


def test_geometry_drift_is_only_tightened_to_fresh_target_limit():
    calls, tightenings = tighten_model_geometry_drift_tolerances(
        [
            {
                "target_entity_ids": ["blue_block", "grey_bin"],
                "geometry_drift_tolerance": {
                    "maximum_center_shift_m": 0.05,
                    "maximum_extent_change_fraction": 0.2,
                },
            }
        ],
        {
            "geometry_drift_limits": [
                {"entity_id": "blue_block", "maximum_center_shift_m": 0.041},
                {"entity_id": "grey_bin", "maximum_center_shift_m": 0.08},
            ]
        },
    )
    assert calls[0]["geometry_drift_tolerance"][
        "maximum_center_shift_m"
    ] == 0.041
    assert calls[0]["geometry_drift_tolerance"][
        "maximum_extent_change_fraction"
    ] == 0.2
    assert tightenings[0]["tightened"] is True


def test_tool_call_is_bound_to_exact_runtime_requirement_tuple():
    calls, bindings = bind_model_tool_calls_to_runtime_candidates(
        [
            {
                "requirement_id": "wrong_requirement",
                "tool_id": "sensor.rgbd_scene_geometry",
                "tool_family": "sensor",
                "semantic_effect_id": "verify.scene",
            },
            {
                "requirement_id": "reversible_entity_attachment",
                "tool_id": "binary_end_effector_clamp",
                "tool_family": "actuator",
                "semantic_effect_id": "entity_attachment.maintain",
            },
        ],
        [
            {
                "requirement_id": "fresh_scene_geometry",
                "tool_id": "sensor.rgbd_scene_geometry",
                "tool_family": "sensor",
                "semantic_effect_id": None,
            },
            {
                "requirement_id": "observation_bound_spatial_motion",
                "tool_id": "bounded_dls_ik",
                "tool_family": "motion",
                "semantic_effect_id": None,
            },
        ],
    )
    assert calls[0]["requirement_id"] == "fresh_scene_geometry"
    assert calls[0]["semantic_effect_id"] is None
    assert len(calls) == 1
    assert bindings[0]["binding_source"] == (
        "active_runtime_operation_candidates"
    )
    assert bindings[-1]["action"] == "dropped_unbound_future_draft"


def test_pending_interaction_call_rebinds_to_fresh_rgbd_relation_without_model():
    step = ComposedToolCallDraft(
        call_id="align",
        requirement_id="observation_bound_spatial_motion",
        tool_id="bounded_dls_ik",
        tool_family="motion",
        semantic_effect_id=None,
        purpose="establish_precondition",
        target_entity_ids=("blue_block",),
        desired_outcome="Align for acquisition.",
        stop_condition="Fresh interaction relation is established.",
        tool_configuration={"require_interaction_relation": True},
        geometry_drift_tolerance=GeometryDriftTolerance(0.04, 0.1),
        position_anchor_id="blue_block.center",
        interaction_offset_from_anchor_m=(0.0, 0.0, 0.02),
        orientation_alignment_id="blue_block.axis.0",
        invocation_arguments={},
        expected_state_change="Controlled frame aligns with the object.",
        reason="Prepare the grasp.",
    )
    rebound, trace = rebind_composed_tool_call_to_fresh_interaction_relation(
        step,
        {
            "two_pad_grasp_alignment": {
                "available": True,
                "object_center_inside_full_grasp_corridor": False,
                "corrective_motion_grounding_contract": {
                    "required_terminal_position_anchor_id": "blue_block.center",
                    "required_terminal_interaction_offset_from_anchor_m": [
                        0.0,
                        0.0,
                        0.0235,
                    ],
                    "maximum_terminal_position_error_m": 0.003,
                },
            }
        },
    )
    assert rebound.interaction_offset_from_anchor_m == (0.0, 0.0, 0.0235)
    assert rebound.tool_configuration["position_tolerance_m"] == 0.003
    assert trace["model_called"] is False


def test_loaded_motion_rebinds_fresh_retained_attachment_as_effect_target():
    step = ComposedToolCallDraft(
        call_id="transport",
        requirement_id="observation_bound_spatial_motion",
        tool_id="bounded_dls_ik",
        tool_family="motion",
        semantic_effect_id=None,
        purpose="realize_effect",
        target_entity_ids=("grey_bin",),
        desired_outcome="Carry the object above the receptacle.",
        stop_condition="The carried object clears the receptacle.",
        tool_configuration={
            "require_contact": True,
            "tracked_object_id": "blue_block",
            "minimum_observed_clearance_m": 0.0,
        },
        geometry_drift_tolerance=GeometryDriftTolerance(0.04, 0.1),
        position_anchor_id="grey_bin.center",
        interaction_offset_from_anchor_m=(0.0, 0.0, 0.1),
        orientation_alignment_id="grey_bin.axis.0",
        invocation_arguments={},
        expected_state_change="The object moves with the gripper.",
        reason="Continue the retained transport.",
    )
    rebound, trace = rebind_loaded_motion_targets_to_fresh_attachment(
        step,
        {
            "entities": [
                {"entity_id": "blue_block"},
                {"entity_id": "grey_bin"},
            ],
            "world_effect_continuation_evidence": {
                "retained_contact_supported": True,
                "attachment_entity_ids": ["blue_block"],
            },
        },
    )

    assert rebound.target_entity_ids == ("grey_bin", "blue_block")
    assert rebound.tool_configuration["tracked_object_id"] == "blue_block"
    assert trace["model_called"] is False


def test_loaded_motion_does_not_claim_attachment_without_retained_contact():
    step = ComposedToolCallDraft(
        call_id="transport",
        requirement_id="observation_bound_spatial_motion",
        tool_id="bounded_dls_ik",
        tool_family="motion",
        semantic_effect_id=None,
        purpose="realize_effect",
        target_entity_ids=("grey_bin",),
        desired_outcome="Carry the object above the receptacle.",
        stop_condition="The carried object clears the receptacle.",
        tool_configuration={"require_contact": True},
        geometry_drift_tolerance=GeometryDriftTolerance(0.04, 0.1),
        position_anchor_id="grey_bin.center",
        interaction_offset_from_anchor_m=(0.0, 0.0, 0.1),
        orientation_alignment_id="grey_bin.axis.0",
        invocation_arguments={},
        expected_state_change="The object moves with the gripper.",
        reason="Continue the retained transport.",
    )
    rebound, trace = rebind_loaded_motion_targets_to_fresh_attachment(
        step,
        {
            "entities": [
                {"entity_id": "blue_block"},
                {"entity_id": "grey_bin"},
            ],
            "world_effect_continuation_evidence": {
                "retained_contact_supported": False,
                "attachment_entity_ids": ["blue_block"],
            },
        },
    )

    assert rebound is step
    assert trace is None
