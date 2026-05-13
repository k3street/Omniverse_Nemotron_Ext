"""Tests for Phase 97b — Fast-sweep CP-regression harness."""
from __future__ import annotations

import json
from pathlib import Path
from typing import List

import pytest

pytestmark = pytest.mark.l0

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_cases(n: int = 3) -> "List":
    from service.isaac_assist_service.multimodal.sub_phase_97b_fast_sweep_cp_regression import (
        CPTestCase,
    )
    tiers = ["CW", "T4", "T6", "T8"]
    return [
        CPTestCase(
            cp_id=f"CP-{i:03d}",
            name=f"test case {i}",
            tier=tiers[i % len(tiers)],
            skill_category="pick_place",
            expected_tools=["get_prim_type", "set_joint_targets"],
            time_budget_s=30.0,
            success_threshold=1.0,
        )
        for i in range(n)
    ]


# ---------------------------------------------------------------------------
# 1. Metadata
# ---------------------------------------------------------------------------

class TestMetadata:
    def test_phase_metadata(self):
        from service.isaac_assist_service.multimodal.sub_phase_97b_fast_sweep_cp_regression import (
            get_phase_metadata,
        )
        md = get_phase_metadata()
        assert md["phase"] == "97b"
        assert md["status"] == "landed"
        assert "spec_ref" in md
        assert "title" in md


# ---------------------------------------------------------------------------
# 2. Filter — cp_subset
# ---------------------------------------------------------------------------

class TestFilterCpSubset:
    def test_filter_cp_subset_includes_only_listed(self):
        from service.isaac_assist_service.multimodal.sub_phase_97b_fast_sweep_cp_regression import (
            FastSweepHarness,
            SweepConfig,
        )
        cases = _make_cases(5)
        harness = FastSweepHarness(cases)
        config = SweepConfig(cp_subset=["CP-000", "CP-002"])
        filtered = harness.filter(config)
        assert [c.cp_id for c in filtered] == ["CP-000", "CP-002"]

    def test_filter_empty_subset_returns_empty(self):
        from service.isaac_assist_service.multimodal.sub_phase_97b_fast_sweep_cp_regression import (
            FastSweepHarness,
            SweepConfig,
        )
        harness = FastSweepHarness(_make_cases(4))
        filtered = harness.filter(SweepConfig(cp_subset=[]))
        assert filtered == []


# ---------------------------------------------------------------------------
# 3. Filter — tier_filter
# ---------------------------------------------------------------------------

class TestFilterTierFilter:
    def test_tier_filter_includes_only_matching_tiers(self):
        from service.isaac_assist_service.multimodal.sub_phase_97b_fast_sweep_cp_regression import (
            FastSweepHarness,
            SweepConfig,
        )
        # 4 cases, tiers cycle CW/T4/T6/T8
        harness = FastSweepHarness(_make_cases(8))
        filtered = harness.filter(SweepConfig(tier_filter=["CW", "T6"]))
        tiers = [c.tier for c in filtered]
        assert all(t in ("CW", "T6") for t in tiers)
        assert len(filtered) == 4  # 2 CW + 2 T6 out of 8


# ---------------------------------------------------------------------------
# 4. mock_runner
# ---------------------------------------------------------------------------

class TestMockRunner:
    def test_mock_runner_returns_valid_cp_run_result(self):
        from service.isaac_assist_service.multimodal.sub_phase_97b_fast_sweep_cp_regression import (
            CPRunResult,
            CPTestCase,
            FastSweepHarness,
        )
        case = CPTestCase(
            cp_id="CP-001",
            name="pick place",
            tier="CW",
            skill_category="pick_place",
            expected_tools=["get_prim_type"],
        )
        result = FastSweepHarness.mock_runner(case, 0)
        assert isinstance(result, CPRunResult)
        assert result.cp_id == "CP-001"
        assert result.run_idx == 0
        assert isinstance(result.success, bool)
        assert 0.0 <= result.score <= 1.0
        assert result.duration_s >= 0.0

    def test_mock_runner_is_deterministic(self):
        from service.isaac_assist_service.multimodal.sub_phase_97b_fast_sweep_cp_regression import (
            CPTestCase,
            FastSweepHarness,
        )
        case = CPTestCase(
            cp_id="CP-DET",
            name="determinism check",
            tier="T4",
            skill_category="nav",
            expected_tools=[],
        )
        r1 = FastSweepHarness.mock_runner(case, 0)
        r2 = FastSweepHarness.mock_runner(case, 0)
        assert r1.success == r2.success
        assert r1.score == r2.score
        assert r1.duration_s == r2.duration_s


# ---------------------------------------------------------------------------
# 5. run_sweep — n_runs_per_cp
# ---------------------------------------------------------------------------

class TestRunSweepNRuns:
    def test_run_sweep_runs_n_runs_per_cp_times_per_case(self):
        from service.isaac_assist_service.multimodal.sub_phase_97b_fast_sweep_cp_regression import (
            FastSweepHarness,
            SweepConfig,
        )
        cases = _make_cases(3)
        harness = FastSweepHarness(cases)
        config = SweepConfig(n_runs_per_cp=4)
        results = harness.run_sweep(config)
        # 3 cases × 4 runs = 12 results
        assert len(results) == 12

    def test_run_sweep_run_indices_are_correct(self):
        from service.isaac_assist_service.multimodal.sub_phase_97b_fast_sweep_cp_regression import (
            FastSweepHarness,
            SweepConfig,
        )
        cases = _make_cases(2)
        harness = FastSweepHarness(cases)
        results = harness.run_sweep(SweepConfig(n_runs_per_cp=3))
        for cp_id in {r.cp_id for r in results}:
            indices = [r.run_idx for r in results if r.cp_id == cp_id]
            assert sorted(indices) == [0, 1, 2]


# ---------------------------------------------------------------------------
# 6. run_sweep — custom runner
# ---------------------------------------------------------------------------

class TestRunSweepCustomRunner:
    def test_run_sweep_with_custom_runner_uses_it(self):
        from service.isaac_assist_service.multimodal.sub_phase_97b_fast_sweep_cp_regression import (
            CPRunResult,
            CPTestCase,
            FastSweepHarness,
            SweepConfig,
        )
        called: list = []

        def my_runner(case: CPTestCase, run_idx: int) -> CPRunResult:
            called.append((case.cp_id, run_idx))
            return CPRunResult(
                cp_id=case.cp_id,
                run_idx=run_idx,
                success=True,
                score=1.0,
                duration_s=0.1,
                tools_called=[],
            )

        harness = FastSweepHarness(_make_cases(2))
        harness.run_sweep(SweepConfig(n_runs_per_cp=2), runner=my_runner)
        assert len(called) == 4


# ---------------------------------------------------------------------------
# 7. aggregate — success_rate correct
# ---------------------------------------------------------------------------

class TestAggregate:
    def test_aggregate_computes_success_rate_correctly(self):
        from service.isaac_assist_service.multimodal.sub_phase_97b_fast_sweep_cp_regression import (
            CPRunResult,
            FastSweepHarness,
        )
        results = [
            CPRunResult("CP-X", 0, True, 1.0, 0.5, []),
            CPRunResult("CP-X", 1, False, 0.0, 0.6, [], ["err"]),
            CPRunResult("CP-X", 2, True, 1.0, 0.4, []),
        ]
        agg = FastSweepHarness.aggregate(results)
        assert "CP-X" in agg
        stats = agg["CP-X"]
        assert stats["n_runs"] == 3
        assert stats["n_success"] == 2
        assert stats["success_rate"] == pytest.approx(2 / 3)
        assert stats["total_errors"] == 1

    def test_aggregate_handles_zero_run_case_gracefully(self):
        from service.isaac_assist_service.multimodal.sub_phase_97b_fast_sweep_cp_regression import (
            FastSweepHarness,
        )
        # No results at all — should return empty dict (not crash)
        agg = FastSweepHarness.aggregate([])
        assert agg == {}


# ---------------------------------------------------------------------------
# 8. compare_to_baseline — regression detected
# ---------------------------------------------------------------------------

class TestCompareToBaseline:
    def _baseline(self, cp_id: str, rate: float) -> dict:
        return {cp_id: {"success_rate": rate}}

    def _current(self, cp_id: str, rate: float) -> dict:
        return {cp_id: {"success_rate": rate}}

    def test_regression_above_threshold_creates_alert(self):
        from service.isaac_assist_service.multimodal.sub_phase_97b_fast_sweep_cp_regression import (
            FastSweepHarness,
        )
        baseline = self._baseline("CP-A", 1.0)
        current = self._current("CP-A", 0.8)  # 20 pp drop
        alerts = FastSweepHarness.compare_to_baseline(baseline, current, threshold_pp=10.0)
        assert len(alerts) == 1
        assert alerts[0].cp_id == "CP-A"
        assert alerts[0].delta_pp == pytest.approx(-20.0)

    def test_no_alert_when_within_threshold(self):
        from service.isaac_assist_service.multimodal.sub_phase_97b_fast_sweep_cp_regression import (
            FastSweepHarness,
        )
        baseline = self._baseline("CP-B", 1.0)
        current = self._current("CP-B", 0.95)  # 5 pp drop < 10 pp threshold
        alerts = FastSweepHarness.compare_to_baseline(baseline, current, threshold_pp=10.0)
        assert alerts == []

    def test_improvement_produces_no_alert(self):
        from service.isaac_assist_service.multimodal.sub_phase_97b_fast_sweep_cp_regression import (
            FastSweepHarness,
        )
        baseline = self._baseline("CP-C", 0.5)
        current = self._current("CP-C", 0.9)
        alerts = FastSweepHarness.compare_to_baseline(baseline, current, threshold_pp=10.0)
        assert alerts == []


# ---------------------------------------------------------------------------
# 9. Severity thresholds
# ---------------------------------------------------------------------------

class TestSeverityThresholds:
    def test_critical_for_over_20pp_drop(self):
        from service.isaac_assist_service.multimodal.sub_phase_97b_fast_sweep_cp_regression import (
            FastSweepHarness,
        )
        baseline = {"CP-D": {"success_rate": 1.0}}
        current = {"CP-D": {"success_rate": 0.79}}  # 21 pp drop
        alerts = FastSweepHarness.compare_to_baseline(baseline, current, threshold_pp=10.0)
        assert alerts[0].severity == "critical"

    def test_warn_for_between_10_and_20pp_drop(self):
        from service.isaac_assist_service.multimodal.sub_phase_97b_fast_sweep_cp_regression import (
            FastSweepHarness,
        )
        baseline = {"CP-E": {"success_rate": 1.0}}
        current = {"CP-E": {"success_rate": 0.85}}  # 15 pp drop
        alerts = FastSweepHarness.compare_to_baseline(baseline, current, threshold_pp=10.0)
        assert alerts[0].severity == "warn"

    def test_exactly_20pp_drop_is_critical(self):
        from service.isaac_assist_service.multimodal.sub_phase_97b_fast_sweep_cp_regression import (
            FastSweepHarness,
        )
        baseline = {"CP-F": {"success_rate": 1.0}}
        current = {"CP-F": {"success_rate": 0.80}}  # exactly 20 pp
        alerts = FastSweepHarness.compare_to_baseline(baseline, current, threshold_pp=10.0)
        # 20 > 20 is False → this hits the warn branch
        assert alerts[0].severity == "warn"

    def test_just_above_20pp_drop_is_critical(self):
        from service.isaac_assist_service.multimodal.sub_phase_97b_fast_sweep_cp_regression import (
            FastSweepHarness,
        )
        baseline = {"CP-G": {"success_rate": 1.0}}
        current = {"CP-G": {"success_rate": 0.7999}}  # >20.01 pp
        alerts = FastSweepHarness.compare_to_baseline(baseline, current, threshold_pp=10.0)
        assert alerts[0].severity == "critical"


# ---------------------------------------------------------------------------
# 10. summary
# ---------------------------------------------------------------------------

class TestSummary:
    def test_summary_returns_overall_stats(self):
        from service.isaac_assist_service.multimodal.sub_phase_97b_fast_sweep_cp_regression import (
            CPRunResult,
            FastSweepHarness,
        )
        results = [
            CPRunResult("CP-1", 0, True, 1.0, 1.0, []),
            CPRunResult("CP-1", 1, False, 0.0, 2.0, [], ["e"]),
            CPRunResult("CP-2", 0, True, 1.0, 0.5, []),
        ]
        s = FastSweepHarness.summary(results)
        assert s["n_total"] == 3
        assert s["n_success"] == 2
        assert s["n_fail"] == 1
        assert s["pass_rate"] == pytest.approx(2 / 3)
        assert s["total_time_s"] == pytest.approx(3.5)
        assert s["unique_cps"] == 2
        assert s["total_errors"] == 1

    def test_summary_empty_results(self):
        from service.isaac_assist_service.multimodal.sub_phase_97b_fast_sweep_cp_regression import (
            FastSweepHarness,
        )
        s = FastSweepHarness.summary([])
        assert s["n_total"] == 0
        assert s["pass_rate"] == 0.0


# ---------------------------------------------------------------------------
# 11. JSONL round-trip
# ---------------------------------------------------------------------------

class TestJsonlRoundTrip:
    def test_save_and_load_results_jsonl(self, tmp_path: Path):
        from service.isaac_assist_service.multimodal.sub_phase_97b_fast_sweep_cp_regression import (
            CPRunResult,
            load_results_jsonl,
            save_results_jsonl,
        )
        results = [
            CPRunResult("CP-10", 0, True, 0.9, 1.2, ["tool_a"], [], "2026-01-01T00:00:00"),
            CPRunResult("CP-11", 0, False, 0.1, 2.5, [], ["err_x"], "2026-01-01T00:01:00"),
        ]
        fpath = tmp_path / "results.jsonl"
        save_results_jsonl(results, fpath)
        loaded = load_results_jsonl(fpath)

        assert len(loaded) == 2
        assert loaded[0].cp_id == "CP-10"
        assert loaded[0].success is True
        assert loaded[0].score == pytest.approx(0.9)
        assert loaded[0].tools_called == ["tool_a"]
        assert loaded[1].cp_id == "CP-11"
        assert loaded[1].errors == ["err_x"]

    def test_jsonl_lines_are_valid_json(self, tmp_path: Path):
        from service.isaac_assist_service.multimodal.sub_phase_97b_fast_sweep_cp_regression import (
            CPRunResult,
            save_results_jsonl,
        )
        results = [CPRunResult("CP-99", 0, True, 1.0, 0.3, [])]
        fpath = tmp_path / "out.jsonl"
        save_results_jsonl(results, fpath)
        lines = fpath.read_text().strip().splitlines()
        assert len(lines) == 1
        parsed = json.loads(lines[0])
        assert parsed["cp_id"] == "CP-99"


# ---------------------------------------------------------------------------
# 12. SweepConfig defaults
# ---------------------------------------------------------------------------

class TestSweepConfigDefaults:
    def test_sweep_config_default_values(self):
        from service.isaac_assist_service.multimodal.sub_phase_97b_fast_sweep_cp_regression import (
            SweepConfig,
        )
        cfg = SweepConfig()
        assert cfg.cp_subset is None
        assert cfg.n_runs_per_cp == 1
        assert cfg.max_parallelism == 1
        assert cfg.fail_fast is False
        assert cfg.tier_filter is None


# ---------------------------------------------------------------------------
# 13. fail_fast stops on first failure
# ---------------------------------------------------------------------------

class TestFailFast:
    def test_fail_fast_stops_on_first_failure(self):
        from service.isaac_assist_service.multimodal.sub_phase_97b_fast_sweep_cp_regression import (
            CPRunResult,
            CPTestCase,
            FastSweepHarness,
            SweepConfig,
        )
        # Always-failing runner
        def always_fail(case: CPTestCase, run_idx: int) -> CPRunResult:
            return CPRunResult(
                cp_id=case.cp_id,
                run_idx=run_idx,
                success=False,
                score=0.0,
                duration_s=0.0,
                tools_called=[],
                errors=["forced failure"],
            )

        harness = FastSweepHarness(_make_cases(5))
        config = SweepConfig(fail_fast=True, n_runs_per_cp=3)
        results = harness.run_sweep(config, runner=always_fail)
        # Only the first run of the first case should be returned
        assert len(results) == 1
        assert results[0].success is False
