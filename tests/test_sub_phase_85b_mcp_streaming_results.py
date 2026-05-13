"""Phase 85b contract test."""
import pytest
pytestmark = pytest.mark.l0


def test_phase_85b_metadata():
    from service.isaac_assist_service.multimodal.sub_phase_85b_mcp_streaming_results import get_phase_metadata
    md = get_phase_metadata()
    assert md["phase"] == "85b"
