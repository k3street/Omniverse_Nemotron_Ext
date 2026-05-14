"""Tests for context_distiller.filter_scene_context path extraction.

Pins the fix from 2026-04-19: the message-path regex previously used
\\S+ and would greedily swallow "/World/Robot's" (apostrophe-s contraction)
into a single nonexistent "mentioned" entry, causing the filter to drop
the relevant scene-context lines. Strict char class prevents that.

L0 — pure string processing.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.l0


def _filter():
    from service.isaac_assist_service.chat.context_distiller import (
        filter_scene_context,
        ConversationKnowledge,
    )
    return filter_scene_context, ConversationKnowledge


def test_apostrophe_s_contraction_preserves_relevant_context():
    """'/World/Robot\\'s mass' — the regex must extract /World/Robot and
    keep scene lines mentioning /World/Robot in the filtered output."""
    filter_scene_context, KN = _filter()
    ctx_lines = (
        "prim count: 50\n"
        + "\n".join(f"  /World/Filler_{i} (Cube)" for i in range(45))
        + "\n  /World/Robot (Xform) mass=1.0 friction=0.5\n"
        + "  /World/Other (Cube)\n"
    )
    message = "Is /World/Robot's mass correct?"
    knowledge = KN(session_id="test")
    result = filter_scene_context(ctx_lines, message, knowledge, max_lines=20)
    # The line about /World/Robot must survive the filter
    assert "/World/Robot" in result
    # Must actually have the mass info, not just the bare path somewhere
    assert "mass=1.0" in result


def test_plain_path_mention_survives_filter():
    filter_scene_context, KN = _filter()
    ctx = (
        "prim count: 100\n"
        + "\n".join(f"  /World/X_{i} (Cube)" for i in range(50))
        + "\n  /World/Target (Cube) special=true"
    )
    out = filter_scene_context(ctx, "inspect /World/Target", KN(session_id="test"), max_lines=15)
    assert "/World/Target" in out
    assert "special=true" in out


def test_no_paths_in_message_still_returns_context():
    filter_scene_context, KN = _filter()
    ctx = "prim count: 10\n" + "\n".join(f"  /World/A_{i}" for i in range(10))
    out = filter_scene_context(ctx, "what is this scene about?", KN(session_id="test"))
    assert len(out) > 0


def test_empty_full_context_returns_empty():
    filter_scene_context, KN = _filter()
    assert filter_scene_context("", "anything", KN(session_id="test")) == ""


def test_short_context_returned_unfiltered():
    filter_scene_context, KN = _filter()
    # 3 lines < max_lines=40 → returned as-is
    ctx = "line 1\nline 2\nline 3"
    out = filter_scene_context(ctx, "whatever", KN(session_id="test"))
    assert out == ctx


def test_trailing_punctuation_stripped_from_extracted_path():
    """The path extractor rstrip()s trailing punctuation so 'at /World/X,'
    still recognizes /World/X correctly."""
    filter_scene_context, KN = _filter()
    ctx = (
        "prim count: 50\n"
        + "\n".join(f"  /World/Y_{i}" for i in range(50))
        + "\n  /World/X (Cube) note=important"
    )
    out = filter_scene_context(ctx, "Look at /World/X, please.", KN(session_id="test"), max_lines=10)
    assert "/World/X" in out
