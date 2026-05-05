"""Per-session cancel flags.

Module-level state, lifetime = uvicorn process. The orchestrator polls
:func:`is_cancelled` between rounds and between tools within a round.
It cannot abort an in-flight tool — the smallest unit of cancellation
is "after the current tool returns."

Flag is cleared either by the orchestrator itself once the cancel has
taken effect, or explicitly via :func:`clear` (e.g. at the start of a
new turn).
"""
from __future__ import annotations

from typing import Set

_cancelled: Set[str] = set()


def request_cancel(session_id: str) -> None:
    """Mark a session for cancellation. Idempotent."""
    _cancelled.add(session_id)


def is_cancelled(session_id: str) -> bool:
    return session_id in _cancelled


def clear(session_id: str) -> None:
    """Drop the cancel flag for a session. Safe to call when not set."""
    _cancelled.discard(session_id)
