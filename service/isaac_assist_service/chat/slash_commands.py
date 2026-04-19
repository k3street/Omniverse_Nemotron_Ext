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
    r"^/(?P<cmd>note|block|pin|cite|help)\b(?:\s+(?P<arg>.*))?$",
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
- `/help` — this message

Session traces live in `workspace/session_traces/{session_id}.jsonl`."""


async def execute_slash(
    cmd: str,
    arg: str,
    *,
    history: List[Dict],
    emit_trace,  # callable(event_type: str, payload: dict) -> None
) -> Dict[str, Any]:
    """Run a slash command. Returns a Reply dict matching handle_message shape."""
    if cmd == "help":
        return _reply(_HELP_TEXT)

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


def _reply(text: str) -> Dict[str, Any]:
    """Shape the reply dict to match orchestrator.handle_message's contract."""
    return {
        "intent": "slash_command",
        "reply": text,
        "tool_calls": [],
        "code_patches": [],
    }
