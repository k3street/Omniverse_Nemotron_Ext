"""Phase 63c contract test."""
import pytest
pytestmark = pytest.mark.l0


def test_phase_63c_metadata():
    from service.isaac_assist_service.multimodal.sub_phase_63c_contact_seq_pose_estimation import get_phase_metadata
    md = get_phase_metadata()
    assert md["phase"] == "63c"
