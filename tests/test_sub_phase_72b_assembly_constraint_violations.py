"""Phase 72b contract test."""
import pytest
pytestmark = pytest.mark.l0


def test_phase_72b_metadata():
    from service.isaac_assist_service.multimodal.sub_phase_72b_assembly_constraint_violations import get_phase_metadata
    md = get_phase_metadata()
    assert md["phase"] == "72b"
