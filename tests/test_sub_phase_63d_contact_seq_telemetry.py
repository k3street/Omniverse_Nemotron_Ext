"""Phase 63d contract test."""
import pytest
pytestmark = pytest.mark.l0


def test_phase_63d_metadata():
    from service.isaac_assist_service.multimodal.sub_phase_63d_contact_seq_telemetry import get_phase_metadata
    md = get_phase_metadata()
    assert md["phase"] == "63d"
