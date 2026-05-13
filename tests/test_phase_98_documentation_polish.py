"""Phase 98 contract test."""
import pytest
pytestmark = pytest.mark.l0


def test_phase_98_metadata():
    from service.isaac_assist_service.multimodal.documentation_polish import get_phase_metadata
    md = get_phase_metadata()
    assert md["phase"] == 98
