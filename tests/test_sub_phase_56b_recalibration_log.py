"""Phase 56b contract test."""
import pytest
pytestmark = pytest.mark.l0


def test_phase_56b_metadata():
    from service.isaac_assist_service.multimodal.sub_phase_56b_recalibration_log import get_phase_metadata
    md = get_phase_metadata()
    assert md["phase"] == "56b"
