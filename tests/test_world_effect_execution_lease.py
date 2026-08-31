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


def operation_fixture(*, tool_id="spatial_motion", scene=None, execution_context=None):
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
            "maximum_tracked_orientation_error_deg": {
                "type": "number",
                "minimum": 1.0,
                "maximum": 90.0,
            },
            "tracked_object_id": {
                "type": "string",
                "enum": ["red_block", "grey_bin"],
            },
            "require_contact": {"type": "boolean"},
            "minimum_contact_force_n": {
                "type": "number",
                "minimum": 0.0,
                "maximum": 100.0,
            },
            "minimum_observed_clearance_m": {
                "type": "number",
                "minimum": 0.0,
                "maximum": 0.5,
            },
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
        inventory() if scene is None else scene,
        execution_context=execution_context,
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


def test_contact_force_threshold_is_bounded_by_fresh_retention_evidence():
    context = {
        "current_contact": {
            "touch": True,
            "contact_bodies": {
                "available": True,
                "touch_threshold_n": 0.1,
                "retained_force_n": 2.0,
            },
        }
    }
    _, _, _, candidate_set = operation_fixture(execution_context=context)

    force_schema = candidate_set.candidates[0].tool_configuration_schema[
        "properties"
    ]["minimum_contact_force_n"]
    assert force_schema["minimum"] == pytest.approx(0.1)
    assert force_schema["maximum"] == pytest.approx(1.9)
    assert "fresh opposing contact-body evidence" in force_schema["description"]


def test_contact_force_threshold_is_not_advertised_without_retention_evidence():
    _, _, _, candidate_set = operation_fixture(execution_context={
        "current_contact": {
            "touch": False,
            "contact_bodies": {
                "available": True,
                "touch_threshold_n": 0.1,
                "retained_force_n": 0.0,
            },
        }
    })

    assert "minimum_contact_force_n" not in candidate_set.candidates[
        0
    ].tool_configuration_schema["properties"]


def test_corrective_clearance_ceiling_is_grounded_in_fresh_rgbd_path():
    context = {
        "interaction_frame": {"contact_center_xyz_m": [0.5, 0.2, 0.3]},
        "two_pad_grasp_alignment": {
            "corrective_motion_grounding_contract": {
                "entity_id": "red_block",
                "required_terminal_position_anchor_id": "red_block.center",
            }
        },
        "current_contact": {},
        "fresh_rgbd_geometry": {
            "geometries": [
                {
                    "runtime_id": "red_block",
                    "center_base_m": [0.5, 0.2, 0.04],
                    "visible_aabb_min_base_m": [0.48, 0.18, 0.02],
                    "visible_aabb_max_base_m": [0.52, 0.22, 0.06],
                },
                {
                    "runtime_id": "table",
                    "center_base_m": [0.5, 0.2, -0.01],
                    "visible_aabb_min_base_m": [0.0, -0.5, -0.02],
                    "visible_aabb_max_base_m": [1.0, 0.5, 0.0],
                },
            ]
        },
    }
    _, _, _, candidate_set = operation_fixture(execution_context=context)

    clearance_schema = candidate_set.candidates[0].tool_configuration_schema[
        "properties"
    ]["minimum_observed_clearance_m"]
    assert clearance_schema["maximum"] == pytest.approx(0.04)
    assert "nearest observed entity: table" in clearance_schema["description"]


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


def test_orientation_lease_is_advertised_only_for_preflight_observable_target():
    unobservable_context = {
        "rgbd_orientation_observability": {
            "red_block": {
                "observable": False,
                "reason": "footprint_axis_ambiguous",
            }
        }
    }
    _, _, _, unobservable = operation_fixture(
        execution_context=unobservable_context
    )
    unobservable_candidate = unobservable.candidates[0]
    unobservable_properties = unobservable_candidate.tool_configuration_schema[
        "properties"
    ]

    assert "maximum_tracked_orientation_error_deg" not in unobservable_properties
    assert unobservable_properties["tracked_object_id"]["enum"] == ["red_block"]
    assert "scene.tracked_orientation_error_exceeded" not in {
        item.condition_id
        for item in unobservable_candidate.invalidation_candidates
    }

    observable_context = {
        "rgbd_orientation_observability": {
            "red_block": {
                "observable": True,
                "reason": "major_axis_observable",
            }
        }
    }
    _, _, _, observable = operation_fixture(
        execution_context=observable_context
    )
    observable_properties = observable.candidates[0].tool_configuration_schema[
        "properties"
    ]

    assert observable_properties["tracked_object_id"]["enum"] == ["red_block"]
    assert "maximum_tracked_orientation_error_deg" in observable_properties


def test_occluded_retained_target_binds_only_fresh_tracked_center():
    scene = inventory()
    red = next(item for item in scene["entities"] if item["entity_id"] == "red_block")
    red["observation_status"] = "temporarily_occluded_rgbd"
    red["geometry"] = {}
    red["temporal_presence_evidence"] = {
        "independently_present": True,
        "cached_geometry_exposed": False,
        "completion_evidence": False,
        "execution_authority": False,
    }
    scene["world_effect_continuation_evidence"] = {
        "schema_version": "world-effect-continuation-evidence.v1",
        "attachment_entity_ids": ["red_block"],
        "tracked_present_entity_ids": ["red_block"],
        "tracked_entity_positions_m": {"red_block": [0.52, 0.21, 0.10]},
        "gripper_engaged": True,
        "retained_contact_supported": True,
        "recovery_actuator_only": False,
        "planning_continuation_allowed": True,
        "completion_evidence": False,
        "task_completion_allowed": False,
        "dispatch_enabled": False,
        "motion_authority": False,
        "execution_authority": False,
        "authority_scope": [],
    }

    _, _, _, candidate_set = operation_fixture(scene=scene)
    binding = next(
        item
        for item in candidate_set.candidates[0].geometry_bindings
        if item.entity_id == "red_block"
    )

    assert binding.observation_status == "temporarily_occluded_rgbd"
    assert binding.geometry["center_base_m"] == [0.52, 0.21, 0.10]
    assert binding.geometry["geometry_source"] == (
        "runtime_tracked_retained_attachment"
    )
    assert binding.geometry["visible_geometry_available"] is False
    assert "visible_extent_base_m" not in binding.geometry
    assert "visible_aabb_min_base_m" not in binding.geometry
    assert binding.geometry["completion_evidence"] is False


def test_occluded_target_without_valid_attachment_evidence_still_fails_closed():
    scene = inventory()
    red = next(item for item in scene["entities"] if item["entity_id"] == "red_block")
    red["observation_status"] = "temporarily_occluded_rgbd"
    red["geometry"] = {}

    with pytest.raises(WorldEffectExecutionLeaseError, match="lacks fresh geometry"):
        operation_fixture(scene=scene)


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
        "tracked_object_id": "red_block",
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
        rejection_context={
            "error": "lease provider/operation/tool triple was not advertised",
            "requirements": {"same_fresh_candidate_set": True},
        },
    )
    lowered = prompt.lower()

    assert "geometry-grounded shadow execution lease" in lowered
    assert "event-or-completion" in lowered
    assert "can cover many local runtime steps" in lowered
    assert "does not issue a lease" in lowered
    assert "does not issue a lease, bind a\nhandler, call a tool, or dispatch" in lowered
    assert "do not provide a target pose, pose\ndelta, trajectory" in lowered
    assert "omit it when require_contact is\nfalse or absent" in lowered
    assert "fresh contact\nevidence measures an opposing retained-contact force" in lowered
    assert "previous proposal rejection" in lowered
    assert "same_fresh_candidate_set" in prompt
    assert "do not switch identifiers or evidence" in lowered
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
    lease_reasoner = source.index(
        "_reason_world_effect_execution_lease_with_correction(",
        lease_candidates,
    )
    lease_trace = source.index(
        'episode_trace["world_effect_execution_lease_shadow"]', lease_reasoner
    )
    hard_boundary = source.index("if args_cli.shadow_plan_only:", lease_trace)

    assert (
        operation_gate
        < lease_candidates
        < lease_reasoner
        < lease_trace
        < hard_boundary
    )
    helper_start = source.index(
        "def _reason_world_effect_execution_lease_with_correction("
    )
    helper_end = source.index(
        "def _plan_guarded_world_effect_continuation(", helper_start
    )
    helper = source[helper_start:helper_end]
    assert "for attempt in range(1, 3):" in helper
    assert "build_shadow_execution_lease_prompt(" in helper
    assert "ShadowExecutionLeaseGate(candidate_set).dispatch(payload)" in helper
    block = source[lease_candidates:hard_boundary]
    assert '"execution_lease_issued": False' in block
    assert '"handler_bound": False' in block
    assert '"dispatch_enabled": False' in block
    assert '"motion_authority": False' in block
    assert '"execution_authority": False' in block
    assert "_execute_adaptive_stage(" not in block
    assert "actuator_transition_handler(" not in block
