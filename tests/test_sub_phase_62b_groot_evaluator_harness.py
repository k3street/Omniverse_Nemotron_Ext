"""Phase 62b contract test."""
import pytest
pytestmark = pytest.mark.l0


def test_phase_62b_metadata():
    from service.isaac_assist_service.multimodal.sub_phase_62b_groot_evaluator_harness import get_phase_metadata
    md = get_phase_metadata()
    assert md["phase"] == "62b"
