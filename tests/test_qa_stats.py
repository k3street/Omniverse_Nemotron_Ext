"""Unit tests for scripts/qa/_stats.py — Wilson confidence intervals
and helpers per docs/specs/2026-05-08-kcode-research-and-vault-spec.md
section 8 (Track B)."""
from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

# All tests in this module are L0 (pure functions, no external deps)
pytestmark = pytest.mark.l0

# scripts/ is not a package — load _stats.py directly
_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / "scripts" / "qa"))
import _stats  # type: ignore  # noqa: E402


def test_wilson_zero_trials_returns_full_unit_interval():
    """n=0: no information → uninformative prior [0, 1]."""
    lo, hi = _stats.wilson(0, 0)
    assert lo == 0.0
    assert hi == 1.0


def test_wilson_all_pass_lower_bound_strictly_below_one():
    """10/10 must give lower bound < 1.0 (we cannot prove certainty
    from 10 trials), upper bound = 1.0."""
    lo, hi = _stats.wilson(10, 10)
    assert hi == 1.0
    assert 0.65 < lo < 0.85, f"expected lo near 0.72, got {lo}"


def test_wilson_all_fail_upper_bound_strictly_above_zero():
    """0/10 must give upper bound > 0.0, lower bound = 0.0."""
    lo, hi = _stats.wilson(0, 10)
    assert lo == 0.0
    assert 0.18 < hi < 0.40, f"expected hi near 0.28, got {hi}"


def test_wilson_fifty_fifty_centered_with_wide_ci_at_small_n():
    """5/10 should be centered around 0.5, with wide CI."""
    lo, hi = _stats.wilson(5, 10)
    assert 0.20 < lo < 0.30
    assert 0.70 < hi < 0.80


def test_wilson_fifty_fifty_narrows_with_more_data():
    """50/100 should be centered at 0.5 with narrower CI than 5/10."""
    lo_small, hi_small = _stats.wilson(5, 10)
    lo_large, hi_large = _stats.wilson(50, 100)
    width_small = hi_small - lo_small
    width_large = hi_large - lo_large
    assert width_large < width_small, "more trials should narrow CI"
    assert 0.40 < lo_large < 0.45
    assert 0.55 < hi_large < 0.60


def test_wilson_z_99_wider_than_z_95():
    """Higher confidence level → wider interval (same data)."""
    lo95, hi95 = _stats.wilson(50, 100, z=_stats.Z_95)
    lo99, hi99 = _stats.wilson(50, 100, z=_stats.Z_99)
    assert (hi99 - lo99) > (hi95 - lo95)


def test_wilson_invalid_passes_raises():
    """passes > n is invalid input."""
    with pytest.raises(ValueError):
        _stats.wilson(5, 3)
    with pytest.raises(ValueError):
        _stats.wilson(-1, 10)


def test_wilson_lower_extracts_first_element():
    lo = _stats.wilson_lower(7, 10)
    full_lo, _ = _stats.wilson(7, 10)
    assert lo == full_lo


def test_wilson_upper_extracts_second_element():
    hi = _stats.wilson_upper(7, 10)
    _, full_hi = _stats.wilson(7, 10)
    assert hi == full_hi


def test_format_proportion_includes_counts_percent_and_ci():
    s = _stats.format_proportion(13, 20)
    assert "13/20" in s
    assert "65%" in s
    # CI should be present in [LL%, UU%] form
    assert "[" in s and "]" in s


def test_format_proportion_zero_n_is_safe():
    s = _stats.format_proportion(0, 0)
    assert "0/0" in s


def test_overlapping_returns_true_for_overlapping_intervals():
    """13/20 → ~[0.43, 0.82], 16/20 → ~[0.58, 0.92]. Heavy overlap."""
    assert _stats.overlapping(13, 20, 16, 20) is True


def test_overlapping_returns_false_for_clearly_separated():
    """0/100 → ~[0, 0.04], 100/100 → ~[0.96, 1]. Disjoint."""
    assert _stats.overlapping(0, 100, 100, 100) is False


def test_overlapping_symmetric():
    """overlapping(a, b) should equal overlapping(b, a)."""
    assert (
        _stats.overlapping(5, 10, 15, 20)
        == _stats.overlapping(15, 20, 5, 10)
    )


def test_wilson_clamps_to_unit_interval():
    """Bounds must always be in [0, 1] regardless of inputs."""
    for passes, n in [(0, 1), (1, 1), (1, 2), (99, 100), (1000, 1000)]:
        lo, hi = _stats.wilson(passes, n)
        assert 0.0 <= lo <= 1.0
        assert 0.0 <= hi <= 1.0
        assert lo <= hi


def test_wilson_known_reference_value():
    """Cross-check against a known Wilson value:
    13/20 at z=1.96 → roughly [0.4326, 0.8190]
    (verified via scipy.stats.binomtest or external Wilson calculator)."""
    lo, hi = _stats.wilson(13, 20)
    assert math.isclose(lo, 0.4326, abs_tol=0.01)
    assert math.isclose(hi, 0.8190, abs_tol=0.01)


# ── find_wilson_passing tests (mark_verified.py) ──
# These exercise the verification helper directly. We import the function
# rather than running the CLI to keep tests pure (no chromadb).

def _build_runs(*per_task_results):
    """per_task_results: list of (task_id, [pass_or_fail, ...]) tuples.
    Returns N runs in mark_verified's load_judged dict shape."""
    n_runs = len(per_task_results[0][1]) if per_task_results else 0
    runs = []
    for run_idx in range(n_runs):
        run = {}
        for tid, results in per_task_results:
            ok = results[run_idx]
            run[tid] = {
                "real": ok, "scene": ok, "fab": 0 if ok else 1, "turns": 5,
            }
        runs.append(run)
    return runs


def test_find_wilson_passing_strict_threshold_rejects_2_of_3():
    """2/3 successes — Wilson lower at 95% is ~0.21. So threshold ≥ 0.5
    must reject; threshold ≤ 0.2 must accept."""
    sys.path.insert(0, str(_REPO_ROOT / "scripts" / "qa"))
    import mark_verified as mv  # type: ignore  # noqa: E402
    runs = _build_runs(("T-A", [True, True, False]))
    # Strict 0.5: not enough evidence from n=3
    out = mv.find_wilson_passing(runs, threshold=0.5)
    assert out == [], f"expected empty at threshold 0.5, got {out}"
    # Lenient 0.2: 2/3 should pass
    out = mv.find_wilson_passing(runs, threshold=0.2)
    assert len(out) == 1
    tid, passes, n, lo = out[0]
    assert tid == "T-A" and passes == 2 and n == 3
    assert 0.20 < lo < 0.25, f"expected lo near 0.21, got {lo}"


def test_find_wilson_passing_three_perfect_passes_at_low_threshold():
    """3/3 — Wilson lower at 95% is ~0.44. Threshold ≤ 0.4 should accept,
    ≥ 0.5 should reject."""
    sys.path.insert(0, str(_REPO_ROOT / "scripts" / "qa"))
    import mark_verified as mv  # type: ignore  # noqa: E402
    runs = _build_runs(("T-PERFECT", [True, True, True]))
    out = mv.find_wilson_passing(runs, threshold=0.4)
    assert len(out) == 1, "3/3 should pass at threshold 0.4"
    out = mv.find_wilson_passing(runs, threshold=0.5)
    assert out == [], "3/3 too small for threshold 0.5"


def test_find_wilson_passing_intersects_runs():
    """Tasks present in only some runs are excluded (same as triple-perfect)."""
    sys.path.insert(0, str(_REPO_ROOT / "scripts" / "qa"))
    import mark_verified as mv  # type: ignore  # noqa: E402
    runs = [
        {"T-COMMON": {"real": True, "scene": True, "fab": 0},
         "T-ONLY-1": {"real": True, "scene": True, "fab": 0}},
        {"T-COMMON": {"real": True, "scene": True, "fab": 0}},
    ]
    out = mv.find_wilson_passing(runs, threshold=0.1)
    ids = [t[0] for t in out]
    assert "T-COMMON" in ids
    assert "T-ONLY-1" not in ids, "task missing from one run must be excluded"


def test_find_wilson_passing_empty_runs_returns_empty():
    sys.path.insert(0, str(_REPO_ROOT / "scripts" / "qa"))
    import mark_verified as mv  # type: ignore  # noqa: E402
    assert mv.find_wilson_passing([], threshold=0.5) == []
