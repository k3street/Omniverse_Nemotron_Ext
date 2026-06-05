"""Phase 78c — arena baseline freeze.

Freeze-and-compare API for IsaacLab arena benchmark baselines.
Reads top-N entries from a :class:`Leaderboard`, computes descriptive
stats, persists them as JSON, and produces per-scenario delta reports.

Per specs/IA_FULL_SPEC_2026-05-10.md Phase 78c.
"""
from __future__ import annotations

import json
import math
import os
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from service.isaac_assist_service.multimodal.isaaclab_arena_leaderboard import Leaderboard


PHASE_ID = "78c"
PHASE_TITLE = "arena baseline freeze"
PHASE_STATUS = "landed"


def get_phase_metadata() -> Dict[str, Any]:
    """Return phase identification and status for this phase.

    Returns:
        Dict[str, Any]: Keys ``phase``, ``title``, ``status``, and ``spec_ref``.
    """
    return {
        "phase": PHASE_ID,
        "title": PHASE_TITLE,
        "status": PHASE_STATUS,
        "spec_ref": "specs/IA_FULL_SPEC_2026-05-10.md Phase 78c",
    }


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass
class ArenaBaseline:
    """Frozen baseline statistics for a single scenario.

    Attributes
    ----------
    scenario_id:
        Stable identifier for the scenario (e.g. ``"pick_place_franka"``).
    frozen_at:
        ISO-8601 UTC timestamp of when the baseline was frozen.
    n_runs:
        Number of runs used to build the baseline.
    mean_score:
        Arithmetic mean of the individual scores.
    std_score:
        Population standard deviation of the individual scores.
    min_score:
        Minimum score across runs.
    max_score:
        Maximum score across runs.
    individual_scores:
        The raw score values used to compute the statistics.
    """

    scenario_id: str
    frozen_at: str  # ISO-8601
    n_runs: int
    mean_score: float
    std_score: float
    min_score: float
    max_score: float
    individual_scores: List[float] = field(default_factory=list)


@dataclass
class BaselineDelta:
    """Comparison between a frozen baseline and a fresh set of scores.

    Attributes
    ----------
    scenario_id:
        Stable scenario identifier.
    baseline_mean:
        Mean score from the frozen baseline.
    current_mean:
        Mean score from the current runs.
    delta:
        ``current_mean - baseline_mean``.
    delta_pct:
        ``delta / baseline_mean * 100`` (percentage change).  When
        ``baseline_mean`` is 0 the value is set to 0.0 to avoid a
        division-by-zero.
    regressed:
        ``True`` when ``delta_pct < -5``.
    """

    scenario_id: str
    baseline_mean: float
    current_mean: float
    delta: float
    delta_pct: float
    regressed: bool


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _compute_stats(scores: List[float]) -> tuple[float, float, float, float]:
    """Return (mean, std, min, max) for *scores*.

    Uses population standard deviation (divides by N, not N-1) to match
    numpy's default ``ddof=0`` behaviour.  Returns zeros for an empty
    list rather than raising so callers can handle the edge case cleanly.
    """
    if not scores:
        return 0.0, 0.0, 0.0, 0.0
    n = len(scores)
    mean = sum(scores) / n
    variance = sum((s - mean) ** 2 for s in scores) / n
    std = math.sqrt(variance)
    return mean, std, min(scores), max(scores)


# ---------------------------------------------------------------------------
# ArenaBaselineStore
# ---------------------------------------------------------------------------


class ArenaBaselineStore:
    """JSON-backed store of :class:`ArenaBaseline` records.

    The store is a single JSON file whose top-level value is a dict
    mapping *scenario_id* to a serialised :class:`ArenaBaseline`.
    Writes are atomic (write-tmp + ``os.replace``).

    Parameters
    ----------
    store_path:
        Path to the JSON file.  The parent directory is created on
        first write if it does not already exist.
    """

    def __init__(self, store_path: Path) -> None:
        """Initialise the store pointing at *store_path*.

        Args:
            store_path (Path): Path to the JSON baseline store file.
                The parent directory is created on first write.
        """
        self._path = Path(store_path)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load(self) -> Dict[str, Any]:
        """Return the raw store dict.  Empty dict when file is absent."""
        if not self._path.exists():
            return {}
        with open(self._path, "r", encoding="utf-8") as fh:
            return json.load(fh)

    def _save(self, data: Dict[str, Any]) -> None:
        """Atomically write *data* to disk using a temp-file + ``os.replace`` swap."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp_fd, tmp_name = tempfile.mkstemp(
            dir=self._path.parent,
            prefix=".arena_baseline_",
            suffix=".tmp",
        )
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2)
            os.replace(tmp_name, self._path)
        except Exception:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def freeze(
        self,
        leaderboard: Leaderboard,
        scenario_id: str,
        n_runs: int = 5,
    ) -> ArenaBaseline:
        """Freeze the top *n_runs* entries for *scenario_id* and persist.

        Takes the top *n_runs* entries (by score, descending) from
        *leaderboard*, computes descriptive stats, stores the resulting
        :class:`ArenaBaseline` in the JSON file and returns it.

        If the leaderboard has fewer than *n_runs* entries for the
        scenario, all available entries are used.

        Parameters
        ----------
        leaderboard:
            Source :class:`~isaaclab_arena_leaderboard.Leaderboard`.
        scenario_id:
            Scenario to freeze.
        n_runs:
            How many top entries to include in the baseline.

        Returns
        -------
        ArenaBaseline
            The newly frozen baseline (also persisted to disk).
        """
        top_entries = leaderboard.top_k(scenario_id, k=n_runs)
        scores = [e["score"] for e in top_entries]
        mean, std, lo, hi = _compute_stats(scores)

        baseline = ArenaBaseline(
            scenario_id=scenario_id,
            frozen_at=datetime.now(timezone.utc).isoformat(),
            n_runs=len(scores),
            mean_score=mean,
            std_score=std,
            min_score=lo,
            max_score=hi,
            individual_scores=scores,
        )

        data = self._load()
        data[scenario_id] = asdict(baseline)
        self._save(data)

        return baseline

    def get(self, scenario_id: str) -> Optional[ArenaBaseline]:
        """Return the frozen baseline for *scenario_id*, or ``None``."""
        data = self._load()
        raw = data.get(scenario_id)
        if raw is None:
            return None
        return ArenaBaseline(**raw)

    def list_scenarios(self) -> List[str]:
        """Return a deduplicated list of frozen scenario IDs."""
        return list(self._load().keys())


# ---------------------------------------------------------------------------
# Compare + delta report
# ---------------------------------------------------------------------------


def compare_to_baseline(
    scenario_id: str,
    current_runs: List[float],
    baseline: ArenaBaseline,
) -> BaselineDelta:
    """Compare *current_runs* scores against a frozen *baseline*.

    Parameters
    ----------
    scenario_id:
        Scenario identifier (for the returned delta record).
    current_runs:
        Raw scores from recent evaluation runs.
    baseline:
        The frozen :class:`ArenaBaseline` to compare against.

    Returns
    -------
    BaselineDelta
        A delta record with ``regressed=True`` when the current mean
        drops more than 5% relative to the baseline mean.
    """
    current_mean = sum(current_runs) / len(current_runs) if current_runs else 0.0
    delta = current_mean - baseline.mean_score
    if baseline.mean_score != 0.0:
        delta_pct = delta / baseline.mean_score * 100.0
    else:
        delta_pct = 0.0

    return BaselineDelta(
        scenario_id=scenario_id,
        baseline_mean=baseline.mean_score,
        current_mean=current_mean,
        delta=delta,
        delta_pct=delta_pct,
        regressed=delta_pct < -5.0,
    )


def delta_report(
    store: ArenaBaselineStore,
    leaderboard: Leaderboard,
) -> List[BaselineDelta]:
    """Produce a delta row for every scenario frozen in *store*.

    For each scenario recorded in *store*, the current mean is computed
    from the top-5 leaderboard entries.  If no leaderboard entries exist
    for a scenario the current mean is 0.0.

    Parameters
    ----------
    store:
        :class:`ArenaBaselineStore` with frozen baselines.
    leaderboard:
        :class:`~isaaclab_arena_leaderboard.Leaderboard` to read
        current scores from.

    Returns
    -------
    list[BaselineDelta]
        One delta entry per scenario in *store*, in store-insertion order.
    """
    deltas: List[BaselineDelta] = []
    for scenario_id in store.list_scenarios():
        baseline = store.get(scenario_id)
        if baseline is None:
            continue  # should not happen; defensive
        top_entries = leaderboard.top_k(scenario_id, k=5)
        current_scores = [e["score"] for e in top_entries]
        delta = compare_to_baseline(scenario_id, current_scores, baseline)
        deltas.append(delta)
    return deltas
