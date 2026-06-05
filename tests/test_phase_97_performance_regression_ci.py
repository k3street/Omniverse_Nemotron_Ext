"""Phase 97 contract test."""
import pytest
pytestmark = pytest.mark.l0


def test_phase_97_metadata():
    from service.isaac_assist_service.multimodal.performance_regression_ci import get_phase_metadata
    md = get_phase_metadata()
    assert md["phase"] == 97
