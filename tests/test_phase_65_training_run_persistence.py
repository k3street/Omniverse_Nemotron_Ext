"""Phase 65 contract test."""
import pytest
pytestmark = pytest.mark.l0


def test_phase_65_metadata():
    from service.isaac_assist_service.multimodal.training_run_persistence import get_phase_metadata
    md = get_phase_metadata()
    assert md["phase"] == 65
    assert md["status"] in ("scaffold", "landed")
