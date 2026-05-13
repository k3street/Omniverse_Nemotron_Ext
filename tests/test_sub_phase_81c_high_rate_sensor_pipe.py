"""Phase 81c contract test."""
import pytest
pytestmark = pytest.mark.l0


def test_phase_81c_metadata():
    from service.isaac_assist_service.multimodal.sub_phase_81c_high_rate_sensor_pipe import get_phase_metadata
    md = get_phase_metadata()
    assert md["phase"] == "81c"
