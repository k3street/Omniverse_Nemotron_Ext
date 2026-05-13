"""Phase 77 contract test."""
import pytest
pytestmark = pytest.mark.l0


def test_phase_77_metadata():
    from service.isaac_assist_service.multimodal.vision_viewport_hash_cache import get_phase_metadata
    md = get_phase_metadata()
    assert md["phase"] == 77
    assert md["status"] == "scaffold"
