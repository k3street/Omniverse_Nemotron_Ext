"""
Shared uncertainty primitives for IA — Distribution, GradedScale.

`Distribution` carries replicate statistics (mean / std / n) from
deterministic-critic and sim-replicate paths (Phase 53). `GradedScale`
is the 5-level ordinal severity used by `diagnose/`, MathCritic, and
palette clearance grades.

Zero internal IA dependencies — see __init__.py.
"""
from __future__ import annotations

import math
from enum import IntEnum
from typing import Sequence

from pydantic import BaseModel, ConfigDict, Field, field_validator


# ---------------------------------------------------------------------------
# Distribution
# ---------------------------------------------------------------------------

class Distribution(BaseModel):
    """A 1D distribution summary: sample mean, sample standard deviation,
    and the number of samples that produced the estimate.

    The summary form (no raw values stored) is intentional — replicate
    sets in sim or DR runs are large and we cache the summary downstream.
    Use `from_replicates` to build one from raw measurements; the std is
    computed with Bessel's correction (ddof=1) so it is the unbiased
    estimator of the population std.
    """

    model_config = ConfigDict(frozen=True)

    mean: float
    std: float = Field(ge=0.0)
    n_samples: int = Field(ge=1)

    @field_validator("std")
    @classmethod
    def _std_must_be_finite(cls, v: float) -> float:
        if math.isnan(v) or math.isinf(v):
            raise ValueError(f"Distribution.std must be finite, got {v!r}")
        return v

    @field_validator("mean")
    @classmethod
    def _mean_must_be_finite(cls, v: float) -> float:
        if math.isnan(v) or math.isinf(v):
            raise ValueError(f"Distribution.mean must be finite, got {v!r}")
        return v

    @classmethod
    def from_replicates(cls, values: Sequence[float]) -> "Distribution":
        """Build a Distribution from raw replicate measurements.

        - Empty sequence raises `ValueError`.
        - Single sample → `std = 0.0`, `n_samples = 1` (Bessel correction
          would divide by zero; we report zero spread explicitly).
        - n ≥ 2 → sample std with ddof=1.
        """
        seq = list(values)
        n = len(seq)
        if n == 0:
            raise ValueError(
                "Distribution.from_replicates: at least one value required"
            )

        floats = [float(v) for v in seq]
        mean = sum(floats) / n

        if n == 1:
            std = 0.0
        else:
            # Sample variance with Bessel's correction (ddof=1).
            sq = sum((v - mean) ** 2 for v in floats)
            std = math.sqrt(sq / (n - 1))

        return cls(mean=mean, std=std, n_samples=n)


# ---------------------------------------------------------------------------
# GradedScale
# ---------------------------------------------------------------------------

class GradedScale(IntEnum):
    """5-level ordinal severity / confidence / grade.

    Used uniformly across:
    - `diagnose/` for constraint-violation severity
    - MathCritic for confidence tiers
    - palette metadata for clearance grades

    Higher values = more severe / higher grade. `<` / `>` work via
    integer comparison because `IntEnum` inherits from `int`.
    """

    INFO = 0
    NOTICE = 1
    WARNING = 2
    ERROR = 3
    CRITICAL = 4
