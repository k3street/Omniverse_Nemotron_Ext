"""Phase 82 contract test."""
import pytest
pytestmark = pytest.mark.l0


def test_phase_82_metadata():
    from service.isaac_assist_service.multimodal.epoch_v_convergence import get_phase_metadata
    md = get_phase_metadata()
    assert md["phase"] == 82
    assert md["status"] == "scaffold"
