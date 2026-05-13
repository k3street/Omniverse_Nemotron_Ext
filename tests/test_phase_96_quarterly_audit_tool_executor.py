"""Phase 96 contract test."""
import pytest
pytestmark = pytest.mark.l0


def test_phase_96_metadata():
    from service.isaac_assist_service.multimodal.quarterly_audit_tool_executor import get_phase_metadata
    md = get_phase_metadata()
    assert md["phase"] == 96
