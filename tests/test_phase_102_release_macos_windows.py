"""Phase 102 contract test."""
import pytest
pytestmark = pytest.mark.l0


def test_phase_102_metadata():
    from service.isaac_assist_service.multimodal.release_macos_windows import get_phase_metadata
    md = get_phase_metadata()
    assert md["phase"] == 102
