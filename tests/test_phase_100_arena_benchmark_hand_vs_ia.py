"""Phase 100 contract test."""
import pytest
pytestmark = pytest.mark.l0


def test_phase_100_metadata():
    from service.isaac_assist_service.multimodal.arena_benchmark_hand_vs_ia import get_phase_metadata
    md = get_phase_metadata()
    assert md["phase"] == 100
