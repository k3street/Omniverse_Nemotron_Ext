"""Phase 88b contract test."""
import pytest
pytestmark = pytest.mark.l0


def test_phase_88b_metadata():
    from service.isaac_assist_service.multimodal.sub_phase_88b_windows_msys2_ci import get_phase_metadata
    md = get_phase_metadata()
    assert md["phase"] == "88b"
