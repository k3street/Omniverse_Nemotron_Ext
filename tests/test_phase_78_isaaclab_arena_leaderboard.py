"""Phase 78 contract test."""
import pytest
pytestmark = pytest.mark.l0


def test_phase_78_metadata():
    from service.isaac_assist_service.multimodal.isaaclab_arena_leaderboard import get_phase_metadata
    md = get_phase_metadata()
    assert md["phase"] == 78
    assert md["status"] == "scaffold"
