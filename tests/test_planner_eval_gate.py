import copy
import json
from pathlib import Path

import pytest

from scripts.planner_eval_gate import (
    acceptance_digest,
    check_episode,
    is_frozen,
    parse_budget,
    wilson_interval,
)

ACCEPTANCE = json.loads(
    (Path(__file__).resolve().parents[1] / "config/planner_eval/blocks_in_bin_astra_v1.json").read_text()
)
LOG = "[budget] call 1: ...\n[budget] final: 40 calls, 659099 in / 13703 out, $7.28 of $25.00\n"


def passing_trace():
    """Shape of a real passing BlocksInBinTask trace (2026-09-27), trimmed to what the gate reads."""
    return {
        "status": "complete",
        "physics_steps_are_local": True,
        "critic_memory_applied": {"source_model": None, "lessons": []},
        "initial_state": {"target_receptacle_xyz": [0.467, -0.191, 0.003]},
        "task_feasibility_preflight": {
            "capability_evidence": {
                "target_receptacle": {
                    "visible_rgbd_geometry": {
                        "visible_aabb_min_base_m": [0.263, -0.315, 0.006],
                        "visible_aabb_max_base_m": [0.660, -0.051, 0.109],
                    }
                }
            }
        },
        "final": {
            "movable_object_xyz": [0.4671, -0.1885, 0.0338],
            "target_receptacle_xyz": [0.467, -0.191, 0.003],
            "object_height_above_target_m": 0.0308,
            "contact_telemetry": {"passed": True},
            "tests": {"success": True},
        },
        "release_retreat": {"eef_retreat_z_m": 0.0777, "object_motion_during_retreat_m": 1.3e-5},
    }


def test_the_recorded_success_passes_every_check():
    passed, details = check_episode(passing_trace(), LOG, ACCEPTANCE)
    assert passed, details["failed"]
    assert details["cost_usd"] == pytest.approx(7.28)
    assert details["model_calls"] == 40


def test_the_runners_own_pass_is_not_enough():
    trace = passing_trace()
    trace["final"]["movable_object_xyz"] = [0.40, 0.10, 0.02]  # on the table, beside the bin
    passed, details = check_episode(trace, LOG, ACCEPTANCE)
    assert not passed
    assert details["failed"] == ["object_within_receptacle"]


def test_an_object_on_the_rim_is_inside_the_box_but_not_the_inset():
    trace = passing_trace()
    trace["final"]["movable_object_xyz"] = [0.650, -0.19, 0.11]  # 1 cm inside the wall
    trace["final"]["object_height_above_target_m"] = 0.107
    passed, details = check_episode(trace, LOG, ACCEPTANCE)
    assert set(details["failed"]) == {"object_within_receptacle", "object_resting_low"}


def test_a_pushed_bin_moves_the_footprint_with_it():
    trace = passing_trace()
    trace["final"]["target_receptacle_xyz"] = [0.467, -0.121, 0.003]  # bin shoved 7 cm in +y
    trace["final"]["movable_object_xyz"] = [0.4671, -0.08, 0.0338]  # outside the original footprint
    passed, details = check_episode(trace, LOG, ACCEPTANCE)
    assert passed, details["failed"]


def test_a_missing_trace_fails_instead_of_passing_nothing():
    passed, details = check_episode(None, "", ACCEPTANCE)
    assert not passed
    assert details["failed"] == ["trace_missing"]
    assert details["cost_usd"] is None


def test_lessons_carried_from_another_episode_fail_independence():
    trace = passing_trace()
    trace["critic_memory_applied"]["lessons"] = [{"lesson": "approach higher"}]
    passed, details = check_episode(trace, LOG, ACCEPTANCE)
    assert details["failed"] == ["independent_of_other_episodes"]


def test_the_object_dragged_on_retreat_fails():
    trace = passing_trace()
    trace["release_retreat"]["object_motion_during_retreat_m"] = 0.02
    passed, details = check_episode(trace, LOG, ACCEPTANCE)
    assert details["failed"] == ["object_left_in_place"]


def test_freezing_detects_a_later_edit():
    acceptance = copy.deepcopy(ACCEPTANCE)
    acceptance["frozen_utc"] = "2026-09-28T12:00:00Z"
    acceptance["frozen_sha256"] = acceptance_digest(acceptance)
    assert is_frozen(acceptance)
    acceptance["checks"]["object_within_receptacle_inset_m"] = 0.0  # loosened after freezing
    assert not is_frozen(acceptance)


def test_the_unfrozen_file_in_the_repo_is_not_frozen():
    assert not is_frozen(ACCEPTANCE)


def test_budget_uses_the_final_line():
    assert parse_budget("no budget here") == {"model_calls": None, "cost_usd": None}
    assert parse_budget(LOG)["cost_usd"] == pytest.approx(7.28)


def test_wilson_interval_is_wide_for_one_success():
    low, high = wilson_interval(1, 1)
    assert low < 0.25 and high == pytest.approx(1.0)
    low, high = wilson_interval(24, 30)
    assert 0.6 < low < 0.8 < high < 0.95
