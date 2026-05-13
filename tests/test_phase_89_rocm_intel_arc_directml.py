"""Phase 89 contract test."""
import pytest
pytestmark = pytest.mark.l0


def test_phase_89_metadata():
    from service.isaac_assist_service.multimodal.rocm_intel_arc_directml import get_phase_metadata
    md = get_phase_metadata()
    assert md["phase"] == 89
    assert md["status"] == "scaffold"
