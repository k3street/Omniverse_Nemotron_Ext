"""Phase 71 contract test."""
import pytest
pytestmark = pytest.mark.l0


def test_phase_71_metadata():
    from service.isaac_assist_service.multimodal.yaskawa_gp25_onboarding import get_phase_metadata
    md = get_phase_metadata()
    assert md["phase"] == 71
    assert md["status"] == "scaffold"
