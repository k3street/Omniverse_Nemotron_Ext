"""Phase 8d — tests for the stable-baseline taxonomy and regression harness.

Covers:
  * `classify()` three-tier mapping + invalid-input rejection
  * `BaselineStatus` `IntEnum` severity ordering (stable_fail < flaky < stable_ok)
  * `run_with_seed_set` deterministic ordering with a mock runner
  * Default runner raises `NotImplementedError`
  * `freeze_baseline` writes JSON and round-trips through Pydantic
  * `compare_to_baseline` covers stable, regression, and missing-file paths
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

pytestmark = pytest.mark.l0

from service.isaac_assist_service.qa.baseline import (
    BaselineDelta,
    BaselineSnapshot,
    compare_to_baseline,
    freeze_baseline,
)
from service.isaac_assist_service.qa.baseline_status import (
    BaselineStatus,
    classify,
)
from service.isaac_assist_service.qa.regression import (
    RegressionResult,
    RunResult,
    run_with_seed_set,
)


# ---------------------------------------------------------------------------
# classify() — three-tier mapping
# ---------------------------------------------------------------------------


def test_classify_stable_fail_when_zero_pass():
    """n_pass == 0 always maps to stable_fail, regardless of n_total."""
    assert classify(0, 0) == BaselineStatus.stable_fail
    assert classify(0, 1) == BaselineStatus.stable_fail
    assert classify(0, 5) == BaselineStatus.stable_fail


def test_classify_stable_ok_requires_threshold_and_all_pass():
    """stable_ok needs n_pass >= n_of_m AND n_pass == n_total."""
    assert classify(3, 3) == BaselineStatus.stable_ok
    assert classify(5, 5) == BaselineStatus.stable_ok
    # Custom threshold
    assert classify(2, 2, n_of_m=2) == BaselineStatus.stable_ok


def test_classify_flaky_when_some_pass_some_fail():
    """Some passes but not all => flaky."""
    assert classify(1, 3) == BaselineStatus.flaky
    assert classify(2, 3) == BaselineStatus.flaky
    assert classify(4, 5) == BaselineStatus.flaky


def test_classify_flaky_when_all_pass_but_below_threshold():
    """All passes but count below n_of_m => still flaky (not enough evidence)."""
    assert classify(2, 2) == BaselineStatus.flaky
    assert classify(1, 1) == BaselineStatus.flaky


def test_classify_rejects_invalid_inputs():
    """ValueError on inverted/negative counts."""
    with pytest.raises(ValueError):
        classify(-1, 5)
    with pytest.raises(ValueError):
        classify(3, -1)
    with pytest.raises(ValueError):
        classify(6, 5)


# ---------------------------------------------------------------------------
# BaselineStatus — severity ordering
# ---------------------------------------------------------------------------


def test_baseline_status_ordering():
    """Lower-is-worse: stable_fail < flaky < stable_ok."""
    assert BaselineStatus.stable_fail < BaselineStatus.flaky
    assert BaselineStatus.flaky < BaselineStatus.stable_ok
    assert BaselineStatus.stable_fail < BaselineStatus.stable_ok
    # Severity comparisons round-trip:
    assert min(BaselineStatus.flaky, BaselineStatus.stable_ok) == BaselineStatus.flaky
    assert max(BaselineStatus.stable_fail, BaselineStatus.flaky) == BaselineStatus.flaky


def test_baseline_status_int_values():
    """Persisted JSON integers must match the spec exactly."""
    assert int(BaselineStatus.stable_fail) == -1
    assert int(BaselineStatus.flaky) == 0
    assert int(BaselineStatus.stable_ok) == 1


# ---------------------------------------------------------------------------
# run_with_seed_set — deterministic ordering, mock runner
# ---------------------------------------------------------------------------


def test_run_with_seed_set_with_mock_runner_returns_regression_result():
    """Mock runner produces deterministic per-seed pass/fail."""
    # Seeds [10, 20, 30]: 10 and 30 pass, 20 fails.
    def runner(scenario_id: str, seed: int) -> bool:
        assert scenario_id == "CP-37"
        return seed != 20

    result = run_with_seed_set(
        scenario_id="CP-37",
        seeds=[10, 20, 30],
        n_runs_per_seed=1,
        runner=runner,
    )

    assert isinstance(result, RegressionResult)
    assert result.scenario_id == "CP-37"
    assert result.seeds == [10, 20, 30]
    assert result.n_runs_per_seed == 1
    assert result.total_runs == 3
    assert result.total_pass == 2
    assert result.status == BaselineStatus.flaky
    # Per-run order matches the input seed order:
    assert [r.seed for r in result.runs] == [10, 20, 30]
    assert [r.passed for r in result.runs] == [True, False, True]


def test_run_with_seed_set_same_seeds_same_order():
    """Two invocations with identical seeds produce identical run order."""
    def runner(scenario_id: str, seed: int) -> bool:
        return seed % 2 == 0

    seeds = [7, 4, 11, 2]
    r1 = run_with_seed_set("CP-x", seeds, runner=runner)
    r2 = run_with_seed_set("CP-x", seeds, runner=runner)

    assert [run.seed for run in r1.runs] == [run.seed for run in r2.runs]
    assert [run.passed for run in r1.runs] == [run.passed for run in r2.runs]
    assert r1.status == r2.status


def test_run_with_seed_set_n_runs_per_seed_iterates_correctly():
    """Each seed is executed n_runs_per_seed times in input order."""
    call_log: list[tuple[str, int]] = []

    def runner(scenario_id: str, seed: int) -> bool:
        call_log.append((scenario_id, seed))
        return True

    result = run_with_seed_set(
        scenario_id="CP-y",
        seeds=[1, 2],
        n_runs_per_seed=3,
        runner=runner,
    )

    # 2 seeds * 3 runs each = 6 calls, seeds iterated in order
    assert call_log == [
        ("CP-y", 1), ("CP-y", 1), ("CP-y", 1),
        ("CP-y", 2), ("CP-y", 2), ("CP-y", 2),
    ]
    assert result.total_runs == 6
    assert result.total_pass == 6
    assert result.status == BaselineStatus.stable_ok


def test_run_with_seed_set_captures_runner_exception_as_failure():
    """A runner exception becomes passed=False with error string populated."""
    def runner(scenario_id: str, seed: int) -> bool:
        if seed == 5:
            raise RuntimeError("boom")
        return True

    result = run_with_seed_set("CP-z", seeds=[1, 5, 9], runner=runner)
    # Index 1 is the failing seed
    assert result.runs[1].seed == 5
    assert result.runs[1].passed is False
    assert result.runs[1].error is not None
    assert "boom" in result.runs[1].error
    # Other runs succeeded
    assert result.runs[0].passed is True
    assert result.runs[2].passed is True
    assert result.total_pass == 2


def test_run_with_seed_set_default_runner_raises_not_implemented():
    """The default-runner placeholder must fail loudly when invoked."""
    with pytest.raises(NotImplementedError):
        run_with_seed_set("CP-anything", seeds=[0])


def test_run_with_seed_set_rejects_zero_n_runs_per_seed():
    """n_runs_per_seed < 1 is a contract violation."""
    with pytest.raises(ValueError):
        run_with_seed_set("CP-x", seeds=[0], n_runs_per_seed=0, runner=lambda s, x: True)


# ---------------------------------------------------------------------------
# freeze_baseline + BaselineSnapshot round-trip
# ---------------------------------------------------------------------------


def test_freeze_baseline_writes_json_file(tmp_path: Path):
    """freeze_baseline writes a valid BaselineSnapshot JSON to disk."""
    def runner(scenario_id: str, seed: int) -> bool:
        return True

    snap = freeze_baseline(
        scenario_id="CP-37",
        n_runs=5,
        runner=runner,
        out_dir=tmp_path,
    )

    assert isinstance(snap, BaselineSnapshot)
    assert snap.scenario_id == "CP-37"
    assert snap.n_runs == 5
    assert snap.status == BaselineStatus.stable_ok
    assert len(snap.per_seed_results) == 5
    # Default seeds: list(range(5))
    assert [r.seed for r in snap.per_seed_results] == [0, 1, 2, 3, 4]

    out_file = tmp_path / "CP-37.json"
    assert out_file.exists()
    # File is valid JSON
    payload = json.loads(out_file.read_text())
    assert payload["scenario_id"] == "CP-37"
    assert payload["n_runs"] == 5
    assert payload["status"] == int(BaselineStatus.stable_ok)


def test_baseline_snapshot_round_trip_via_model_dump_validate():
    """A snapshot round-trips losslessly through model_dump_json / model_validate_json."""
    original = BaselineSnapshot(
        scenario_id="CP-37",
        frozen_at=datetime(2026, 5, 12, 8, 42, 11, tzinfo=timezone.utc),
        n_runs=3,
        status=BaselineStatus.stable_ok,
        per_seed_results=[
            RunResult(seed=0, passed=True, elapsed_s=1.23, error=None),
            RunResult(seed=1, passed=True, elapsed_s=1.45, error=None),
            RunResult(seed=2, passed=True, elapsed_s=1.34, error=None),
        ],
        settle_state_hash=None,
    )
    json_str = original.model_dump_json(indent=2)
    restored = BaselineSnapshot.model_validate_json(json_str)
    assert restored == original


def test_freeze_baseline_explicit_seeds_override_n_runs(tmp_path: Path):
    """When seeds is passed, n_runs reflects len(seeds)."""
    def runner(scenario_id: str, seed: int) -> bool:
        return True

    snap = freeze_baseline(
        scenario_id="CP-custom",
        n_runs=99,  # should be ignored
        seeds=[100, 200, 300],
        runner=runner,
        out_dir=tmp_path,
    )
    assert snap.n_runs == 3
    assert [r.seed for r in snap.per_seed_results] == [100, 200, 300]


# ---------------------------------------------------------------------------
# compare_to_baseline
# ---------------------------------------------------------------------------


def _freeze_all_passing(scenario_id: str, tmp_path: Path, n: int = 5) -> BaselineSnapshot:
    """Helper: freeze a baseline where every run passes."""
    return freeze_baseline(
        scenario_id=scenario_id,
        n_runs=n,
        runner=lambda s, x: True,
        out_dir=tmp_path,
    )


def test_compare_to_baseline_identical_runs_no_regression(tmp_path: Path):
    """Same all-pass set => not regressed, no mismatching seeds."""
    _freeze_all_passing("CP-37", tmp_path)

    current = [
        RunResult(seed=i, passed=True, elapsed_s=0.1, error=None) for i in range(5)
    ]
    delta = compare_to_baseline("CP-37", current, baselines_dir=tmp_path)

    assert isinstance(delta, BaselineDelta)
    assert delta.scenario_id == "CP-37"
    assert delta.frozen_status == BaselineStatus.stable_ok
    assert delta.current_status == BaselineStatus.stable_ok
    assert delta.regressed is False
    assert delta.mismatching_seeds == []


def test_compare_to_baseline_one_failure_is_regression(tmp_path: Path):
    """Baseline stable_ok, current has 1 fail => regressed=True with mismatch."""
    _freeze_all_passing("CP-37", tmp_path)

    current = [
        RunResult(seed=0, passed=True, elapsed_s=0.1),
        RunResult(seed=1, passed=True, elapsed_s=0.1),
        RunResult(seed=2, passed=False, elapsed_s=0.1, error="ValueError(...)"),
        RunResult(seed=3, passed=True, elapsed_s=0.1),
        RunResult(seed=4, passed=True, elapsed_s=0.1),
    ]
    delta = compare_to_baseline("CP-37", current, baselines_dir=tmp_path)

    assert delta.frozen_status == BaselineStatus.stable_ok
    assert delta.current_status == BaselineStatus.flaky
    assert delta.regressed is True
    assert 2 in delta.mismatching_seeds
    assert "REGRESSION" in delta.message


def test_compare_to_baseline_missing_file_raises(tmp_path: Path):
    """No snapshot => FileNotFoundError."""
    current = [RunResult(seed=0, passed=True, elapsed_s=0.1)]
    with pytest.raises(FileNotFoundError):
        compare_to_baseline("CP-nonexistent", current, baselines_dir=tmp_path)


def test_compare_to_baseline_flips_in_either_direction_count_as_mismatches(tmp_path: Path):
    """Both baseline-passed-now-fails and vice versa count as mismatches."""
    # Freeze a mixed snapshot: seeds 0,2 pass; seed 1 fails.
    freeze_baseline(
        scenario_id="CP-mixed",
        seeds=[0, 1, 2],
        runner=lambda s, x: x != 1,
        out_dir=tmp_path,
    )
    # Current: seed 0 fails (regression direction), seed 1 now passes
    # (positive flip but still a behaviour change), seed 2 same.
    current = [
        RunResult(seed=0, passed=False, elapsed_s=0.1),
        RunResult(seed=1, passed=True, elapsed_s=0.1),
        RunResult(seed=2, passed=True, elapsed_s=0.1),
    ]
    delta = compare_to_baseline("CP-mixed", current, baselines_dir=tmp_path)
    assert 0 in delta.mismatching_seeds
    assert 1 in delta.mismatching_seeds
    assert 2 not in delta.mismatching_seeds
