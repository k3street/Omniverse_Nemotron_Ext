from pathlib import Path

import pytest

from scripts.world_effect_execution_lease import (
    GeometryEvidenceBinding,
    LeaseInvalidationCandidate,
    LeaseInvalidationSelection,
    ShadowExecutionLeaseCandidate,
    ShadowExecutionLeaseCandidateSet,
    ShadowExecutionLeaseDecision,
)
from scripts.world_effect_operation_plan import (
    PlanningToolActivation,
    PlanningWorldEffectProviderInstance,
)
from scripts.world_effect_tool_invocation import (
    RUNTIME_TOOL_OBSERVATION_SCHEMA_VERSION,
    WORLD_EFFECT_TOOL_INVOCATION_SCHEMA_VERSION,
    ShadowToolInvocationGate,
    WorldEffectToolInvocationError,
    build_shadow_tool_invocation_candidates,
    build_shadow_tool_invocation_prompt,
)


INVOCATION_SCHEMA = {
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
    "required": ["target_position_m", "target_quaternion_wxyz"],
    "x-runtime-constraints": {
        "coordinate_frame": "robot_root",
        "workspace_min_m": [-0.75, -0.75, 0.02],
        "workspace_max_m": [0.90, 0.90, 1.40],
        "maximum_displacement_m": 0.80,
        "maximum_grounding_offset_m": 0.35,
        "maximum_alignment_error_deg": 15.0,
    },
}


def fixture(*, tool_id="spatial_motion"):
    geometry = GeometryEvidenceBinding(
        entity_id="red_block",
        observation_status="visible_rgbd",
        geometry_digest="geometry:red",
        geometry={
            "center_base_m": [0.50, 0.20, 0.04],
            "visible_aabb_min_base_m": [0.48, 0.18, 0.02],
            "visible_aabb_max_base_m": [0.52, 0.22, 0.06],
            "visible_extent_base_m": [0.04, 0.04, 0.04],
            "oriented_footprint_axes_base": [
                [0.0, 1.0, 0.0],
                [1.0, 0.0, 0.0],
            ],
            "support_plane_normal_base": [0.0, 0.0, 1.0],
        },
    )
    invalidation_specs = tuple(
        LeaseInvalidationCandidate(
            condition_id=condition_id,
            evidence_source_id=source,
            description="Stop on fresh evidence change.",
            entity_scope=scope,
            parameter_schema={
                "type": "object",
                "additionalProperties": False,
                "properties": {},
                "required": [],
            },
            mandatory=True,
        )
        for condition_id, source, scope in (
            (
                "scene.target_visibility_lost",
                "scene.geometry.rgbd",
                "operation_targets",
            ),
            (
                "scene.target_geometry_drift",
                "scene.geometry.rgbd",
                "operation_targets",
            ),
            (
                "lease.membership_changed",
                "scene.membership_lease",
                "none",
            ),
            (
                "provider.instance_changed",
                "world_effect.provider_session",
                "none",
            ),
        )
    )
    lease_candidate = ShadowExecutionLeaseCandidate(
        candidate_id="execution-lease:test",
        provider_instance_id="planning-provider:test",
        membership_lease_id="membership:test",
        operation_observation_id="effect-operation-observation:test",
        operation_candidate_id="effect-operation:test",
        requirement_id="observation_bound_spatial_motion",
        tool_id=tool_id,
        tool_family="motion",
        purpose="establish_precondition",
        operation_target_entity_ids=("red_block",),
        desired_outcome="Interaction geometry is ready.",
        stop_condition="Stop at the observable interaction geometry.",
        tool_configuration_schema={
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "position_tolerance_m": {"type": "number"},
            },
        },
        geometry_bindings=(geometry,),
        invalidation_candidates=invalidation_specs,
    )
    lease_candidates = ShadowExecutionLeaseCandidateSet(
        observation_id="execution-lease-observation:test",
        inventory_digest="inventory:test",
        provider_instance_id="planning-provider:test",
        candidates=(lease_candidate,),
    )
    invalidation_selections = tuple(
        LeaseInvalidationSelection(
            condition_id=spec.condition_id,
            target_entity_ids=("red_block",)
            if spec.entity_scope == "operation_targets"
            else (),
            parameters={},
        )
        for spec in invalidation_specs
    )
    lease_decision = ShadowExecutionLeaseDecision(
        observation_id=lease_candidates.observation_id,
        decision="propose_lease",
        lease_id="shadow-execution-lease:test",
        candidate_id=lease_candidate.candidate_id,
        provider_instance_id=lease_candidate.provider_instance_id,
        operation_candidate_id=lease_candidate.operation_candidate_id,
        tool_id=tool_id,
        grounding_entity_ids=("red_block",),
        tool_configuration={"position_tolerance_m": 0.02},
        invalidation_conditions=invalidation_selections,
        confidence=0.9,
        reason="Fresh geometry is bound.",
    )
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
                    "configuration_schema": lease_candidate.tool_configuration_schema,
                    "invocation_schema": INVOCATION_SCHEMA,
                },
                factory_instantiated=True,
            ),
        ),
        activation_blockers=(),
    )
    runtime_observation = {
        "schema_version": RUNTIME_TOOL_OBSERVATION_SCHEMA_VERSION,
        "source": "fresh_test_runtime",
        "coordinate_frame": "robot_root",
        "controlled_frame": {
            "position_m": [0.36, 0.0, 0.47],
            "quaternion_wxyz": [0.70710678, 0.0, 0.70710678, 0.0],
        },
        "interaction_frame": {
            "origin_offset_local_m": [0.12, 0.0, 0.0],
            "alignment_axis_local": [0.0, -1.0, 0.0],
            "alignment_relation": "surface_tangent",
        },
    }
    invocation_candidates = build_shadow_tool_invocation_candidates(
        instance,
        lease_candidates,
        lease_decision,
        runtime_observation,
    )
    return (
        instance,
        lease_candidates,
        lease_decision,
        runtime_observation,
        invocation_candidates,
    )


def proposal(candidate_set, **overrides):
    candidate = candidate_set.candidates[0]
    payload = {
        "schema_version": WORLD_EFFECT_TOOL_INVOCATION_SCHEMA_VERSION,
        "observation_id": candidate_set.observation_id,
        "decision": "propose_invocation",
        "candidate_id": candidate.candidate_id,
        "lease_id": candidate.lease_id,
        "tool_id": candidate.tool_id,
        "position_anchor_id": "red_block.visible_aabb_top_center",
        "interaction_offset_from_anchor_m": [0.0, 0.0, 0.05],
        "orientation_alignment_id": (
            "red_block.oriented_footprint_axes_base.0"
        ),
        "invocation_arguments": {
            "target_quaternion_wxyz": [0.70710678, 0.0, 0.70710678, 0.0],
        },
        "acknowledged_invalidation_condition_ids": list(
            candidate.invalidation_condition_ids
        ),
        "confidence": 0.92,
        "reason": "The interaction frame is grounded above the observed target.",
    }
    payload.update(overrides)
    return payload


def test_candidate_uses_runtime_schema_frame_transform_and_rgbd_axes():
    _, _, lease, _, candidates = fixture()
    candidate = candidates.candidates[0]

    assert candidate.lease_id == lease.lease_id
    assert candidate.tool_id == "spatial_motion"
    assert candidate.position_grounding_required
    assert candidate.orientation_grounding_required
    assert candidate.materialized_argument_fields == ("target_position_m",)
    assert "target_position_m" in candidate.invocation_schema["properties"]
    assert "target_position_m" not in candidate.model_argument_schema["properties"]
    assert {item.anchor_id for item in candidate.position_anchors} == {
        "red_block.center",
        "red_block.visible_aabb_top_center",
    }
    assert "red_block.oriented_footprint_axes_base.0" in {
        item.alignment_id for item in candidate.orientation_axes
    }
    assert "red_block.support_plane_normal_base" not in {
        item.alignment_id for item in candidate.orientation_axes
    }
    top_anchor = next(
        item
        for item in candidate.position_anchors
        if item.anchor_id == "red_block.visible_aabb_top_center"
    )
    assert top_anchor.offset_min_m == pytest.approx((-0.02, -0.02, 0.0))
    assert top_anchor.offset_max_m == pytest.approx((0.02, 0.02, 0.35))
    serialized = candidates.to_dict()
    assert not serialized["execution_lease_issued"]
    assert not serialized["tool_called"]
    assert not serialized["handler_bound"]
    assert not serialized["dispatch_enabled"]


def test_runner_motion_factory_advertises_absolute_pose_invocation_schema():
    source = (
        Path(__file__).parents[1]
        / "scripts"
        / "run_gemini_robotics_robolab.py"
    ).read_text()
    registry_start = source.index("def _local_dls_executor_registry(")
    registry_end = source.index("def _local_binary_actuator_registry(", registry_start)
    registry = source[registry_start:registry_end]

    assert "invocation_schema={" in registry
    assert '"target_position_m"' in registry
    assert '"target_quaternion_wxyz"' in registry
    assert '"x-runtime-constraints"' in registry
    assert '"maximum_alignment_error_deg"' in registry


def test_gate_accepts_exact_grounded_pose_and_recomputes_alignment():
    *_, candidates = fixture()
    decision = ShadowToolInvocationGate(candidates).dispatch(proposal(candidates))
    serialized = decision.to_dict()

    assert decision.tool_id == "spatial_motion"
    assert decision.invocation_arguments["target_position_m"] == pytest.approx(
        [0.50, 0.20, 0.23]
    )
    assert decision.grounding_assessment["grounding_error_m"] < 1.0e-6
    assert decision.grounding_assessment["alignment_error_deg"] < 1.0e-4
    assert serialized["invocation_validated"]
    assert not serialized["execution_lease_issued"]
    assert not serialized["tool_called"]
    assert not serialized["dispatch_enabled"]


def test_gate_rejects_stale_unadvertised_and_unacknowledged_invocations():
    *_, candidates = fixture()
    gate = ShadowToolInvocationGate(candidates)

    with pytest.raises(WorldEffectToolInvocationError, match="stale"):
        gate.dispatch(proposal(candidates, observation_id="stale:invocation"))

    with pytest.raises(WorldEffectToolInvocationError, match="not advertised"):
        gate.dispatch(proposal(candidates, candidate_id="invented:invocation"))

    with pytest.raises(WorldEffectToolInvocationError, match="acknowledge every"):
        gate.dispatch(
            proposal(candidates, acknowledged_invalidation_condition_ids=[])
        )


def test_gate_rejects_model_materialized_field_and_wrong_orientation_axis():
    *_, candidates = fixture()
    gate = ShadowToolInvocationGate(candidates)
    injected_arguments = dict(
        proposal(candidates)["invocation_arguments"],
        target_position_m=[0.60, 0.20, 0.23],
    )
    with pytest.raises(WorldEffectToolInvocationError, match="unknown fields"):
        gate.dispatch(
            proposal(candidates, invocation_arguments=injected_arguments)
        )

    rotated_arguments = dict(
        proposal(candidates)["invocation_arguments"],
        target_quaternion_wxyz=[0.70710678, 0.0, 0.0, 0.70710678],
    )
    with pytest.raises(WorldEffectToolInvocationError, match="alignment limit"):
        gate.dispatch(
            proposal(candidates, invocation_arguments=rotated_arguments)
        )


def test_gate_rejects_schema_and_runtime_bound_violations():
    *_, candidates = fixture()
    gate = ShadowToolInvocationGate(candidates)
    extra_arguments = dict(
        proposal(candidates)["invocation_arguments"],
        invented_argument=True,
    )
    with pytest.raises(WorldEffectToolInvocationError, match="unknown fields"):
        gate.dispatch(proposal(candidates, invocation_arguments=extra_arguments))

    with pytest.raises(WorldEffectToolInvocationError, match="grounding limit"):
        gate.dispatch(
            proposal(
                candidates,
                interaction_offset_from_anchor_m=[0.0, 0.0, 0.50],
            )
        )


def test_runtime_tool_can_change_without_changing_goal_or_invocation_contract():
    instance, _, _, _, candidates = fixture(tool_id="whole_body_reach")

    assert instance.goal_id == "red-in-bin"
    assert candidates.candidates[0].tool_id == "whole_body_reach"
    assert candidates.candidates[0].position_anchors[0].entity_id == "red_block"


def test_prompt_is_explicitly_typed_grounded_and_non_dispatching():
    *_, candidates = fixture()
    prompt = build_shadow_tool_invocation_prompt(
        instruction="Clean the table",
        candidate_set=candidates,
    )
    lowered = prompt.lower()

    assert "typed, rgb-d-grounded invocation" in lowered
    assert "model_argument_schema" in lowered
    assert "materialized_argument_fields" in lowered
    assert "gate materializes the controlled" in lowered
    assert "controlled target + rotated local interaction origin offset" in lowered
    assert "target quaternion must rotate" in lowered
    assert "lease remains\nunissued" in lowered
    assert "no handler is bound and no tool or simulator action is called" in lowered
    assert '"execution_authority": false' in lowered


def test_runner_wires_invocation_after_lease_and_before_shadow_boundary():
    source = (
        Path(__file__).parents[1]
        / "scripts"
        / "run_gemini_robotics_robolab.py"
    ).read_text()
    lease_gate = source.index("ShadowExecutionLeaseGate(")
    invocation_candidates = source.index(
        "build_shadow_tool_invocation_candidates(", lease_gate
    )
    invocation_prompt = source.index(
        "build_shadow_tool_invocation_prompt(", invocation_candidates
    )
    invocation_gate = source.index("ShadowToolInvocationGate(", invocation_prompt)
    invocation_trace = source.index(
        'episode_trace["world_effect_tool_invocation_shadow"]', invocation_gate
    )
    hard_boundary = source.index("if args_cli.shadow_plan_only:", invocation_trace)

    assert (
        lease_gate
        < invocation_candidates
        < invocation_prompt
        < invocation_gate
        < invocation_trace
        < hard_boundary
    )
    block = source[invocation_candidates:hard_boundary]
    assert '"execution_lease_issued": False' in block
    assert '"tool_called": False' in block
    assert '"handler_bound": False' in block
    assert '"dispatch_enabled": False' in block
    assert '"motion_authority": False' in block
    assert '"execution_authority": False' in block
    assert "_execute_adaptive_stage(" not in block
    assert "actuator_transition_handler(" not in block
