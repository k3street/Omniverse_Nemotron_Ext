"""Phase 73 contract test."""
import pytest
pytestmark = pytest.mark.l0


def test_phase_73_metadata():
    from service.isaac_assist_service.multimodal.sensor_catalog_expansion import get_phase_metadata
    md = get_phase_metadata()
    assert md["phase"] == 73
    assert md["status"] == "scaffold"
