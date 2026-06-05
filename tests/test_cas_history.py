"""Phase 26 — CAS history."""
import pytest
pytestmark = pytest.mark.l0


def test_commit_returns_hash():
    from service.isaac_assist_service.multimodal.cas_history import CASHistory
    h = CASHistory()
    rev = h.commit("session1", {"objects": [{"object_class": "cube"}]})
    assert len(rev) == 16
    assert isinstance(rev, str)


def test_commit_then_history_returns_revision():
    from service.isaac_assist_service.multimodal.cas_history import CASHistory
    h = CASHistory()
    rev = h.commit("session1", {"objects": []})
    history = h.history("session1")
    assert len(history) == 1
    assert history[0].revision_hash == rev


def test_rollback_to_known_revision():
    from service.isaac_assist_service.multimodal.cas_history import CASHistory
    h = CASHistory()
    rev1 = h.commit("session1", {"objects": [{"a": 1}]})
    rev2 = h.commit("session1", {"objects": [{"a": 2}]})
    assert h.rollback("session1", rev1)
    history = h.history("session1")
    assert history[0].revision_hash == rev1


def test_rollback_to_unknown_returns_false():
    from service.isaac_assist_service.multimodal.cas_history import CASHistory
    h = CASHistory()
    assert h.rollback("session1", "deadbeef") is False
