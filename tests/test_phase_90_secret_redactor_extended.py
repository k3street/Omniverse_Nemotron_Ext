"""Phase 90 contract test."""
import pytest
pytestmark = pytest.mark.l0


def test_phase_90_metadata():
    from service.isaac_assist_service.multimodal.secret_redactor_extended import get_phase_metadata
    md = get_phase_metadata()
    assert md["phase"] == 90
