"""Phase 70d contract test."""
import pytest
pytestmark = pytest.mark.l0


def test_phase_70d_metadata():
    from service.isaac_assist_service.multimodal.sub_phase_70d_bin_interior_metadata import get_phase_metadata
    md = get_phase_metadata()
    assert md["phase"] == "70d"
