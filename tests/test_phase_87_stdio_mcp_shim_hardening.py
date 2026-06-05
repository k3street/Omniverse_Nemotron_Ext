"""Phase 87 contract test."""
import pytest
pytestmark = pytest.mark.l0


def test_phase_87_metadata():
    from service.isaac_assist_service.multimodal.stdio_mcp_shim_hardening import get_phase_metadata
    md = get_phase_metadata()
    assert md["phase"] == 87
    assert md["status"] == "scaffold"
