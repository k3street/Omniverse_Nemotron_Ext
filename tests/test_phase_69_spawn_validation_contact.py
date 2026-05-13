"""Phase 69 contract test."""
import pytest
pytestmark = pytest.mark.l0


def test_phase_69_metadata():
    from service.isaac_assist_service.multimodal.spawn_validation_contact import get_phase_metadata
    md = get_phase_metadata()
    assert md["phase"] == 69
    assert md["status"] == "landed"
    assert "canonical_module" in md
