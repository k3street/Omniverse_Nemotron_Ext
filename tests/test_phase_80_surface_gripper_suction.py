"""Phase 80 contract test."""
import pytest
pytestmark = pytest.mark.l0


def test_phase_80_metadata():
    from service.isaac_assist_service.multimodal.surface_gripper_suction import get_phase_metadata
    md = get_phase_metadata()
    assert md["phase"] == 80
    assert md["status"] == "scaffold"
