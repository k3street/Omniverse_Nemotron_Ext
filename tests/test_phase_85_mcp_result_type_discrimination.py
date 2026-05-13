"""Phase 85 contract test."""
import pytest
pytestmark = pytest.mark.l0


def test_phase_85_metadata():
    from service.isaac_assist_service.multimodal.mcp_result_type_discrimination import get_phase_metadata
    md = get_phase_metadata()
    assert md["phase"] == 85
    assert md["status"] == "scaffold"
