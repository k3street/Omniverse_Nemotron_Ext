"""Phase 79b contract test."""
import pytest
pytestmark = pytest.mark.l0


def test_phase_79b_metadata():
    from service.isaac_assist_service.multimodal.sub_phase_79b_wbc_locomotion import get_phase_metadata
    md = get_phase_metadata()
    assert md["phase"] == "79b"
