"""Phase 94 contract test."""
import pytest
pytestmark = pytest.mark.l0


def test_phase_94_metadata():
    from service.isaac_assist_service.multimodal.kb_feedback_loop import get_phase_metadata
    md = get_phase_metadata()
    assert md["phase"] == 94
