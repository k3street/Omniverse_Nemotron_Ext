"""Phase 63 contract test."""
import pytest
pytestmark = pytest.mark.l0


def test_phase_63_metadata():
    from service.isaac_assist_service.multimodal.execute_contact_sequence_plan import get_phase_metadata
    md = get_phase_metadata()
    assert md["phase"] == 63
    assert md["status"] == "landed"
    assert "canonical_module" in md
