"""Phase 56c contract test."""
import pytest
pytestmark = pytest.mark.l0


def test_phase_56c_metadata():
    from service.isaac_assist_service.multimodal.sub_phase_56c_ctrl_profile_attr import get_phase_metadata
    md = get_phase_metadata()
    assert md["phase"] == "56c"
