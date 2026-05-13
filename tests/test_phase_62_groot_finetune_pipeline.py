"""Phase 62 contract test."""
import pytest
pytestmark = pytest.mark.l0


def test_phase_62_metadata():
    from service.isaac_assist_service.multimodal.groot_finetune_pipeline import get_phase_metadata
    md = get_phase_metadata()
    assert md["phase"] == 62
    assert md["status"] == "scaffold"
