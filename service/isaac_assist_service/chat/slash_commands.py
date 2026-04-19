"""Slash-command interceptor for chat sessions.

Commands execute deterministically WITHOUT hitting the LLM — fast, free,
predictable. They write events to the session trace (see session_trace.py)
so a later /stuck or /report can pick them up.

Supported today:
  /note <text>   — attach an observation to the session trace
  /block <text>  — mark the session as blocked with a reason
  /pin           — pin the most recent assistant reply as an artifact
  /pin <text>    — pin a specific snippet (arbitrary text)
  /cite <topic>  — fetch a cite-fact paragraph from the deprecations index
  /help          — list these commands inline

Design: an incoming message starting with `/` is parsed here. If
recognized, we short-circuit and return a minimal reply. If not, we fall
through to the normal LLM+tool path (so `/unrelated` doesn't silently
vanish).
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

# Order matters — more specific first
_CMD_PATTERN = re.compile(
    r"^/(?P<cmd>note|block|pin|cite|thoughts|undo|help)\b(?:\s+(?P<arg>.*))?$",
    re.S | re.I,
)


def parse_slash(message: str) -> Optional[Dict[str, str]]:
    """Return `{cmd, arg}` if `message` is a recognized slash command, else None."""
    if not message:
        return None
    m = _CMD_PATTERN.match(message.strip())
    if not m:
        return None
    return {"cmd": m.group("cmd").lower(), "arg": (m.group("arg") or "").strip()}


def _last_assistant_reply(history: List[Dict]) -> Optional[str]:
    """Find the most recent assistant message in the session history."""
    for msg in reversed(history or []):
        if msg.get("role") == "assistant":
            return msg.get("content", "")
    return None


_HELP_TEXT = """**Slash commands** (executed locally, no LLM cost):

- `/note <text>` — attach an observation to the session trace (for later `/report`)
- `/block <reason>` — mark session as blocked; surfaces in day summary
- `/pin` — pin the most recent assistant reply as an artifact
- `/pin <text>` — pin arbitrary text as an artifact
- `/cite <topic>` — fetch a ready-to-paste paragraph from the deprecations corpus
  (try: `/cite deterministic`, `/cite ros2 namespace`, `/cite urdf importer`)
- `/thoughts` — show the agent's chain-of-thought from this session's last turn
  (requires `GEMINI_EXPOSE_THOUGHTS=1`)
- `/undo` — revert the last stage-mutating turn (restores root layer from
  auto-snapshot). `/undo N` reverts N turns. `/undo clear` wipes history.
- `/help` — this message

Session traces live in `workspace/session_traces/{session_id}.jsonl`."""


async def execute_slash(
    cmd: str,
    arg: str,
    *,
    history: List[Dict],
    emit_trace,  # callable(event_type: str, payload: dict) -> None
    session_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Run a slash command. Returns a Reply dict matching handle_message shape."""
    if cmd == "help":
        return _reply(_HELP_TEXT)

    if cmd == "thoughts":
        return _reply(_thoughts_reply(session_id, arg))

    if cmd == "undo":
        return _reply(await _undo_reply(session_id, arg, emit_trace))

    if cmd == "note":
        if not arg:
            return _reply(
                "Usage: `/note <your observation>` — writes to the session trace "
                "so you can compile a report later without losing it to scroll."
            )
        emit_trace("note", {"text": arg})
        return _reply(f"📝 Note saved ({len(arg)} chars). Use `/help` for more commands.")

    if cmd == "block":
        if not arg:
            return _reply(
                "Usage: `/block <reason>` — flags the session. Useful when you're "
                "stuck and want to come back to it or share with the team."
            )
        emit_trace("block", {"text": arg})
        return _reply(
            f"🚧 Blocker recorded: {arg[:100]}{'...' if len(arg) > 100 else ''}\n\n"
            f"Session now marked as `has_blockers=True`. When you build a `/stuck` "
            f"report later (coming soon), this reason is included."
        )

    if cmd == "pin":
        text = arg or (_last_assistant_reply(history) or "")
        if not text:
            return _reply(
                "Nothing to pin — no prior assistant reply found and no text "
                "provided. Try `/pin <text>` or send a message first."
            )
        emit_trace("pin", {"text": text, "source": "arg" if arg else "last_reply"})
        preview = text[:140].replace("\n", " ")
        suffix = "..." if len(text) > 140 else ""
        return _reply(
            f"📌 Pinned ({len(text)} chars): {preview}{suffix}\n\n"
            f"Saved to session trace; included in `/report` export."
        )

    if cmd == "cite":
        if not arg:
            return _reply(
                "Usage: `/cite <topic>` — pulls a cite-able paragraph from the "
                "deprecations corpus. Try: `/cite deterministic`, "
                "`/cite ros2 namespace`, `/cite urdf importer`, "
                "`/cite replicator randomizer`."
            )
        # Local import to avoid circular — tool_executor imports from chat.* too
        from .tools.tool_executor import _handle_lookup_api_deprecation

        r = await _handle_lookup_api_deprecation({"query": arg, "top_k": 1})
        results = r.get("results") or []
        if not results:
            emit_trace("cite_miss", {"query": arg})
            return _reply(
                f"No cite-fact on file for `{arg}`.\n\n"
                f"Corpus currently covers: deterministic replay, ROS2 bridge "
                f"namespace, URDF importer, articulation tensor view, "
                f"omni.isaac.core namespace migration, Replicator DR entry points, "
                f"ArticulationRootAPI placement. Add more rows to "
                f"`service/isaac_assist_service/knowledge/deprecations.jsonl`."
            )
        row = results[0]
        text_parts = [f"**Cite** (`{row['id']}`):\n", row["cite"]]
        if row.get("deprecated_4x"):
            text_parts.append(
                f"\n**Deprecated / removed in 5.x:** "
                + ", ".join(f"`{d}`" for d in row["deprecated_4x"])
            )
        if row.get("caveats"):
            text_parts.append(
                "\n**Caveats:**\n"
                + "\n".join(f"- {c}" for c in row["caveats"])
            )
        emit_trace("cite_returned", {"query": arg, "row_id": row["id"]})
        return _reply("\n".join(text_parts))

    # Shouldn't reach here due to regex allow-list, but defensively:
    return _reply(f"Unknown slash command: `/{cmd}`. Try `/help`.")


async def _undo_reply(session_id: Optional[str], arg: str, emit_trace) -> str:
    """Roll back the stage by N turns via the turn_snapshot module.

    `/undo` → rollback 1 turn.
    `/undo 3` → rollback 3 turns.
    `/undo clear` → wipe snapshot history for this session.
    """
    if not session_id:
        return "No session_id available — can't run /undo."
    from . import turn_snapshot
    arg_clean = (arg or "").strip().lower()

    if arg_clean == "clear":
        removed = turn_snapshot.clear(session_id)
        emit_trace("undo_clear", {"removed": removed})
        return f"🧹 Cleared {removed} snapshot(s). New turns will start fresh."

    if not arg_clean:
        steps = 1
    else:
        try:
            steps = int(arg_clean)
        except ValueError:
            return (
                f"Usage: `/undo` (revert last turn) or `/undo N` (revert N turns) "
                f"or `/undo clear` (wipe history). Got: `{arg}`"
            )
        if steps < 1:
            return "`/undo N` requires N >= 1."

    available = turn_snapshot.snapshot_count(session_id)
    if available == 0:
        return (
            "No snapshots available for this session yet. Snapshots are saved "
            "automatically before stage-mutating turns; send one first."
        )
    if steps > available:
        return (
            f"Requested `{steps}` step(s) but only `{available}` snapshot(s) "
            f"are available. Try `/undo {available}` to go back as far as possible."
        )

    result = await turn_snapshot.restore(session_id, steps=steps)
    if not result.get("ok"):
        emit_trace("undo_failed", {"steps": steps, "error": result.get("error")})
        return f"❌ Undo failed: {result.get('error', 'unknown error')}"

    emit_trace("undo_applied", {
        "steps": steps,
        "path": result.get("path"),
        "remaining_snapshots": result.get("remaining_snapshots"),
    })
    return (
        f"↺ Reverted {steps} turn{'s' if steps != 1 else ''}. "
        f"Restored {result.get('imported_size', 0)} chars of root-layer state. "
        f"{result.get('remaining_snapshots', 0)} snapshot(s) remain."
    )


def _thoughts_reply(session_id: Optional[str], arg: str) -> str:
    """Pull `agent_thought` events from the session trace for display.

    Without arg: show thoughts from the LAST user turn (most recent run).
    With arg 'all': show every captured thought in the session.
    """
    import os
    if os.environ.get("GEMINI_EXPOSE_THOUGHTS", "0") != "1":
        return (
            "Chain-of-thought exposure is disabled. Set "
            "`GEMINI_EXPOSE_THOUGHTS=1` and restart the service to enable."
        )
    if not session_id:
        return "No session_id available — can't look up thoughts."
    from .session_trace import read_trace
    events = read_trace(session_id)
    thoughts = [e for e in events if e.get("type") == "agent_thought"]
    if not thoughts:
        return (
            "No thoughts captured yet. Either the agent hasn't run a turn "
            "with thinking enabled, or the model didn't return any thought-parts."
        )
    # Default: only the most recent user-turn's thoughts. A user_msg event
    # marks the start of a new turn; we slice from the last user_msg onward.
    show_all = arg.strip().lower() == "all"
    if not show_all:
        last_user_idx = None
        for i, e in enumerate(events):
            if e.get("type") == "user_msg":
                last_user_idx = i
        if last_user_idx is not None:
            thoughts = [
                e for e in events[last_user_idx:]
                if e.get("type") == "agent_thought"
            ]
    if not thoughts:
        return "No thoughts for the latest turn."
    lines = [
        f"**Agent thoughts** ({len(thoughts)} part{'s' if len(thoughts) != 1 else ''}, "
        f"{'all session' if show_all else 'last turn'}):\n"
    ]
    for i, th in enumerate(thoughts, 1):
        rnd = th.get("payload", {}).get("round", "?")
        text = th.get("payload", {}).get("text", "")
        lines.append(f"**Round {rnd}, part {i}:**\n{text}\n")
    return "\n---\n".join(lines)


def _reply(text: str) -> Dict[str, Any]:
    """Shape the reply dict to match orchestrator.handle_message's contract."""
    return {
        "intent": "slash_command",
        "reply": text,
        "tool_calls": [],
        "code_patches": [],
    }
