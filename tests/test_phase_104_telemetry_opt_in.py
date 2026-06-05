"""Phase 104 contract test."""
import pytest
pytestmark = pytest.mark.l0


def test_phase_104_metadata():
    from service.isaac_assist_service.multimodal.telemetry_opt_in import get_phase_metadata
    md = get_phase_metadata()
    assert md["phase"] == 104
