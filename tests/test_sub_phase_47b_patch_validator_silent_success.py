"""Phase 47b contract test."""
import pytest
pytestmark = pytest.mark.l0


def test_phase_47b_metadata():
    from service.isaac_assist_service.multimodal.sub_phase_47b_patch_validator_silent_success import get_phase_metadata
    md = get_phase_metadata()
    assert md["phase"] == "47b"
