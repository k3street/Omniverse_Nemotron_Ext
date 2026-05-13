"""Phase 97b contract test."""
import pytest
pytestmark = pytest.mark.l0


def test_phase_97b_metadata():
    from service.isaac_assist_service.multimodal.sub_phase_97b_long_running_benchmark import get_phase_metadata
    md = get_phase_metadata()
    assert md["phase"] == "97b"
