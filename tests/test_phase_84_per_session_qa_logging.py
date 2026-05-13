"""Phase 84 contract test."""
import pytest
pytestmark = pytest.mark.l0


def test_phase_84_metadata():
    from service.isaac_assist_service.multimodal.per_session_qa_logging import get_phase_metadata
    md = get_phase_metadata()
    assert md["phase"] == 84
    assert md["status"] == "scaffold"
