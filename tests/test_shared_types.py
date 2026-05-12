"""
L0 tests for the shared typed primitives module (spec Phase 8c).

Covers Vec3 / Pose6D / Bbox3, Distribution, GradedScale, Source —
plus the import-purity contract that gates the whole phase's
risk-mitigation claim: `service.isaac_assist_service.types` must NOT
transitively load any other IA module.
"""
from __future__ import annotations

import math
import subprocess
import sys
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest
from pydantic import ValidationError

pytestmark = pytest.mark.l0

from service.isaac_assist_service.types import (
    Bbox3,
    Distribution,
    GradedScale,
    Pose6D,
    Source,
    Vec3,
)


# ---------------------------------------------------------------------------
# Vec3
# ---------------------------------------------------------------------------

class TestVec3:
    def test_to_tuple_round_trip(self):
        v = Vec3(x=1.5, y=-2.0, z=0.25)
        assert v.to_tuple() == (1.5, -2.0, 0.25)
        assert Vec3.from_tuple(v.to_tuple()) == v

    def test_from_tuple_rejects_wrong_length(self):
        with pytest.raises(ValueError):
            Vec3.from_tuple((1.0, 2.0))  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Pose6D — round-trip is the spec's primary acceptance test
# ---------------------------------------------------------------------------

class TestPose6DRoundTrip:
    """Spec §Test.1: round-trip 100 random poses within 1e-9."""

    def test_to_from_matrix_round_trip_100_random_poses(self):
        rng = np.random.default_rng(42)
        # Sample translations in [-5, 5]^3 and Euler angles in
        # (-pi/2 + delta, pi/2 - delta) for pitch to stay clear of
        # gimbal lock; roll/yaw range across [-pi, pi].
        delta = 0.05  # 50 mrad away from singularity
        n_poses = 100

        max_err = 0.0
        for _ in range(n_poses):
            tx, ty, tz = rng.uniform(-5.0, 5.0, size=3)
            roll = rng.uniform(-math.pi, math.pi)
            pitch = rng.uniform(-math.pi / 2 + delta, math.pi / 2 - delta)
            yaw = rng.uniform(-math.pi, math.pi)

            p = Pose6D(
                x=float(tx),
                y=float(ty),
                z=float(tz),
                roll=float(roll),
                pitch=float(pitch),
                yaw=float(yaw),
            )
            m = p.to_matrix()
            p2 = Pose6D.from_matrix(m)
            m2 = p2.to_matrix()

            # Compare matrices (Euler representation may differ by 2π
            # equivalences near the wrap; matrix comparison is canonical).
            err = float(np.max(np.abs(m - m2)))
            max_err = max(max_err, err)

        assert max_err < 1e-9, (
            f"Pose6D round-trip max matrix error {max_err} exceeded 1e-9"
        )

    def test_to_matrix_homogeneous_row(self):
        p = Pose6D(x=1.0, y=2.0, z=3.0, roll=0.1, pitch=-0.2, yaw=0.3)
        m = p.to_matrix()
        assert m.shape == (4, 4)
        assert np.allclose(m[3, :], [0.0, 0.0, 0.0, 1.0])

    def test_to_matrix_translation_block(self):
        p = Pose6D(x=7.0, y=-3.0, z=0.5)
        m = p.to_matrix()
        assert m[0, 3] == pytest.approx(7.0)
        assert m[1, 3] == pytest.approx(-3.0)
        assert m[2, 3] == pytest.approx(0.5)

    def test_from_quaternion_identity(self):
        # Identity quaternion → zero Euler angles.
        p = Pose6D.from_quaternion(0.0, 0.0, 0.0, 1.0, position=(1.0, 2.0, 3.0))
        assert p.x == 1.0 and p.y == 2.0 and p.z == 3.0
        assert p.roll == pytest.approx(0.0)
        assert p.pitch == pytest.approx(0.0)
        assert p.yaw == pytest.approx(0.0)

    def test_from_quaternion_rejects_zero_norm(self):
        with pytest.raises(ValueError):
            Pose6D.from_quaternion(0.0, 0.0, 0.0, 0.0)


# ---------------------------------------------------------------------------
# Bbox3
# ---------------------------------------------------------------------------

class TestBbox3:
    def test_intersects_overlapping_pair(self):
        a = Bbox3(min=Vec3(x=0, y=0, z=0), max=Vec3(x=2, y=2, z=2))
        b = Bbox3(min=Vec3(x=1, y=1, z=1), max=Vec3(x=3, y=3, z=3))
        assert a.intersects(b) is True
        assert b.intersects(a) is True

    def test_intersects_disjoint_pair(self):
        a = Bbox3(min=Vec3(x=0, y=0, z=0), max=Vec3(x=1, y=1, z=1))
        b = Bbox3(min=Vec3(x=2, y=2, z=2), max=Vec3(x=3, y=3, z=3))
        assert a.intersects(b) is False
        assert b.intersects(a) is False

    def test_volume_unit_two_cube(self):
        # 2 m cube at the origin → 8 m³.
        b = Bbox3(min=Vec3(x=0, y=0, z=0), max=Vec3(x=2, y=2, z=2))
        assert b.volume_m3() == pytest.approx(8.0)

    def test_expand_unit_cube_by_half_meter(self):
        # 1×1×1 box, expand by 0.5 m → 2×2×2.
        b = Bbox3(min=Vec3(x=0, y=0, z=0), max=Vec3(x=1, y=1, z=1))
        expanded = b.expand(0.5)
        assert expanded.min.to_tuple() == pytest.approx((-0.5, -0.5, -0.5))
        assert expanded.max.to_tuple() == pytest.approx((1.5, 1.5, 1.5))
        # Each axis is now 2.0 m wide
        dx = expanded.max.x - expanded.min.x
        dy = expanded.max.y - expanded.min.y
        dz = expanded.max.z - expanded.min.z
        assert (dx, dy, dz) == pytest.approx((2.0, 2.0, 2.0))

    def test_min_must_be_le_max(self):
        with pytest.raises(ValidationError):
            Bbox3(min=Vec3(x=1, y=0, z=0), max=Vec3(x=0, y=1, z=1))


# ---------------------------------------------------------------------------
# Distribution
# ---------------------------------------------------------------------------

class TestDistribution:
    def test_from_replicates_three_values(self):
        # Spec §Test.3: mean=1.0, std≈0.1, n=3.
        d = Distribution.from_replicates([1.0, 1.1, 0.9])
        assert d.mean == pytest.approx(1.0, abs=1e-6)
        assert d.std == pytest.approx(0.1, abs=0.01)
        assert d.n_samples == 3

    def test_from_replicates_single_sample(self):
        d = Distribution.from_replicates([5.0])
        assert d.mean == pytest.approx(5.0)
        assert d.std == 0.0
        assert d.n_samples == 1

    def test_from_replicates_empty_raises(self):
        with pytest.raises(ValueError):
            Distribution.from_replicates([])

    def test_std_must_be_non_negative(self):
        with pytest.raises(ValidationError):
            Distribution(mean=0.0, std=-0.1, n_samples=3)

    def test_n_samples_must_be_positive(self):
        with pytest.raises(ValidationError):
            Distribution(mean=0.0, std=0.0, n_samples=0)


# ---------------------------------------------------------------------------
# GradedScale
# ---------------------------------------------------------------------------

class TestGradedScale:
    def test_warning_lt_error(self):
        assert GradedScale.WARNING < GradedScale.ERROR

    def test_critical_gt_info(self):
        assert GradedScale.CRITICAL > GradedScale.INFO

    def test_full_ordering(self):
        ordered = [
            GradedScale.INFO,
            GradedScale.NOTICE,
            GradedScale.WARNING,
            GradedScale.ERROR,
            GradedScale.CRITICAL,
        ]
        assert ordered == sorted(ordered)

    def test_values(self):
        # Spec requires these exact integer values.
        assert int(GradedScale.INFO) == 0
        assert int(GradedScale.NOTICE) == 1
        assert int(GradedScale.WARNING) == 2
        assert int(GradedScale.ERROR) == 3
        assert int(GradedScale.CRITICAL) == 4


# ---------------------------------------------------------------------------
# Source
# ---------------------------------------------------------------------------

class TestSource:
    def test_accepts_utc_aware(self):
        s = Source(
            stage="vlm_extract",
            confidence_0_1=0.85,
            ts_utc=datetime.now(timezone.utc),
        )
        assert s.stage == "vlm_extract"
        assert s.confidence_0_1 == 0.85
        assert s.ts_utc.tzinfo is not None

    def test_normalizes_non_utc_aware_to_utc(self):
        tz_plus_two = timezone(timedelta(hours=2))
        local_dt = datetime(2026, 5, 12, 14, 0, 0, tzinfo=tz_plus_two)
        s = Source(stage="sim", confidence_0_1=1.0, ts_utc=local_dt)
        # Equivalent UTC instant should be 12:00.
        assert s.ts_utc == datetime(2026, 5, 12, 12, 0, 0, tzinfo=timezone.utc)

    def test_rejects_naive_datetime(self):
        with pytest.raises(ValidationError):
            Source(
                stage="vlm_extract",
                confidence_0_1=0.5,
                ts_utc=datetime(2026, 5, 12, 14, 0, 0),  # naive — no tzinfo
            )

    def test_rejects_confidence_below_zero(self):
        with pytest.raises(ValidationError):
            Source(
                stage="x",
                confidence_0_1=-0.01,
                ts_utc=datetime.now(timezone.utc),
            )

    def test_rejects_confidence_above_one(self):
        with pytest.raises(ValidationError):
            Source(
                stage="x",
                confidence_0_1=1.01,
                ts_utc=datetime.now(timezone.utc),
            )


# ---------------------------------------------------------------------------
# Import purity smoke test
# ---------------------------------------------------------------------------

class TestImportPurity:
    """Phase 8c risk-mitigation contract: `types/` must NOT pull in any
    other IA module (multimodal/, diagnose/, governance/, chat/, etc.).

    We run the import in a fresh subprocess so it isn't contaminated by
    `pytest`'s own preloaded modules.
    """

    def test_types_package_has_no_internal_ia_deps(self):
        script = (
            "import sys, json\n"
            "from service.isaac_assist_service.types import (\n"
            "    spatial, uncertainty, provenance,\n"
            "    Vec3, Pose6D, Bbox3, Distribution, GradedScale, Source,\n"
            ")\n"
            # Capture every loaded module whose name starts with the IA prefix.
            "ia_loaded = sorted(\n"
            "    m for m in sys.modules\n"
            "    if m == 'service' or m.startswith('service.')\n"
            ")\n"
            "print(json.dumps(ia_loaded))\n"
        )
        proc = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert proc.returncode == 0, (
            f"types/ import failed:\nSTDOUT: {proc.stdout}\nSTDERR: {proc.stderr}"
        )

        import json as _json

        ia_loaded = _json.loads(proc.stdout.strip().splitlines()[-1])

        # Whitelist: only the types/ package itself, its submodules, and
        # the parent path prefixes Python needs to resolve them. Phase 11b
        # added `violations` as a fourth submodule under the same
        # zero-internal-deps contract.
        allowed = {
            "service",
            "service.isaac_assist_service",
            "service.isaac_assist_service.types",
            "service.isaac_assist_service.types.spatial",
            "service.isaac_assist_service.types.uncertainty",
            "service.isaac_assist_service.types.provenance",
            "service.isaac_assist_service.types.violations",
        }
        forbidden = [m for m in ia_loaded if m not in allowed]
        assert forbidden == [], (
            "Importing service.isaac_assist_service.types must not pull in "
            f"any other IA module. Got forbidden imports: {forbidden}"
        )
