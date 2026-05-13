"""Phase 81 contract test."""
import pytest
pytestmark = pytest.mark.l0


def test_phase_81_metadata():
    from service.isaac_assist_service.multimodal.multi_rate_physics import get_phase_metadata
    md = get_phase_metadata()
    assert md["phase"] == 81
    assert md["status"] == "scaffold"
