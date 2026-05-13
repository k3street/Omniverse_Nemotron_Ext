"""Phase 67 contract test."""
import pytest
pytestmark = pytest.mark.l0


def test_phase_67_metadata():
    from service.isaac_assist_service.multimodal.spawn_validation_joint import get_phase_metadata
    md = get_phase_metadata()
    assert md["phase"] == 67
    assert md["status"] == "scaffold"
