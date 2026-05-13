"""Phase 86 contract test."""
import pytest
pytestmark = pytest.mark.l0


def test_phase_86_metadata():
    from service.isaac_assist_service.multimodal.settings_exposure_mcp import get_phase_metadata
    md = get_phase_metadata()
    assert md["phase"] == 86
    assert md["status"] == "scaffold"
