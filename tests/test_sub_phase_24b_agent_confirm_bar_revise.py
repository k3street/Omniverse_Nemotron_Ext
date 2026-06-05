"""Phase 24b contract test."""
import pytest
pytestmark = pytest.mark.l0


def test_phase_24b_metadata():
    from service.isaac_assist_service.multimodal.sub_phase_24b_agent_confirm_bar_revise import get_phase_metadata
    md = get_phase_metadata()
    assert md["phase"] == "24b"
