"""Multi-run regression harness — consolidated `run_with_seed_set` API.

Replaces the splayed loops previously living under `scripts/qa/*`
(`baseline_compare.py`, `multi_run_regression.py`, etc.) with a
single deterministic primitive:

    run_with_seed_set(scenario_id, seeds, n_runs_per_seed) -> RegressionResult

Iteration order is the caller-supplied seed list times
`n_runs_per_seed` — same inputs always produce the same per-seed
ordering. The `runner` callable is injected so this module stays
dependency-free of Kit RPC / canonical_instantiator; real wiring
lands in later phases.

Per `specs/IA_FULL_SPEC_2026-05-10.md` Phase 8d.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Callable, List, Optional

from pydantic import BaseModel, Field

from .baseline_status import BaselineStatus, classify


# Type alias: a runner takes (scenario_id, seed) and returns whether the run passed.
# Real callers will wrap whatever drives the scenario (Kit RPC, MockSimulationRunner,
# pytest fixture, etc.) behind this signature.
RunnerFn = Callable[[str, int], bool]


# ---------------------------------------------------------------------------
# Result models
# ---------------------------------------------------------------------------


class RunResult(BaseModel):
    """One execution of one (scenario, seed) pair.

    `error` is the string repr of any exception the runner raised
    (None on success). `passed` is False whenever an exception was
    caught — the harness doesn't propagate runner exceptions because
    a runtime failure is *one possible mode of failure* the taxonomy
    needs to count, not a harness fault.
    """

    seed: int
    passed: bool
    elapsed_s: float = Field(ge=0.0)
    error: Optional[str] = None


class RegressionResult(BaseModel):
    """Aggregate result for a (scenario, seeds, n_runs_per_seed) execution."""

    scenario_id: str
    seeds: List[int]
    n_runs_per_seed: int = Field(ge=1)
    runs: List[RunResult]
    total_pass: int = Field(ge=0)
    total_runs: int = Field(ge=0)
    status: BaselineStatus
    started_at: datetime
    ended_at: datetime


# ---------------------------------------------------------------------------
# Default runner — explicit placeholder so callers can't silently no-op
# ---------------------------------------------------------------------------


def _default_runner(scenario_id: str, seed: int) -> bool:  # noqa: ARG001
    """Stub runner; real wiring is deferred to a later phase.

    The 8d scope is the harness *primitive*, not its integration. Any
    call that lands here is a contract violation (the caller forgot
    to wire in a real runner) — raise loudly rather than silently
    returning False, which would masquerade as a legitimate failure
    in the regression result.
    """
    raise NotImplementedError(
        "run_with_seed_set was invoked without a runner. The default "
        "runner is a placeholder; Phase 8d ships the harness primitive "
        "only. Real Kit-RPC / MockSimulationRunner wiring lands in a "
        "later phase."
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def run_with_seed_set(
    scenario_id: str,
    seeds: List[int],
    n_runs_per_seed: int = 1,
    runner: Optional[RunnerFn] = None,
    n_of_m: int = 3,
) -> RegressionResult:
    """Execute `scenario_id` across `seeds` x `n_runs_per_seed` runs.

    Seeds are iterated in input order (deterministic). For each seed,
    `runner(scenario_id, seed)` is invoked `n_runs_per_seed` times.

    Each run's pass/fail is captured as a `RunResult`; an exception
    becomes `passed=False, error=repr(exc)`. The overall
    `BaselineStatus` is derived via `classify(total_pass, total_runs,
    n_of_m)`.

    Args:
        scenario_id: Stable string identifier for the CP under test.
        seeds: Ordered list of seeds. Empty list is allowed and yields
            a zero-run RegressionResult with status `stable_fail`
            (no observed passes).
        n_runs_per_seed: Number of times each seed is re-executed.
            Must be >= 1.
        runner: Callable that performs one run. Defaults to a stub
            that raises `NotImplementedError` — the harness primitive
            is shipped without an integrated runner.
        n_of_m: Threshold passed through to `classify`.

    Returns:
        A `RegressionResult` capturing per-run outcomes and the
        aggregate status.
    """
    if n_runs_per_seed < 1:
        raise ValueError(
            f"n_runs_per_seed must be >= 1, got {n_runs_per_seed}"
        )

    effective_runner: RunnerFn = runner if runner is not None else _default_runner

    started_at = datetime.now(timezone.utc)
    runs: List[RunResult] = []

    for seed in seeds:
        for _ in range(n_runs_per_seed):
            t0 = time.monotonic()
            try:
                passed = bool(effective_runner(scenario_id, seed))
                err: Optional[str] = None
            except NotImplementedError:
                # The default-runner sentinel must propagate so callers
                # learn they forgot to wire one in. Any other exception
                # is captured as a failure mode (see below).
                raise
            except Exception as exc:  # noqa: BLE001 — runner failures are data
                passed = False
                err = repr(exc)
            elapsed_s = time.monotonic() - t0
            runs.append(
                RunResult(
                    seed=seed,
                    passed=passed,
                    elapsed_s=elapsed_s,
                    error=err,
                )
            )

    ended_at = datetime.now(timezone.utc)
    total_runs = len(runs)
    total_pass = sum(1 for r in runs if r.passed)
    status = (
        classify(total_pass, total_runs, n_of_m)
        if total_runs > 0
        else BaselineStatus.stable_fail
    )

    return RegressionResult(
        scenario_id=scenario_id,
        seeds=list(seeds),
        n_runs_per_seed=n_runs_per_seed,
        runs=runs,
        total_pass=total_pass,
        total_runs=total_runs,
        status=status,
        started_at=started_at,
        ended_at=ended_at,
    )
