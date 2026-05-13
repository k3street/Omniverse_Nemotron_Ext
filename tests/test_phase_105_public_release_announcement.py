"""Phase 105 contract tests."""
import pytest
pytestmark = pytest.mark.l0


def test_phase_105_metadata():
    from service.isaac_assist_service.multimodal.public_release_announcement import get_phase_metadata
    md = get_phase_metadata()
    assert md["phase"] == 105


def test_phase_105_status_landed():
    from service.isaac_assist_service.multimodal.public_release_announcement import get_phase_metadata
    md = get_phase_metadata()
    assert md["status"] == "landed"


def test_announcement_file_exists_and_has_content():
    from service.isaac_assist_service.multimodal.public_release_announcement import load_announcement
    text = load_announcement()
    assert len(text) > 200, f"Announcement too short: {len(text)} chars"


def test_announcement_contains_required_sections():
    from service.isaac_assist_service.multimodal.public_release_announcement import load_announcement
    text = load_announcement()
    required = [
        "Headline features",
        "Install",
        "Credits",
        "Roadmap",
    ]
    for section in required:
        assert section in text, f"Missing required section: {section!r}"
