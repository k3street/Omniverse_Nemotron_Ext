"""Phase 61 — SDG: correlated DR for sensor-camera pairs.

When lighting (color temperature) randomizes, camera exposure, white
balance, and noise level should covary realistically. This module
expresses correlation as a covariance matrix between named DR axes
and draws samples from the implied multivariate normal.

Per specs/IA_FULL_SPEC_2026-05-10.md Phase 61.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

PHASE_ID = 61
PHASE_TITLE = "SDG: correlated DR for sensor-camera pairs"
PHASE_STATUS = "landed"


def get_phase_metadata() -> Dict[str, Any]:
    """Return phase metadata for spec-coverage audits."""
    return {
        "phase": PHASE_ID,
        "title": PHASE_TITLE,
        "status": PHASE_STATUS,
        "spec_ref": "specs/IA_FULL_SPEC_2026-05-10.md Phase 61",
    }


@dataclass
class DRAxis:
    """One scalar DR axis: name + Gaussian parameters."""
    name: str
    mean: float
    std: float

    def __post_init__(self) -> None:
        if self.std < 0:
            raise ValueError(f"std must be non-negative, got {self.std}")


@dataclass
class CorrelationPair:
    """One pairwise correlation between two named DR axes."""
    axis_a: str
    axis_b: str
    rho: float  # in [-1, 1]

    def __post_init__(self) -> None:
        if not -1.0 <= self.rho <= 1.0:
            raise ValueError(f"rho must be in [-1, 1], got {self.rho}")
        if self.axis_a == self.axis_b:
            raise ValueError(f"axis_a and axis_b must differ; both = {self.axis_a}")


@dataclass
class CorrelatedDRConfig:
    """Full correlated-DR preset: axes + correlation graph."""
    name: str
    axes: List[DRAxis]
    correlations: List[CorrelationPair] = field(default_factory=list)
    num_samples: int = 100

    def axis_names(self) -> List[str]:
        return [a.name for a in self.axes]

    def axis_index(self, name: str) -> int:
        for i, a in enumerate(self.axes):
            if a.name == name:
                return i
        raise KeyError(f"unknown DR axis: {name}")


def correlation_matrix(config: CorrelatedDRConfig) -> List[List[float]]:
    """Build a dense symmetric correlation matrix from the sparse pair spec.

    Diagonal = 1.0, off-diagonal = rho for explicit pairs (in both orders),
    other entries = 0.0.
    """
    n = len(config.axes)
    matrix = [[0.0] * n for _ in range(n)]
    for i in range(n):
        matrix[i][i] = 1.0
    for pair in config.correlations:
        i = config.axis_index(pair.axis_a)
        j = config.axis_index(pair.axis_b)
        matrix[i][j] = pair.rho
        matrix[j][i] = pair.rho
    return matrix


def is_positive_semidefinite(matrix: Sequence[Sequence[float]], tol: float = 1e-9) -> bool:
    """Check positive-semi-definiteness via Cholesky attempt with tolerance."""
    try:
        cholesky(matrix, tol=tol)
        return True
    except ValueError:
        return False


def cholesky(matrix: Sequence[Sequence[float]], tol: float = 1e-9) -> List[List[float]]:
    """Lower-triangular Cholesky decomposition. Raises ValueError if not PSD.

    For zero diagonal entries (rank-deficient), uses zero rows below.
    """
    n = len(matrix)
    L: List[List[float]] = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i + 1):
            s = sum(L[i][k] * L[j][k] for k in range(j))
            if i == j:
                val = matrix[i][i] - s
                if val < -tol:
                    raise ValueError(
                        f"matrix not positive-semi-definite at [{i},{i}]: {val:.6g}"
                    )
                L[i][j] = math.sqrt(max(val, 0.0))
            else:
                if L[j][j] <= tol:
                    L[i][j] = 0.0
                else:
                    L[i][j] = (matrix[i][j] - s) / L[j][j]
    return L


def sample_correlated(
    config: CorrelatedDRConfig,
    rng: Optional[random.Random] = None,
    n_samples: Optional[int] = None,
) -> List[Dict[str, float]]:
    """Draw correlated samples from the configured multivariate normal.

    Returns a list of dicts {axis_name: value}, one per sample.
    """
    rng = rng or random.Random()
    n = n_samples if n_samples is not None else config.num_samples
    R = correlation_matrix(config)
    L = cholesky(R)
    names = config.axis_names()
    means = [a.mean for a in config.axes]
    stds = [a.std for a in config.axes]
    samples: List[Dict[str, float]] = []
    for _ in range(n):
        z = [rng.gauss(0.0, 1.0) for _ in range(len(names))]
        y = [sum(L[i][k] * z[k] for k in range(len(names))) for i in range(len(names))]
        sample = {names[i]: means[i] + stds[i] * y[i] for i in range(len(names))}
        samples.append(sample)
    return samples


def empirical_correlation(
    samples: Sequence[Dict[str, float]],
    axis_a: str,
    axis_b: str,
) -> float:
    """Pearson correlation between two axes over the drawn samples."""
    n = len(samples)
    if n < 2:
        raise ValueError("need >= 2 samples to compute correlation")
    xs = [s[axis_a] for s in samples]
    ys = [s[axis_b] for s in samples]
    mx = sum(xs) / n
    my = sum(ys) / n
    cov = sum((xs[i] - mx) * (ys[i] - my) for i in range(n)) / n
    vx = sum((x - mx) ** 2 for x in xs) / n
    vy = sum((y - my) ** 2 for y in ys) / n
    denom = math.sqrt(vx * vy)
    if denom <= 0:
        return 0.0
    return cov / denom


def empirical_correlation_matrix(
    samples: Sequence[Dict[str, float]],
    axes: Sequence[str],
) -> List[List[float]]:
    """Full empirical correlation matrix between named axes."""
    n = len(axes)
    matrix = [[0.0] * n for _ in range(n)]
    for i in range(n):
        matrix[i][i] = 1.0
        for j in range(i + 1, n):
            r = empirical_correlation(samples, axes[i], axes[j])
            matrix[i][j] = r
            matrix[j][i] = r
    return matrix


SENSOR_CAMERA_PRESET = CorrelatedDRConfig(
    name="sensor_camera_correlated",
    axes=[
        DRAxis(name="lighting_kelvin", mean=5500.0, std=1500.0),
        DRAxis(name="camera_exposure_ms", mean=10.0, std=4.0),
        DRAxis(name="camera_white_balance_kelvin", mean=5500.0, std=1200.0),
        DRAxis(name="camera_noise_sigma", mean=0.02, std=0.01),
    ],
    correlations=[
        CorrelationPair("lighting_kelvin", "camera_exposure_ms", rho=-0.7),
        CorrelationPair("camera_white_balance_kelvin", "lighting_kelvin", rho=0.5),
        CorrelationPair("camera_exposure_ms", "camera_noise_sigma", rho=0.5),
    ],
    num_samples=200,
)
