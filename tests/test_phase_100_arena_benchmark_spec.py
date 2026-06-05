"""Phase 100 contract tests — Arena benchmark spec layer.

Tests the scenario registry, scoring rubric, and head-to-head comparator.
All tests are pure-Python (no Kit / GR00T required).
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.l0

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mod():
    from service.isaac_assist_service.multimodal import arena_benchmark_spec as m
    return m


def _runner(scenarios=None):
    from service.isaac_assist_service.multimodal.arena_benchmark_spec import (
        ArenaBenchmarkRunner,
        ARENA_SCENARIOS,
    )
    return ArenaBenchmarkRunner(scenarios=scenarios if scenarios is not None else ARENA_SCENARIOS)


def _result(scenario_id, agent, score, time_used_s=10.0, success=True, notes=""):
    from service.isaac_assist_service.multimodal.arena_benchmark_spec import BenchmarkResult
    return BenchmarkResult(
        scenario_id=scenario_id,
        agent=agent,
        score=score,
        time_used_s=time_used_s,
        success=success,
        notes=notes,
    )


# ---------------------------------------------------------------------------
# Test 1 — metadata contract
# ---------------------------------------------------------------------------


def test_phase_100_metadata():
    m = _mod()
    md = m.get_phase_metadata()
    assert md["phase"] == 100
    assert md["status"] == "landed"
    assert "title" in md
    assert "spec_ref" in md


# ---------------------------------------------------------------------------
# Test 2 — ARENA_SCENARIOS has at least 20 entries
# ---------------------------------------------------------------------------


def test_arena_scenarios_count():
    from service.isaac_assist_service.multimodal.arena_benchmark_spec import ARENA_SCENARIOS
    assert len(ARENA_SCENARIOS) >= 20


# ---------------------------------------------------------------------------
# Test 3 — all 8 categories present
# ---------------------------------------------------------------------------


def test_all_categories_present():
    from service.isaac_assist_service.multimodal.arena_benchmark_spec import ARENA_SCENARIOS

    expected = {
        "pick_place", "assembly", "navigation", "inspection",
        "welding", "palletizing", "sorting", "kitting",
    }
    actual = {s.category for s in ARENA_SCENARIOS}
    assert expected <= actual, f"Missing categories: {expected - actual}"


# ---------------------------------------------------------------------------
# Test 4 — all 5 difficulty levels present
# ---------------------------------------------------------------------------


def test_all_difficulty_levels_present():
    from service.isaac_assist_service.multimodal.arena_benchmark_spec import ARENA_SCENARIOS

    expected = {"L1", "L2", "L3", "L4", "L5"}
    actual = {s.difficulty for s in ARENA_SCENARIOS}
    assert expected <= actual, f"Missing difficulties: {expected - actual}"


# ---------------------------------------------------------------------------
# Test 5 — score_against_rubric: success=False → 0.0
# ---------------------------------------------------------------------------


def test_score_rubric_failure_returns_zero():
    from service.isaac_assist_service.multimodal.arena_benchmark_spec import score_against_rubric
    result = score_against_rubric(
        time_used_s=10.0, time_limit_s=30.0, success=False, max_score=100.0
    )
    assert result == 0.0


# ---------------------------------------------------------------------------
# Test 6 — score_against_rubric: success=True at 50% time → 75% max_score
# ---------------------------------------------------------------------------


def test_score_rubric_half_time():
    from service.isaac_assist_service.multimodal.arena_benchmark_spec import score_against_rubric
    # 1 - 0.5 * (15/30) = 1 - 0.25 = 0.75
    result = score_against_rubric(
        time_used_s=15.0, time_limit_s=30.0, success=True, max_score=100.0
    )
    assert abs(result - 75.0) < 1e-6


# ---------------------------------------------------------------------------
# Test 7 — score_against_rubric: clamped to 0.5*max even at 100% time
# ---------------------------------------------------------------------------


def test_score_rubric_clamp_at_full_time():
    from service.isaac_assist_service.multimodal.arena_benchmark_spec import score_against_rubric
    # At 100% time: 1 - 0.5*1 = 0.5 * max_score → exactly at floor, still valid
    result = score_against_rubric(
        time_used_s=30.0, time_limit_s=30.0, success=True, max_score=100.0
    )
    assert result == 50.0

    # At 110% time (over budget): still clamped to floor
    result_over = score_against_rubric(
        time_used_s=33.0, time_limit_s=30.0, success=True, max_score=100.0
    )
    assert result_over == 50.0


# ---------------------------------------------------------------------------
# Test 8 — score_against_rubric: maximum score at zero time
# ---------------------------------------------------------------------------


def test_score_rubric_zero_time_gives_max():
    from service.isaac_assist_service.multimodal.arena_benchmark_spec import score_against_rubric
    result = score_against_rubric(
        time_used_s=0.0, time_limit_s=60.0, success=True, max_score=80.0
    )
    assert result == 80.0


# ---------------------------------------------------------------------------
# Test 9 — compare: IA wins all scenarios → ia_wins=True everywhere, winrate=1.0
# ---------------------------------------------------------------------------


def test_compare_ia_wins_all():
    from service.isaac_assist_service.multimodal.arena_benchmark_spec import ARENA_SCENARIOS

    runner = _runner()
    # IA scores 10 pts above hand-crafted on every scenario
    hand = [_result(s.scenario_id, "hand_crafted", 70.0) for s in ARENA_SCENARIOS]
    ia = [_result(s.scenario_id, "IA_authored", 85.0) for s in ARENA_SCENARIOS]

    comparisons = runner.compare(hand, ia)

    assert len(comparisons) == len(ARENA_SCENARIOS)
    assert all(c.ia_wins for c in comparisons), "Expected IA to win every scenario"
    assert all(c.winner == "IA_authored" for c in comparisons)
    assert runner.overall_ia_winrate(comparisons) == 1.0


# ---------------------------------------------------------------------------
# Test 10 — compare: tie within tolerance (delta < 0.01 * max_score)
# ---------------------------------------------------------------------------


def test_compare_tie_within_tolerance():
    from service.isaac_assist_service.multimodal.arena_benchmark_spec import (
        ARENA_SCENARIOS,
        ArenaBenchmarkRunner,
    )

    # Use a single scenario with max_score=100 → tie threshold = 1.0
    scenario = ARENA_SCENARIOS[0]  # pp_01, max_score=100
    runner = ArenaBenchmarkRunner(scenarios=[scenario])

    hand = [_result(scenario.scenario_id, "hand_crafted", 80.0)]
    ia = [_result(scenario.scenario_id, "IA_authored", 80.5)]  # delta=0.5 < 1.0

    comparisons = runner.compare(hand, ia)
    assert len(comparisons) == 1
    assert comparisons[0].winner == "tie"
    assert not comparisons[0].ia_wins


# ---------------------------------------------------------------------------
# Test 11 — category_breakdown sums to total number of scenarios
# ---------------------------------------------------------------------------


def test_category_breakdown_total():
    from service.isaac_assist_service.multimodal.arena_benchmark_spec import ARENA_SCENARIOS

    runner = _runner()
    hand = [_result(s.scenario_id, "hand_crafted", 70.0) for s in ARENA_SCENARIOS]
    ia = [_result(s.scenario_id, "IA_authored", 80.0) for s in ARENA_SCENARIOS]
    comparisons = runner.compare(hand, ia)

    breakdown = runner.category_breakdown(comparisons)
    total = sum(v["n_scenarios"] for v in breakdown.values())
    assert total == len(ARENA_SCENARIOS)


# ---------------------------------------------------------------------------
# Test 12 — difficulty_breakdown sums to total number of scenarios
# ---------------------------------------------------------------------------


def test_difficulty_breakdown_total():
    from service.isaac_assist_service.multimodal.arena_benchmark_spec import ARENA_SCENARIOS

    runner = _runner()
    hand = [_result(s.scenario_id, "hand_crafted", 70.0) for s in ARENA_SCENARIOS]
    ia = [_result(s.scenario_id, "IA_authored", 80.0) for s in ARENA_SCENARIOS]
    comparisons = runner.compare(hand, ia)

    breakdown = runner.difficulty_breakdown(comparisons)
    total = sum(v["n_scenarios"] for v in breakdown.values())
    assert total == len(ARENA_SCENARIOS)


# ---------------------------------------------------------------------------
# Test 13 — scenario_ids are unique
# ---------------------------------------------------------------------------


def test_scenario_ids_unique():
    from service.isaac_assist_service.multimodal.arena_benchmark_spec import ARENA_SCENARIOS

    ids = [s.scenario_id for s in ARENA_SCENARIOS]
    assert len(ids) == len(set(ids)), "Duplicate scenario_id found"


# ---------------------------------------------------------------------------
# Test 14 — hand_crafted_reference_score in [0, max_score] where present
# ---------------------------------------------------------------------------


def test_reference_scores_within_bounds():
    from service.isaac_assist_service.multimodal.arena_benchmark_spec import ARENA_SCENARIOS

    for s in ARENA_SCENARIOS:
        if s.hand_crafted_reference_score is not None:
            assert 0.0 <= s.hand_crafted_reference_score <= s.max_score, (
                f"{s.scenario_id}: reference_score {s.hand_crafted_reference_score} "
                f"out of [0, {s.max_score}]"
            )


# ---------------------------------------------------------------------------
# Test 15 — overall_ia_winrate returns 0.0 on empty list
# ---------------------------------------------------------------------------


def test_overall_ia_winrate_empty():
    runner = _runner()
    assert runner.overall_ia_winrate([]) == 0.0
