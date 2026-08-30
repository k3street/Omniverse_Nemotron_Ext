from copy import deepcopy
from pathlib import Path

import pytest

from scripts.world_effect_closed_loop import (
    WorldEffectClosedLoopError,
    WorldEffectSequenceBudget,
    assess_world_effect_progress,
)
from scripts.world_goal_activation import shadow_world_capability_registry
from scripts.world_goal_graph_membership import SceneMembershipLease
from scripts.world_predicate_evaluator_registry import (
    rgbd_world_predicate_evaluator_registry,
)
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


def test_lost_goal_geometry_requires_observation_instead_of_motion():
    changed = deepcopy(inventory())
    red = next(item for item in changed["entities"] if item["entity_id"] == "red_block")
    red["geometry"] = {}
    result = assess(changed)

    assert result.status == "observe_again"
    assert result.selected_goal_satisfied is None
    assert not result.may_plan_another_operation


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
    assert "for invocation_attempt in range(1, 3):" in source
    assert "do_not_repeat_rejected_arguments" in source
    assert '"task_completion_claimed": False' in source


def test_runner_contract_exposes_a_bounded_sequence_not_one_open_loop_call():
    source = Path("scripts/run_gemini_robotics_robolab.py").read_text()

    assert '"--world-effect-max-operations"' in source
    assert "WorldEffectSequenceBudget(" in source
    assert "Every operation receives a fresh-evidence, single-use permit" in source
    assert 'default=120.0' in source
    assert "wall-clock deadman" in source
