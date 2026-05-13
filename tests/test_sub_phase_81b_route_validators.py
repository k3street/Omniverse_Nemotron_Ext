"""Phase 81b contract test."""
import pytest
pytestmark = pytest.mark.l0


def test_phase_81b_metadata():
    from service.isaac_assist_service.multimodal.sub_phase_81b_route_validators import get_phase_metadata
    md = get_phase_metadata()
    assert md["phase"] == "81b"
