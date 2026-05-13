"""Phase 78b contract test."""
import pytest
pytestmark = pytest.mark.l0


def test_phase_78b_metadata():
    from service.isaac_assist_service.multimodal.sub_phase_78b_arena_leaderboard_uploader import get_phase_metadata
    md = get_phase_metadata()
    assert md["phase"] == "78b"
