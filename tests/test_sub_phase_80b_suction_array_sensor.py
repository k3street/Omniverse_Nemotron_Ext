"""Phase 80b contract test."""
import pytest
pytestmark = pytest.mark.l0


def test_phase_80b_metadata():
    from service.isaac_assist_service.multimodal.sub_phase_80b_suction_array_sensor import get_phase_metadata
    md = get_phase_metadata()
    assert md["phase"] == "80b"
