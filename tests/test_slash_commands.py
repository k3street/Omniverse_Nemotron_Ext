"""Tests for the 2026-04-19 slash-command + session-trace primitives.

Pins the session-notebook UX:
  /note, /block, /pin, /cite, /help
  session_trace JSONL append-only log
  trace_summary aggregation

L0 — pure Python, no Kit, no LLM (the /cite path uses the deprecations
index which is also pure Python).
"""
from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path

import pytest

pytestmark = pytest.mark.l0


# ── parse_slash ─────────────────────────────────────────────────────────
def test_parse_slash_basic_commands():
    from service.isaac_assist_service.chat.slash_commands import parse_slash

    assert parse_slash("/note hello") == {"cmd": "note", "arg": "hello"}
    assert parse_slash("/block stuck on rmw") == {"cmd": "block", "arg": "stuck on rmw"}
    assert parse_slash("/pin") == {"cmd": "pin", "arg": ""}
    assert parse_slash("/cite deterministic") == {"cmd": "cite", "arg": "deterministic"}
    assert parse_slash("/help") == {"cmd": "help", "arg": ""}


def test_parse_slash_whitespace_tolerated():
    from service.isaac_assist_service.chat.slash_commands import parse_slash
    assert parse_slash("  /note  some text  ") == {"cmd": "note", "arg": "some text"}


def test_parse_slash_case_insensitive():
    from service.isaac_assist_service.chat.slash_commands import parse_slash
    assert parse_slash("/NOTE hello")["cmd"] == "note"
    assert parse_slash("/Cite thing")["cmd"] == "cite"


def test_parse_slash_unrecognized_returns_none():
    from service.isaac_assist_service.chat.slash_commands import parse_slash
    assert parse_slash("normal message") is None
    assert parse_slash("/unknown hello") is None
    assert parse_slash("/") is None
    assert parse_slash("") is None
    assert parse_slash(None) is None


def test_parse_slash_non_slash_prefix_word_not_matched():
    """A message that mentions '/note' mid-text isn't a command."""
    from service.isaac_assist_service.chat.slash_commands import parse_slash
    assert parse_slash("can you /note that?") is None


# ── execute_slash ───────────────────────────────────────────────────────
def test_execute_note_captures_event():
    from service.isaac_assist_service.chat.slash_commands import execute_slash
    events = []
    def emit(t, p): events.append((t, p))
    reply = asyncio.run(execute_slash("note", "gpu is flaky", history=[], emit_trace=emit))
    assert reply["intent"] == "slash_command"
    assert "Note saved" in reply["reply"] or "note" in reply["reply"].lower()
    assert events == [("note", {"text": "gpu is flaky"})]


def test_execute_block_marks_session():
    from service.isaac_assist_service.chat.slash_commands import execute_slash
    events = []
    def emit(t, p): events.append((t, p))
    reply = asyncio.run(execute_slash(
        "block", "rmw_init fails — ROS2 not sourced",
        history=[], emit_trace=emit,
    ))
    assert "Blocker recorded" in reply["reply"]
    assert events[0] == ("block", {"text": "rmw_init fails — ROS2 not sourced"})


def test_execute_pin_uses_last_assistant_reply():
    from service.isaac_assist_service.chat.slash_commands import execute_slash
    history = [
        {"role": "user", "content": "what's up?"},
        {"role": "assistant", "content": "All clear. Physics scene at /World/PhysicsScene."},
        {"role": "user", "content": "/pin"},
    ]
    events = []
    def emit(t, p): events.append((t, p))
    reply = asyncio.run(execute_slash("pin", "", history=history, emit_trace=emit))
    assert "Pinned" in reply["reply"]
    assert events[0][0] == "pin"
    assert "Physics scene" in events[0][1]["text"]
    assert events[0][1]["source"] == "last_reply"


def test_execute_pin_with_arg_pins_arbitrary_text():
    from service.isaac_assist_service.chat.slash_commands import execute_slash
    events = []
    def emit(t, p): events.append((t, p))
    reply = asyncio.run(execute_slash(
        "pin", "enable_deterministic_mode(seed=42)",
        history=[], emit_trace=emit,
    ))
    assert "Pinned" in reply["reply"]
    assert events[0][1]["text"] == "enable_deterministic_mode(seed=42)"
    assert events[0][1]["source"] == "arg"


def test_execute_pin_nothing_to_pin_returns_hint():
    from service.isaac_assist_service.chat.slash_commands import execute_slash
    events = []
    def emit(t, p): events.append((t, p))
    reply = asyncio.run(execute_slash("pin", "", history=[], emit_trace=emit))
    assert "Nothing to pin" in reply["reply"]
    assert events == []


def test_execute_cite_with_matching_topic():
    from service.isaac_assist_service.chat.slash_commands import execute_slash
    events = []
    def emit(t, p): events.append((t, p))
    reply = asyncio.run(execute_slash("cite", "deterministic replay", history=[], emit_trace=emit))
    # Must contain the ready-to-paste cite paragraph
    assert "enable_deterministic_mode" in reply["reply"]
    # And flag the deprecated API
    assert "SimulationContext.set_deterministic" in reply["reply"]
    assert events[0] == ("cite_returned", {"query": "deterministic replay", "row_id": "deterministic_replay"})


def test_execute_cite_unknown_topic_returns_helpful_miss():
    from service.isaac_assist_service.chat.slash_commands import execute_slash
    events = []
    def emit(t, p): events.append((t, p))
    reply = asyncio.run(execute_slash(
        "cite", "what colour is the sky on Mars",
        history=[], emit_trace=emit,
    ))
    assert "No cite-fact on file" in reply["reply"]
    # Still emits a trace event so /report can show the user looked
    assert events[0] == ("cite_miss", {"query": "what colour is the sky on Mars"})


def test_execute_help_lists_commands():
    from service.isaac_assist_service.chat.slash_commands import execute_slash
    reply = asyncio.run(execute_slash("help", "", history=[], emit_trace=lambda *a: None))
    for cmd in ("/note", "/block", "/pin", "/cite", "/help"):
        assert cmd in reply["reply"], f"{cmd} missing from /help output"


# ── session_trace ──────────────────────────────────────────────────────
def test_trace_emit_and_read_roundtrip(tmp_path, monkeypatch):
    """emit() appends; read_trace() loads back."""
    from service.isaac_assist_service.chat import session_trace as st
    monkeypatch.setattr(st, "_TRACE_ROOT", tmp_path)

    sess = "test_roundtrip"
    st.emit(sess, "user_msg", {"text": "hi"})
    st.emit(sess, "note", {"text": "first observation"})
    st.emit(sess, "tool_call", {"tool": "create_prim"})

    events = st.read_trace(sess)
    assert len(events) == 3
    assert events[0]["type"] == "user_msg"
    assert events[1]["payload"]["text"] == "first observation"
    assert events[2]["payload"]["tool"] == "create_prim"
    assert all("ts" in e for e in events)


def test_trace_summary_aggregates(tmp_path, monkeypatch):
    from service.isaac_assist_service.chat import session_trace as st
    monkeypatch.setattr(st, "_TRACE_ROOT", tmp_path)

    sess = "test_summary"
    st.emit(sess, "user_msg", {"text": "hi"})
    st.emit(sess, "note", {"text": "first"})
    st.emit(sess, "note", {"text": "second"})
    st.emit(sess, "block", {"text": "rmw broken"})
    st.emit(sess, "pin", {"text": "important artifact"})
    st.emit(sess, "agent_reply", {"text": "ok"})

    s = st.trace_summary(sess)
    assert s["event_count"] == 6
    assert s["counts"]["note"] == 2
    assert s["counts"]["block"] == 1
    assert s["counts"]["pin"] == 1
    assert s["notes"] == ["first", "second"]
    assert s["blocks"] == ["rmw broken"]
    assert s["pins"] == ["important artifact"]
    assert s["has_blockers"] is True
    assert s["duration_s"] >= 0


def test_trace_emit_never_raises_on_bad_session_id(tmp_path, monkeypatch):
    """Session IDs with dodgy chars get sanitized; emit shouldn't raise."""
    from service.isaac_assist_service.chat import session_trace as st
    monkeypatch.setattr(st, "_TRACE_ROOT", tmp_path)
    st.emit("sess/../../../etc/passwd", "user_msg", {"text": "x"})
    # No exception = success; trace lands on a sanitized filename
    assert any(f.is_file() for f in tmp_path.iterdir())


def test_trace_missing_session_returns_empty(tmp_path, monkeypatch):
    from service.isaac_assist_service.chat import session_trace as st
    monkeypatch.setattr(st, "_TRACE_ROOT", tmp_path)
    assert st.read_trace("does_not_exist") == []
    assert st.trace_summary("does_not_exist")["event_count"] == 0


# ── /thoughts (2026-04-19) — chain-of-thought exposure ─────────────────
def test_parse_slash_recognizes_thoughts():
    from service.isaac_assist_service.chat.slash_commands import parse_slash
    assert parse_slash("/thoughts") == {"cmd": "thoughts", "arg": ""}
    assert parse_slash("/thoughts all") == {"cmd": "thoughts", "arg": "all"}


def test_execute_thoughts_disabled_when_env_off(monkeypatch):
    from service.isaac_assist_service.chat.slash_commands import execute_slash
    monkeypatch.delenv("GEMINI_EXPOSE_THOUGHTS", raising=False)
    reply = asyncio.run(execute_slash(
        "thoughts", "", history=[], emit_trace=lambda *a: None,
        session_id="test",
    ))
    assert "disabled" in reply["reply"].lower()
    assert "GEMINI_EXPOSE_THOUGHTS=1" in reply["reply"]


def test_execute_thoughts_shows_last_turn(tmp_path, monkeypatch):
    """/thoughts without arg should return only the latest turn's thoughts —
    the slice from the most recent user_msg onward."""
    from service.isaac_assist_service.chat.slash_commands import execute_slash
    from service.isaac_assist_service.chat import session_trace as st
    monkeypatch.setenv("GEMINI_EXPOSE_THOUGHTS", "1")
    monkeypatch.setattr(st, "_TRACE_ROOT", tmp_path)

    sess = "test_thoughts_last_turn"
    # Two turns — we should only see turn 2's thought.
    st.emit(sess, "user_msg", {"text": "first msg"})
    st.emit(sess, "agent_thought", {"round": 0, "text": "turn 1 thinking"})
    st.emit(sess, "agent_reply", {"text": "turn 1 reply"})
    st.emit(sess, "user_msg", {"text": "second msg"})
    st.emit(sess, "agent_thought", {"round": 0, "text": "turn 2 thinking"})
    st.emit(sess, "agent_reply", {"text": "turn 2 reply"})

    reply = asyncio.run(execute_slash(
        "thoughts", "", history=[], emit_trace=lambda *a: None,
        session_id=sess,
    ))
    assert "turn 2 thinking" in reply["reply"]
    assert "turn 1 thinking" not in reply["reply"]


def test_execute_thoughts_all_shows_every_turn(tmp_path, monkeypatch):
    from service.isaac_assist_service.chat.slash_commands import execute_slash
    from service.isaac_assist_service.chat import session_trace as st
    monkeypatch.setenv("GEMINI_EXPOSE_THOUGHTS", "1")
    monkeypatch.setattr(st, "_TRACE_ROOT", tmp_path)

    sess = "test_thoughts_all"
    st.emit(sess, "user_msg", {"text": "msg1"})
    st.emit(sess, "agent_thought", {"round": 0, "text": "thought 1"})
    st.emit(sess, "user_msg", {"text": "msg2"})
    st.emit(sess, "agent_thought", {"round": 0, "text": "thought 2"})

    reply = asyncio.run(execute_slash(
        "thoughts", "all", history=[], emit_trace=lambda *a: None,
        session_id=sess,
    ))
    assert "thought 1" in reply["reply"]
    assert "thought 2" in reply["reply"]


def test_execute_thoughts_no_thoughts_captured(tmp_path, monkeypatch):
    from service.isaac_assist_service.chat.slash_commands import execute_slash
    from service.isaac_assist_service.chat import session_trace as st
    monkeypatch.setenv("GEMINI_EXPOSE_THOUGHTS", "1")
    monkeypatch.setattr(st, "_TRACE_ROOT", tmp_path)

    sess = "test_thoughts_empty"
    st.emit(sess, "user_msg", {"text": "hi"})
    st.emit(sess, "agent_reply", {"text": "hey"})

    reply = asyncio.run(execute_slash(
        "thoughts", "", history=[], emit_trace=lambda *a: None,
        session_id=sess,
    ))
    assert "No thoughts captured yet" in reply["reply"]


def test_help_now_lists_thoughts():
    from service.isaac_assist_service.chat.slash_commands import execute_slash
    reply = asyncio.run(execute_slash(
        "help", "", history=[], emit_trace=lambda *a: None,
    ))
    assert "/thoughts" in reply["reply"]
