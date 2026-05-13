"""Phase 70 contract test."""
import pytest
pytestmark = pytest.mark.l0


def test_phase_70_metadata():
    from service.isaac_assist_service.multimodal.assemble_robot import get_phase_metadata
    md = get_phase_metadata()
    assert md["phase"] == 70
    assert md["status"] == "scaffold"
