"""Phase 60 contract test."""
import pytest
pytestmark = pytest.mark.l0


def test_phase_60_metadata():
    from service.isaac_assist_service.multimodal.sdg_5_more_presets import get_phase_metadata
    md = get_phase_metadata()
    assert md["phase"] == 60
    assert md["status"] == "scaffold"
