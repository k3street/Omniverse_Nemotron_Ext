"""Phase 93 contract test."""
import pytest
pytestmark = pytest.mark.l0


def test_phase_93_metadata():
    from service.isaac_assist_service.multimodal.proactive_check_deepening import get_phase_metadata
    md = get_phase_metadata()
    assert md["phase"] == 93
