"""Phase 70b contract test."""
import pytest
pytestmark = pytest.mark.l0


def test_phase_70b_metadata():
    from service.isaac_assist_service.multimodal.sub_phase_70b_robot_subassembly_library import get_phase_metadata
    md = get_phase_metadata()
    assert md["phase"] == "70b"
