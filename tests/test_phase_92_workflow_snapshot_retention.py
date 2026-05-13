"""Phase 92 contract test."""
import pytest
pytestmark = pytest.mark.l0


def test_phase_92_metadata():
    from service.isaac_assist_service.multimodal.workflow_snapshot_retention import get_phase_metadata
    md = get_phase_metadata()
    assert md["phase"] == 92
