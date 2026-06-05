"""Phase 66 contract test."""
import pytest
pytestmark = pytest.mark.l0


def test_phase_66_metadata():
    from service.isaac_assist_service.multimodal.spawn_validation_usd_ref import get_phase_metadata
    md = get_phase_metadata()
    assert md["phase"] == 66
    assert md["status"] == "scaffold"
