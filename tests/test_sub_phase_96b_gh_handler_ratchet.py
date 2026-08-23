"""Phase 96b contract test."""
import pytest
pytestmark = pytest.mark.l0


def test_phase_96b_metadata():
    from service.isaac_assist_service.multimodal.sub_phase_96b_gh_handler_ratchet import get_phase_metadata
    md = get_phase_metadata()
    assert md["phase"] == "96b"
    assert md["status"] == "landed"


def test_handler_ratchet_passes_and_fails_against_explicit_baseline(tmp_path):
    from service.isaac_assist_service.multimodal.sub_phase_96b_gh_handler_ratchet import (
        check_handler_ratchet,
        count_handlers,
    )

    (tmp_path / "handlers.py").write_text(
        "def _handle_one(): pass\nasync def _handle_two(): pass\ndef helper(): pass\n",
        encoding="utf-8",
    )
    assert count_handlers(tmp_path) == 2
    assert check_handler_ratchet(2, tmp_path).passed is True
    failed = check_handler_ratchet(3, tmp_path)
    assert failed.passed is False
    assert failed.delta == -1
