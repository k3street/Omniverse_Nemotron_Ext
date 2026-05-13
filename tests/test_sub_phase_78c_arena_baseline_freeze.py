"""Phase 78c contract tests — arena baseline freeze.

Covers:
  1. Phase metadata shape
  2. freeze() + get() round-trip (data survives serialise/deserialise)
  3. freeze() stats correctness (manually verified mean/std/min/max)
  4. compare_to_baseline() regression detection (>5% drop → regressed=True)
  5. compare_to_baseline() no-regression positive case
  6. delta_report() iterates all scenarios in the store
  7. list_scenarios() deduplicates (each scenario appears once)
"""
from __future__ import annotations

import math

import pytest

pytestmark = pytest.mark.l0

# ---------------------------------------------------------------------------
# Module import helpers
# ---------------------------------------------------------------------------


def _mod():
    from service.isaac_assist_service.multimodal import sub_phase_78c_arena_baseline_freeze as m
    return m


def _leaderboard(tmp_path):
    from service.isaac_assist_service.multimodal.isaaclab_arena_leaderboard import Leaderboard
    return Leaderboard(path=tmp_path / "lb.json")


# ---------------------------------------------------------------------------
# 1. Metadata
# ---------------------------------------------------------------------------


def test_phase_78c_metadata():
    md = _mod().get_phase_metadata()
    assert md["phase"] == "78c"
    assert md["status"] == "landed"
    assert "title" in md
    assert "spec_ref" in md


# ---------------------------------------------------------------------------
# 2. freeze + get round-trip
# ---------------------------------------------------------------------------


def test_freeze_get_roundtrip(tmp_path):
    lb = _leaderboard(tmp_path)
    for score in [0.8, 0.9, 0.7, 0.6, 0.95]:
        lb.submit("scenario_A", score, "agent_x")

    m = _mod()
    store = m.ArenaBaselineStore(tmp_path / "baselines.json")
    frozen = store.freeze(lb, "scenario_A", n_runs=5)

    retrieved = store.get("scenario_A")
    assert retrieved is not None
    assert retrieved.scenario_id == frozen.scenario_id
    assert retrieved.n_runs == frozen.n_runs
    assert math.isclose(retrieved.mean_score, frozen.mean_score, rel_tol=1e-9)
    assert math.isclose(retrieved.std_score, frozen.std_score, rel_tol=1e-9)
    assert math.isclose(retrieved.min_score, frozen.min_score, rel_tol=1e-9)
    assert math.isclose(retrieved.max_score, frozen.max_score, rel_tol=1e-9)
    assert retrieved.individual_scores == frozen.individual_scores


# ---------------------------------------------------------------------------
# 3. Stats correctness on known data
# ---------------------------------------------------------------------------


def test_freeze_stats_correct(tmp_path):
    # Known scores: 10, 20, 30, 40, 50
    # mean = 30
    # variance (pop) = ((10-30)^2 + (20-30)^2 + (30-30)^2 + (40-30)^2 + (50-30)^2) / 5
    #                = (400 + 100 + 0 + 100 + 400) / 5 = 200
    # std  = sqrt(200) ≈ 14.142...
    # min  = 10, max = 50
    lb = _leaderboard(tmp_path)
    for score in [10.0, 20.0, 30.0, 40.0, 50.0]:
        lb.submit("stats_scenario", score, "agent")

    m = _mod()
    store = m.ArenaBaselineStore(tmp_path / "baselines.json")
    bl = store.freeze(lb, "stats_scenario", n_runs=5)

    assert bl.n_runs == 5
    # top_k returns descending: [50, 40, 30, 20, 10]
    assert set(bl.individual_scores) == {10.0, 20.0, 30.0, 40.0, 50.0}
    assert math.isclose(bl.mean_score, 30.0, abs_tol=1e-9)
    assert math.isclose(bl.std_score, math.sqrt(200), rel_tol=1e-9)
    assert math.isclose(bl.min_score, 10.0, abs_tol=1e-9)
    assert math.isclose(bl.max_score, 50.0, abs_tol=1e-9)


# ---------------------------------------------------------------------------
# 4. compare_to_baseline — regression detection (>5% drop)
# ---------------------------------------------------------------------------


def test_compare_regression_detected(tmp_path):
    lb = _leaderboard(tmp_path)
    for score in [1.0, 1.0, 1.0, 1.0, 1.0]:
        lb.submit("reg_scenario", score, "agent")

    m = _mod()
    store = m.ArenaBaselineStore(tmp_path / "baselines.json")
    baseline = store.freeze(lb, "reg_scenario", n_runs=5)

    # Current scores drop by 10% (well above the 5% threshold)
    current_runs = [0.9, 0.9, 0.9, 0.9, 0.9]
    delta = m.compare_to_baseline("reg_scenario", current_runs, baseline)

    assert delta.regressed is True
    assert delta.delta_pct < -5.0
    assert math.isclose(delta.current_mean, 0.9, rel_tol=1e-9)
    assert math.isclose(delta.baseline_mean, 1.0, rel_tol=1e-9)


# ---------------------------------------------------------------------------
# 5. compare_to_baseline — no regression positive case
# ---------------------------------------------------------------------------


def test_compare_no_regression(tmp_path):
    lb = _leaderboard(tmp_path)
    for score in [1.0, 1.0, 1.0, 1.0, 1.0]:
        lb.submit("stable_scenario", score, "agent")

    m = _mod()
    store = m.ArenaBaselineStore(tmp_path / "baselines.json")
    baseline = store.freeze(lb, "stable_scenario", n_runs=5)

    # Current scores drop by only 3% — within the 5% tolerance
    current_runs = [0.97, 0.97, 0.97, 0.97, 0.97]
    delta = m.compare_to_baseline("stable_scenario", current_runs, baseline)

    assert delta.regressed is False
    assert delta.delta_pct > -5.0

    # Improvement also should not be flagged as a regression
    current_improved = [1.05, 1.05, 1.05]
    delta_improved = m.compare_to_baseline("stable_scenario", current_improved, baseline)
    assert delta_improved.regressed is False
    assert delta_improved.delta_pct > 0


# ---------------------------------------------------------------------------
# 6. delta_report iterates all scenarios
# ---------------------------------------------------------------------------


def test_delta_report_all_scenarios(tmp_path):
    lb = _leaderboard(tmp_path)
    scenarios = ["alpha", "beta", "gamma"]
    for sid in scenarios:
        for score in [0.5, 0.6, 0.7, 0.8, 0.9]:
            lb.submit(sid, score, "agent")

    m = _mod()
    store = m.ArenaBaselineStore(tmp_path / "baselines.json")
    for sid in scenarios:
        store.freeze(lb, sid, n_runs=5)

    report = m.delta_report(store, lb)

    # One row per frozen scenario
    assert len(report) == len(scenarios)
    reported_ids = {d.scenario_id for d in report}
    assert reported_ids == set(scenarios)

    # Each delta should have sane structure
    for d in report:
        assert hasattr(d, "baseline_mean")
        assert hasattr(d, "current_mean")
        assert hasattr(d, "regressed")


# ---------------------------------------------------------------------------
# 7. list_scenarios deduplicates
# ---------------------------------------------------------------------------


def test_list_scenarios_deduplicates(tmp_path):
    lb = _leaderboard(tmp_path)
    # Submit multiple entries to the same scenario
    for _ in range(10):
        lb.submit("dup_scenario", 1.0, "agent")

    m = _mod()
    store = m.ArenaBaselineStore(tmp_path / "baselines.json")
    store.freeze(lb, "dup_scenario", n_runs=5)
    # Freeze a second scenario too
    lb2 = _leaderboard(tmp_path)
    for _ in range(5):
        lb2.submit("other_scenario", 0.5, "agent")
    store.freeze(lb2, "other_scenario", n_runs=5)

    scenarios = store.list_scenarios()
    # Each scenario appears exactly once
    assert len(scenarios) == len(set(scenarios))
    assert set(scenarios) == {"dup_scenario", "other_scenario"}
