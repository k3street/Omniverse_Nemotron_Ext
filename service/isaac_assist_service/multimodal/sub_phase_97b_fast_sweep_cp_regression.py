"""Phase 97b — Fast-sweep CP-regression harness.

Enumerates canonical-prompt (CP) test cases, schedules N-run sweeps,
aggregates results, and detects pass-rate regressions against a saved baseline.

Per specs/IA_FULL_SPEC_2026-05-10.md Phase 97b.
"""
from __future__ import annotations

import datetime
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Literal, Optional

# ---------------------------------------------------------------------------
# Phase metadata
# ---------------------------------------------------------------------------

PHASE_ID = "97b"
PHASE_TITLE = "Fast-sweep CP-regression harness"
PHASE_STATUS = "landed"


def get_phase_metadata() -> Dict[str, Any]:
    """Return phase metadata for spec-coverage audits."""
    return {
        "phase": PHASE_ID,
        "title": PHASE_TITLE,
        "status": PHASE_STATUS,
        "spec_ref": "specs/IA_FULL_SPEC_2026-05-10.md Phase 97b",
    }


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class CPTestCase:
    """A single canonical-prompt test case descriptor."""

    cp_id: str
    name: str
    tier: Literal["CW", "T4", "T6", "T8"]
    skill_category: str
    expected_tools: List[str]
    time_budget_s: float = 30.0
    success_threshold: float = 1.0


@dataclass
class CPRunResult:
    """Result of a single CP run."""

    cp_id: str
    run_idx: int
    success: bool
    score: float
    duration_s: float
    tools_called: List[str]
    errors: List[str] = field(default_factory=list)
    run_at: str = ""

    def __post_init__(self) -> None:
        if not self.run_at:
            self.run_at = datetime.datetime.now(datetime.timezone.utc).isoformat()


@dataclass
class SweepConfig:
    """Configuration for a CP regression sweep."""

    cp_subset: Optional[List[str]] = None
    n_runs_per_cp: int = 1
    max_parallelism: int = 1
    fail_fast: bool = False
    tier_filter: Optional[List[str]] = None


@dataclass
class RegressionAlert:
    """Signals a detected regression in CP pass-rate."""

    cp_id: str
    baseline_success_rate: float
    current_success_rate: float
    delta_pp: float
    severity: Literal["info", "warn", "critical"]


# ---------------------------------------------------------------------------
# Serialisation helpers
# ---------------------------------------------------------------------------

def _result_to_dict(r: CPRunResult) -> Dict[str, Any]:
    return {
        "cp_id": r.cp_id,
        "run_idx": r.run_idx,
        "success": r.success,
        "score": r.score,
        "duration_s": r.duration_s,
        "tools_called": r.tools_called,
        "errors": r.errors,
        "run_at": r.run_at,
    }


def _dict_to_result(d: Dict[str, Any]) -> CPRunResult:
    return CPRunResult(
        cp_id=d["cp_id"],
        run_idx=d["run_idx"],
        success=d["success"],
        score=d["score"],
        duration_s=d["duration_s"],
        tools_called=d.get("tools_called", []),
        errors=d.get("errors", []),
        run_at=d.get("run_at", ""),
    )


def save_results_jsonl(results: List[CPRunResult], path: Path) -> None:
    """Persist *results* to a JSON-Lines file at *path*."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for r in results:
            fh.write(json.dumps(_result_to_dict(r)) + "\n")


def load_results_jsonl(path: Path) -> List[CPRunResult]:
    """Load a JSON-Lines file produced by :func:`save_results_jsonl`."""
    results: List[CPRunResult] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                results.append(_dict_to_result(json.loads(line)))
    return results


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------

class FastSweepHarness:
    """Orchestrates CP regression sweeps.

    Parameters
    ----------
    test_cases:
        Full list of :class:`CPTestCase` objects representing the canonical
        prompt library.
    """

    def __init__(self, test_cases: List[CPTestCase]) -> None:
        self._test_cases: List[CPTestCase] = list(test_cases)

    # ------------------------------------------------------------------
    # Filtering
    # ------------------------------------------------------------------

    def filter(self, config: SweepConfig) -> List[CPTestCase]:
        """Return the subset of test cases that pass *config* filters."""
        cases = self._test_cases

        if config.cp_subset is not None:
            subset_set = set(config.cp_subset)
            cases = [c for c in cases if c.cp_id in subset_set]

        if config.tier_filter is not None:
            tier_set = set(config.tier_filter)
            cases = [c for c in cases if c.tier in tier_set]

        return cases

    # ------------------------------------------------------------------
    # Mock runner (deterministic, for harness mechanics tests)
    # ------------------------------------------------------------------

    @staticmethod
    def mock_runner(case: CPTestCase, run_idx: int) -> CPRunResult:
        """Deterministic mock that returns success based on ``cp_id`` hash.

        The hash ensures the same CP always gets the same synthetic result,
        making the mock reproducible across test runs.
        """
        digest = int(hashlib.sha256(case.cp_id.encode()).hexdigest(), 16)
        # Use the least-significant 4 bits to give ~93.75 % success (15/16)
        success = (digest & 0xF) != 0
        score = 1.0 if success else 0.0
        duration_s = 0.001 + (digest & 0xFF) * 0.0001
        return CPRunResult(
            cp_id=case.cp_id,
            run_idx=run_idx,
            success=success,
            score=score,
            duration_s=duration_s,
            tools_called=list(case.expected_tools),
            errors=[] if success else ["mock_failure"],
            run_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        )

    # ------------------------------------------------------------------
    # Sweep
    # ------------------------------------------------------------------

    def run_sweep(
        self,
        config: SweepConfig,
        runner: Optional[Callable[[CPTestCase, int], CPRunResult]] = None,
    ) -> List[CPRunResult]:
        """Execute the sweep and return all :class:`CPRunResult` objects.

        Parameters
        ----------
        config:
            Sweep configuration (subset, n_runs, fail_fast, etc.).
        runner:
            Callable ``(case, run_idx) -> CPRunResult``.  When *None* the
            built-in :meth:`mock_runner` is used.
        """
        if runner is None:
            runner = self.mock_runner

        cases = self.filter(config)
        results: List[CPRunResult] = []

        for case in cases:
            for run_idx in range(config.n_runs_per_cp):
                result = runner(case, run_idx)
                results.append(result)

                if config.fail_fast and not result.success:
                    return results

        return results

    # ------------------------------------------------------------------
    # Aggregation
    # ------------------------------------------------------------------

    @staticmethod
    def aggregate(results: List[CPRunResult]) -> Dict[str, Dict[str, Any]]:
        """Aggregate *results* by ``cp_id``.

        Returns
        -------
        dict keyed by ``cp_id``, each value a dict with:
        ``n_runs``, ``n_success``, ``success_rate``, ``mean_score``,
        ``mean_duration``, ``total_errors``.
        """
        buckets: Dict[str, List[CPRunResult]] = {}
        for r in results:
            buckets.setdefault(r.cp_id, []).append(r)

        agg: Dict[str, Dict[str, Any]] = {}
        for cp_id, runs in buckets.items():
            n_runs = len(runs)
            n_success = sum(1 for r in runs if r.success)
            total_errors = sum(len(r.errors) for r in runs)
            success_rate = n_success / n_runs if n_runs > 0 else 0.0
            mean_score = sum(r.score for r in runs) / n_runs if n_runs > 0 else 0.0
            mean_duration = (
                sum(r.duration_s for r in runs) / n_runs if n_runs > 0 else 0.0
            )
            agg[cp_id] = {
                "n_runs": n_runs,
                "n_success": n_success,
                "success_rate": success_rate,
                "mean_score": mean_score,
                "mean_duration": mean_duration,
                "total_errors": total_errors,
            }
        return agg

    # ------------------------------------------------------------------
    # Regression detection
    # ------------------------------------------------------------------

    @staticmethod
    def compare_to_baseline(
        baseline: Dict[str, Dict[str, Any]],
        current: Dict[str, Dict[str, Any]],
        threshold_pp: float = 10.0,
    ) -> List[RegressionAlert]:
        """Compare *current* aggregated stats against *baseline*.

        A :class:`RegressionAlert` is emitted for every CP whose success-rate
        dropped by more than *threshold_pp* percentage points.

        Severity scale
        --------------
        - ``>20 pp`` drop  → ``"critical"``
        - ``>10 pp`` drop  → ``"warn"``
        - ``<=10 pp`` drop → no alert (within threshold)
        """
        alerts: List[RegressionAlert] = []
        for cp_id, base_stats in baseline.items():
            if cp_id not in current:
                continue
            base_rate = base_stats.get("success_rate", 0.0)
            curr_rate = current[cp_id].get("success_rate", 0.0)
            # delta_pp is negative when there is a regression
            delta_pp = (curr_rate - base_rate) * 100.0

            drop_pp = -delta_pp  # positive = regression magnitude
            if drop_pp > 20.0:
                severity: Literal["info", "warn", "critical"] = "critical"
            elif drop_pp > threshold_pp:
                severity = "warn"
            else:
                continue  # within threshold — no alert

            alerts.append(
                RegressionAlert(
                    cp_id=cp_id,
                    baseline_success_rate=base_rate,
                    current_success_rate=curr_rate,
                    delta_pp=delta_pp,
                    severity=severity,
                )
            )
        return alerts

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------

    @staticmethod
    def summary(results: List[CPRunResult]) -> Dict[str, Any]:
        """Compute overall sweep statistics from *results*."""
        n_total = len(results)
        n_success = sum(1 for r in results if r.success)
        total_time = sum(r.duration_s for r in results)
        unique_cps = len({r.cp_id for r in results})
        total_errors = sum(len(r.errors) for r in results)
        pass_rate = n_success / n_total if n_total > 0 else 0.0

        return {
            "n_total": n_total,
            "n_success": n_success,
            "n_fail": n_total - n_success,
            "pass_rate": pass_rate,
            "total_time_s": total_time,
            "unique_cps": unique_cps,
            "total_errors": total_errors,
        }
