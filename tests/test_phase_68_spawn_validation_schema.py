"""Phase 68 contract test."""
import pytest
pytestmark = pytest.mark.l0


def test_phase_68_metadata():
    from service.isaac_assist_service.multimodal.spawn_validation_schema import get_phase_metadata
    md = get_phase_metadata()
    assert md["phase"] == 68
    assert md["status"] == "landed"
    assert "canonical_module" in md
