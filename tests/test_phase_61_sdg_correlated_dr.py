"""Phase 61 contract test."""
import pytest
pytestmark = pytest.mark.l0


def test_phase_61_metadata():
    from service.isaac_assist_service.multimodal.sdg_correlated_dr import get_phase_metadata
    md = get_phase_metadata()
    assert md["phase"] == 61
    assert md["status"] == "scaffold"
