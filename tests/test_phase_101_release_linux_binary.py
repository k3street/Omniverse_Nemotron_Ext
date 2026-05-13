"""Phase 101 contract test."""
import pytest
pytestmark = pytest.mark.l0


def test_phase_101_metadata():
    from service.isaac_assist_service.multimodal.release_linux_binary import get_phase_metadata
    md = get_phase_metadata()
    assert md["phase"] == 101
