"""Phase 97b — resumable long-running canonical-prompt benchmark."""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional

from .sub_phase_97b_fast_sweep_cp_regression import (
    CPRunResult,
    CPTestCase,
    FastSweepHarness,
    load_results_jsonl,
    save_results_jsonl,
)

PHASE_ID = "97b"
PHASE_TITLE = "Resumable long-running CP benchmark"
PHASE_STATUS = "landed"


@dataclass(frozen=True)
class LongRunConfig:
    max_cycles: int
    time_budget_s: float
    checkpoint_path: Path
    fail_fast: bool = False

    def __post_init__(self) -> None:
        if self.max_cycles < 1:
            raise ValueError("max_cycles must be >= 1")
        if self.time_budget_s <= 0:
            raise ValueError("time_budget_s must be > 0")


@dataclass(frozen=True)
class LongRunSummary:
    completed_runs: int
    completed_cycles: int
    stopped_reason: str
    checkpoint_path: str


def get_phase_metadata():
    return {
        "phase": PHASE_ID,
        "title": PHASE_TITLE,
        "status": PHASE_STATUS,
        "spec_ref": "specs/IA_FULL_SPEC_2026-05-10.md Phase 97b",
    }


class LongRunningBenchmark:
    """Run repeated CP cycles with atomic-enough JSONL checkpoints and resume."""

    def __init__(self, test_cases: List[CPTestCase]) -> None:
        if not test_cases:
            raise ValueError("at least one test case is required")
        self._cases = list(test_cases)

    def _existing(self, path: Path) -> List[CPRunResult]:
        return load_results_jsonl(path) if path.is_file() else []

    def run(
        self,
        config: LongRunConfig,
        runner: Optional[Callable[[CPTestCase, int], CPRunResult]] = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> LongRunSummary:
        runner = runner or FastSweepHarness.mock_runner
        results = self._existing(config.checkpoint_path)
        next_indices: Dict[str, int] = {}
        for result in results:
            next_indices[result.cp_id] = max(
                next_indices.get(result.cp_id, 0), result.run_idx + 1
            )

        start = clock()
        completed_cycles = 0
        stopped_reason = "max_cycles"
        for _cycle in range(config.max_cycles):
            if clock() - start >= config.time_budget_s:
                stopped_reason = "time_budget"
                break
            cycle_complete = True
            for case in self._cases:
                if clock() - start >= config.time_budget_s:
                    stopped_reason = "time_budget"
                    cycle_complete = False
                    break
                run_idx = next_indices.get(case.cp_id, 0)
                result = runner(case, run_idx)
                if result.cp_id != case.cp_id or result.run_idx != run_idx:
                    raise ValueError("runner returned mismatched cp_id or run_idx")
                results.append(result)
                next_indices[case.cp_id] = run_idx + 1
                save_results_jsonl(results, config.checkpoint_path)
                if config.fail_fast and not result.success:
                    stopped_reason = "failure"
                    cycle_complete = False
                    break
            if cycle_complete:
                completed_cycles += 1
            if stopped_reason in {"failure", "time_budget"}:
                break

        return LongRunSummary(
            completed_runs=len(results),
            completed_cycles=completed_cycles,
            stopped_reason=stopped_reason,
            checkpoint_path=str(config.checkpoint_path),
        )
