"""Phase 72 contract test."""
import pytest
pytestmark = pytest.mark.l0


def test_phase_72_metadata():
    from service.isaac_assist_service.multimodal.setup_assembly_constraint import get_phase_metadata
    md = get_phase_metadata()
    assert md["phase"] == 72
    assert md["status"] == "landed"
    assert "canonical_module" in md
