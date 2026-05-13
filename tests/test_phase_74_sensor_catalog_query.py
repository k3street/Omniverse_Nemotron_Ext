"""Phase 74 contract test."""
import pytest
pytestmark = pytest.mark.l0


def test_phase_74_metadata():
    from service.isaac_assist_service.multimodal.sensor_catalog_query import get_phase_metadata
    md = get_phase_metadata()
    assert md["phase"] == 74
    assert md["status"] == "scaffold"
