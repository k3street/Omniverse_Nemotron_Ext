"""Phase 61 — SDG correlated DR for sensor-camera pairs.

Per specs/IA_FULL_SPEC_2026-05-10.md Phase 61.
"""
from __future__ import annotations

import math
import random

import pytest

from service.isaac_assist_service.multimodal.sdg_correlated_dr import (
    SENSOR_CAMERA_PRESET,
    CorrelatedDRConfig,
    CorrelationPair,
    DRAxis,
    cholesky,
    correlation_matrix,
    empirical_correlation,
    empirical_correlation_matrix,
    get_phase_metadata,
    is_positive_semidefinite,
    sample_correlated,
)

pytestmark = pytest.mark.l0


def test_metadata_status_landed():
    md = get_phase_metadata()
    assert md["phase"] == 61
    assert md["status"] == "landed"


def test_dr_axis_rejects_negative_std():
    with pytest.raises(ValueError):
        DRAxis(name="x", mean=0, std=-1.0)


def test_correlation_pair_rejects_out_of_range():
    with pytest.raises(ValueError):
        CorrelationPair("a", "b", rho=1.5)
    with pytest.raises(ValueError):
        CorrelationPair("a", "b", rho=-1.5)


def test_correlation_pair_rejects_self_correlation():
    with pytest.raises(ValueError):
        CorrelationPair("a", "a", rho=0.5)


def test_correlation_matrix_builds_symmetric_with_diagonal_ones():
    cfg = CorrelatedDRConfig(
        name="t",
        axes=[DRAxis("a", 0, 1), DRAxis("b", 0, 1), DRAxis("c", 0, 1)],
        correlations=[CorrelationPair("a", "b", 0.5), CorrelationPair("b", "c", -0.3)],
    )
    R = correlation_matrix(cfg)
    assert R[0][0] == 1.0
    assert R[1][1] == 1.0
    assert R[2][2] == 1.0
    assert R[0][1] == 0.5
    assert R[1][0] == 0.5
    assert R[1][2] == -0.3
    assert R[2][1] == -0.3
    assert R[0][2] == 0.0


def test_cholesky_identity():
    I = [[1.0, 0.0], [0.0, 1.0]]
    L = cholesky(I)
    assert L[0][0] == 1.0
    assert L[1][1] == 1.0
    assert L[0][1] == 0.0


def test_cholesky_rejects_non_psd():
    bad = [[1.0, 2.0], [2.0, 1.0]]
    with pytest.raises(ValueError):
        cholesky(bad)


def test_is_positive_semidefinite():
    assert is_positive_semidefinite([[1.0, 0.5], [0.5, 1.0]])
    assert not is_positive_semidefinite([[1.0, 2.0], [2.0, 1.0]])


def test_empirical_correlation_matches_requested_within_spec_tolerance():
    """Spec contract: empirical correlation within ±0.1 of requested ρ over
    100 samples. We use 2000 samples here for stability."""
    cfg = CorrelatedDRConfig(
        name="t",
        axes=[
            DRAxis("lighting_kelvin", mean=5500.0, std=1500.0),
            DRAxis("camera_exposure_ms", mean=10.0, std=4.0),
        ],
        correlations=[
            CorrelationPair("lighting_kelvin", "camera_exposure_ms", rho=-0.7),
        ],
        num_samples=2000,
    )
    rng = random.Random(42)
    samples = sample_correlated(cfg, rng=rng)
    r = empirical_correlation(samples, "lighting_kelvin", "camera_exposure_ms")
    assert abs(r - (-0.7)) < 0.1, f"empirical r={r:.3f} not within 0.1 of -0.7"


def test_sample_correlated_uncorrelated_axes_give_near_zero():
    cfg = CorrelatedDRConfig(
        name="t",
        axes=[DRAxis("a", 0, 1), DRAxis("b", 0, 1)],
        correlations=[],
        num_samples=2000,
    )
    rng = random.Random(7)
    samples = sample_correlated(cfg, rng=rng)
    r = empirical_correlation(samples, "a", "b")
    assert abs(r) < 0.1


def test_sample_correlated_means_recovered():
    cfg = CorrelatedDRConfig(
        name="t",
        axes=[DRAxis("x", 100.0, 5.0), DRAxis("y", -50.0, 2.0)],
        correlations=[CorrelationPair("x", "y", 0.5)],
        num_samples=2000,
    )
    rng = random.Random(11)
    samples = sample_correlated(cfg, rng=rng)
    mean_x = sum(s["x"] for s in samples) / len(samples)
    mean_y = sum(s["y"] for s in samples) / len(samples)
    assert abs(mean_x - 100.0) < 1.0
    assert abs(mean_y - (-50.0)) < 0.5


def test_sample_correlated_stds_recovered():
    cfg = CorrelatedDRConfig(
        name="t",
        axes=[DRAxis("x", 0.0, 7.0)],
        correlations=[],
        num_samples=2000,
    )
    rng = random.Random(13)
    samples = sample_correlated(cfg, rng=rng)
    mean = sum(s["x"] for s in samples) / len(samples)
    var = sum((s["x"] - mean) ** 2 for s in samples) / len(samples)
    assert abs(math.sqrt(var) - 7.0) < 0.5


def test_sensor_camera_preset_three_pairs_within_tolerance():
    rng = random.Random(2026)
    samples = sample_correlated(SENSOR_CAMERA_PRESET, rng=rng, n_samples=3000)
    r_le = empirical_correlation(samples, "lighting_kelvin", "camera_exposure_ms")
    r_lw = empirical_correlation(samples, "lighting_kelvin", "camera_white_balance_kelvin")
    r_en = empirical_correlation(samples, "camera_exposure_ms", "camera_noise_sigma")
    assert abs(r_le - (-0.7)) < 0.1
    assert abs(r_lw - 0.5) < 0.1
    assert abs(r_en - 0.5) < 0.1


def test_empirical_correlation_matrix_shape_and_diagonal():
    cfg = CorrelatedDRConfig(
        name="t",
        axes=[DRAxis("a", 0, 1), DRAxis("b", 0, 1), DRAxis("c", 0, 1)],
        correlations=[CorrelationPair("a", "b", 0.5)],
        num_samples=500,
    )
    rng = random.Random(99)
    samples = sample_correlated(cfg, rng=rng)
    M = empirical_correlation_matrix(samples, ["a", "b", "c"])
    assert M[0][0] == 1.0
    assert M[1][1] == 1.0
    assert M[2][2] == 1.0
    assert abs(M[0][1] - M[1][0]) < 1e-12
    assert abs(M[0][1] - 0.5) < 0.15


def test_n_samples_override_in_sample_correlated():
    cfg = CorrelatedDRConfig(
        name="t",
        axes=[DRAxis("a", 0, 1)],
        correlations=[],
        num_samples=100,
    )
    rng = random.Random(1)
    samples = sample_correlated(cfg, rng=rng, n_samples=50)
    assert len(samples) == 50
