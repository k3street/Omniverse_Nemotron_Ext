"""Phase 96b contract test."""
import pytest
pytestmark = pytest.mark.l0


def test_phase_96b_metadata():
    from service.isaac_assist_service.multimodal.sub_phase_96b_gh_handler_ratchet import get_phase_metadata
    md = get_phase_metadata()
    assert md["phase"] == "96b"
