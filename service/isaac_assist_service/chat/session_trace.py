"""Append-only JSONL session trace — the foundation for /stuck, /report,
and post-session introspection.

One file per session at `workspace/session_traces/{session_id}.jsonl`.
Each line is a single event:

    {"ts": 1776580000.123, "type": "user_msg", "payload": {"text": "..."}}
    {"ts": 1776580001.456, "type": "tool_call", "payload": {"tool": "create_prim", "args": {...}}}
    {"ts": 1776580002.789, "type": "note", "payload": {"text": "GPU determinism flaky"}}

Event types used today:
 - session_start  — first message of a session (metadata only)
 - user_msg       — user-typed text (incl. slash commands)
 - slash_cmd      — a slash command intercepted locally
 - agent_reply    — final assistant text
 - tool_call      — each tool invocation (name + args, not results)
 - tool_result    — outcome of tool_call (success + truncated output)
 - error          — any exception surfaced into chat
 - note           — /note <text>
 - block          — /block <reason> (session flagged as blocked)
 - pin            — /pin (artifact saved)
 - cite_returned  — /cite <topic> succeeded

Silent-on-error: trace must NEVER raise into the chat path. If disk
is full or permission denied, the chat continues.
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any, Dict, List

_TRACE_ROOT = (
    Path(__file__).resolve().parent.parent.parent.parent
    / "workspace"
    / "session_traces"
)

# ── Live subscribers (in-process pub/sub) ───────────────────────────────
# Per-session list of asyncio.Queue. Subscribers (the SSE endpoint) call
# subscribe() on connect and unsubscribe() in a finally block. emit()
# fans out via put_nowait — slow consumers get dropped events rather
# than blocking the orchestrator hot path. Queue size 200 is deep
# enough to absorb a typical turn's worth of events comfortably.
_listeners: Dict[str, List["asyncio.Queue"]] = {}


def subscribe(session_id: str) -> "asyncio.Queue":
    """Subscribe to live trace events for a session.

    Returns an asyncio.Queue that the caller drains. Caller MUST call
    :func:`unsubscribe` when done (typically in a try/finally around
    the consume loop) to avoid leaking queues across reconnects.
    """
    q: "asyncio.Queue" = asyncio.Queue(maxsize=200)
    _listeners.setdefault(session_id, []).append(q)
    return q


def unsubscribe(session_id: str, q: "asyncio.Queue") -> None:
    """Drop a subscriber queue. Safe if already removed."""
    if session_id in _listeners:
        _listeners[session_id] = [x for x in _listeners[session_id] if x is not q]
        if not _listeners[session_id]:
            del _listeners[session_id]


def _trace_path(session_id: str) -> Path:
    _TRACE_ROOT.mkdir(parents=True, exist_ok=True)
    safe = "".join(c if c.isalnum() or c in "_-." else "_" for c in session_id)
    return _TRACE_ROOT / f"{safe}.jsonl"


def emit(session_id: str, event_type: str, payload: Dict[str, Any] | None = None) -> None:
    """Append one event line and fan out to live subscribers.

    Best-effort on both legs: disk write failures are swallowed (chat
    must never break because of trace I/O), and queue.put_nowait drops
    silently on QueueFull (slow SSE consumer doesn't block emit).
    """
    line = {
        "ts": time.time(),
        "type": event_type,
        "payload": payload or {},
    }
    # 1. Disk write
    try:
        with _trace_path(session_id).open("a", encoding="utf-8") as f:
            f.write(json.dumps(line, default=str, ensure_ascii=False) + "\n")
    except Exception:
        pass
    # 2. Live fan-out — never blocks orchestrator
    for q in list(_listeners.get(session_id, [])):
        try:
            q.put_nowait(line)
        except asyncio.QueueFull:
            pass  # slow consumer; drop event silently
        except Exception:
            pass  # belt-and-braces — emit() must never raise


def read_trace(session_id: str) -> List[Dict]:
    """Load all events for a session. Returns [] if no trace exists."""
    path = _trace_path(session_id)
    if not path.exists():
        return []
    events: List[Dict] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            events.append(json.loads(raw))
        except json.JSONDecodeError:
            continue
    return events


def trace_summary(session_id: str) -> Dict[str, Any]:
    """Compact at-a-glance summary of a session. Used by /report."""
    events = read_trace(session_id)
    counts: Dict[str, int] = {}
    first_ts = events[0]["ts"] if events else None
    last_ts = events[-1]["ts"] if events else None
    notes = [e["payload"].get("text", "") for e in events if e.get("type") == "note"]
    blocks = [e["payload"].get("text", "") for e in events if e.get("type") == "block"]
    pins = [e["payload"].get("text", "") for e in events if e.get("type") == "pin"]
    for e in events:
        counts[e.get("type", "unknown")] = counts.get(e.get("type", "unknown"), 0) + 1
    return {
        "session_id": session_id,
        "event_count": len(events),
        "started_at": first_ts,
        "last_event_at": last_ts,
        "duration_s": (last_ts - first_ts) if (first_ts and last_ts) else 0,
        "counts": counts,
        "notes": notes,
        "blocks": blocks,
        "pins": pins,
        "has_blockers": bool(blocks),
    }
