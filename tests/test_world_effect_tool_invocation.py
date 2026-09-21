from copy import deepcopy
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
    shadow_tool_invocation_json_schema,
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

ORDERED_WAYPOINT_INVOCATION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "ordered_waypoints": {
            "type": "array",
            "minItems": 2,
            "maxItems": 4,
            "items": {
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
            },
        },
    },
    "required": ["ordered_waypoints"],
    "x-runtime-constraints": {
        "grounding_mode": "ordered_waypoint_path",
        "coordinate_frame": "robot_root",
        "workspace_min_m": [-0.75, -0.75, 0.02],
        "workspace_max_m": [0.90, 0.90, 1.40],
        "maximum_segment_displacement_m": 0.50,
        "maximum_path_length_m": 1.0,
        "maximum_grounding_offset_m": 0.35,
        "maximum_alignment_error_deg": 15.0,
    },
}


REACHABILITY_BOUNDED_INVOCATION_SCHEMA = deepcopy(INVOCATION_SCHEMA)
REACHABILITY_BOUNDED_INVOCATION_SCHEMA["x-runtime-constraints"].update(
    {
        "minimum_reachable_radius_m": 0.20,
        "maximum_reachable_radius_m": 0.60,
    }
)


def fixture(
    *,
    tool_id="spatial_motion",
    invocation_schema=INVOCATION_SCHEMA,
    tool_family="motion",
    semantic_effect_id=None,
    required_invocation_arguments=None,
    grasp_corridor_aligned=True,
    retained_contact=False,
):
    capability_tags = (
        (
            "spatial.pose_target",
            "motion.observation_bound",
            "motion.invalidation_feedback",
        )
        if tool_family == "motion"
        else (
            "entity_attachment.acquire",
            "entity_attachment.release",
            "actuation.observation_bound",
        )
    )
    requirement_id = (
        "observation_bound_spatial_motion"
        if tool_family == "motion"
        else "reversible_entity_attachment"
    )
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
        requirement_id=requirement_id,
        tool_id=tool_id,
        tool_family=tool_family,
        purpose="establish_precondition",
        operation_target_entity_ids=("red_block",),
        desired_outcome="Interaction geometry is ready.",
        stop_condition="Stop at the observable interaction geometry.",
        tool_configuration_schema={
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "position_tolerance_m": {"type": "number"},
                "require_interaction_relation": {"type": "boolean"},
            },
        },
        geometry_bindings=(geometry,),
        invalidation_candidates=invalidation_specs,
        semantic_effect_id=semantic_effect_id,
        required_invocation_arguments=(required_invocation_arguments or {}),
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
        tool_configuration={
            "position_tolerance_m": 0.02,
            "require_interaction_relation": True,
        },
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
                requirement_id=requirement_id,
                source_tool_id="factory.spatial",
                activated_tool_id=tool_id,
                tool_family=tool_family,
                capability_tags=capability_tags,
                tool_advertisement={
                    "executor_id": tool_id,
                    "tool_name": "execute_spatial_motion",
                    "tool_family": tool_family,
                    "capability_tags": list(capability_tags),
                    "configuration_schema": lease_candidate.tool_configuration_schema,
                    "invocation_schema": invocation_schema,
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
            "grasp_geometry": {
                "center_local_m": [0.12, 0.0, 0.0],
                "closing_axis_local": [0.0, -1.0, 0.0],
                "configured_open_aperture_m": 0.085,
                "transverse_axes_local": [
                    [1.0, 0.0, 0.0],
                    [0.0, 0.0, 1.0],
                ],
                "transverse_axis_ranges_from_center_m": [
                    [-0.03, 0.03],
                    [-0.03, 0.03],
                ],
                "geometry_state": "configured_open_envelope",
            },
            "two_pad_grasp_alignment": {
                "available": True,
                "object_fits_configured_aperture": True,
                "object_fully_between_open_pad_planes": True,
                "object_center_inside_transverse_pad_bounds": True,
                "object_center_inside_full_grasp_corridor": grasp_corridor_aligned,
                "corrective_motion_grounding_contract": {
                    "relation_id": (
                        "interaction_origin_coincident_with_entity_center"
                    ),
                    "entity_id": "red_block",
                    "required_terminal_position_anchor_id": (
                        "red_block.center"
                    ),
                    "required_terminal_interaction_offset_from_anchor_m": [
                        0.0,
                        0.0,
                        0.0,
                    ],
                    "applies_when": (
                        "object_center_inside_full_grasp_corridor_false"
                    ),
                },
                "required_contact_center_translation_robot_root_m": [
                    0.0,
                    0.0,
                    0.0,
                ],
            },
        },
        "current_contact": (
            {
                "available": True,
                "touch": True,
                "contact_bodies": {
                    "available": True,
                    "active_body_count": 2,
                    "pairwise_force_direction_cosine": -0.95,
                    "force_magnitude_ratio_min_over_max": 0.9,
                    "channels": [
                        {"body": "left", "touch": True},
                        {"body": "right", "touch": True},
                    ],
                },
            }
            if retained_contact
            else {
                "available": True,
                "touch": False,
                "contact_bodies": {
                    "available": True,
                    "active_body_count": 0,
                    "channels": [
                        {"body": "left", "touch": False},
                        {"body": "right", "touch": False},
                    ],
                },
            }
        ),
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
        "invocation_arguments": {},
        "acknowledged_invalidation_condition_ids": list(
            candidate.invalidation_condition_ids
        ),
        "confidence": 0.92,
        "reason": "The interaction frame is grounded above the observed target.",
    }
    payload.update(overrides)
    return payload


def waypoint_proposal(candidate_set, **overrides):
    candidate = candidate_set.candidates[0]
    payload = {
        "schema_version": WORLD_EFFECT_TOOL_INVOCATION_SCHEMA_VERSION,
        "observation_id": candidate_set.observation_id,
        "decision": "propose_invocation",
        "candidate_id": candidate.candidate_id,
        "lease_id": candidate.lease_id,
        "tool_id": candidate.tool_id,
        "position_anchor_id": None,
        "interaction_offset_from_anchor_m": [],
        "orientation_alignment_id": None,
        "invocation_arguments": {
            "ordered_waypoints": [
                {
                    "position_anchor_id": (
                        "red_block.visible_aabb_top_center"
                    ),
                    "interaction_offset_from_anchor_m": [0.0, 0.0, 0.20],
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
            ],
        },
        "acknowledged_invalidation_condition_ids": list(
            candidate.invalidation_condition_ids
        ),
        "confidence": 0.92,
        "reason": "The ordered path raises and then lowers the interaction frame.",
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
    assert candidate.materialized_argument_fields == (
        "target_position_m",
        "target_quaternion_wxyz",
    )
    assert "target_position_m" in candidate.invocation_schema["properties"]
    assert "target_position_m" not in candidate.model_argument_schema["properties"]
    assert (
        "target_quaternion_wxyz"
        not in candidate.model_argument_schema["properties"]
    )
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
    center_anchor = next(
        item
        for item in candidate.position_anchors
        if item.anchor_id == "red_block.center"
    )
    assert center_anchor.offset_min_m == pytest.approx(
        (-0.02, -0.02, -0.02)
    )
    assert center_anchor.offset_max_m == pytest.approx((0.02, 0.02, 0.35))
    serialized = candidates.to_dict()
    current_offsets = serialized["candidates"][0][
        "current_interaction_offsets_from_anchors"
    ]
    assert {item["anchor_id"] for item in current_offsets} == {
        "red_block.center",
        "red_block.visible_aabb_top_center",
    }
    assert serialized["candidates"][0]["current_interaction_position_m"] is not None
    assert serialized["candidates"][0]["interaction_frame"][
        "grasp_geometry"
    ]["configured_open_aperture_m"] == pytest.approx(0.085)
    assert serialized["candidates"][0]["interaction_frame"][
        "two_pad_grasp_alignment"
    ]["object_fully_between_open_pad_planes"] is True
    assert not serialized["execution_lease_issued"]
    assert not serialized["tool_called"]
    assert not serialized["handler_bound"]
    assert not serialized["dispatch_enabled"]


def test_actuator_semantic_effect_binding_rejects_contradictory_command():
    actuator_schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "state": {
                "type": "string",
                "enum": ["engage", "disengage", "maintain"],
            }
        },
        "required": ["state"],
    }
    *_, candidates = fixture(
        tool_id="binary_clamp",
        invocation_schema=actuator_schema,
        tool_family="actuator",
        semantic_effect_id="entity_attachment.release",
        required_invocation_arguments={"state": "disengage"},
    )
    candidate = candidates.candidates[0]
    assert candidate.semantic_effect_id == "entity_attachment.release"
    assert candidate.model_argument_schema["properties"]["state"]["enum"] == [
        "disengage"
    ]
    payload = {
        "schema_version": WORLD_EFFECT_TOOL_INVOCATION_SCHEMA_VERSION,
        "observation_id": candidates.observation_id,
        "decision": "propose_invocation",
        "candidate_id": candidate.candidate_id,
        "lease_id": candidate.lease_id,
        "tool_id": candidate.tool_id,
        "position_anchor_id": None,
        "interaction_offset_from_anchor_m": [],
        "orientation_alignment_id": None,
        "invocation_arguments": {"state": "engage"},
        "acknowledged_invalidation_condition_ids": list(
            candidate.invalidation_condition_ids
        ),
        "confidence": 0.95,
        "reason": "Attempt a contradictory transition.",
    }

    with pytest.raises(WorldEffectToolInvocationError, match="not an allowed value"):
        ShadowToolInvocationGate(candidates).dispatch(payload)

    payload["invocation_arguments"] = {"state": "disengage"}
    accepted = ShadowToolInvocationGate(candidates).dispatch(payload)
    assert accepted.invocation_arguments == {"state": "disengage"}

    prompt = build_shadow_tool_invocation_prompt(
        instruction="Recover the interaction.",
        candidate_set=candidates,
    )
    assert '"semantic_effect_id": "entity_attachment.release"' in prompt
    assert '"state": "disengage"' in prompt
    assert "contradictory actuator state" in prompt
    assert "must not\nveto the effect" in prompt
    assert "apparent enclosure alone is not proof" in prompt
    assert "For blocked or observe_again, do not copy them" in prompt
    assert "A non-executing decision must never carry a latent command" in prompt


def test_acquire_fails_closed_outside_advertised_two_pad_corridor():
    actuator_schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "state": {
                "type": "string",
                "enum": ["engage", "disengage", "maintain"],
            }
        },
        "required": ["state"],
    }
    instance, lease_candidates, lease_decision, observation, _ = fixture(
        tool_id="binary_clamp",
        invocation_schema=actuator_schema,
        tool_family="actuator",
        semantic_effect_id="entity_attachment.acquire",
        required_invocation_arguments={"state": "engage"},
    )
    observation = deepcopy(observation)
    observation["interaction_frame"]["two_pad_grasp_alignment"][
        "object_fully_between_open_pad_planes"
    ] = False
    candidates = build_shadow_tool_invocation_candidates(
        instance,
        lease_candidates,
        lease_decision,
        observation,
    )
    candidate = candidates.candidates[0]
    payload = {
        "schema_version": WORLD_EFFECT_TOOL_INVOCATION_SCHEMA_VERSION,
        "observation_id": candidates.observation_id,
        "decision": "propose_invocation",
        "candidate_id": candidate.candidate_id,
        "lease_id": candidate.lease_id,
        "tool_id": candidate.tool_id,
        "position_anchor_id": None,
        "interaction_offset_from_anchor_m": [],
        "orientation_alignment_id": None,
        "invocation_arguments": {"state": "engage"},
        "acknowledged_invalidation_condition_ids": list(
            candidate.invalidation_condition_ids
        ),
        "confidence": 0.95,
        "reason": "Acquire before the object is between both pads.",
    }

    with pytest.raises(
        WorldEffectToolInvocationError,
        match="fully inside the advertised two-pad grasp corridor",
    ):
        ShadowToolInvocationGate(candidates).dispatch(payload)

    observation["interaction_frame"]["two_pad_grasp_alignment"][
        "object_fully_between_open_pad_planes"
    ] = True
    observation["interaction_frame"]["two_pad_grasp_alignment"][
        "object_center_inside_transverse_pad_bounds"
    ] = False
    transversely_misaligned = build_shadow_tool_invocation_candidates(
        instance,
        lease_candidates,
        lease_decision,
        observation,
    )
    payload["observation_id"] = transversely_misaligned.observation_id
    payload["candidate_id"] = transversely_misaligned.candidates[0].candidate_id
    with pytest.raises(
        WorldEffectToolInvocationError,
        match="inside the advertised transverse pad-face bounds",
    ):
        ShadowToolInvocationGate(transversely_misaligned).dispatch(payload)

    observation["interaction_frame"]["two_pad_grasp_alignment"][
        "object_center_inside_transverse_pad_bounds"
    ] = True
    centered = build_shadow_tool_invocation_candidates(
        instance,
        lease_candidates,
        lease_decision,
        observation,
    )
    payload["observation_id"] = centered.observation_id
    payload["candidate_id"] = centered.candidates[0].candidate_id
    accepted = ShadowToolInvocationGate(centered).dispatch(payload)
    assert accepted.invocation_arguments == {"state": "engage"}


def test_corrective_motion_must_terminate_on_advertised_interaction_relation():
    instance, lease_candidates, lease_decision, observation, _ = fixture()
    observation = deepcopy(observation)
    observation["interaction_frame"]["two_pad_grasp_alignment"][
        "object_center_inside_full_grasp_corridor"
    ] = False
    candidates = build_shadow_tool_invocation_candidates(
        instance,
        lease_candidates,
        lease_decision,
        observation,
    )

    with pytest.raises(
        WorldEffectToolInvocationError,
        match="terminal anchor contradicts",
    ):
        ShadowToolInvocationGate(candidates).dispatch(proposal(candidates))

    centered = proposal(
        candidates,
        position_anchor_id="red_block.center",
        interaction_offset_from_anchor_m=[0.0, 0.0, 0.0],
    )
    decision = ShadowToolInvocationGate(candidates).dispatch(centered)
    assert decision.position_anchor_id == "red_block.center"
    assert decision.interaction_offset_from_anchor_m == (0.0, 0.0, 0.0)


def test_retained_tactile_attachment_disables_pregrasp_terminal_correction():
    *_, candidates = fixture(
        grasp_corridor_aligned=False,
        retained_contact=True,
    )
    candidate = candidates.candidates[0]

    assert candidate.retained_contact_supported is True
    transported = ShadowToolInvocationGate(candidates).dispatch(
        proposal(candidates)
    )
    assert transported.position_anchor_id == "red_block.visible_aabb_top_center"
    assert transported.interaction_offset_from_anchor_m == (0.0, 0.0, 0.05)


def test_corrective_waypoint_path_may_use_clearance_but_must_finish_centered():
    instance, lease_candidates, lease_decision, observation, _ = fixture(
        tool_id="bounded_dls_waypoint_path",
        invocation_schema=ORDERED_WAYPOINT_INVOCATION_SCHEMA,
    )
    observation = deepcopy(observation)
    observation["interaction_frame"]["two_pad_grasp_alignment"][
        "object_center_inside_full_grasp_corridor"
    ] = False
    candidates = build_shadow_tool_invocation_candidates(
        instance,
        lease_candidates,
        lease_decision,
        observation,
    )
    invalid = waypoint_proposal(candidates)
    with pytest.raises(
        WorldEffectToolInvocationError,
        match="terminal offset contradicts",
    ):
        ShadowToolInvocationGate(candidates).dispatch(invalid)

    valid = waypoint_proposal(candidates)
    valid["invocation_arguments"]["ordered_waypoints"][-1].update(
        {
            "position_anchor_id": "red_block.center",
            "interaction_offset_from_anchor_m": [0.0, 0.0, 0.0],
        }
    )
    accepted = ShadowToolInvocationGate(candidates).dispatch(valid)
    assert accepted.grounding_assessment["ordered_waypoints"][-1][
        "position_anchor_id"
    ] == "red_block.center"


def test_motion_invocation_rejects_a_materialized_target_already_within_tolerance():
    instance, lease_candidates, lease_decision, runtime_observation, candidates = (
        fixture()
    )
    first = ShadowToolInvocationGate(candidates).dispatch(proposal(candidates))
    at_target = deepcopy(runtime_observation)
    at_target["controlled_frame"] = {
        "position_m": first.invocation_arguments["target_position_m"],
        "quaternion_wxyz": first.invocation_arguments[
            "target_quaternion_wxyz"
        ],
    }
    fresh_candidates = build_shadow_tool_invocation_candidates(
        instance,
        lease_candidates,
        lease_decision,
        at_target,
    )

    with pytest.raises(
        WorldEffectToolInvocationError,
        match="already within the configured position tolerance",
    ):
        ShadowToolInvocationGate(fresh_candidates).dispatch(
            proposal(fresh_candidates)
        )


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
    assert 'executor_id="bounded_dls_waypoint_path"' in registry
    assert '"ordered_waypoints"' in registry
    assert '"grounding_mode": "ordered_waypoint_path"' in registry
    assert source.count("for invocation_attempt in range") == 2


def test_ordered_waypoint_candidate_exposes_model_grounding_not_runtime_positions():
    *_, candidates = fixture(
        tool_id="bounded_dls_waypoint_path",
        invocation_schema=ORDERED_WAYPOINT_INVOCATION_SCHEMA,
    )
    candidate = candidates.candidates[0]
    item_schema = candidate.model_argument_schema["properties"][
        "ordered_waypoints"
    ]["items"]

    assert candidate.ordered_waypoint_grounding_required
    assert candidate.materialized_argument_fields == (
        "ordered_waypoints[].target_position_m",
        "ordered_waypoints[].target_quaternion_wxyz",
    )
    assert "target_position_m" not in item_schema["properties"]
    assert "target_quaternion_wxyz" not in item_schema["properties"]
    assert set(item_schema["properties"]["position_anchor_id"]["enum"]) == {
        "red_block.center",
        "red_block.visible_aabb_top_center",
    }
    assert (
        "red_block.oriented_footprint_axes_base.0"
        in item_schema["properties"]["orientation_alignment_id"]["enum"]
    )
    response_schema = shadow_tool_invocation_json_schema(candidates)
    response_properties = response_schema["properties"]
    assert response_properties["position_anchor_id"]["enum"] == [None]
    assert response_properties["interaction_offset_from_anchor_m"][
        "maxItems"
    ] == 0
    assert response_properties["orientation_alignment_id"]["enum"] == [None]


def test_gate_materializes_every_ordered_waypoint_under_one_invocation():
    *_, candidates = fixture(
        tool_id="bounded_dls_waypoint_path",
        invocation_schema=ORDERED_WAYPOINT_INVOCATION_SCHEMA,
    )
    decision = ShadowToolInvocationGate(candidates).dispatch(
        waypoint_proposal(candidates)
    )

    assert decision.position_anchor_id is None
    assert decision.orientation_alignment_id is None
    assert decision.invocation_arguments["ordered_waypoints"][0][
        "target_position_m"
    ] == pytest.approx([0.50, 0.20, 0.38])
    assert decision.invocation_arguments["ordered_waypoints"][1][
        "target_position_m"
    ] == pytest.approx([0.50, 0.20, 0.26])
    assert decision.grounding_assessment["ordered_waypoint_count"] == 2
    assert len(decision.grounding_assessment["ordered_waypoints"]) == 2
    assert decision.to_dict()["invocation_validated"]
    assert not decision.to_dict()["execution_lease_issued"]


def test_gate_rejects_invalid_ordered_waypoint_envelope_and_noop_segment():
    *_, candidates = fixture(
        tool_id="bounded_dls_waypoint_path",
        invocation_schema=ORDERED_WAYPOINT_INVOCATION_SCHEMA,
    )
    outside = waypoint_proposal(candidates)
    outside["invocation_arguments"]["ordered_waypoints"][0][
        "interaction_offset_from_anchor_m"
    ] = [0.03, 0.0, 0.20]
    with pytest.raises(WorldEffectToolInvocationError, match="anchor envelope"):
        ShadowToolInvocationGate(candidates).dispatch(outside)

    duplicate = waypoint_proposal(candidates)
    duplicate["invocation_arguments"]["ordered_waypoints"][1] = deepcopy(
        duplicate["invocation_arguments"]["ordered_waypoints"][0]
    )
    with pytest.raises(WorldEffectToolInvocationError, match="position tolerance"):
        ShadowToolInvocationGate(candidates).dispatch(duplicate)


def test_segment_bound_rejection_reports_measured_subdivision_evidence():
    schema = deepcopy(ORDERED_WAYPOINT_INVOCATION_SCHEMA)
    schema["x-runtime-constraints"]["maximum_segment_displacement_m"] = 0.10
    *_, candidates = fixture(
        tool_id="bounded_dls_waypoint_path",
        invocation_schema=schema,
    )

    with pytest.raises(
        WorldEffectToolInvocationError,
        match="subdivide into at least 3 segments",
    ) as exc_info:
        ShadowToolInvocationGate(candidates).dispatch(
            waypoint_proposal(candidates)
        )

    evidence = exc_info.value.evidence
    assert evidence["waypoint_index"] == 0
    assert evidence["segment_displacement_m"] == pytest.approx(0.2601922)
    assert evidence["maximum_segment_displacement_m"] == pytest.approx(0.10)
    assert evidence["required_segment_count"] == 3
    assert evidence["required_intermediate_waypoint_count"] == 2
    assert evidence["previous_controlled_target_position_m"] == pytest.approx(
        [0.36, 0.0, 0.47]
    )
    assert evidence["rejected_controlled_target_position_m"] == pytest.approx(
        [0.50, 0.20, 0.38]
    )


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


def test_gate_records_executor_published_reachability_before_authority():
    *_, candidates = fixture(
        invocation_schema=REACHABILITY_BOUNDED_INVOCATION_SCHEMA,
    )
    decision = ShadowToolInvocationGate(candidates).dispatch(
        proposal(candidates)
    )

    reachability = decision.grounding_assessment["reachability"]
    assert reachability["within_runtime_reachability_shell"] is True
    assert reachability["minimum_reachable_radius_m"] == 0.20
    assert reachability["maximum_reachable_radius_m"] == 0.60
    assert decision.grounding_assessment["reachability_source"] == (
        "runtime_executor_advertisement"
    )


def test_gate_rejects_target_outside_executor_published_reachability():
    schema = deepcopy(REACHABILITY_BOUNDED_INVOCATION_SCHEMA)
    schema["x-runtime-constraints"]["maximum_reachable_radius_m"] = 0.55
    *_, candidates = fixture(invocation_schema=schema)

    with pytest.raises(
        WorldEffectToolInvocationError,
        match="outside the runtime-published reachable radius",
    ):
        ShadowToolInvocationGate(candidates).dispatch(proposal(candidates))


def test_gate_rejects_stale_unadvertised_and_unacknowledged_invocations():
    *_, candidates = fixture()
    gate = ShadowToolInvocationGate(candidates)

    with pytest.raises(WorldEffectToolInvocationError, match="stale"):
        gate.dispatch(proposal(candidates, observation_id="stale:invocation"))

    with pytest.raises(WorldEffectToolInvocationError, match="not advertised"):
        gate.dispatch(proposal(candidates, candidate_id="invented:invocation"))

    with pytest.raises(
        WorldEffectToolInvocationError,
        match="selected candidate requires lease_id='shadow-execution-lease:test'",
    ):
        gate.dispatch(proposal(candidates, lease_id="execution-lease:test"))

    with pytest.raises(WorldEffectToolInvocationError, match="acknowledge every"):
        gate.dispatch(
            proposal(candidates, acknowledged_invalidation_condition_ids=[])
        )


def test_prompt_surfaces_exact_candidate_lease_tool_identity_triple():
    *_, candidates = fixture(tool_id="bounded_dls_waypoint_path")

    prompt = build_shadow_tool_invocation_prompt(
        instruction="Move the red block.",
        candidate_set=candidates,
    )

    assert "Exact proposal identity triples" in prompt
    assert f'"candidate_id": "{candidates.candidates[0].candidate_id}"' in prompt
    assert f'"lease_id": "{candidates.candidates[0].lease_id}"' in prompt
    assert '"tool_id": "bounded_dls_waypoint_path"' in prompt


def test_gate_rejects_both_model_materialized_pose_fields():
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

    injected_quaternion = dict(
        proposal(candidates)["invocation_arguments"],
        target_quaternion_wxyz=[0.70710678, 0.0, 0.0, 0.70710678],
    )
    with pytest.raises(WorldEffectToolInvocationError, match="unknown fields"):
        gate.dispatch(
            proposal(candidates, invocation_arguments=injected_quaternion)
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
        rejection_context={
            "error": "materialized motion target is already within tolerance",
            "evidence": {
                "segment_displacement_m": 0.42,
                "maximum_segment_displacement_m": 0.35,
                "required_segment_count": 2,
            },
        },
    )
    lowered = prompt.lower()

    assert "typed, rgb-d-grounded invocation" in lowered
    assert "model_argument_schema" in lowered
    assert "materialized_argument_fields" in lowered
    assert "gate materializes the controlled" in lowered
    assert "controlled target + rotated local interaction origin offset" in lowered
    assert "current_interaction_offsets_from_anchors" in lowered
    assert "never repeat the current offset" in lowered
    assert "gripper-base-to-contact distance" in lowered
    assert "previous proposal rejection" in lowered
    assert "already within tolerance" in lowered
    assert '"required_segment_count": 2' in prompt
    assert "do not resubmit that no-op target" in lowered
    assert "do not output a target quaternion" in lowered
    assert "preserving the current twist" in lowered
    assert "object center\ncoincident with the advertised grasp-corridor center" in lowered
    assert "rejects\npremature acquisition" in lowered
    assert "opposing tactile\nevidence has already established the attachment" in lowered
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


def test_runner_recomposes_a_nonexecuting_initial_invocation_before_handoff():
    source = (
        Path(__file__).parents[1]
        / "scripts"
        / "run_gemini_robotics_robolab.py"
    ).read_text()
    replan_flag = source.index(
        "initial_world_effect_replan_required = True"
    )
    replan_branch = source.index(
        "if initial_world_effect_replan_required:", replan_flag
    )
    composition_planner = source.index(
        "_reason_composed_tool_sequence(", replan_branch
    )
    local_materializer = source.index(
        "_materialize_guarded_composed_step(", composition_planner
    )
    required_handoff = source.index(
        "required_handoff = {", local_materializer
    )

    assert (
        replan_flag
        < replan_branch
        < composition_planner
        < local_materializer
        < required_handoff
    )
    branch = source[replan_branch:required_handoff]
    assert '"world_effect_initial_replans"' in branch
    assert '== "runtime_lease_issued"' in branch
    assert "issued_runtime_lease = bootstrap_bundle" in branch
    assert "composed_tool_sequence_model_call_count += int(" in branch
    assert '"model_call_count"' in branch
    assert '"model_calls_for_step_materialization": 0' in branch


def test_runner_replans_a_rejected_initial_motion_target_before_handoff():
    source = (
        Path(__file__).parents[1]
        / "scripts"
        / "run_gemini_robotics_robolab.py"
    ).read_text()
    rejection_handler = source.index(
        "except Exception as invocation_error:"
    )
    replan_flag = source.index(
        "initial_world_effect_replan_required = True",
        rejection_handler,
    )
    typed_error_check = source.index(
        "WorldEffectToolInvocationError,", rejection_handler
    )
    replan_branch = source.index(
        "if initial_world_effect_replan_required:", replan_flag
    )
    required_handoff = source.index("required_handoff = {", replan_branch)

    assert rejection_handler < typed_error_check < replan_flag
    assert replan_flag < replan_branch < required_handoff
