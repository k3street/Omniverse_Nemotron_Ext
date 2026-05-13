"""Phase 83 contract test."""
import pytest
pytestmark = pytest.mark.l0


def test_phase_83_metadata():
    from service.isaac_assist_service.multimodal.governance_overnight_patches import get_phase_metadata
    md = get_phase_metadata()
    assert md["phase"] == 83
    assert md["status"] == "scaffold"
