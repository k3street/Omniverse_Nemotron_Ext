"""Phase 106 contract test."""
import pytest
pytestmark = pytest.mark.l0


def test_phase_106_metadata():
    from service.isaac_assist_service.multimodal.post_release_retrospective import get_phase_metadata
    md = get_phase_metadata()
    assert md["phase"] == 106
