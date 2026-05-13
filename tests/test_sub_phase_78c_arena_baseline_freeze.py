"""Phase 78c contract test."""
import pytest
pytestmark = pytest.mark.l0


def test_phase_78c_metadata():
    from service.isaac_assist_service.multimodal.sub_phase_78c_arena_baseline_freeze import get_phase_metadata
    md = get_phase_metadata()
    assert md["phase"] == "78c"
