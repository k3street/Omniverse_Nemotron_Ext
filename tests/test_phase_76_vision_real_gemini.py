"""Phase 76 contract test."""
import pytest
pytestmark = pytest.mark.l0


def test_phase_76_metadata():
    from service.isaac_assist_service.multimodal.vision_real_gemini import get_phase_metadata
    md = get_phase_metadata()
    assert md["phase"] == 76
    assert md["status"] == "scaffold"
