"""Phase 91 contract test."""
import pytest
pytestmark = pytest.mark.l0


def test_phase_91_metadata():
    from service.isaac_assist_service.multimodal.audit_log_retention import get_phase_metadata
    md = get_phase_metadata()
    assert md["phase"] == 91
