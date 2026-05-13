"""Phase 95 contract test."""
import pytest
pytestmark = pytest.mark.l0


def test_phase_95_metadata():
    from service.isaac_assist_service.multimodal.rag_nvidia_scraping import get_phase_metadata
    md = get_phase_metadata()
    assert md["phase"] == 95
