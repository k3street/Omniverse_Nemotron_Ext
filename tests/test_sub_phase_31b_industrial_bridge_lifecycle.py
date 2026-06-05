"""Phase 31b contract test."""
import pytest
pytestmark = pytest.mark.l0


def test_phase_31b_metadata():
    from service.isaac_assist_service.multimodal.sub_phase_31b_industrial_bridge_lifecycle import get_phase_metadata
    md = get_phase_metadata()
    assert md["phase"] == "31b"
