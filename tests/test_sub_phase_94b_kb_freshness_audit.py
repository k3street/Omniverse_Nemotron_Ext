"""Phase 94b contract test."""
import pytest
pytestmark = pytest.mark.l0


def test_phase_94b_metadata():
    from service.isaac_assist_service.multimodal.sub_phase_94b_kb_freshness_audit import get_phase_metadata
    md = get_phase_metadata()
    assert md["phase"] == "94b"
