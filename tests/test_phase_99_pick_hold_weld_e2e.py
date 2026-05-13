"""Phase 99 contract test."""
import pytest
pytestmark = pytest.mark.l0


def test_phase_99_metadata():
    from service.isaac_assist_service.multimodal.pick_hold_weld_e2e import get_phase_metadata
    md = get_phase_metadata()
    assert md["phase"] == 99
