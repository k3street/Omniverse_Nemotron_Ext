"""L0 unit tests for cancel_registry — per-session cancel flags.

Module-level state lives for the uvicorn lifetime. Tests use unique
session_ids to avoid cross-test contamination.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.l0


def _imp():
    from service.isaac_assist_service.chat.cancel_registry import (
        request_cancel, is_cancelled, clear,
    )
    return request_cancel, is_cancelled, clear


def test_unset_session_is_not_cancelled():
    _, is_c, _ = _imp()
    assert is_c("test-cancel-001-fresh") is False


def test_request_cancel_sets_flag():
    req, is_c, clr = _imp()
    sid = "test-cancel-002-set"
    try:
        req(sid)
        assert is_c(sid) is True
    finally:
        clr(sid)


def test_request_cancel_is_idempotent():
    """Calling twice must not raise; flag stays True."""
    req, is_c, clr = _imp()
    sid = "test-cancel-003-idempotent"
    try:
        req(sid)
        req(sid)
        req(sid)
        assert is_c(sid) is True
    finally:
        clr(sid)


def test_clear_drops_flag():
    req, is_c, clr = _imp()
    sid = "test-cancel-004-clear"
    req(sid)
    clr(sid)
    assert is_c(sid) is False


def test_clear_when_not_set_is_safe():
    """Documented: safe to call when flag is unset."""
    _, is_c, clr = _imp()
    sid = "test-cancel-005-clearunset"
    clr(sid)  # no-op
    assert is_c(sid) is False


def test_distinct_sessions_independent():
    """Cancelling one session does not affect another."""
    req, is_c, clr = _imp()
    a = "test-cancel-006-a"
    b = "test-cancel-006-b"
    try:
        req(a)
        assert is_c(a) is True
        assert is_c(b) is False
    finally:
        clr(a)
        clr(b)


def test_clear_affects_only_target():
    req, is_c, clr = _imp()
    a = "test-cancel-007-a"
    b = "test-cancel-007-b"
    try:
        req(a)
        req(b)
        clr(a)
        assert is_c(a) is False
        assert is_c(b) is True
    finally:
        clr(b)


def test_recancel_after_clear():
    """Once cleared, can be re-cancelled."""
    req, is_c, clr = _imp()
    sid = "test-cancel-008-recancel"
    try:
        req(sid)
        clr(sid)
        assert is_c(sid) is False
        req(sid)
        assert is_c(sid) is True
    finally:
        clr(sid)


def test_empty_session_id_works():
    """Edge: empty string is a valid Python set element."""
    req, is_c, clr = _imp()
    try:
        req("")
        assert is_c("") is True
    finally:
        clr("")
