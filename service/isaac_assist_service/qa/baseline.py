"""Baseline freeze + compare API.

`freeze_baseline(scenario, n_runs=5)` writes a `BaselineSnapshot`
into ``data/baselines/{scenario_id}.json``.
`compare_to_baseline(scenario, current_runs)` returns a
`BaselineDelta` describing regressions vs the frozen point.

Per the spec these snapshots are *sacred* — they are the load-bearing
artefact every later "harness honesty" claim cites. Keeping them as
schema-typed JSON (rather than QA-script implementation detail)
means a regression introduced in Epoch IV can be pinned to an exact
fail-vs-pass per-seed delta against the frozen baseline.

Per `specs/IA_FULL_SPEC_2026-05-10.md` Phase 8d.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from pydantic import BaseModel, Field

from .baseline_status import BaselineStatus, classify
from .regression import RegressionResult, RunResult, RunnerFn, run_with_seed_set


# Repo-root anchored default. Computed once at import; callers can
# override via `out_dir` / `baselines_dir`.
_REPO_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_BASELINES_DIR = _REPO_ROOT / "data" / "baselines"


# ---------------------------------------------------------------------------
# Snapshot + delta models
# ---------------------------------------------------------------------------


class BaselineSnapshot(BaseModel):
    """A frozen point-in-time recording of a CP's baseline behaviour.

    `settle_state_hash` is an optional fingerprint of the post-run
    simulation state (e.g. the hash that Phase 8b's
    `content_hash` would produce over the relevant USD prim
    state). It's optional because not every CP has a settled-state
    representation yet; when present, drift in this hash flags
    silent state regressions even when pass/fail counts match.
    """

    scenario_id: str
    frozen_at: datetime
    n_runs: int = Field(ge=0)
    status: BaselineStatus
    per_seed_results: List[RunResult]
    settle_state_hash: Optional[str] = None


class BaselineDelta(BaseModel):
    """Diff between a frozen baseline and a fresh set of runs.

    `regressed` is True iff `current_status < frozen_status` (the
    `BaselineStatus` `IntEnum` ordering is severity-ascending —
    `stable_ok` > `flaky` > `stable_fail`).

    `mismatching_seeds` lists every seed for which the baseline pass
    flag and the current pass flag disagree (in either direction:
    baseline-passed-now-fails *and* baseline-failed-now-passes both
    count, the latter is a positive regression but still a behaviour
    change worth surfacing).
    """

    scenario_id: str
    frozen_status: BaselineStatus
    current_status: BaselineStatus
    regressed: bool
    mismatching_seeds: List[int]
    message: str


# ---------------------------------------------------------------------------
# Freeze
# ---------------------------------------------------------------------------


def freeze_baseline(
    scenario_id: str,
    n_runs: int = 5,
    runner: Optional[RunnerFn] = None,
    out_dir: Optional[Path] = None,
    seeds: Optional[List[int]] = None,
) -> BaselineSnapshot:
    """Run `scenario_id` over `n_runs` seeds and persist the result.

    Default seeds are ``list(range(n_runs))``; passing `seeds`
    overrides both the seed values and the run count
    (``n_runs := len(seeds)`` in that case so the snapshot reflects
    the actual run population).

    The snapshot is written to ``{out_dir or default}/{scenario_id}.json``
    via ``BaselineSnapshot.model_dump_json(indent=2)``. The directory
    is created if missing.

    Args:
        scenario_id: Stable CP identifier (e.g. ``"CP-37"``).
        n_runs: Number of runs in the baseline. Ignored if `seeds`
            is supplied.
        runner: Callable that executes one run; same contract as
            `run_with_seed_set`. Defaults to the regression module's
            stub runner (raises `NotImplementedError`).
        out_dir: Directory to write the snapshot into. Defaults to
            ``<repo_root>/data/baselines/``.
        seeds: Explicit seed list. When provided, overrides
            ``list(range(n_runs))``.

    Returns:
        The persisted `BaselineSnapshot`.
    """
    effective_seeds: List[int] = list(seeds) if seeds is not None else list(range(n_runs))
    effective_n_runs = len(effective_seeds)
    effective_out_dir = out_dir if out_dir is not None else _DEFAULT_BASELINES_DIR

    regression = run_with_seed_set(
        scenario_id=scenario_id,
        seeds=effective_seeds,
        n_runs_per_seed=1,
        runner=runner,
    )

    snapshot = BaselineSnapshot(
        scenario_id=scenario_id,
        frozen_at=datetime.now(timezone.utc),
        n_runs=effective_n_runs,
        status=regression.status,
        per_seed_results=regression.runs,
        settle_state_hash=None,
    )

    effective_out_dir.mkdir(parents=True, exist_ok=True)
    target = effective_out_dir / f"{scenario_id}.json"
    target.write_text(snapshot.model_dump_json(indent=2))

    return snapshot


# ---------------------------------------------------------------------------
# Compare
# ---------------------------------------------------------------------------


def _load_snapshot(scenario_id: str, baselines_dir: Path) -> BaselineSnapshot:
    target = baselines_dir / f"{scenario_id}.json"
    if not target.exists():
        raise FileNotFoundError(
            f"No baseline snapshot found for {scenario_id!r} at {target}"
        )
    return BaselineSnapshot.model_validate_json(target.read_text())


def compare_to_baseline(
    scenario_id: str,
    current_runs: List[RunResult],
    baselines_dir: Optional[Path] = None,
) -> BaselineDelta:
    """Diff `current_runs` against the frozen snapshot for `scenario_id`.

    Args:
        scenario_id: Stable CP identifier (matches the snapshot file
            stem).
        current_runs: Fresh `RunResult`s from a recent
            `run_with_seed_set` call.
        baselines_dir: Directory containing snapshot JSONs. Defaults
            to ``<repo_root>/data/baselines/``.

    Returns:
        A `BaselineDelta` summarising the regression posture.

    Raises:
        FileNotFoundError: If no baseline snapshot file exists for
            `scenario_id`.
    """
    effective_baselines_dir = (
        baselines_dir if baselines_dir is not None else _DEFAULT_BASELINES_DIR
    )

    snapshot = _load_snapshot(scenario_id, effective_baselines_dir)

    current_pass = sum(1 for r in current_runs if r.passed)
    current_total = len(current_runs)
    current_status = (
        classify(current_pass, current_total)
        if current_total > 0
        else BaselineStatus.stable_fail
    )

    # Per-seed pass flag from baseline. If the baseline recorded
    # multiple runs at the same seed, treat "any pass" as the
    # baseline's verdict for that seed (most lenient interpretation
    # — we're looking for regressions, not edge cases).
    baseline_pass_by_seed: dict[int, bool] = {}
    for r in snapshot.per_seed_results:
        baseline_pass_by_seed[r.seed] = baseline_pass_by_seed.get(r.seed, False) or r.passed

    current_pass_by_seed: dict[int, bool] = {}
    for r in current_runs:
        current_pass_by_seed[r.seed] = current_pass_by_seed.get(r.seed, False) or r.passed

    mismatching_seeds: List[int] = []
    all_seeds = sorted(set(baseline_pass_by_seed) | set(current_pass_by_seed))
    for seed in all_seeds:
        b = baseline_pass_by_seed.get(seed)
        c = current_pass_by_seed.get(seed)
        if b is None or c is None:
            # Seed appears on one side only — that itself is a mismatch
            # worth surfacing (the run population changed).
            mismatching_seeds.append(seed)
        elif b != c:
            mismatching_seeds.append(seed)

    regressed = current_status < snapshot.status

    if regressed:
        message = (
            f"REGRESSION: {scenario_id} dropped from {snapshot.status.name} "
            f"to {current_status.name} "
            f"({len(mismatching_seeds)} seed(s) mismatched)"
        )
    elif mismatching_seeds:
        message = (
            f"BEHAVIOUR CHANGE (status unchanged): {scenario_id} status "
            f"{current_status.name}; {len(mismatching_seeds)} seed(s) "
            f"flipped pass/fail vs baseline"
        )
    else:
        message = (
            f"STABLE: {scenario_id} matches baseline "
            f"({current_status.name}, no per-seed mismatches)"
        )

    return BaselineDelta(
        scenario_id=scenario_id,
        frozen_status=snapshot.status,
        current_status=current_status,
        regressed=regressed,
        mismatching_seeds=mismatching_seeds,
        message=message,
    )
