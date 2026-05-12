"""Stable-baseline taxonomy — the typed three-way classification.

`stable_ok` (passes >= N-of-M runs at fixed seed), `flaky` (passes some
runs, fails others), `stable_fail` (fails every run with documented
root cause). Encoded as `IntEnum` so lower = worse — natural ordering
lets callers compare severity (e.g. `current < frozen` => regressed).

Per `specs/IA_FULL_SPEC_2026-05-10.md` Phase 8d.
"""
from __future__ import annotations

from enum import IntEnum


class BaselineStatus(IntEnum):
    """Three-way severity-ordered classification of a CP's baseline.

    Ordering invariant: ``stable_fail < flaky < stable_ok``. Lower is
    worse. Callers can compare two statuses to detect regression
    (``current < frozen`` means the CP got worse) or pick the more
    severe of a set with ``min()``.
    """

    stable_fail = -1
    flaky = 0
    stable_ok = 1


def classify(n_pass: int, n_total: int, n_of_m: int = 3) -> BaselineStatus:
    """Map (n_pass, n_total) to a `BaselineStatus`.

    Rules:
      * ``n_pass == 0``                              -> ``stable_fail``
      * ``n_pass >= n_of_m AND n_pass == n_total``   -> ``stable_ok``
        (all observed runs passed and we cleared the N-of-M threshold)
      * otherwise                                    -> ``flaky``

    `n_of_m` is the minimum number of consecutive clears required for
    `stable_ok`; defaults to 3.

    Raises:
        ValueError: If `n_pass < 0`, `n_total < 0`, or `n_pass > n_total`.
    """
    if n_total < 0:
        raise ValueError(f"n_total must be >= 0, got {n_total}")
    if n_pass < 0:
        raise ValueError(f"n_pass must be >= 0, got {n_pass}")
    if n_pass > n_total:
        raise ValueError(
            f"n_pass ({n_pass}) cannot exceed n_total ({n_total})"
        )

    if n_pass == 0:
        return BaselineStatus.stable_fail
    if n_pass >= n_of_m and n_pass == n_total:
        return BaselineStatus.stable_ok
    return BaselineStatus.flaky
