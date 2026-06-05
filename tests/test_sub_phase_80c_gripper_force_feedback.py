"""Phase 80c contract test."""
import pytest
pytestmark = pytest.mark.l0


def test_phase_80c_metadata():
    from service.isaac_assist_service.multimodal.sub_phase_80c_gripper_force_feedback import get_phase_metadata
    md = get_phase_metadata()
    assert md["phase"] == "80c"
