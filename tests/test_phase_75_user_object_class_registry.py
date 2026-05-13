"""Phase 75 contract test."""
import pytest
pytestmark = pytest.mark.l0


def test_phase_75_metadata():
    from service.isaac_assist_service.multimodal.user_object_class_registry import get_phase_metadata
    md = get_phase_metadata()
    assert md["phase"] == 75
    assert md["status"] == "scaffold"
