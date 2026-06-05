"""Phase 79 contract test."""
import pytest
pytestmark = pytest.mark.l0


def test_phase_79_metadata():
    from service.isaac_assist_service.multimodal.whole_body_control_humanoid import get_phase_metadata
    md = get_phase_metadata()
    assert md["phase"] == 79
    assert md["status"] == "scaffold"
