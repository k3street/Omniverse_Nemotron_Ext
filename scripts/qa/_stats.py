"""
_stats.py — small, dependency-free statistics helpers for QA reporting.

Per docs/specs/2026-05-08-kcode-research-and-vault-spec.md Track B (section 8):
binomial proportion confidence intervals via the Wilson score interval.
Used for honest pass/fail reporting in canary_trend, T4-tier verification,
and adversarial-suite expansion.

The Wilson interval is a binomial proportion CI that handles the edge
cases (p=0, p=1, small n) where the normal-approximation Wald interval
gives nonsense bounds. Reference: Wilson 1927, "Probable Inference, the
Law of Succession, and Statistical Inference".

Two integers in (passes, n), two floats out (lower, upper). No
vocabulary, no tokenizer, no infrastructure dependency. Operates on
existing `qa_runs/*.jsonl` schema unchanged.
"""
from __future__ import annotations

from typing import Tuple


# z-values for common confidence levels (one-sided z * 2 = two-sided CI)
Z_90 = 1.6449
Z_95 = 1.96
Z_99 = 2.5758


def wilson(passes: int, n: int, z: float = Z_95) -> Tuple[float, float]:
    """Wilson score interval for binomial proportion at confidence level z.

    Args:
        passes: number of successes (≥ 0, ≤ n)
        n: total trials (≥ 0)
        z: z-score for the desired CI; 1.96 = 95% (default), 2.576 = 99%

    Returns:
        (lower, upper) interval bounds, both in [0, 1].
        For n=0, returns (0.0, 1.0) — uninformative prior.

    Examples:
        wilson(10, 10)  → (0.722, 1.000)  # all pass, narrow upper
        wilson(0, 10)   → (0.000, 0.278)  # all fail, narrow lower
        wilson(5, 10)   → (0.237, 0.763)  # 50/50, wide
        wilson(50, 100) → (0.402, 0.598)  # 50/50, narrower with more data
    """
    if n <= 0:
        return (0.0, 1.0)
    if passes < 0 or passes > n:
        raise ValueError(f"wilson: passes={passes} out of range [0, {n}]")
    p = passes / n
    z2 = z * z
    denom = 1.0 + z2 / n
    centre = (p + z2 / (2.0 * n)) / denom
    half = z * ((p * (1.0 - p) / n + z2 / (4.0 * n * n)) ** 0.5) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def wilson_lower(passes: int, n: int, z: float = Z_95) -> float:
    """Lower bound of Wilson interval. Use as a verified-threshold —
    e.g., `wilson_lower(passes, n) > 0.5` is more honest than
    triple-perfect when n is small."""
    return wilson(passes, n, z)[0]


def wilson_upper(passes: int, n: int, z: float = Z_95) -> float:
    """Upper bound of Wilson interval."""
    return wilson(passes, n, z)[1]


def format_proportion(passes: int, n: int, z: float = Z_95) -> str:
    """Format a pass/fail proportion as 'P/N (XX% [LL%, UU%])'.

    Use in QA report tables to give readers both the point estimate
    and the CI. CI overlap is the honest non-significance test —
    e.g., baseline 13/20 [0.43, 0.82] vs treatment 16/20 [0.58, 0.92]
    have overlapping intervals → improvement is not yet statistically
    significant; need more data.
    """
    if n <= 0:
        return f"{passes}/{n} (n/a)"
    p = passes / n
    lo, hi = wilson(passes, n, z)
    return f"{passes}/{n} ({100 * p:.0f}% [{100 * lo:.0f}%, {100 * hi:.0f}%])"


def overlapping(a_passes: int, a_n: int, b_passes: int, b_n: int,
                z: float = Z_95) -> bool:
    """True if two Wilson intervals overlap — meaning the difference
    between the two proportions is not statistically significant at z.
    Non-overlapping intervals = significant difference."""
    a_lo, a_hi = wilson(a_passes, a_n, z)
    b_lo, b_hi = wilson(b_passes, b_n, z)
    return not (a_hi < b_lo or b_hi < a_lo)
