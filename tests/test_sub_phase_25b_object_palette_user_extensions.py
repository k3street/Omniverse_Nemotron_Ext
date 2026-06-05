"""Phase 25b contract test."""
import pytest
pytestmark = pytest.mark.l0


def test_phase_25b_metadata():
    from service.isaac_assist_service.multimodal.sub_phase_25b_object_palette_user_extensions import get_phase_metadata
    md = get_phase_metadata()
    assert md["phase"] == "25b"
