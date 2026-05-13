"""Phase 88 contract test."""
import pytest
pytestmark = pytest.mark.l0


def test_phase_88_metadata():
    from service.isaac_assist_service.multimodal.linux_prebuilt_binary_ci import get_phase_metadata
    md = get_phase_metadata()
    assert md["phase"] == 88
    assert md["status"] == "scaffold"
