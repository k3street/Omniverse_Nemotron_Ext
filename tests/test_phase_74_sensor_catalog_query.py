"""Phase 74 contract tests — sensor catalog structured filter.

Spec: specs/IA_FULL_SPEC_2026-05-10.md Phase 74
"""
import pytest

pytestmark = pytest.mark.l0


# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------

def test_phase_74_metadata():
    """Phase is landed, not scaffold."""
    from service.isaac_assist_service.multimodal.sensor_catalog_query import (
        get_phase_metadata,
    )

    md = get_phase_metadata()
    assert md["phase"] == 74
    assert md["status"] == "landed"


# ---------------------------------------------------------------------------
# Spec-required assertion: D435i + D455 in result, L515 excluded
# ---------------------------------------------------------------------------

def test_depth_stereo_subtype_filter_excludes_l515():
    """Querying 'depth camera' with subtype='depth_stereo' returns D435i and D455
    but NOT L515, which is a lidar_camera subtype.

    The spec cites the range discrepancy as context (L515 min range starts at
    0.25 m), but the primary discriminator here is subtype: L515 is classified
    as 'lidar_camera', not 'depth_stereo', so it is correctly excluded.
    """
    from service.isaac_assist_service.multimodal.sensor_catalog_query import (
        query_sensors,
    )

    results = query_sensors(
        "depth camera",
        filters={"subtype": "depth_stereo"},
    )
    products = {r["product"] for r in results}

    assert "Intel RealSense D435i" in products, "D435i must be in depth_stereo results"
    assert "Intel RealSense D455" in products, "D455 must be in depth_stereo results"
    assert "Intel RealSense L515" not in products, (
        "L515 (lidar_camera subtype) must NOT appear in depth_stereo results"
    )


# ---------------------------------------------------------------------------
# Filter axis: max_range_m — sensor must reach this distance
# ---------------------------------------------------------------------------

def test_max_range_m_filter():
    """max_range_m filters out sensors whose maximum detection range is less
    than the requested value.

    D435i has depth_range_m=[0.105, 10.0] so it passes max_range_m=9.5.
    Orbbec Femto Mega has depth_range_m=[0.25, 5.5] so it must be absent.
    """
    from service.isaac_assist_service.multimodal.sensor_catalog_query import (
        query_sensors,
    )

    results = query_sensors(
        "depth camera",
        filters={"max_range_m": 9.5},
    )
    products = {r["product"] for r in results}

    assert "Intel RealSense D435i" in products, "D435i (max 10.0 m) must pass max_range_m=9.5"
    assert "Orbbec Femto Mega" not in products, (
        "Orbbec Femto Mega (max 5.5 m) must be filtered by max_range_m=9.5"
    )


# ---------------------------------------------------------------------------
# Filter axis: min_range_m — sensor must work at close range
# ---------------------------------------------------------------------------

def test_min_range_m_filter():
    """min_range_m filters out sensors whose minimum detection range is greater
    than the requested value (sensor has too large a blind zone).

    D455 has depth_range_m=[0.4, 6.0] so it fails min_range_m=0.3 (sensor's
    closest point 0.4 m exceeds the required 0.3 m).
    D435i has depth_range_m=[0.105, 10.0] so it passes.
    """
    from service.isaac_assist_service.multimodal.sensor_catalog_query import (
        query_sensors,
    )

    results = query_sensors(
        "depth camera",
        filters={"min_range_m": 0.3},
    )
    products = {r["product"] for r in results}

    assert "Intel RealSense D435i" in products, (
        "D435i (min range 0.105 m <= 0.3) must pass min_range_m=0.3"
    )
    assert "Intel RealSense D455" not in products, (
        "D455 (min range 0.4 m > 0.3) must be filtered by min_range_m=0.3"
    )


# ---------------------------------------------------------------------------
# Filter axis: min_resolution
# ---------------------------------------------------------------------------

def test_min_resolution_filter():
    """min_resolution=[1280, 720] retains sensors with resolution >= 1280x720
    and excludes lower-resolution sensors.
    """
    from service.isaac_assist_service.multimodal.sensor_catalog_query import (
        query_sensors,
    )

    results = query_sensors(
        "depth camera",
        filters={"min_resolution": [1280, 720]},
    )
    products = {r["product"] for r in results}

    # D435i resolution=[1280, 720] exactly meets the requirement
    assert "Intel RealSense D435i" in products, (
        "D435i (1280x720) must pass min_resolution=[1280, 720]"
    )
    # D455 resolution=[1280, 800] exceeds the requirement
    assert "Intel RealSense D455" in products, (
        "D455 (1280x800) must pass min_resolution=[1280, 720]"
    )

    # Every returned sensor must actually meet the resolution requirement
    for sensor in results:
        res = sensor.get("resolution") or sensor.get("depth_resolution")
        assert res is not None, f"{sensor['product']} has no resolution field"
        assert res[0] >= 1280 and res[1] >= 720, (
            f"{sensor['product']} resolution {res} does not meet [1280, 720]"
        )


# ---------------------------------------------------------------------------
# Filter axis: manufacturer (categorical)
# ---------------------------------------------------------------------------

def test_manufacturer_filter():
    """manufacturer='Intel' returns only Intel sensors; non-Intel sensors absent."""
    from service.isaac_assist_service.multimodal.sensor_catalog_query import (
        query_sensors,
    )

    results = query_sensors("camera", filters={"manufacturer": "Intel"})
    assert len(results) > 0, "Expected at least one Intel sensor"

    for sensor in results:
        assert "Intel" in sensor.get("manufacturer", ""), (
            f"{sensor['product']} must be manufactured by Intel"
        )

    products = {r["product"] for r in results}
    # Basler is not Intel
    assert not any("Basler" in p for p in products), (
        "Basler sensors must not appear when filtering by manufacturer=Intel"
    )
