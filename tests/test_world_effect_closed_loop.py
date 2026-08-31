from copy import deepcopy
from pathlib import Path

import pytest

from scripts.world_effect_closed_loop import (
    WorldEffectClosedLoopError,
    WorldEffectSequenceBudget,
    assess_retained_attachment_continuation,
    assess_world_effect_progress,
)
from scripts.world_goal_activation import shadow_world_capability_registry
from scripts.world_goal_graph_membership import SceneMembershipLease
from scripts.world_predicate_evaluator_registry import (
    rgbd_world_predicate_evaluator_registry,
)
from scripts.world_scene_inventory_memory import TemporalSceneInventoryMemory
from tests.test_world_goal_activation import graph, inventory


def assess(scene, *, operation_index=1):
    task_graph = graph(green_depends_on_red=True)
    lease = SceneMembershipLease.issue(task_graph, inventory())
    return assess_world_effect_progress(
        graph=task_graph,
        membership_lease=lease,
        selected_goal_id="red-in-bin",
        predicate_registry=rgbd_world_predicate_evaluator_registry(),
        capability_registry=shadow_world_capability_registry(),
        inventory=scene,
        operation_index=operation_index,
    )


def test_unsatisfied_goal_authorizes_only_another_planning_round():
    result = assess(inventory(), operation_index=2)

    assert result.status == "continue_selected_goal"
    assert result.may_plan_another_operation
    assert not result.requires_fresh_graph
    assert result.selected_goal_satisfied is False
    assert result.continuation_candidates is not None
    assert [
        item.goal_id for item in result.continuation_candidates.candidates
    ] == ["red-in-bin"]
    serialized = result.to_dict()
    assert not serialized["dispatch_enabled"]
    assert not serialized["execution_authority"]
    assert serialized["authority_scope"] == []


def test_fresh_predicate_completion_expires_the_graph_membership_lease():
    result = assess(inventory(red_inside=True), operation_index=4)

    assert result.status == "selected_goal_completed"
    assert result.selected_goal_satisfied is True
    assert result.requires_fresh_graph
    assert not result.may_plan_another_operation
    assert result.membership_assessment.reasons == (
        "goal_completion_requires_fresh_graph",
    )
    assert result.continuation_candidates is None


def test_satisfied_placement_requires_release_of_attached_goal_subject():
    scene = inventory(red_inside=True)
    scene["world_effect_continuation_evidence"] = {
        "selected_goal_id": "red-in-bin",
        "attachment_entity_ids": ["red_block"],
        "gripper_engaged": True,
        "task_completion_allowed": False,
    }

    result = assess(scene, operation_index=4)

    assert result.status == "continue_selected_goal"
    assert result.selected_goal_satisfied is True
    assert result.completion_blocking_attachment_entity_ids == ("red_block",)
    assert result.reason == (
        "fresh_predicates_satisfied_attachment_release_required"
    )
    assert result.continuation_candidates is not None
    assert "red-in-bin" in {
        item.goal_id for item in result.continuation_candidates.candidates
    }


def test_unrelated_attachment_does_not_block_selected_goal_completion():
    scene = inventory(red_inside=True)
    scene["world_effect_continuation_evidence"] = {
        "selected_goal_id": "red-in-bin",
        "attachment_entity_ids": ["green_block"],
        "gripper_engaged": True,
        "task_completion_allowed": False,
    }

    result = assess(scene, operation_index=4)

    assert result.status == "selected_goal_completed"
    assert result.completion_blocking_attachment_entity_ids == ()


def test_membership_change_forces_fresh_graph_before_another_operation():
    changed = deepcopy(inventory())
    changed["entities"].append(
        {
            "entity_id": "new_block",
            "label": "new block",
            "observation_status": "visible_rgbd",
            "geometry": {},
        }
    )
    result = assess(changed)

    assert result.status == "fresh_graph_required"
    assert result.requires_fresh_graph
    assert result.membership_assessment.reasons == ("scene_entity_added",)


def test_unrelated_tracker_confirmed_occlusion_does_not_abort_selected_goal():
    baseline = inventory()
    changed = deepcopy(baseline)
    changed["entities"] = [
        item
        for item in changed["entities"]
        if item["entity_id"] != "green_block"
    ]
    memory = TemporalSceneInventoryMemory(
        baseline,
        maximum_missed_observations=0,
    )
    fused = memory.update(
        changed,
        independently_present_entity_ids=("green_block",),
    )

    result = assess(fused.inventory, operation_index=2)

    assert result.status == "continue_selected_goal"
    assert result.selected_goal_satisfied is False
    assert result.membership_assessment.valid
    assert result.membership_assessment.transient_occlusion_status_changes == (
        {
            "entity_id": "green_block",
            "before": "visible_rgbd",
            "after": "temporarily_occluded_rgbd",
        },
    )


def test_lost_goal_geometry_requires_observation_instead_of_motion():
    changed = deepcopy(inventory())
    red = next(item for item in changed["entities"] if item["entity_id"] == "red_block")
    red["geometry"] = {}
    result = assess(changed)

    assert result.status == "observe_again"
    assert result.selected_goal_satisfied is None
    assert not result.may_plan_another_operation


def test_contact_and_tracker_supported_grasp_occlusion_replans_without_completion():
    baseline = inventory()
    changed = deepcopy(baseline)
    changed["entities"] = [
        item for item in changed["entities"] if item["entity_id"] != "red_block"
    ]
    fused = TemporalSceneInventoryMemory(
        baseline,
        maximum_missed_observations=0,
    ).update(
        changed,
        independently_present_entity_ids=("red_block",),
    ).inventory
    evidence = assess_retained_attachment_continuation(
        selected_goal_id="red-in-bin",
        source_operation_index=4,
        attachment_entity_ids=("red_block",),
        inventory=fused,
        tracked_entity_positions_m={"red_block": (0.51, 0.21, 0.08)},
        gripper_engaged=True,
        retained_contact_supported=True,
    )
    fused = dict(fused)
    fused["world_effect_continuation_evidence"] = evidence.to_dict()

    result = assess(fused, operation_index=4)

    assert evidence.planning_continuation_allowed
    assert result.status == "continue_selected_goal"
    assert result.reason == (
        "selected_goal_continuable_with_unknown_completion_evidence"
    )
    assert result.selected_goal_satisfied is None
    assert result.may_plan_another_operation
    assert [
        item.goal_id for item in result.continuation_candidates.candidates
    ] == ["red-in-bin"]
    serialized = evidence.to_dict()
    assert not serialized["completion_evidence"]
    assert not serialized["execution_authority"]


@pytest.mark.parametrize(
    ("tracked_positions", "gripper_engaged", "retained_contact", "reason"),
    [
        ({}, True, True, "attachment_entity_not_tracker_confirmed"),
        ({"red_block": (0.5, 0.2, 0.08)}, False, True, "actuator_not_engaged"),
    ],
)
def test_attachment_continuation_fails_closed_without_each_fresh_signal(
    tracked_positions,
    gripper_engaged,
    retained_contact,
    reason,
):
    baseline = inventory()
    changed = deepcopy(baseline)
    changed["entities"] = [
        item for item in changed["entities"] if item["entity_id"] != "red_block"
    ]
    fused = TemporalSceneInventoryMemory(
        baseline,
        maximum_missed_observations=0,
    ).update(
        changed,
        independently_present_entity_ids=("red_block",),
    ).inventory

    evidence = assess_retained_attachment_continuation(
        selected_goal_id="red-in-bin",
        source_operation_index=4,
        attachment_entity_ids=("red_block",),
        inventory=fused,
        tracked_entity_positions_m=tracked_positions,
        gripper_engaged=gripper_engaged,
        retained_contact_supported=retained_contact,
    )

    assert not evidence.planning_continuation_allowed
    assert evidence.reason == reason


def test_unretained_engaged_attempt_allows_only_a_fresh_recovery_plan():
    baseline = inventory()
    changed = deepcopy(baseline)
    changed["entities"] = [
        item for item in changed["entities"] if item["entity_id"] != "red_block"
    ]
    fused = TemporalSceneInventoryMemory(
        baseline,
        maximum_missed_observations=0,
    ).update(
        changed,
        independently_present_entity_ids=("red_block",),
    ).inventory

    evidence = assess_retained_attachment_continuation(
        selected_goal_id="red-in-bin",
        source_operation_index=3,
        attachment_entity_ids=("red_block",),
        inventory=fused,
        tracked_entity_positions_m={"red_block": (0.5, 0.2, 0.08)},
        gripper_engaged=True,
        retained_contact_supported=False,
    )

    assert evidence.planning_continuation_allowed
    assert evidence.recovery_actuator_only
    assert evidence.reason == (
        "engaged_attachment_not_retained_requires_actuator_recovery"
    )
    assert not evidence.retained_contact_supported
    assert not evidence.to_dict()["motion_authority"]


def test_sequence_budget_is_runtime_owned_and_bounded():
    budget = WorldEffectSequenceBudget(maximum_operations=4)

    assert budget.allows(1)
    assert budget.allows(4)
    assert not budget.allows(5)
    assert budget.to_dict()["authority"] == "runtime_configuration"
    with pytest.raises(WorldEffectClosedLoopError):
        WorldEffectSequenceBudget(maximum_operations=0)
    with pytest.raises(WorldEffectClosedLoopError):
        budget.allows(True)


def test_runner_reobserves_then_replans_motion_or_actuator_with_single_use_leases():
    source = Path("scripts/run_gemini_robotics_robolab.py").read_text()

    progress = source.index("progress = assess_world_effect_progress(")
    progress_gate = source.index(
        "if not progress.may_plan_another_operation:", progress
    )
    budget_gate = source.index(
        "world_effect_sequence_budget.allows(", progress_gate
    )
    replan = source.index(
        "_plan_guarded_world_effect_continuation(", budget_gate
    )
    dispatch = source.index(
        "_dispatch_guarded_world_effect_continuation(", replan
    )
    assert progress < progress_gate < budget_gate < replan < dispatch
    assert 'lease_candidate.tool_family == "motion"' in source
    assert 'lease_candidate.tool_family == "actuator"' in source
    assert "dispatcher.mint_permit(fresh_evidence)" in source
    assert "dispatcher.dispatch(permit)" in source
    assert "summarize_world_effect_operation_history(" in source
    assert '"recent_operation_history": dict(recent_operation_history)' in source
    assert "for invocation_attempt in range(1, 3):" in source
    assert "do_not_repeat_rejected_arguments" in source
    assert '"status": "operation_replan_required"' in source
    assert '"error_type": "invocation_not_proposed"' in source
    assert '== "operation_replan_required"' in source
    assert "operation_replan_attempts < 2" in source
    assert "REPLAN_OPERATION" in source
    assert '"task_completion_claimed": False' in source


def test_runner_contract_exposes_a_bounded_sequence_not_one_open_loop_call():
    source = Path("scripts/run_gemini_robotics_robolab.py").read_text()

    assert '"--world-effect-max-operations"' in source
    assert "WorldEffectSequenceBudget(" in source
    assert "Every operation receives a fresh-evidence, single-use permit" in source
    assert 'default=120.0' in source
    assert "wall-clock deadman" in source
    assert '"--world-effect-occlusion-grace-observations"' in source
    assert "TemporalSceneInventoryMemory(" in source
    assert "raw_continuation_inventory" in source
    assert "temporal_progress_update.inventory" in source
    assert "active_target_visibility_uses_raw_rgbd" in source
    assert "retained_attachment_is_fresh" in source
    assert "preserve_actuator_engagement" in source
    assert "assess_retained_attachment_continuation" in source
    assert source.count("_rgbd_axis_references_for_motion_lease(") >= 3
    assert 'tool_configuration.get("tracked_object_id")' in source
    assert source.count("scene_inventory_memory=scene_inventory_memory") >= 7
    invalidation = source.index("def _guarded_dispatch_invalidation_events(")
    raw_geometry = source.index(
        "current_geometry = _runtime_geometry_by_id(state)", invalidation
    )
    temporal_identity = source.index("temporal_update = (", raw_geometry)
    target_visibility = source.index(
        'elif condition_id == "scene.target_visibility_lost":',
        temporal_identity,
    )
    raw_target_gate = source.index(
        "targets - set(current_geometry)", target_visibility
    )
    assert raw_geometry < temporal_identity < target_visibility < raw_target_gate
    target_drift = source.index(
        'elif condition_id == "scene.target_geometry_drift":', raw_target_gate
    )
    intended_attachment_skip = source.index(
        "if retained_attachment_is_fresh(", target_drift
    )
    baseline_geometry = source.index(
        "baseline = baseline_geometry.get(entity_id)", target_drift
    )
    assert target_drift < intended_attachment_skip < baseline_geometry
