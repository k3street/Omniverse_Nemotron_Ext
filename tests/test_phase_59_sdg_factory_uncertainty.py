"""Phase 59 contract test."""
import pytest
pytestmark = pytest.mark.l0


def test_phase_59_metadata():
    from service.isaac_assist_service.multimodal.sdg_factory_uncertainty import get_phase_metadata
    md = get_phase_metadata()
    assert md["phase"] == 59
    assert md["status"] == "scaffold"
