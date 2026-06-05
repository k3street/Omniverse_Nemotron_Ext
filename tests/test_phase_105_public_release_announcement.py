"""Phase 105 contract test."""
import pytest
pytestmark = pytest.mark.l0


def test_phase_105_metadata():
    from service.isaac_assist_service.multimodal.public_release_announcement import get_phase_metadata
    md = get_phase_metadata()
    assert md["phase"] == 105
