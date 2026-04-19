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

import json
import time
from pathlib import Path
from typing import Any, Dict, List

_TRACE_ROOT = (
    Path(__file__).resolve().parent.parent.parent.parent
    / "workspace"
    / "session_traces"
)


def _trace_path(session_id: str) -> Path:
    _TRACE_ROOT.mkdir(parents=True, exist_ok=True)
    safe = "".join(c if c.isalnum() or c in "_-." else "_" for c in session_id)
    return _TRACE_ROOT / f"{safe}.jsonl"


def emit(session_id: str, event_type: str, payload: Dict[str, Any] | None = None) -> None:
    """Append one event line. Best-effort; never raises."""
    try:
        line = {
            "ts": time.time(),
            "type": event_type,
            "payload": payload or {},
        }
        with _trace_path(session_id).open("a", encoding="utf-8") as f:
            f.write(json.dumps(line, default=str, ensure_ascii=False) + "\n")
    except Exception:
        pass


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
