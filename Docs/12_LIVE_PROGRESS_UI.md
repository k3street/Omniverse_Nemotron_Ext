# 12 — Live Progress UI (Claude-Code-style)

## Purpose

Make the Isaac Assist chat panel feel like working *with* the agent rather than waiting *on* it. Users see what the agent is doing as it happens, can stop it mid-flight, and can read at a glance what changed in the scene when the turn ends.

Two failure modes this spec eliminates:

1. **Dead air.** Today the chat shows nothing between message-sent and final-bubble — often 30+ seconds. Users wonder if it crashed.
2. **Runaway agent.** When the agent goes down a wrong path, there is no way to interrupt. The user watches helplessly until the spam-halt fires at round 6 or the turn ends.

## Runtime

Background service (FastAPI/uvicorn :8000), Extension UI (omni.ui in Isaac Sim).

## Phase

Single coherent feature, implementable in isolation. Five phases internally (see § Implementation Order).

## Dependencies

- `chat/session_trace.py` — already emits the events we need; we add a pub/sub fan-out
- `chat/orchestrator.py` — already calls `_trace_emit` at the right points; we add two more events and cancel-checks
- `chat/routes.py` — add SSE endpoint and cancel endpoint
- `service_client.py` — add SSE consumer and cancel call
- `ui/chat_view.py` — major rewrite of presentation layer (no protocol breakage)

No new external dependencies. `aiohttp` and `fastapi` already in the stack.

---

## Goals

1. User sees first visual feedback within **150 ms** of clicking Send.
2. Every tool call is visible in a "live strip" with verb-first phrasing, args, and elapsed time.
3. User can hit **Stop** at any point and the agent halts after the current tool returns.
4. After the turn, a **diff chip** shows what changed in the stage (`+Conveyor`, `+4 Cubes`).
5. UI degrades gracefully if SSE drops — POST blob remains the canonical record.
6. Animation is purposeful, peripheral-friendly, and never distracting.
7. **One-click undo** of the last mutating turn, with subsequent undos chaining backwards through history. Soft-confirm before commit. The bubble is visibly marked as undone (dimmed + tagged) — the chat history remains a true record of what the user tried.
8. **Clear chat** option that wipes conversation history without touching the USD stage — distinct from `New Scene` which wipes both.
9. **User-controllable text/UI scaling** with seven discrete steps (80%–175%), persisted across sessions via Kit's settings system. Three discovery surfaces: header `Aa` button (popup), right-click context menu in chat area, and keyboard shortcuts (`Ctrl+=` / `Ctrl+-` / `Ctrl+0`).

## Non-goals

- **Token-level streaming of assistant text.** Out of scope; would require touching all four LLM provider implementations.
- **Real progress inside a long-running tool.** Kit RPC `/exec_sync` returns one blob; no stdout streaming. Best we can do is elapsed-time on the row.
- **Markdown rendering in chat bubbles.** `ui.Label` is plain text; defer to a later spec.
- **Persisting chat history across window restarts.** Today's behavior preserved.
- **Audio cues.** Mentioned as optional follow-up; not in this spec.

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│  omni.ui Extension (Isaac Sim main thread)              │
│                                                         │
│  ChatViewWindow                                         │
│   ├─ ScrollingFrame (chat history)                      │
│   ├─ LiveStrip ◀──────────────────┐                     │
│   ├─ Input + Send/Stop button     │                     │
│   └─ AssistServiceClient          │                     │
│       ├─ POST /chat/message ──────┼──┐                  │
│       ├─ POST /chat/cancel ───────┼──┤                  │
│       └─ GET  /chat/stream ◀──────┘  │  (SSE)           │
└──────────────────────────────────────┼──────────────────┘
                                       │
                                       ▼
┌─────────────────────────────────────────────────────────┐
│  uvicorn :8000 (separate process)                       │
│                                                         │
│  routes.py                                              │
│   ├─ POST /chat/message  → orchestrator.handle_message  │
│   ├─ POST /chat/cancel   → cancel_registry.request      │
│   └─ GET  /chat/stream   → session_trace.subscribe()    │
│                                                         │
│  orchestrator.py                                        │
│   ├─ emit turn_started                                  │
│   ├─ for round in rounds:                               │
│   │    if cancel_registry.is_cancelled(): break         │
│   │    for tc in tool_calls:                            │
│   │      emit tool_call_started                         │
│   │      result = await execute_tool_call(...)          │
│   │      emit tool_call_finished                        │
│   │      if cancel_registry.is_cancelled(): break       │
│   ├─ compute turn_diff → emit turn_diff_computed        │
│   └─ emit agent_reply                                   │
│                                                         │
│  session_trace.py                                       │
│   ├─ emit() — writes JSONL AND fans out to subscribers  │
│   └─ subscribe(sid) → asyncio.Queue                     │
│                                                         │
│  cancel_registry.py (new, ~20 lines)                    │
│   └─ {request_cancel, is_cancelled, clear}              │
└─────────────────────────────────────────────────────────┘
```

**Two-channel design rationale.** The POST `/chat/message` call returns the full canonical record (text, tool_calls, code_patches). The SSE stream carries ephemeral live-progress events. If SSE drops mid-turn, the POST blob still arrives and the UI renders correctly from it. The SSE channel is a *progressive enhancement*, not a critical dependency.

---

## File-by-file changes

### NEW: `service/isaac_assist_service/chat/cancel_registry.py`

```python
"""Per-session cancel flags. Module-level state — uvicorn lifetime.

The orchestrator polls is_cancelled() between rounds and between tools
within a round. It cannot abort an in-flight tool — the smallest unit
of cancellation is "after the current tool returns."
"""
from __future__ import annotations
from typing import Set

_cancelled: Set[str] = set()


def request_cancel(session_id: str) -> None:
    _cancelled.add(session_id)


def is_cancelled(session_id: str) -> bool:
    return session_id in _cancelled


def clear(session_id: str) -> None:
    _cancelled.discard(session_id)
```

### MODIFY: `service/isaac_assist_service/chat/session_trace.py`

Add pub/sub fan-out alongside the existing JSONL write. **Existing emit() callers do not change.**

```python
# Add at module top, after imports:
import asyncio
from typing import Dict, List

_listeners: Dict[str, List[asyncio.Queue]] = {}


def subscribe(session_id: str) -> asyncio.Queue:
    """Subscribe to live trace events for a session.

    Returns an asyncio.Queue that the caller drains. Caller MUST call
    unsubscribe() when done (typically in a try/finally around their loop).
    Queue size 200 — drops oldest on overflow rather than blocking emit().
    """
    q = asyncio.Queue(maxsize=200)
    _listeners.setdefault(session_id, []).append(q)
    return q


def unsubscribe(session_id: str, q: asyncio.Queue) -> None:
    if session_id in _listeners:
        _listeners[session_id] = [x for x in _listeners[session_id] if x is not q]
        if not _listeners[session_id]:
            del _listeners[session_id]
```

Modify `emit()` to fan out after the existing JSONL write:

```python
def emit(session_id: str, event_type: str, payload: Dict[str, Any] | None = None) -> None:
    """Append one event line. Best-effort; never raises."""
    line = {
        "ts": time.time(),
        "type": event_type,
        "payload": payload or {},
    }
    # 1. Disk write (existing)
    try:
        with _trace_path(session_id).open("a", encoding="utf-8") as f:
            f.write(json.dumps(line, default=str, ensure_ascii=False) + "\n")
    except Exception:
        pass
    # 2. Live fan-out (new) — never blocks orchestrator
    for q in list(_listeners.get(session_id, [])):
        try:
            q.put_nowait(line)
        except asyncio.QueueFull:
            pass  # slow consumer; drop event silently
        except Exception:
            pass  # belt-and-braces — emit() must never raise
```

### MODIFY: `service/isaac_assist_service/chat/orchestrator.py`

Three additions, all minimal and gated by `if STREAM_PROGRESS_ENABLED` for the first iteration so they can be A/B tested.

**A. Emit `turn_started` at the very top of `handle_message`** (around line 467, right after the existing `user_msg` emit):

```python
_trace_emit(session_id, "turn_started", {
    "user_message_preview": (user_message or "")[:120],
})
```

**B. Wrap each `await execute_tool_call(...)` in the round loop** (orchestrator.py around line 823). Replace:

```python
result = await execute_tool_call(fn_name, fn_args)
```

with:

```python
import time as _t  # if not already imported at top
from .tools.descriptions import describe as _describe_tool
_t0 = _t.monotonic()
_trace_emit(session_id, "tool_call_started", {
    "tc_id": tc_id,
    "tool": fn_name,
    "args_preview": _summarize_args(fn_args),
    "args_full": fn_args,
    "description": _describe_tool(fn_name),
})
result = await execute_tool_call(fn_name, fn_args)
_elapsed_ms = int((_t.monotonic() - _t0) * 1000)
_success = result.get("success") if "success" in result else (result.get("type") != "error")
_trace_emit(session_id, "tool_call_finished", {
    "tc_id": tc_id,
    "tool": fn_name,
    "success": _success,
    "elapsed_ms": _elapsed_ms,
    "error": result.get("error") if result.get("type") == "error" else None,
})
```

Add at the top of the file:

```python
def _summarize_args(args: dict) -> str:
    """One-line summary of args for live UI display."""
    if not args:
        return ""
    # Priority: prim_path leaf → path leaf → first non-None value
    for key in ("prim_path", "path", "prim", "target"):
        if key in args and args[key]:
            v = str(args[key])
            return v.rsplit("/", 1)[-1] if "/" in v else v[:30]
    for v in args.values():
        if v not in (None, "", [], {}):
            return str(v)[:30]
    return ""
```

**C. Cancel checks** — at the top of the round loop (orchestrator.py around line 759):

```python
from .cancel_registry import is_cancelled as _is_cancelled, clear as _cancel_clear

# ...inside handle_message, before the round loop:
_cancel_clear(session_id)  # clear any stale flag from previous turn

# Inside the round loop, at the very top:
for round_idx in range(max_rounds):
    if _is_cancelled(session_id):
        _trace_emit(session_id, "cancel_acknowledged", {"round": round_idx})
        break
    # ... existing round body ...
```

And inside the per-tool inner loop (around line 801, the `for tc in real_tool_calls:` loop), check between tools:

```python
for tc in real_tool_calls:
    if _is_cancelled(session_id):
        _trace_emit(session_id, "cancel_acknowledged", {"round": round_idx, "tc": tc_id})
        break
    # ... existing tool execution ...
```

**D. Cancel reply path** — after the round loop, if the turn was cancelled, short-circuit to a canned reply:

```python
# After the round loop, before the existing code that builds `reply`:
if _is_cancelled(session_id):
    _cancel_clear(session_id)
    reply = (
        f"Stopped. Completed {len(executed_tools)} step"
        f"{'s' if len(executed_tools) != 1 else ''} before stop. "
        "Type a new prompt to continue or refine."
    )
    # Skip the rest of the reply-generation pipeline (verify, code-block validation, etc)
    # but still emit agent_reply and persist to history below.
    # Easiest: jump to the trace+history+return block at the end.
```

Mark this with a comment explaining the early-exit path. Do not skip `_trace_emit("agent_reply", ...)` — the UI relies on it to render the bubble and clear the live strip.

**E. Augment `turn_diff_computed` payload** to include path lists for the diff chip (orchestrator.py around line 1345):

```python
_trace_emit(session_id, "turn_diff_computed", {
    "added": len(_diff.added),
    "removed": len(_diff.removed),
    "modified": len(_diff.modified),
    "total_changes": _diff.total_changes,
    # NEW — top-N path samples for UI rendering
    "added_paths": list(_diff.added)[:8],
    "removed_paths": list(_diff.removed)[:8],
    "modified_paths": list(_diff.modified.keys())[:8],
})
```

**F. Augment `agent_reply` payload** with `has_snapshot` so the UI knows whether to show an undo button. The orchestrator already calls `turn_snapshot.capture()` and gets back `{ok: bool, ...}`. Stash that result and include in the reply event (orchestrator.py around line 1534):

```python
_trace_emit(session_id, "agent_reply", {
    "text": reply[:500],
    "intent": intent,
    "tool_count": len(executed_tools or []),
    "patch_count": len(code_patches or []),
    # NEW — gate undo button visibility
    "has_snapshot": bool(_snapshot_result and _snapshot_result.get("ok")),
})
```

where `_snapshot_result` is the dict captured at the top of `handle_message` when `turn_snapshot.capture()` is called. If snapshot save failed (rare — disk full, no stage), the bubble simply won't get an undo button.

### MODIFY: `service/isaac_assist_service/chat/routes.py`

**A. Add SSE endpoint:**

```python
import asyncio
import json
from fastapi.responses import StreamingResponse
from . import session_trace

# Events to forward to the UI. Keep this set tight — JSONL trace has many
# diagnostic event types that are noise to the user.
_USER_VISIBLE_EVENTS = {
    "turn_started",
    "tool_call_started",
    "tool_call_finished",
    "patch_executed",
    "retry_spam_halt",
    "cancel_acknowledged",
    "turn_diff_computed",
    "agent_reply",
    "error",
}


@router.get("/stream/{session_id}")
async def stream_session(session_id: str):
    """Server-Sent Events stream of live trace events for a session.

    Client subscribes once at window open, keeps connection open for the
    window's lifetime. On reconnect, no event replay — caller relies on
    POST /message blob for canonical state.
    """
    q = session_trace.subscribe(session_id)

    async def gen():
        try:
            yield "event: connected\ndata: {}\n\n"
            while True:
                try:
                    evt = await asyncio.wait_for(q.get(), timeout=15.0)
                    if evt["type"] in _USER_VISIBLE_EVENTS:
                        # SSE frame format: event: <type>\ndata: <json>\n\n
                        yield f"event: {evt['type']}\ndata: {json.dumps(evt, default=str)}\n\n"
                except asyncio.TimeoutError:
                    # Keepalive — prevents idle-connection killers
                    yield ": keepalive\n\n"
        finally:
            session_trace.unsubscribe(session_id, q)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # disable nginx-style proxy buffering
        },
    )
```

**B. Add cancel endpoint:**

```python
from . import cancel_registry


class CancelRequest(BaseModel):
    session_id: str = "default_session"


@router.post("/cancel")
async def cancel_turn(req: CancelRequest):
    """Request cancellation of the in-flight turn for this session.

    Sets a flag; orchestrator checks it between rounds and between tools.
    The active tool (if any) completes; subsequent tools are skipped.
    """
    cancel_registry.request_cancel(req.session_id)
    return {"status": "cancel_requested"}
```

**C. Add undo endpoint** — wraps the existing `turn_snapshot` infrastructure (which already auto-prunes consumed snapshots after restore, so no separate stack is needed):

```python
from . import turn_snapshot


class UndoRequest(BaseModel):
    session_id: str = "default_session"
    steps: int = 1


@router.post("/undo")
async def undo_chat_turn(req: UndoRequest):
    """Revert N most-recent stage-mutating turns.

    The snapshot stack is implicit on disk in workspace/turn_snapshots/{sid}/
    — `turn_snapshot.restore()` removes consumed snapshots, so successive
    undo calls naturally chain backwards through history.
    """
    session_trace.emit(req.session_id, "undo_started", {"steps": req.steps})

    available = turn_snapshot.snapshot_count(req.session_id)
    if available == 0:
        session_trace.emit(req.session_id, "undo_failed", {"error": "no snapshots"})
        return {"ok": False, "error": "No undo history for this session."}

    # Silently cap rather than 4xx — caller intent is "undo as much as I can"
    steps = min(req.steps, available)
    result = await turn_snapshot.restore(req.session_id, steps=steps)

    if result.get("ok"):
        session_trace.emit(req.session_id, "undo_applied", {
            "steps": steps,
            "remaining_snapshots": result.get("remaining_snapshots", 0),
        })
    else:
        session_trace.emit(req.session_id, "undo_failed", {
            "steps": steps,
            "error": result.get("error", "unknown"),
        })
    return result
```

**D. Add clear-chat endpoint** — wipes server-side history without touching the stage:

```python
class ClearChatRequest(BaseModel):
    session_id: str = "default_session"


@router.post("/clear_chat")
async def clear_chat(req: ClearChatRequest):
    """Wipe conversation history for this session WITHOUT touching the stage.

    Distinct from /reset which also opens a fresh stage. Use this when
    chat gets cluttered but you want to keep working in the same scene.
    Snapshots are kept — undo still works on prior turns.
    """
    # Reuse orchestrator's existing history-clear logic, just don't reset the stage.
    orchestrator.clear_history(req.session_id)
    session_trace.emit(req.session_id, "chat_cleared", {})
    return {"status": "cleared"}
```

`orchestrator.clear_history(sid)` already exists (called by `/reset`). If not, add a tiny helper that calls `_session_history.pop(sid, None)`.

**E. Update `_USER_VISIBLE_EVENTS`** to include the undo events:

```python
_USER_VISIBLE_EVENTS = {
    "turn_started",
    "tool_call_started",
    "tool_call_finished",
    "patch_executed",
    "retry_spam_halt",
    "cancel_acknowledged",
    "turn_diff_computed",
    "agent_reply",
    "error",
    # NEW
    "undo_started",
    "undo_applied",
    "undo_failed",
    "chat_cleared",
}
```

### NEW: `service/isaac_assist_service/chat/tools/descriptions.py`

Generated dict mapping tool name → one-line user-facing description. Generated once by `scripts/regenerate_descriptions.py` (see below). Hand-tuned overrides live in `_OVERRIDES`.

The orchestrator's `tool_call_started` event includes the description in its payload, so the UI can show it as a tooltip subtitle without the extension needing its own copy of the data.

```python
"""User-facing one-line descriptions of every tool handler.

Generated by scripts/regenerate_descriptions.py from tool_executor.py.
Hand overrides go in _OVERRIDES — those win over the generated ones.

Voice: short, plain, written for a robotics engineer watching the agent
work. NOT written for an LLM that needs to call the tool. ~10-15 words.
"""
from __future__ import annotations
from typing import Dict

# ── Generated descriptions (do not hand-edit; use _OVERRIDES instead) ───
_GENERATED: Dict[str, str] = {
    # Filled by regenerate_descriptions.py — example entries:
    "create_prim": "Adds a new USD prim (cube, sphere, xform, etc.) to the stage at a given path.",
    "apply_physics_material": "Gives an object physical behavior — rigid body, soft body, cloth, or particle.",
    "scatter_on_surface": "Randomly distributes objects across the surface of a mesh.",
    "run_usd_script": "Executes Python code on Kit's main thread to mutate the stage.",
    "get_viewport_image": "Captures the current viewport as a PNG.",
    "scene_summary": "Reads and summarizes what's currently in the stage.",
    # ... ~344 entries total, one per registered tool
}

# ── Hand overrides (these win) ──────────────────────────────────────────
_OVERRIDES: Dict[str, str] = {
    # Add entries here as you spot bad generated descriptions in use.
    # Example:
    # "configure_correlated_dr": "Sets up domain randomization where multiple parameters change together.",
}


def describe(tool_name: str) -> str:
    """Return the one-line description for this tool, or '' if unknown."""
    return _OVERRIDES.get(tool_name) or _GENERATED.get(tool_name, "")
```

### NEW: `scripts/regenerate_descriptions.py`

Idempotent regenerator. Diffs the live tool registry against `_GENERATED`, calls Claude only for the missing/new tools, and rewrites `descriptions.py` preserving `_OVERRIDES` untouched.

```python
"""Regenerate descriptions.py from the current tool_executor.py.

Diff-aware: only calls Claude for tools that are NEW or MISSING from
the existing _GENERATED dict. Existing entries are kept. _OVERRIDES is
never touched. Safe to run as a pre-commit hook.

Usage:
    python scripts/regenerate_descriptions.py             # incremental
    python scripts/regenerate_descriptions.py --rebuild   # regenerate all

Requires ANTHROPIC_API_KEY in env.
"""
from __future__ import annotations
import argparse
import ast
import json
import os
import re
import sys
from pathlib import Path

import anthropic

ROOT = Path(__file__).resolve().parents[1]
TOOL_EXECUTOR = ROOT / "service" / "isaac_assist_service" / "chat" / "tools" / "tool_executor.py"
DESCRIPTIONS  = ROOT / "service" / "isaac_assist_service" / "chat" / "tools" / "descriptions.py"

PROMPT_TEMPLATE = """You are documenting tool handlers for a robotics simulation assistant UI.

Each handler appears below with its function name and source. For each one,
write ONE LINE (10-15 words, max 100 chars) that describes what the tool
does FROM A USER'S PERSPECTIVE.

Voice rules:
- Plain English. Avoid USD/jargon unless unavoidable.
- Active verb. Start with "Adds...", "Reads...", "Configures...", etc.
- No parameter lists, no implementation detail.
- A robotics engineer watching the agent work should understand it instantly.

Return ONLY a JSON object {{"tool_name": "description", ...}}. No prose, no markdown fences.

Tools:
{tools_block}
"""


def extract_handlers(source: str) -> dict[str, str]:
    """Parse tool_executor.py and return {tool_name: source_snippet}."""
    tree = ast.parse(source)
    handlers: dict[str, str] = {}
    src_lines = source.splitlines()
    # Tools are exposed via DATA_HANDLERS / CODE_GEN_HANDLERS dicts and via
    # async def _handle_<name> functions. Find both.
    for node in ast.walk(tree):
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
            name = node.name
            if name.startswith("_handle_"):
                tool_name = name[len("_handle_"):]
                # Snippet: signature + first ~12 lines of body
                start = node.lineno - 1
                end = min(start + 14, len(src_lines))
                snippet = "\n".join(src_lines[start:end])
                handlers[tool_name] = snippet
    return handlers


def load_existing_descriptions() -> dict[str, str]:
    if not DESCRIPTIONS.exists():
        return {}
    src = DESCRIPTIONS.read_text(encoding="utf-8")
    m = re.search(r"_GENERATED:\s*Dict\[str,\s*str\]\s*=\s*(\{.*?\n\})", src, re.S)
    if not m:
        return {}
    # Parse the literal dict safely with ast
    try:
        return ast.literal_eval(m.group(1))
    except Exception:
        return {}


def write_descriptions(generated: dict[str, str]) -> None:
    """Rewrite descriptions.py preserving _OVERRIDES section."""
    if DESCRIPTIONS.exists():
        existing = DESCRIPTIONS.read_text(encoding="utf-8")
        overrides_match = re.search(
            r"(_OVERRIDES:\s*Dict\[str,\s*str\]\s*=\s*\{.*?\n\})", existing, re.S
        )
        overrides_block = overrides_match.group(1) if overrides_match else (
            "_OVERRIDES: Dict[str, str] = {\n}"
        )
    else:
        overrides_block = "_OVERRIDES: Dict[str, str] = {\n}"

    body_lines = ['_GENERATED: Dict[str, str] = {']
    for k in sorted(generated):
        v = generated[k].replace('"', '\\"')
        body_lines.append(f'    "{k}": "{v}",')
    body_lines.append("}")
    generated_block = "\n".join(body_lines)

    out = f'''"""User-facing one-line descriptions of every tool handler.

Generated by scripts/regenerate_descriptions.py from tool_executor.py.
Hand overrides go in _OVERRIDES — those win over the generated ones.
"""
from __future__ import annotations
from typing import Dict

# ── Generated (do not hand-edit; use _OVERRIDES) ────────────────────────
{generated_block}

# ── Hand overrides (these win) ──────────────────────────────────────────
{overrides_block}


def describe(tool_name: str) -> str:
    return _OVERRIDES.get(tool_name) or _GENERATED.get(tool_name, "")
'''
    DESCRIPTIONS.write_text(out, encoding="utf-8")


def call_claude_batched(handlers_to_doc: dict[str, str], batch_size: int = 30) -> dict[str, str]:
    client = anthropic.Anthropic()
    out: dict[str, str] = {}
    items = list(handlers_to_doc.items())
    for i in range(0, len(items), batch_size):
        batch = items[i:i + batch_size]
        block = "\n\n".join(f"### {name}\n```python\n{src}\n```" for name, src in batch)
        msg = client.messages.create(
            model="claude-opus-4-7",
            max_tokens=2048,
            messages=[{"role": "user", "content": PROMPT_TEMPLATE.format(tools_block=block)}],
        )
        text = msg.content[0].text.strip()
        # Strip code fences if model added them despite instructions
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
        try:
            parsed = json.loads(text)
            out.update(parsed)
        except json.JSONDecodeError:
            print(f"WARN: batch {i//batch_size} returned invalid JSON, skipping", file=sys.stderr)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rebuild", action="store_true",
                   help="Regenerate ALL descriptions, not just missing ones")
    args = ap.parse_args()

    handlers = extract_handlers(TOOL_EXECUTOR.read_text(encoding="utf-8"))
    existing = {} if args.rebuild else load_existing_descriptions()
    missing = {k: v for k, v in handlers.items() if k not in existing}

    if not missing:
        print(f"Up to date — {len(existing)} descriptions, no changes needed.")
        return

    print(f"Generating {len(missing)} new description(s) (have {len(existing)})...")
    new = call_claude_batched(missing)
    merged = {**existing, **new}
    # Drop entries for tools that no longer exist
    merged = {k: v for k, v in merged.items() if k in handlers}

    write_descriptions(merged)
    print(f"Wrote {len(merged)} descriptions to {DESCRIPTIONS.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
```

**Run it:**

```bash
export ANTHROPIC_API_KEY=...
python scripts/regenerate_descriptions.py            # first run: generates all ~344
python scripts/regenerate_descriptions.py            # subsequent: only new tools
python scripts/regenerate_descriptions.py --rebuild  # force full regenerate
```

**Optional pre-commit hook** (add to `.git/hooks/pre-commit` or via pre-commit-hooks config):

```bash
if git diff --cached --name-only | grep -q "tool_executor.py"; then
    python scripts/regenerate_descriptions.py
    git add service/isaac_assist_service/chat/tools/descriptions.py
fi
```

### MODIFY: `exts/isaac_6.0/omni.isaac.assist/omni/isaac/assist/service_client.py`

Add session_id parameter, SSE consumer, and cancel method. Keep `send_message` and `reset_session` exactly as they are — backward compatible.

```python
import asyncio
import json
import logging
import uuid

try:
    import aiohttp
    HAS_AIOHTTP = True
except ImportError:
    HAS_AIOHTTP = False

logger = logging.getLogger(__name__)


class AssistServiceClient:
    def __init__(self, base_url: str = "http://localhost:8000", session_id: str | None = None):
        self.base_url = base_url
        # Per-extension UUID — prevents two extensions on the same uvicorn from
        # interleaving SSE streams.
        self.session_id = session_id or f"ext_{uuid.uuid4().hex[:8]}"
        self._stream_task: asyncio.Task | None = None
        self._stream_stop = False

    # ── existing send_message and reset_session unchanged ────────────────────

    async def cancel_turn(self) -> dict:
        if not HAS_AIOHTTP:
            return {"status": "skipped"}
        url = f"{self.base_url}/api/v1/chat/cancel"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json={"session_id": self.session_id}) as resp:
                    return await resp.json() if resp.status == 200 else {"error": f"HTTP {resp.status}"}
        except Exception as e:
            return {"error": str(e)}

    async def undo_turn(self, steps: int = 1) -> dict:
        if not HAS_AIOHTTP:
            return {"ok": False, "error": "aiohttp missing"}
        url = f"{self.base_url}/api/v1/chat/undo"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json={"session_id": self.session_id, "steps": steps}) as resp:
                    return await resp.json() if resp.status == 200 else {"ok": False, "error": f"HTTP {resp.status}"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    async def clear_chat(self) -> dict:
        if not HAS_AIOHTTP:
            return {"status": "skipped"}
        url = f"{self.base_url}/api/v1/chat/clear_chat"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json={"session_id": self.session_id}) as resp:
                    return await resp.json() if resp.status == 200 else {"error": f"HTTP {resp.status}"}
        except Exception as e:
            return {"error": str(e)}

    def start_stream(self, on_event):
        """Start SSE consumer in the background. Idempotent.

        on_event: callable(event_type: str, payload: dict, raw_event: dict) -> None
                  Called from the asyncio loop. Safe to mutate omni.ui widgets directly.
        """
        if self._stream_task and not self._stream_task.done():
            return
        self._stream_stop = False
        self._stream_task = asyncio.ensure_future(self._stream_loop(on_event))

    def stop_stream(self):
        self._stream_stop = True
        if self._stream_task:
            self._stream_task.cancel()

    async def _stream_loop(self, on_event):
        if not HAS_AIOHTTP:
            return
        url = f"{self.base_url}/api/v1/chat/stream/{self.session_id}"
        backoff = 1.0
        while not self._stream_stop:
            try:
                timeout = aiohttp.ClientTimeout(total=None, sock_read=30)
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.get(url) as resp:
                        if resp.status != 200:
                            logger.warning(f"SSE returned HTTP {resp.status}")
                            await asyncio.sleep(backoff)
                            backoff = min(backoff * 2, 16.0)
                            continue
                        backoff = 1.0  # connected — reset backoff
                        on_event("__connection__", {"state": "connected"}, {})
                        current_event = None
                        async for raw_line in resp.content:
                            if self._stream_stop:
                                return
                            line = raw_line.decode("utf-8", errors="ignore").rstrip("\n")
                            if line.startswith("event:"):
                                current_event = line[6:].strip()
                            elif line.startswith("data:"):
                                data_str = line[5:].strip()
                                if not data_str:
                                    continue
                                try:
                                    raw_evt = json.loads(data_str)
                                except json.JSONDecodeError:
                                    continue
                                evt_type = current_event or raw_evt.get("type", "")
                                payload = raw_evt.get("payload", raw_evt)
                                try:
                                    on_event(evt_type, payload, raw_evt)
                                except Exception as e:
                                    logger.exception(f"on_event handler raised: {e}")
                            elif line.startswith(":"):
                                pass  # SSE comment (keepalive)
                            elif line == "":
                                current_event = None
            except asyncio.CancelledError:
                return
            except Exception as e:
                logger.warning(f"SSE loop error: {e} — reconnecting in {backoff}s")
                on_event("__connection__", {"state": "reconnecting"}, {})
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 16.0)
        on_event("__connection__", {"state": "stopped"}, {})
```

### NEW: `exts/isaac_6.0/omni.isaac.assist/omni/isaac/assist/ui/verbs.py`

Tool name → present-progressive phrase. Auto-derive + override file.

```python
"""Tool name → present-progressive verb phrase for the live UI.

The agent's tool registry has ~344 handlers. We auto-derive a verb
from the function name (`create_prim` → `Creating prim`), with a manual
override table for the awkward cases (`run_usd_script` → `Running script`).
"""
from __future__ import annotations
from typing import Dict

# Verb-stem conjugations. Auto-applied to tool names whose first segment matches.
_CONJUGATION: Dict[str, str] = {
    "add": "Adding",
    "analyze": "Analyzing",
    "anchor": "Anchoring",
    "apply": "Applying",
    "approve": "Approving",
    "assemble": "Assembling",
    "assign": "Assigning",
    "batch": "Batch processing",
    "build": "Building",
    "calibrate": "Calibrating",
    "cancel": "Cancelling",
    "capture": "Capturing",
    "check": "Checking",
    "checkpoint": "Checkpointing",
    "clone": "Cloning",
    "compare": "Comparing",
    "compute": "Computing",
    "configure": "Configuring",
    "connect": "Connecting",
    "count": "Counting",
    "create": "Creating",
    "debug": "Debugging",
    "define": "Defining",
    "delete": "Removing",
    "detect": "Detecting",
    "diagnose": "Diagnosing",
    "disable": "Disabling",
    "dispatch": "Dispatching",
    "download": "Downloading",
    "duplicate": "Duplicating",
    "edit": "Editing",
    "enable": "Enabling",
    "enforce": "Enforcing",
    "evaluate": "Evaluating",
    "execute": "Executing",
    "explain": "Explaining",
    "export": "Exporting",
    "extract": "Extracting",
    "filter": "Filtering",
    "find": "Finding",
    "finetune": "Fine-tuning",
    "fix": "Fixing",
    "flatten": "Flattening",
    "focus": "Focusing",
    "generate": "Generating",
    "get": "Reading",
    "grasp": "Grasping",
    "group": "Grouping",
    "hardware": "Checking hardware",
    "highlight": "Highlighting",
    "import": "Importing",
    "inspect": "Inspecting",
    "interpolate": "Interpolating",
    "iterate": "Iterating",
    "launch": "Launching",
    "list": "Listing",
    "load": "Loading",
    "lookup": "Looking up",
    "measure": "Measuring",
    "merge": "Merging",
    "monitor": "Monitoring",
    "move": "Moving",
    "navigate": "Navigating",
    "open": "Opening",
    "optimize": "Optimizing",
    "overlap": "Checking overlap",
    "pause": "Pausing",
    "pixel": "Mapping pixel",
    "plan": "Planning",
    "play": "Playing",
    "post": "Posting",
    "preflight": "Pre-flighting",
    "preview": "Previewing",
    "prim": "Reading prim",
    "proactive": "Checking",
    "profile": "Profiling",
    "publish": "Publishing",
    "query": "Querying",
    "queue": "Queueing",
    "quick": "Quick action",
    "raycast": "Raycasting",
    "record": "Recording",
    "redact": "Redacting",
    "remove": "Removing",
    "render": "Rendering",
    "replay": "Replaying",
    "restore": "Restoring",
    "review": "Reviewing",
    "robot": "Robot wizard",
    "ros2": "ROS2 call",
    "run": "Running",
    "save": "Saving",
    "scatter": "Scattering",
    "scene": "Reading scene",
    "select": "Selecting",
    "set": "Setting",
    "setup": "Setting up",
    "show": "Showing",
    "simplify": "Simplifying",
    "sim": "Simulating",
    "slash": "Slash command",
    "solve": "Solving",
    "start": "Starting",
    "stop": "Stopping",
    "suggest": "Suggesting",
    "summarize": "Summarizing",
    "sweep": "Sweeping",
    "teach": "Teaching",
    "teleop": "Teleop",
    "teleport": "Teleporting",
    "trace": "Tracing",
    "train": "Training",
    "tune": "Tuning",
    "update": "Updating",
    "upload": "Uploading",
    "validate": "Validating",
    "verify": "Verifying",
    "vision": "Vision",
    "visualize": "Visualizing",
    "watch": "Watching",
}

# Manual overrides for awkward auto-derived names. Add as you spot bad ones in use.
_OVERRIDES: Dict[str, str] = {
    "run_usd_script": "Running USD script",
    "queue_exec_patch": "Applying patch",
    "exec_sync": "Executing on Kit",
    "lookup_knowledge": "Looking up reference",
    "lookup_product_spec": "Looking up product spec",
    "lookup_material": "Looking up material",
    "scatter_on_surface": "Scattering objects",
    "scene_summary": "Summarizing scene",
    "scene_diff": "Diffing scene",
    "scene_aware_starter_prompts": "Suggesting prompts",
    "get_viewport_image": "Capturing viewport",
    "capture_viewport": "Capturing viewport",
    "capture_camera_image": "Capturing camera",
    "create_prim": "Creating prim",
    "delete_prim": "Removing prim",
    "apply_physics_material": "Applying physics material",
    "apply_api_schema": "Applying API schema",
    "apply_dr_preset": "Applying randomization",
    "set_attribute": "Setting attribute",
    "get_attribute": "Reading attribute",
    "robot_wizard": "Running robot wizard",
    "ros2_publish": "ROS2 publishing",
    "ros2_subscribe_once": "ROS2 subscribing",
    "preflight_check": "Pre-flight check",
    "diagnose_physics_error": "Diagnosing physics",
    "diagnose_training": "Diagnosing training",
    "find_prims_by_name": "Finding prims",
    "find_prims_by_schema": "Finding prims",
    "list_all_prims": "Listing prims",
    "build_stage_index": "Indexing stage",
    "save_delta_snapshot": "Saving snapshot",
    "restore_delta_snapshot": "Restoring snapshot",
    "compute_convex_hull": "Computing hull",
    "set_camera_look_at": "Aiming camera",
    "set_viewport_camera": "Switching camera",
    "load_groot_policy": "Loading policy",
    "evaluate_groot": "Evaluating policy",
    "finetune_groot": "Fine-tuning policy",
}


def verb_for(tool_name: str) -> str:
    """Return the present-progressive phrase to display for this tool."""
    if tool_name in _OVERRIDES:
        return _OVERRIDES[tool_name]
    parts = tool_name.split("_")
    if not parts:
        return tool_name
    head = parts[0].lower()
    rest = " ".join(parts[1:])
    if head in _CONJUGATION:
        return f"{_CONJUGATION[head]} {rest}".rstrip()
    # Fallback: capitalize the tool name as-is, replace underscores
    return tool_name.replace("_", " ").capitalize()
```

### NEW: `exts/isaac_6.0/omni.isaac.assist/omni/isaac/assist/ui/animations.py`

```python
"""Reusable animation primitives for omni.ui.

All animations are coroutines that mutate widget style/properties
over time. The Kit asyncio loop ticks at ~60Hz; these target ~60Hz
updates with cubic ease-out curves for natural-feeling motion.

Color format is omni.ui ABGR: 0xAABBGGRR.
"""
from __future__ import annotations
import asyncio
import time
from typing import Callable

# Frame budget — 60Hz target. Lower if profiling shows pressure.
_FRAME_S = 1.0 / 60.0


def _ease_out_cubic(t: float) -> float:
    return 1.0 - (1.0 - t) ** 3


def _lerp_int(a: int, b: int, t: float) -> int:
    return int(a + (b - a) * t)


def _lerp_color_abgr(c0: int, c1: int, t: float) -> int:
    a0, b0, g0, r0 = (c0 >> 24) & 0xFF, (c0 >> 16) & 0xFF, (c0 >> 8) & 0xFF, c0 & 0xFF
    a1, b1, g1, r1 = (c1 >> 24) & 0xFF, (c1 >> 16) & 0xFF, (c1 >> 8) & 0xFF, c1 & 0xFF
    a = _lerp_int(a0, a1, t)
    b = _lerp_int(b0, b1, t)
    g = _lerp_int(g0, g1, t)
    r = _lerp_int(r0, r1, t)
    return (a << 24) | (b << 16) | (g << 8) | r


async def lerp_color(set_fn: Callable[[int], None], c0: int, c1: int, ms: int = 300) -> None:
    """Interpolate ABGR color from c0 to c1 over `ms` ms with ease-out cubic.

    set_fn is called with the current color each frame.
    """
    t0 = time.monotonic()
    duration_s = ms / 1000.0
    while True:
        elapsed = time.monotonic() - t0
        if elapsed >= duration_s:
            set_fn(c1)
            return
        t = _ease_out_cubic(elapsed / duration_s)
        set_fn(_lerp_color_abgr(c0, c1, t))
        await asyncio.sleep(_FRAME_S)


async def fade_in_widget(widget, color_key: str, target_color: int, ms: int = 150) -> None:
    """Fade widget's color (e.g. 'background_color') from alpha=0 to target."""
    base = target_color & 0x00FFFFFF  # alpha=0
    def _set(c):
        s = dict(widget.style or {})
        s[color_key] = c
        widget.style = s
    await lerp_color(_set, base, target_color, ms)


async def pulse_widget(widget, color_key: str, base_color: int, peak_color: int,
                      up_ms: int = 250, down_ms: int = 600) -> None:
    """One-shot pulse: base → peak → base."""
    def _set(c):
        s = dict(widget.style or {})
        s[color_key] = c
        widget.style = s
    await lerp_color(_set, base_color, peak_color, up_ms)
    await lerp_color(_set, peak_color, base_color, down_ms)


async def slow_pulse_loop(widget, color_key: str, c_dim: int, c_bright: int,
                         period_ms: int = 1500, stop_check: Callable[[], bool] = None) -> None:
    """Repeating slow pulse for status indicators (e.g. reconnecting dot).

    stop_check is polled each cycle; when it returns True, the loop exits.
    """
    half = period_ms // 2
    while not (stop_check and stop_check()):
        await pulse_widget(widget, color_key, c_dim, c_bright, half, half)
```

### MAJOR REWRITE: `exts/isaac_6.0/omni.isaac.assist/omni/isaac/assist/ui/chat_view.py`

Full rewrite below. Key changes from current implementation:

- Live progress strip below the chat scroll
- Send button mutates to Stop / Stopping based on turn state
- SSE consumer wired up at construction
- Diff chip rendered below assistant bubbles
- Connection-health dot in header
- Empty-state suggestion chips on first launch
- Soft-confirm on New Scene
- Re-run button on user bubbles

```python
import omni.ui as ui
import asyncio
import time
import logging
import os
import json
import uuid
from typing import Optional, List, Dict

from ..service_client import AssistServiceClient
from ..webrtc_client import ViewportWebRTCClient
from .verbs import verb_for
from . import animations as anim

logger = logging.getLogger(__name__)

# ── Color palette (omni.ui ABGR: 0xAABBGGRR) ──────────────────────────────
COL_BG_USER         = 0xFF2A2E33
COL_BG_ASSIST       = 0xFF1E2125
COL_BG_LIVE_STRIP   = 0xFF181A1D
COL_TEXT            = 0xFFDDDDDD
COL_TEXT_DIM        = 0xFF8A8E92
COL_TEXT_SUBTLE     = 0xFF666A6E
COL_NV_GREEN        = 0xFF00B976  # NVIDIA green #76B900 in ABGR
COL_AMBER           = 0xFF00A8FF  # warning / slow
COL_AMBER_DIM       = 0xFF0078B0
COL_RED             = 0xFF4444FF  # error
COL_DOT_GOOD        = 0xFF00B976
COL_DOT_WARN        = 0xFF00A8FF
COL_DOT_BAD         = 0xFF4444FF
COL_BORDER_PULSE    = 0xFF00B976
COL_BORDER_NEUTRAL  = 0x00000000

# ── Spinner glyphs (Braille — slow, calm) ─────────────────────────────────
SPINNER_GLYPHS = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
SPIN_INTERVAL_S = 0.20  # peripheral-friendly cadence
ELAPSED_TICK_S  = 0.20

# Time thresholds for color escalation on long-running tools
SLOW_THRESHOLD_S = 10.0
VERY_SLOW_THRESHOLD_S = 20.0


class ChatViewWindow(ui.Window):
    def __init__(self, title: str, **kwargs):
        super().__init__(title, **kwargs)
        self.service = AssistServiceClient()  # generates per-instance UUID
        self.webrtc = None

        # Turn lifecycle state
        self._turn_active = False
        self._turn_rendered_via_sse = False
        self._pending_assistant_bubble: Optional[Dict] = None

        # Live strip rows by tc_id
        self._live_rows: Dict[str, Dict] = {}
        self._spin_task: Optional[asyncio.Task] = None
        self._destroyed = False

        # Empty state chips visible until first message ever sent
        self._chips_shown = True

        # New-Scene / Clear-chat confirmation state
        self._new_scene_confirm = False
        self._clear_chat_confirm = False

        # Undo state — bubbles eligible for undo, in chronological order.
        # Latest entry = the one with a visible ↶ button. SSE undo_applied
        # pops + dims; the new latest gets the button.
        self._undoable_bubbles: List[Dict] = []
        self._undo_progress_row = None
        self._undo_handled_via_sse = False

        self._build_ui()
        self.service.start_stream(self._on_sse_event)
        self._spin_task = asyncio.ensure_future(self._tick_loop())

    # ═══════════════════════════════════════════════════════════════════════
    # UI construction
    # ═══════════════════════════════════════════════════════════════════════
    def _build_ui(self):
        with self.frame:
            with ui.VStack(spacing=4):
                self._build_header()
                self._build_chat_area()
                self._build_live_strip()
                self._build_chips()
                self._build_input()

    def _build_header(self):
        with ui.HStack(height=26, spacing=6):
            ui.Label("Isaac Assist", width=0, style={"color": COL_TEXT, "font_size": 13})
            # Connection dot — 6px, sits just after title
            self.conn_dot = ui.Rectangle(width=6, height=6, style={
                "background_color": COL_DOT_GOOD,
                "border_radius": 3,
            })
            ui.Spacer()
            self.btn_new = ui.Button("New", width=40, height=22,
                                     clicked_fn=self._on_new_scene_clicked,
                                     style={"font_size": 11},
                                     tooltip="Wipe stage and chat. Confirm required.")
            self.btn_clear = ui.Button("Clear", width=44, height=22,
                                       clicked_fn=self._on_clear_chat_clicked,
                                       style={"font_size": 11},
                                       tooltip="Clear chat history (keeps stage). Confirm required.")
            self.btn_livekit = ui.Button("Vision", width=54, height=22,
                                         clicked_fn=self._toggle_livekit,
                                         style={"font_size": 11})

    def _build_chat_area(self):
        self.scroll = ui.ScrollingFrame(
            horizontal_scrollbar_policy=ui.ScrollBarPolicy.SCROLLBAR_ALWAYS_OFF,
            vertical_scrollbar_policy=ui.ScrollBarPolicy.SCROLLBAR_AS_NEEDED,
        )
        with self.scroll:
            self.chat_layout = ui.VStack(spacing=8)

    def _build_live_strip(self):
        # Container is collapsed (height=0) until first event arrives.
        self.live_strip_container = ui.ZStack(height=0)
        with self.live_strip_container:
            ui.Rectangle(style={"background_color": COL_BG_LIVE_STRIP, "border_radius": 4})
            with ui.VStack(spacing=2, name="live_inner"):
                ui.Spacer(height=4)
                with ui.HStack(height=14):
                    ui.Spacer(width=6)
                    self.live_header = ui.Label("Live", width=0,
                                                style={"color": COL_TEXT_SUBTLE, "font_size": 10})
                    ui.Spacer()
                self.live_rows_layout = ui.VStack(spacing=2)
                ui.Spacer(height=4)

    def _build_chips(self):
        self.chips_container = ui.HStack(height=22, spacing=4)
        with self.chips_container:
            ui.Spacer(width=2)
            for label in ("Build a pick-and-place scene",
                         "Add a Franka arm",
                         "Inspect the stage"):
                btn = ui.Button(label, height=20,
                                clicked_fn=lambda t=label: self._on_chip(t),
                                style={"font_size": 10, "background_color": 0xFF2A2E33,
                                       "color": COL_TEXT_DIM})

    def _build_input(self):
        with ui.HStack(height=28, spacing=4):
            self.input_field = ui.StringField(multiline=False, style={"font_size": 12})
            # Send/Stop button — text + style mutate by state
            self.btn_send = ui.Button("Send", width=64, height=24,
                                      clicked_fn=self._on_send_or_stop,
                                      style={"font_size": 12})

    # ═══════════════════════════════════════════════════════════════════════
    # Send / Stop button state machine
    # ═══════════════════════════════════════════════════════════════════════
    def _set_button_state(self, state: str):
        """state ∈ {idle, busy, stopping}"""
        if state == "idle":
            self.btn_send.text = "Send"
            self.btn_send.enabled = True
            self.btn_send.style = {"font_size": 12}
        elif state == "busy":
            self.btn_send.text = "Stop"
            self.btn_send.enabled = True
            self.btn_send.style = {"font_size": 12, "color": COL_AMBER}
        elif state == "stopping":
            self.btn_send.text = "Stopping…"
            self.btn_send.enabled = False
            self.btn_send.style = {"font_size": 12, "color": COL_TEXT_SUBTLE}
        self._btn_state = state

    def _on_send_or_stop(self):
        if getattr(self, "_btn_state", "idle") == "idle":
            self._submit_message()
        elif self._btn_state == "busy":
            self._set_button_state("stopping")
            asyncio.ensure_future(self.service.cancel_turn())
        # In "stopping", button is disabled — no action.

    # ═══════════════════════════════════════════════════════════════════════
    # Message submission
    # ═══════════════════════════════════════════════════════════════════════
    def _submit_message(self):
        text = self.input_field.model.get_value_as_string().strip()
        if not text:
            return
        self.input_field.model.set_value("")
        self._add_user_bubble(text)
        self._hide_chips()
        self._turn_active = True
        self._turn_rendered_via_sse = False
        self._pending_assistant_bubble = None
        self._set_button_state("busy")
        asyncio.ensure_future(self._handle_service_request(text))

    async def _handle_service_request(self, text: str):
        try:
            response = await self.service.send_message(text)
            # SSE may have already rendered the assistant bubble; if so, just augment
            if not self._turn_rendered_via_sse:
                self._render_assistant_from_post(response)
        finally:
            self._turn_active = False
            self._set_button_state("idle")

    def _render_assistant_from_post(self, response: dict):
        """Fallback render when SSE didn't deliver agent_reply (drop, slow)."""
        if "error" in response:
            self._add_assistant_bubble(response["error"], error=True)
            return
        for msg in response.get("response_messages", []):
            content = msg.get("content", "")
            if content:
                self._add_assistant_bubble(content)

    # ═══════════════════════════════════════════════════════════════════════
    # SSE event handler
    # ═══════════════════════════════════════════════════════════════════════
    def _on_sse_event(self, evt_type: str, payload: dict, raw: dict):
        try:
            if evt_type == "__connection__":
                self._on_connection_state(payload.get("state"))
            elif evt_type == "turn_started":
                self._on_turn_started(payload)
            elif evt_type == "tool_call_started":
                self._on_tool_started(payload)
            elif evt_type == "tool_call_finished":
                self._on_tool_finished(payload)
            elif evt_type == "patch_executed":
                # Already covered by tool_call_finished if the wrap-call was added.
                # Kept here for older orchestrator paths that emit this directly.
                pass
            elif evt_type == "retry_spam_halt":
                self._on_spam_halt(payload)
            elif evt_type == "cancel_acknowledged":
                self._on_cancel_ack(payload)
            elif evt_type == "turn_diff_computed":
                self._pending_diff = payload  # attach when bubble renders
            elif evt_type == "agent_reply":
                self._on_agent_reply(payload)
            elif evt_type == "undo_started":
                self._on_undo_started(payload)
            elif evt_type == "undo_applied":
                self._on_undo_applied(payload)
            elif evt_type == "undo_failed":
                self._on_undo_failed(payload)
            elif evt_type == "chat_cleared":
                pass  # UI handled it locally; server confirmation only
        except Exception as e:
            logger.exception(f"SSE handler failed for {evt_type}: {e}")

    # ── Turn lifecycle ────────────────────────────────────────────────────
    def _on_turn_started(self, payload):
        self._show_live_strip()
        self._add_thinking_row()

    def _on_tool_started(self, payload):
        tc_id = payload.get("tc_id", f"unk_{time.time()}")
        tool = payload.get("tool", "unknown")
        args_preview = payload.get("args_preview", "")
        description = payload.get("description", "")

        # Remove the "thinking" placeholder if present (first real tool fired)
        self._remove_thinking_row()

        # Retry-storm compression: if we have a recent FAILED row for the same tool, recycle it
        existing = self._find_recyclable_row(tool)
        if existing is not None:
            existing["attempts"] += 1
            existing["spinner_lbl"].text = SPINNER_GLYPHS[0]
            existing["spinner_lbl"].style = {"color": COL_NV_GREEN, "font_size": 13}
            verb_text = f"{verb_for(tool)} ×{existing['attempts']}"
            existing["verb_lbl"].text = verb_text
            existing["args_lbl"].text = args_preview
            existing["state"] = "running"
            existing["started_at"] = time.monotonic()
            existing["tc_id"] = tc_id
            self._live_rows[tc_id] = existing
            return

        self._make_live_row(tc_id, tool, args_preview, payload.get("args_full", {}), description)

    def _on_tool_finished(self, payload):
        tc_id = payload.get("tc_id", "")
        row = self._live_rows.get(tc_id)
        if not row:
            return
        success = payload.get("success", True)
        elapsed_ms = payload.get("elapsed_ms", 0)
        row["state"] = "done_ok" if success else "done_fail"
        row["elapsed_lbl"].text = f"{elapsed_ms/1000:.1f}s"
        if success:
            row["spinner_lbl"].text = "✓"
            asyncio.ensure_future(anim.lerp_color(
                lambda c: row["spinner_lbl"].__setattr__("style",
                    {**(row["spinner_lbl"].style or {}), "color": c}),
                COL_NV_GREEN, COL_TEXT_DIM, ms=400))
            asyncio.ensure_future(anim.lerp_color(
                lambda c: row["verb_lbl"].__setattr__("style",
                    {**(row["verb_lbl"].style or {}), "color": c}),
                COL_TEXT, COL_TEXT_DIM, ms=400))
        else:
            row["spinner_lbl"].text = "✗"
            row["spinner_lbl"].style = {"color": COL_RED, "font_size": 13}
            err = payload.get("error", "")
            if err:
                row["args_lbl"].text = (str(err)[:50] + "…") if len(str(err)) > 50 else str(err)
                row["args_lbl"].style = {"color": COL_RED, "font_size": 11}

    def _on_spam_halt(self, payload):
        # Visual: append a dim summary row
        with self.live_rows_layout:
            with ui.HStack(height=14):
                ui.Spacer(width=10)
                ui.Label(f"⚠ Stopped after {payload.get('consecutive_fails', 0)} failed attempts",
                        style={"color": COL_AMBER, "font_size": 11})

    def _on_cancel_ack(self, payload):
        # Visual: append a dim "stopped" indicator
        with self.live_rows_layout:
            with ui.HStack(height=14):
                ui.Spacer(width=10)
                ui.Label("■ Stopped by user", style={"color": COL_TEXT_DIM, "font_size": 11})

    def _on_agent_reply(self, payload):
        text = payload.get("text", "")
        has_snapshot = payload.get("has_snapshot", False)
        bubble = self._add_assistant_bubble(text, snapshot_live_rows=True)
        # Pulse the bubble border briefly
        if bubble and "border_rect" in bubble:
            asyncio.ensure_future(anim.pulse_widget(
                bubble["border_rect"], "background_color",
                COL_BORDER_NEUTRAL, COL_BORDER_PULSE, up_ms=300, down_ms=700))
        # Attach diff chip if pending
        diff = getattr(self, "_pending_diff", None)
        if diff:
            self._attach_diff_chip(bubble, diff)
            # Undo eligibility: this turn mutated the stage AND a snapshot exists.
            # Pop the prior bubble's undo button (if any), give this one the button.
            if has_snapshot and diff.get("total_changes", 0) > 0:
                self._transfer_undo_button(bubble, diff)
            del self._pending_diff
        self._collapse_live_strip()
        self._turn_rendered_via_sse = True

    def _on_connection_state(self, state):
        if state == "connected":
            self.conn_dot.style = {"background_color": COL_DOT_GOOD, "border_radius": 3}
        elif state == "reconnecting":
            self.conn_dot.style = {"background_color": COL_DOT_WARN, "border_radius": 3}
        elif state in ("stopped", "error"):
            self.conn_dot.style = {"background_color": COL_DOT_BAD, "border_radius": 3}

    # ═══════════════════════════════════════════════════════════════════════
    # Live strip operations
    # ═══════════════════════════════════════════════════════════════════════
    def _show_live_strip(self):
        # Lazy-grow: actual height is sum of rows + padding, computed on each row add
        self._live_strip_visible = True
        self.live_strip_container.height = ui.Pixel(28)  # header + 1 row min

    def _collapse_live_strip(self):
        self._live_strip_visible = False
        self.live_strip_container.height = ui.Pixel(0)
        self.live_rows_layout.clear()
        self._live_rows.clear()

    def _add_thinking_row(self):
        with self.live_rows_layout:
            with ui.HStack(height=18, spacing=6) as row:
                ui.Spacer(width=6)
                spin = ui.Label(SPINNER_GLYPHS[0], width=14,
                              style={"color": COL_NV_GREEN, "font_size": 13})
                lbl = ui.Label("Thinking…", width=0,
                             style={"color": COL_TEXT_DIM, "font_size": 12})
        self._live_rows["__thinking__"] = {
            "row": row,
            "spinner_lbl": spin,
            "verb_lbl": lbl,
            "args_lbl": lbl,  # alias — only one label
            "elapsed_lbl": lbl,
            "started_at": time.monotonic(),
            "state": "running",
            "tool": "__thinking__",
            "attempts": 1,
            "tc_id": "__thinking__",
        }
        self._bump_strip_height()

    def _remove_thinking_row(self):
        if "__thinking__" in self._live_rows:
            # omni.ui doesn't let us cleanly remove a single child; rebuild by
            # snapshot is overkill for one ephemeral row. Easiest: hide it.
            r = self._live_rows.pop("__thinking__")
            r["row"].visible = False

    def _make_live_row(self, tc_id: str, tool: str, args_preview: str, args_full: dict,
                       description: str = ""):
        verb = verb_for(tool)
        full_args_json = json.dumps(args_full, default=str)[:500]
        # Tooltip layout: description (if any) on top, then function signature,
        # then full args. Two newlines between sections for readability.
        tooltip_parts = []
        if description:
            tooltip_parts.append(description)
        tooltip_parts.append(f"{tool}({full_args_json})")
        verb_tooltip = "\n\n".join(tooltip_parts)
        with self.live_rows_layout:
            with ui.HStack(height=18, spacing=6) as row:
                ui.Spacer(width=6)
                spinner_lbl = ui.Label(SPINNER_GLYPHS[0], width=14,
                                       style={"color": COL_NV_GREEN, "font_size": 13})
                verb_lbl = ui.Label(verb, width=0,
                                    style={"color": COL_TEXT, "font_size": 12},
                                    tooltip=verb_tooltip)
                args_lbl = ui.Label(args_preview, width=0,
                                    style={"color": COL_TEXT_DIM, "font_size": 11},
                                    tooltip=full_args_json)
                ui.Spacer()
                elapsed_lbl = ui.Label("0.0s", width=40,
                                       style={"color": COL_TEXT_SUBTLE, "font_size": 10})
                ui.Spacer(width=4)
        self._live_rows[tc_id] = {
            "row": row,
            "spinner_lbl": spinner_lbl,
            "verb_lbl": verb_lbl,
            "args_lbl": args_lbl,
            "elapsed_lbl": elapsed_lbl,
            "started_at": time.monotonic(),
            "state": "running",
            "tool": tool,
            "attempts": 1,
            "tc_id": tc_id,
        }
        # Fade in
        asyncio.ensure_future(anim.fade_in_widget(row, "background_color",
                                                  COL_BG_LIVE_STRIP | 0xFF000000, ms=150))
        self._bump_strip_height()

    def _find_recyclable_row(self, tool: str) -> Optional[dict]:
        # Return most-recent failed row for this tool, if any in the current strip
        candidates = [r for r in self._live_rows.values()
                      if r.get("tool") == tool and r.get("state") == "done_fail"]
        if not candidates:
            return None
        return max(candidates, key=lambda r: r["started_at"])

    def _bump_strip_height(self):
        n = sum(1 for r in self._live_rows.values() if r["row"].visible)
        # 14 (header) + 18 per row + 8 padding
        self.live_strip_container.height = ui.Pixel(14 + n * 18 + 8)

    async def _tick_loop(self):
        """One coroutine drives spinner glyph + elapsed time for all rows."""
        i = 0
        while not self._destroyed:
            i += 1
            glyph = SPINNER_GLYPHS[i % len(SPINNER_GLYPHS)]
            now = time.monotonic()
            for r in list(self._live_rows.values()):
                if r["state"] != "running":
                    continue
                r["spinner_lbl"].text = glyph
                elapsed = now - r["started_at"]
                if r.get("elapsed_lbl") is not r.get("verb_lbl"):
                    r["elapsed_lbl"].text = f"{elapsed:.1f}s"
                # Color escalation on slow tools
                if elapsed > VERY_SLOW_THRESHOLD_S:
                    r["spinner_lbl"].style = {"color": COL_AMBER, "font_size": 13}
                elif elapsed > SLOW_THRESHOLD_S:
                    r["spinner_lbl"].style = {"color": COL_AMBER_DIM, "font_size": 13}
            await asyncio.sleep(SPIN_INTERVAL_S)

    # ═══════════════════════════════════════════════════════════════════════
    # Bubble rendering
    # ═══════════════════════════════════════════════════════════════════════
    def _add_user_bubble(self, text: str):
        with self.chat_layout:
            with ui.HStack():
                ui.Spacer(width=24)  # right-indent so user msgs are visually distinct
                with ui.ZStack():
                    ui.Rectangle(style={"background_color": COL_BG_USER, "border_radius": 6})
                    with ui.VStack():
                        ui.Spacer(height=4)
                        with ui.HStack():
                            ui.Spacer(width=8)
                            ui.Label("You", width=0,
                                    style={"color": COL_TEXT_SUBTLE, "font_size": 10})
                            ui.Spacer()
                            # Re-run button
                            ui.Button("↻", width=18, height=14,
                                     clicked_fn=lambda t=text: self._rerun(t),
                                     style={"color": COL_TEXT_SUBTLE, "font_size": 10,
                                            "background_color": 0x00000000})
                            ui.Spacer(width=4)
                        with ui.HStack():
                            ui.Spacer(width=8)
                            ui.Label(text, word_wrap=True,
                                    style={"color": COL_TEXT, "font_size": 12})
                            ui.Spacer(width=8)
                        ui.Spacer(height=4)

    def _add_assistant_bubble(self, text: str, error: bool = False,
                              snapshot_live_rows: bool = False) -> Optional[Dict]:
        snapshot = []
        if snapshot_live_rows:
            for tc_id, r in self._live_rows.items():
                if tc_id == "__thinking__":
                    continue
                snapshot.append({
                    "tool": r.get("tool"),
                    "verb": verb_for(r.get("tool", "")),
                    "state": r.get("state"),
                    "attempts": r.get("attempts", 1),
                    "elapsed": r["elapsed_lbl"].text if r.get("elapsed_lbl") else "",
                })

        bubble_refs: Dict = {}
        with self.chat_layout:
            with ui.ZStack() as outer:
                # Border rect for pulse animation (sits behind body)
                bubble_refs["border_rect"] = ui.Rectangle(style={
                    "background_color": COL_BORDER_NEUTRAL,
                    "border_radius": 8,
                })
                with ui.VStack():
                    ui.Spacer(height=2)
                    with ui.HStack():
                        ui.Spacer(width=2)
                        with ui.ZStack():
                            # Inner background — this is what dims when bubble is undone
                            bubble_refs["inner_bg_rect"] = ui.Rectangle(
                                style={"background_color": COL_BG_ASSIST,
                                       "border_radius": 6})
                            with ui.VStack():
                                ui.Spacer(height=4)
                                with ui.HStack():
                                    ui.Spacer(width=8)
                                    bubble_refs["header_lbl"] = ui.Label(
                                        "Isaac Assist", width=0,
                                        style={"color": COL_TEXT_SUBTLE, "font_size": 10})
                                    ui.Spacer()
                                # Steps section (foldable)
                                if snapshot:
                                    self._build_steps_section(snapshot)
                                # Body
                                with ui.HStack():
                                    ui.Spacer(width=8)
                                    body_color = COL_RED if error else COL_TEXT
                                    bubble_refs["body_lbl"] = ui.Label(
                                        text, word_wrap=True,
                                        style={"color": body_color, "font_size": 12})
                                    ui.Spacer(width=8)
                                # Diff chip placeholder (filled later by _attach_diff_chip
                                # and _attach_undo_button — both write into this slot)
                                bubble_refs["diff_slot"] = ui.VStack(spacing=0, height=0)
                                ui.Spacer(height=4)
                        ui.Spacer(width=24)  # left-aligned bias
                    ui.Spacer(height=2)
        return bubble_refs

    def _build_steps_section(self, snapshot: List[dict]):
        is_expanded = [False]
        n = len(snapshot)
        with ui.HStack(height=18):
            ui.Spacer(width=8)
            toggle_btn = ui.Button(f"▸ {n} step{'s' if n != 1 else ''}",
                                   width=80, height=16,
                                   style={"color": COL_TEXT_DIM, "font_size": 10,
                                          "background_color": 0x00000000})
            ui.Spacer()
        rows_container = ui.VStack(spacing=1, height=0)
        with rows_container:
            for s in snapshot:
                with ui.HStack(height=14):
                    ui.Spacer(width=18)
                    icon = "✓" if s["state"] == "done_ok" else ("✗" if s["state"] == "done_fail" else "·")
                    icon_col = COL_NV_GREEN if s["state"] == "done_ok" else (COL_RED if s["state"] == "done_fail" else COL_TEXT_DIM)
                    ui.Label(icon, width=12, style={"color": icon_col, "font_size": 11})
                    label_text = s["verb"]
                    if s["attempts"] > 1:
                        label_text += f" ×{s['attempts']}"
                    ui.Label(label_text, style={"color": COL_TEXT_DIM, "font_size": 11})
                    ui.Spacer()
                    ui.Label(s["elapsed"], width=36,
                           style={"color": COL_TEXT_SUBTLE, "font_size": 10})

        def toggle():
            is_expanded[0] = not is_expanded[0]
            rows_container.height = ui.Pixel(0 if not is_expanded[0] else n * 15 + 4)
            toggle_btn.text = ("▾" if is_expanded[0] else "▸") + f" {n} step{'s' if n != 1 else ''}"

        toggle_btn.set_clicked_fn(toggle)

    def _attach_diff_chip(self, bubble: dict, diff: dict):
        if not bubble or "diff_slot" not in bubble:
            return
        if diff.get("total_changes", 0) == 0:
            return
        slot = bubble["diff_slot"]
        slot.clear()
        slot.height = ui.Pixel(20)
        with slot:
            with ui.HStack(height=18):
                ui.Spacer(width=8)
                added = diff.get("added_paths", [])
                rem = diff.get("removed_paths", [])
                mod = diff.get("modified_paths", [])
                parts = []
                if added: parts.append(f"+{len(added)} added")
                if rem:   parts.append(f"−{len(rem)} removed")
                if mod:   parts.append(f"~{len(mod)} modified")
                full_paths = "Added:\n  " + "\n  ".join(added[:8]) if added else ""
                if rem:
                    full_paths += "\nRemoved:\n  " + "\n  ".join(rem[:8])
                if mod:
                    full_paths += "\nModified:\n  " + "\n  ".join(mod[:8])
                # Store ref so dim-on-undo can recolor it
                bubble["diff_chip_lbl"] = ui.Label(
                    "Changed: " + " ".join(parts),
                    style={"color": COL_NV_GREEN, "font_size": 10},
                    tooltip=full_paths)
                ui.Spacer()

    # ═══════════════════════════════════════════════════════════════════════
    # Empty-state chips, re-run, soft confirm
    # ═══════════════════════════════════════════════════════════════════════
    def _hide_chips(self):
        if self._chips_shown:
            self.chips_container.visible = False
            self.chips_container.height = ui.Pixel(0)
            self._chips_shown = False

    def _on_chip(self, text: str):
        self.input_field.model.set_value(text)

    def _rerun(self, text: str):
        self.input_field.model.set_value(text)

    def _on_new_scene_clicked(self):
        if not self._new_scene_confirm:
            self._new_scene_confirm = True
            self.btn_new.text = "Confirm?"
            self.btn_new.style = {"font_size": 11, "color": COL_AMBER}
            asyncio.ensure_future(self._reset_new_scene_after(3.0))
        else:
            self._new_scene_confirm = False
            self.btn_new.text = "New"
            self.btn_new.style = {"font_size": 11}
            asyncio.ensure_future(self._do_new_scene())

    async def _reset_new_scene_after(self, sec: float):
        await asyncio.sleep(sec)
        if self._new_scene_confirm:
            self._new_scene_confirm = False
            self.btn_new.text = "New"
            self.btn_new.style = {"font_size": 11}

    async def _do_new_scene(self):
        try:
            import omni.usd
            omni.usd.get_context().new_stage()
        except Exception as e:
            logger.error(f"new_stage failed: {e}")
        try:
            await self.service.reset_session()
        except Exception as e:
            logger.warning(f"reset_session failed: {e}")
        self.chat_layout.clear()
        self._chips_shown = True
        self.chips_container.visible = True
        self.chips_container.height = ui.Pixel(22)
        self._collapse_live_strip()
        self._undoable_bubbles = []

    # ═══════════════════════════════════════════════════════════════════════
    # Undo & Clear chat
    # ═══════════════════════════════════════════════════════════════════════
    # Snapshot stack lives on disk in turn_snapshot.py — UI just tracks
    # which bubbles correspond to undoable turns, in order. The latest
    # entry is the only one with a visible ↶ button. On undo_applied, we
    # pop and dim, then attach the button to the new latest entry.
    def _transfer_undo_button(self, new_bubble: dict, diff: dict):
        # Remove button from the previous-latest undoable bubble
        if self._undoable_bubbles:
            prev = self._undoable_bubbles[-1]
            self._remove_undo_button(prev)
        new_bubble["diff_summary"] = diff
        new_bubble["undo_state"] = "idle"
        self._attach_undo_button(new_bubble)
        self._undoable_bubbles.append(new_bubble)

    def _attach_undo_button(self, bubble: dict):
        slot = bubble.get("diff_slot")
        if not slot:
            return
        # The diff chip is already in `slot`. We append a button to the same row.
        # Keep handles so we can mutate text/style and remove later.
        with slot:
            with ui.HStack(height=18) as undo_row:
                ui.Spacer()
                btn = ui.Button("↶", width=22, height=16,
                               clicked_fn=lambda b=bubble: self._on_undo_clicked(b),
                               style={"font_size": 11, "color": COL_TEXT_DIM,
                                      "background_color": 0x00000000},
                               tooltip=self._undo_tooltip(bubble.get("diff_summary", {})))
                ui.Spacer(width=4)
        bubble["undo_btn"] = btn
        bubble["undo_row"] = undo_row

    def _undo_tooltip(self, diff: dict) -> str:
        a = diff.get("added_paths", [])
        r = diff.get("removed_paths", [])
        m = diff.get("modified_paths", [])
        parts = []
        if a: parts.append(f"+{len(a)} added")
        if r: parts.append(f"−{len(r)} removed")
        if m: parts.append(f"~{len(m)} modified")
        head = "Undo this turn — will revert: " + " ".join(parts) if parts else "Undo this turn"
        if a: head += "\n\nAdded:\n  " + "\n  ".join(a[:8])
        if r: head += "\n\nRemoved:\n  " + "\n  ".join(r[:8])
        if m: head += "\n\nModified:\n  " + "\n  ".join(m[:8])
        return head

    def _remove_undo_button(self, bubble: dict):
        # omni.ui has no clean "remove single child" — hide the button row instead
        row = bubble.get("undo_row")
        if row:
            row.visible = False
            row.height = ui.Pixel(0)

    def _on_undo_clicked(self, bubble: dict):
        if self._turn_active:
            return  # never undo during a live turn
        state = bubble.get("undo_state", "idle")
        btn = bubble.get("undo_btn")
        if state == "idle":
            bubble["undo_state"] = "confirm"
            btn.text = "Undo?"
            btn.style = {"font_size": 11, "color": COL_AMBER, "background_color": 0x00000000}
            btn.width = ui.Pixel(48)
            asyncio.ensure_future(self._reset_undo_after(bubble, 3.0))
        elif state == "confirm":
            bubble["undo_state"] = "pending"
            btn.text = "…"
            btn.style = {"font_size": 11, "color": COL_TEXT_SUBTLE, "background_color": 0x00000000}
            btn.enabled = False
            asyncio.ensure_future(self._do_undo())

    async def _reset_undo_after(self, bubble: dict, sec: float):
        await asyncio.sleep(sec)
        if bubble.get("undo_state") == "confirm":
            bubble["undo_state"] = "idle"
            btn = bubble.get("undo_btn")
            if btn:
                btn.text = "↶"
                btn.style = {"font_size": 11, "color": COL_TEXT_DIM,
                            "background_color": 0x00000000}
                btn.width = ui.Pixel(22)

    async def _do_undo(self):
        # Show progress in the live strip
        self._show_live_strip()
        with self.live_rows_layout:
            with ui.HStack(height=18, spacing=6) as row:
                ui.Spacer(width=6)
                ui.Label("⠋", width=14, style={"color": COL_AMBER, "font_size": 13})
                ui.Label("Reverting…", style={"color": COL_TEXT_DIM, "font_size": 12})
        self._undo_progress_row = row
        result = await self.service.undo_turn(steps=1)
        # Server emits undo_applied/undo_failed via SSE — handlers below take over.
        # If POST returned an error and SSE hasn't fired (e.g. service down), surface it here.
        if not result.get("ok") and not getattr(self, "_undo_handled_via_sse", False):
            self._on_undo_failed({"error": result.get("error", "unknown")})

    def _on_undo_started(self, payload):
        # Already showing "Reverting…" from _do_undo; nothing more needed.
        # If undo was triggered via slash command (/undo from chat), this
        # event still fires — make sure live strip shows progress.
        if not getattr(self, "_undo_progress_row", None):
            self._show_live_strip()
            with self.live_rows_layout:
                with ui.HStack(height=18, spacing=6) as row:
                    ui.Spacer(width=6)
                    ui.Label("⠋", width=14, style={"color": COL_AMBER, "font_size": 13})
                    ui.Label("Reverting…", style={"color": COL_TEXT_DIM, "font_size": 12})
            self._undo_progress_row = row

    def _on_undo_applied(self, payload):
        self._undo_handled_via_sse = True
        steps = payload.get("steps", 1)
        # Pop and dim N most-recent undoable bubbles
        for _ in range(min(steps, len(self._undoable_bubbles))):
            popped = self._undoable_bubbles.pop()
            self._dim_bubble_as_undone(popped)
        # Attach button to new latest, if any
        if self._undoable_bubbles:
            self._attach_undo_button(self._undoable_bubbles[-1])
        # Clear the live strip's "Reverting…" indicator
        asyncio.ensure_future(self._collapse_undo_progress())

    def _on_undo_failed(self, payload):
        self._undo_handled_via_sse = True
        err = payload.get("error", "unknown error")
        # Reset the most-recent bubble's button to idle if it was pending
        if self._undoable_bubbles:
            b = self._undoable_bubbles[-1]
            b["undo_state"] = "idle"
            btn = b.get("undo_btn")
            if btn:
                btn.text = "↶"
                btn.enabled = True
                btn.style = {"font_size": 11, "color": COL_RED,
                            "background_color": 0x00000000}
                btn.tooltip = f"Undo failed: {err}\nClick to retry."
        # Surface in live strip
        if getattr(self, "_undo_progress_row", None):
            self._undo_progress_row.clear()
            with self._undo_progress_row:
                ui.Spacer(width=6)
                ui.Label("✗", width=14, style={"color": COL_RED, "font_size": 13})
                ui.Label(f"Undo failed: {err[:50]}",
                        style={"color": COL_RED, "font_size": 12})
        asyncio.ensure_future(self._collapse_undo_progress(delay=3.0))

    async def _collapse_undo_progress(self, delay: float = 0.5):
        await asyncio.sleep(delay)
        self._collapse_live_strip()
        self._undo_progress_row = None
        self._undo_handled_via_sse = False

    def _dim_bubble_as_undone(self, bubble: dict):
        """Dim the bubble visually and tag it as undone."""
        # Dim the inner background rectangle to ~50%
        for key in ("inner_bg_rect",):
            r = bubble.get(key)
            if r:
                r.style = {"background_color": 0x801E2125, "border_radius": 6}
        # Dim text labels stored in bubble refs
        for key in ("body_lbl", "header_lbl"):
            lbl = bubble.get(key)
            if lbl:
                cur = lbl.style or {}
                lbl.style = {**cur, "color": COL_TEXT_DIM}
        # Append "(undone)" to header
        hl = bubble.get("header_lbl")
        if hl:
            hl.text = "Isaac Assist (undone)"
        # Recolor the diff chip and remove undo button
        chip = bubble.get("diff_chip_lbl")
        if chip:
            chip.style = {"color": COL_TEXT_DIM, "font_size": 10}
        self._remove_undo_button(bubble)

    # ── Clear chat ────────────────────────────────────────────────────────
    def _on_clear_chat_clicked(self):
        if not self._clear_chat_confirm:
            self._clear_chat_confirm = True
            self.btn_clear.text = "Confirm?"
            self.btn_clear.style = {"font_size": 11, "color": COL_AMBER}
            asyncio.ensure_future(self._reset_clear_chat_after(3.0))
        else:
            self._clear_chat_confirm = False
            self.btn_clear.text = "Clear"
            self.btn_clear.style = {"font_size": 11}
            asyncio.ensure_future(self._do_clear_chat())

    async def _reset_clear_chat_after(self, sec: float):
        await asyncio.sleep(sec)
        if self._clear_chat_confirm:
            self._clear_chat_confirm = False
            self.btn_clear.text = "Clear"
            self.btn_clear.style = {"font_size": 11}

    async def _do_clear_chat(self):
        try:
            await self.service.clear_chat()
        except Exception as e:
            logger.warning(f"clear_chat failed: {e}")
        self.chat_layout.clear()
        self._undoable_bubbles = []
        self._chips_shown = True
        self.chips_container.visible = True
        self.chips_container.height = ui.Pixel(22)
        self._collapse_live_strip()

    # ═══════════════════════════════════════════════════════════════════════
    # LiveKit toggle (existing functionality, copied)
    # ═══════════════════════════════════════════════════════════════════════
    def _toggle_livekit(self):
        if self.webrtc and self.webrtc._streaming:
            self.btn_livekit.text = "Vision"
            asyncio.ensure_future(self.webrtc.disconnect())
        else:
            self.btn_livekit.text = "Stop"
            url = os.environ.get("LIVEKIT_URL", "ws://localhost:7880")
            key = os.environ.get("LIVEKIT_API_KEY", "devkey")
            secret = os.environ.get("LIVEKIT_API_SECRET", "secret")
            if not self.webrtc:
                self.webrtc = ViewportWebRTCClient(url, key, secret)
            asyncio.ensure_future(self.webrtc.connect_and_publish())

    # ═══════════════════════════════════════════════════════════════════════
    # Lifecycle
    # ═══════════════════════════════════════════════════════════════════════
    def destroy(self):
        self._destroyed = True
        if self._spin_task:
            self._spin_task.cancel()
        self.service.stop_stream()
        if self.webrtc:
            asyncio.ensure_future(self.webrtc.disconnect())
        super().destroy()
```

### MODIFY: `exts/isaac_6.0/omni.isaac.assist/omni/isaac/assist/extension.py`

Generate per-extension session ID at startup and pass to ChatViewWindow.

```python
import omni.ext
import logging
import uuid
from .ui import ChatViewWindow

logger = logging.getLogger(__name__)


class IsaacAssistExtension(omni.ext.IExt):
    def on_startup(self, ext_id):
        logger.info("[omni.isaac.assist] startup")
        self._window = ChatViewWindow("Isaac Assist", width=420, height=620)

    def on_shutdown(self):
        logger.info("[omni.isaac.assist] shutdown")
        if self._window:
            self._window.destroy()
            self._window = None
```

### ADDITIONS to `chat_view.py` — text/UI scaling subsystem

The whole chat window scales to one of seven discrete steps. Scale is persisted via Kit's settings system (`/persistent/...` keys auto-save on shutdown). Three control surfaces: header `Aa` button, right-click context menu in chat area, keyboard shortcuts. Scale changes trigger a full UI rebuild — chat history is preserved by re-rendering from a stored data list.

```python
import carb.settings
import carb.input
import omni.appwindow

# ── Scale steps ──────────────────────────────────────────────────────────
SCALE_STEPS  = [0.80, 0.90, 1.00, 1.10, 1.25, 1.50, 1.75]
SCALE_LABELS = ["80%", "90%", "100%", "110%", "125%", "150%", "175%"]
DEFAULT_SCALE_INDEX = 2
SCALE_SETTING_KEY = "/persistent/exts/omni.isaac.assist/text_scale_index"


class ChatViewWindow(ui.Window):
    # ── Add to __init__ before _build_ui() ───────────────────────────────
    def __init__(self, title: str, **kwargs):
        super().__init__(title, **kwargs)
        # ... existing service / state init ...

        # Scale state
        self._settings = carb.settings.get_settings()
        self._scale_index = self._load_scale_index()
        self._scale = SCALE_STEPS[self._scale_index]

        # Chat history — data-driven so we can re-render on scale change.
        # Each entry: {role, text, diff?, undo_state?, snapshot_id?, ts}
        self._chat_history: List[Dict] = []

        self._build_ui()
        self._register_keyboard_shortcuts()
        # ... rest of init ...

    # ── Helpers — wrap raw constants with scale ──────────────────────────
    def _sz(self, n) -> int:
        """Scale a numeric value (font size, px width/height) by current scale."""
        return max(1, int(round(n * self._scale)))

    def _px(self, n):
        """Scaled Pixel value."""
        return ui.Pixel(self._sz(n))

    def _ss(self, style: dict) -> dict:
        """Scale size-related keys in a style dict (font_size, border_radius)."""
        out = dict(style or {})
        for k in ("font_size", "border_radius"):
            if k in out:
                out[k] = self._sz(out[k])
        return out

    # ── Persistence ──────────────────────────────────────────────────────
    def _load_scale_index(self) -> int:
        idx = self._settings.get(SCALE_SETTING_KEY)
        if idx is None or not isinstance(idx, int) or not (0 <= idx < len(SCALE_STEPS)):
            return DEFAULT_SCALE_INDEX
        return idx

    def _save_scale_index(self, idx: int):
        self._settings.set(SCALE_SETTING_KEY, idx)

    # ── Scale change ─────────────────────────────────────────────────────
    def _change_scale(self, delta: int):
        """delta = +1 / -1 / 0 (reset). No-op if a turn is in flight."""
        if self._turn_active:
            return
        if delta == 0:
            new_idx = DEFAULT_SCALE_INDEX
        else:
            new_idx = max(0, min(len(SCALE_STEPS) - 1, self._scale_index + delta))
        if new_idx == self._scale_index:
            return  # already at edge or default
        self._scale_index = new_idx
        self._scale = SCALE_STEPS[new_idx]
        self._save_scale_index(new_idx)
        self._rebuild_ui()

    def _rebuild_ui(self):
        """Tear down the UI and rebuild it from scratch + chat history.

        Forbidden during a live turn — `_change_scale` already gates this.
        Preserves: scroll position, input field text, chat history, scale.
        Resets: live_rows, undoable_bubbles refs (regenerated from history).
        """
        # Save preserved state
        try:
            scroll_y = self.scroll.scroll_y
        except Exception:
            scroll_y = 0
        try:
            input_text = self.input_field.model.get_value_as_string()
        except Exception:
            input_text = ""
        history_snapshot = list(self._chat_history)  # data only, no widgets

        # Tear down
        self.frame.clear()
        self._live_rows.clear()
        self._undoable_bubbles = []

        # Rebuild
        self._build_ui()

        # Restore
        try:
            self.input_field.model.set_value(input_text)
        except Exception:
            pass
        for entry in history_snapshot:
            self._render_history_entry(entry)
        self._chat_history = history_snapshot  # preserved
        try:
            self.scroll.scroll_y = scroll_y
        except Exception:
            pass

    # ── Chat history: data-driven rendering ──────────────────────────────
    # Every _add_user_bubble / _add_assistant_bubble call ALSO appends to
    # self._chat_history. _render_history_entry replays one entry into
    # widgets without re-appending. This split is what makes rebuild safe.
    def _record_history(self, entry: dict):
        self._chat_history.append(entry)

    def _render_history_entry(self, entry: dict):
        role = entry.get("role")
        if role == "user":
            self._render_user_bubble(entry["text"], record=False)
        elif role == "assistant":
            bubble = self._render_assistant_bubble(
                entry["text"],
                error=entry.get("error", False),
                steps_snapshot=entry.get("steps", []),
                record=False,
            )
            diff = entry.get("diff")
            if diff and bubble:
                self._attach_diff_chip(bubble, diff)
                if entry.get("undo_state") == "available" and entry.get("has_snapshot"):
                    self._undoable_bubbles.append(bubble)
                    self._attach_undo_button(self._undoable_bubbles[-1])
                elif entry.get("undo_state") == "undone":
                    self._dim_bubble_as_undone(bubble)

    # _add_user_bubble and _add_assistant_bubble are renamed to
    # _render_user_bubble / _render_assistant_bubble; they accept a
    # `record: bool = True` flag. The original call sites pass record=True;
    # _render_history_entry passes False to avoid double-recording.

    # ── Header `Aa` button ───────────────────────────────────────────────
    # Add to _build_header(), between btn_clear and btn_livekit:
    #   self.btn_scale = ui.Button("Aa", width=self._px(24), height=self._px(22),
    #                              clicked_fn=self._open_scale_popup,
    #                              style=self._ss({"font_size": 11}),
    #                              tooltip=f"Text size: {SCALE_LABELS[self._scale_index]}")

    def _open_scale_popup(self):
        """Small floating popup with [A−] [label] [A+] [Reset]."""
        # Positioned just below the Aa button. omni.ui.Window with no chrome.
        if getattr(self, "_scale_popup", None):
            self._scale_popup.visible = True
            return
        self._scale_popup = ui.Window(
            "Text size", width=180, height=80,
            flags=ui.WINDOW_FLAGS_NO_TITLE_BAR | ui.WINDOW_FLAGS_NO_RESIZE | ui.WINDOW_FLAGS_NO_MOVE,
        )
        with self._scale_popup.frame:
            with ui.VStack(spacing=self._sz(4)):
                ui.Label("Text size", style=self._ss({"color": COL_TEXT_DIM, "font_size": 10}))
                with ui.HStack(spacing=self._sz(6)):
                    ui.Button("A−", width=self._px(28), clicked_fn=lambda: self._change_scale(-1),
                             style=self._ss({"font_size": 12}))
                    self._scale_lbl = ui.Label(SCALE_LABELS[self._scale_index],
                                              style=self._ss({"color": COL_TEXT, "font_size": 12}),
                                              alignment=ui.Alignment.CENTER)
                    ui.Button("A+", width=self._px(28), clicked_fn=lambda: self._change_scale(1),
                             style=self._ss({"font_size": 12}))
                ui.Button("Reset", clicked_fn=lambda: self._change_scale(0),
                         style=self._ss({"font_size": 11}))

    # ── Right-click context menu ─────────────────────────────────────────
    # Wire to chat area in _build_chat_area:
    #   self.scroll.set_mouse_pressed_fn(self._on_chat_mouse_pressed)
    def _on_chat_mouse_pressed(self, x, y, button, modifier):
        if button == 1:  # right-click
            self._show_zoom_menu()

    def _show_zoom_menu(self):
        self._zoom_menu = ui.Menu("zoom_menu")
        with self._zoom_menu:
            ui.MenuItem("Zoom in", triggered_fn=lambda: self._change_scale(1))
            ui.MenuItem("Zoom out", triggered_fn=lambda: self._change_scale(-1))
            ui.Separator()
            ui.MenuItem("Reset to default", triggered_fn=lambda: self._change_scale(0))
        self._zoom_menu.show()

    # ── Keyboard shortcuts ───────────────────────────────────────────────
    # Registers Ctrl+= / Ctrl+- / Ctrl+0 via carb input subscription.
    # The subscription is window-scoped and only fires while the chat
    # window has focus — verify with omni.appwindow API on Isaac 6.0.
    def _register_keyboard_shortcuts(self):
        try:
            iface = carb.input.acquire_input_interface()
            app_win = omni.appwindow.get_default_app_window()
            kbd = app_win.get_keyboard()

            def on_event(evt, *_):
                if evt.type != carb.input.KeyboardEventType.KEY_PRESS:
                    return True
                if not (evt.modifiers & carb.input.KEYBOARD_MODIFIER_FLAG_CONTROL):
                    return True
                if not self._window_has_focus():
                    return True
                if evt.input == carb.input.KeyboardInput.EQUAL:
                    self._change_scale(1)
                elif evt.input == carb.input.KeyboardInput.MINUS:
                    self._change_scale(-1)
                elif evt.input == carb.input.KeyboardInput.KEY_0:
                    self._change_scale(0)
                return True

            self._kbd_sub = iface.subscribe_to_keyboard_events(kbd, on_event)
        except Exception as e:
            logger.warning(f"Keyboard shortcut registration failed: {e}")
            self._kbd_sub = None

    def _window_has_focus(self) -> bool:
        # ui.Window has a `focused` property in omni.ui 6.0; verify on first run.
        return getattr(self, "focused", True)

    # destroy() must release the input subscription:
    def destroy(self):
        if getattr(self, "_kbd_sub", None):
            try:
                iface = carb.input.acquire_input_interface()
                iface.unsubscribe_to_keyboard_events(self._kbd_sub)
            except Exception:
                pass
        # ... existing destroy logic ...
        super().destroy()
```

**Mechanical conversion across all `_build_*` methods.** Every hardcoded size becomes scale-aware:

```python
# Before:
ui.Label("Isaac Assist", width=0, style={"color": COL_TEXT, "font_size": 13})
ui.Button("Send", width=64, height=24, style={"font_size": 12})
ui.Spacer(height=8)

# After:
ui.Label("Isaac Assist", width=0, style=self._ss({"color": COL_TEXT, "font_size": 13}))
ui.Button("Send", width=self._px(64), height=self._px(24), style=self._ss({"font_size": 12}))
ui.Spacer(height=self._px(8))
```

Apply this pattern to every widget construction in `_build_header`, `_build_chat_area`, `_build_live_strip`, `_build_chips`, `_build_input`, `_render_user_bubble`, `_render_assistant_bubble`, `_make_live_row`, `_attach_diff_chip`, `_attach_undo_button`, `_build_steps_section`. The conversion is mechanical (~80 lines of diff) — review by skimming for any size literal that escaped wrapping.

**`record=True` flag added to bubble rendering.** `_render_user_bubble(text, record=True)` and `_render_assistant_bubble(text, ..., record=True)` append to `_chat_history` when called from real turn handlers; `_render_history_entry` calls them with `record=False` during rebuild to avoid double-recording.

---

## Color palette (definitive)

omni.ui colors are ABGR (`0xAABBGGRR`). All values used:

| Constant            | Hex (ABGR)   | RGB equivalent  | Use                                      |
|---------------------|--------------|-----------------|------------------------------------------|
| `COL_BG_USER`       | `0xFF2A2E33` | `#33 2E 2A`     | User message bubble background            |
| `COL_BG_ASSIST`     | `0xFF1E2125` | `#25 21 1E`     | Assistant message bubble background       |
| `COL_BG_LIVE_STRIP` | `0xFF181A1D` | `#1D 1A 18`     | Live strip background                     |
| `COL_TEXT`          | `0xFFDDDDDD` | `#DD DD DD`     | Primary text                              |
| `COL_TEXT_DIM`      | `0xFF8A8E92` | `#92 8E 8A`     | Secondary text, args, dimmed past steps   |
| `COL_TEXT_SUBTLE`   | `0xFF666A6E` | `#6E 6A 66`     | Labels, timestamps, fine print            |
| `COL_NV_GREEN`      | `0xFF00B976` | `#76 B9 00`     | Active spinner, success ✓, diff chip      |
| `COL_AMBER`         | `0xFF00A8FF` | `#FF A8 00`     | Slow-tool spinner, Stop button, warnings  |
| `COL_AMBER_DIM`     | `0xFF0078B0` | `#B0 78 00`     | Slow-tool spinner (10-20s threshold)      |
| `COL_RED`           | `0xFF4444FF` | `#FF 44 44`     | Errors, failed tool ✗                     |

**Discipline:** NVIDIA green is used ONLY for active spinner, the assistant-bubble border pulse on completion, and the diff chip text. Past successes fade to `COL_TEXT_DIM` grey. Saving green for the live frontier prevents the wall-of-green effect.

---

## Animation budget

Four animations ship in v1. No others.

| Animation                         | Duration | Trigger                              | Implementation                |
|-----------------------------------|----------|--------------------------------------|-------------------------------|
| Live row fade-in (alpha 0→FF)     | 150 ms   | `tool_call_started`                  | `anim.fade_in_widget`         |
| Spinner glyph cycle               | 200 ms   | continuous while row state=running   | `_tick_loop`                  |
| Completion color (green→grey)     | 400 ms   | `tool_call_finished` with success    | `anim.lerp_color` × 2 (spinner+verb) |
| Assistant bubble border pulse     | 300+700  | `agent_reply` SSE event              | `anim.pulse_widget`           |

**Explicitly NOT animated:**
- Send/Stop button mutation — instant swap reads as responsive
- Strip height changes — let layout snap; animating reads as the panel "breathing"
- Typewriter on assistant text — feels artificial when not actually streaming
- Hover states — peripheral hover animation is the worst kind of motion noise

---

## UX behavior table

| Moment                              | UI state                                                                       |
|-------------------------------------|--------------------------------------------------------------------------------|
| Empty window, never used            | Empty chat area + 3 suggestion chips visible above input                       |
| User typing                         | Input shows text; Send enabled                                                 |
| User clicks Send                    | User bubble appears; chips hide; Send→Stop; live strip slides in with `Thinking…` |
| LLM call latency (1-8s)             | `Thinking…` row, spinner cycling                                               |
| First tool fires                    | `Thinking…` row hidden; first tool row materializes (150ms fade-in)            |
| Tool running                        | Spinner cycling, elapsed counter ticking                                       |
| Tool slow (>10s)                    | Spinner color shifts amber-dim                                                 |
| Tool very slow (>20s)               | Spinner color shifts full amber                                                |
| Tool succeeds                       | Spinner→✓ green, then over 400ms transitions to dim grey                       |
| Tool fails                          | Spinner→✗ red, args label shows truncated error in red, stays bright          |
| Same tool retried after fail        | Existing failed row recycles, attempts counter increments, spinner restarts    |
| Spam halt fires (≥6 fails)          | `⚠ Stopped after N attempts` row appended to strip in amber                    |
| User clicks Stop                    | Send→Stopping; `cancel_turn` POST sent; button disabled                        |
| Cancel acknowledged by orchestrator | `■ Stopped by user` row appended                                               |
| Agent reply arrives via SSE         | Assistant bubble rendered with text + collapsed steps section + border pulse   |
| Diff event arrives                  | `Changed: +N added −N removed` chip attached below assistant bubble            |
| Reply arrives via POST only (SSE dropped) | Bubble rendered from POST; no steps section, no diff chip                |
| Turn ends                           | Live strip clears (height=0); Stop→Send                                        |
| User hovers a `You` bubble          | Re-run ↻ visible (always-on for v1; hover-detection later)                     |
| User clicks New                     | Button→Confirm? for 3s; second click within 3s wipes; otherwise reverts        |
| User clicks Clear                   | Same soft-confirm as New; on confirm: wipes chat history, KEEPS stage and snapshots |
| Mutating turn completes (with snapshot) | `↶` button appears on the diff chip row of the new bubble                  |
| User clicks `↶`                     | Button mutates to `Undo?` for 3s; live strip ready                             |
| User clicks `↶` a second time within 3s | Button→`…` disabled; live strip shows `⠋ Reverting…`; POST /chat/undo fires |
| Undo succeeds                       | The latest mutating bubble dims to ~50% opacity, header gets "(undone)", diff chip greys, `↶` removed; previous mutating bubble grows a `↶`; live strip clears |
| Undo fails (Kit error, no snapshot) | `↶` button text restored, color shifts red, tooltip shows error; live strip shows `✗ Undo failed: ...` for 3s then clears |
| `/undo` slash command from chat     | Same UI behavior as button — server emits `undo_applied`; UI dims latest bubble |
| `/undo N` slash command             | UI pops and dims N bubbles in sequence; only the new latest gets `↶`           |
| Snapshot save failed for a turn     | No `↶` button ever appears on that bubble (gated on `has_snapshot` from agent_reply) |
| User clicks `↶` while another turn is in flight | Click ignored; button visible but inert until turn ends                  |
| User clicks `Aa` in header          | Small popup opens with `[A−] 100% [A+] [Reset]`; updates live as buttons clicked |
| User presses `Ctrl+=` / `Ctrl+-`    | Scale advances/retreats one discrete step; UI rebuilds; popup label updates if open |
| User presses `Ctrl+0`               | Scale resets to 100%; UI rebuilds                                              |
| User right-clicks chat area         | Context menu: Zoom in / Zoom out / —— / Reset to default                       |
| User changes scale during a turn    | All scale controls inert (button disabled, shortcuts no-op); changes allowed once turn ends |
| User reopens Isaac Sim              | Last scale persists (loaded from `/persistent/.../text_scale_index`)           |
| User at scale 175% — header overflows | `ui.Spacer()` between header buttons compresses; verify visually at each step |
| SSE drops mid-turn                  | Connection dot turns amber and slow-pulses; reconnects with backoff            |
| SSE permanently dead                | Connection dot turns red; UI works via POST only                               |

---

## Edge cases & failure modes

**E1: SSE connects but `agent_reply` never arrives.** POST blob still arrives; `_render_assistant_from_post` fallback fires. Live strip never clears via SSE — it's cleared in the POST handler too as a safety net (add to `_render_assistant_from_post`).

**E2: POST returns before `agent_reply` SSE event.** POST handler checks `_turn_rendered_via_sse`; if False, renders bubble from POST. SSE may then arrive late and try to render again — guard `_on_agent_reply` with `if self._turn_rendered_via_sse: return`.

**E3: User hits Send during an in-flight turn.** Should be impossible because button is in Stop state. But if state machine has a bug, ignore the second send: check `_turn_active` first.

**E4: User closes the window mid-turn.** `destroy()` cancels stream task and tick loop. Orchestrator continues to completion server-side (acceptable — it's a side effect, but Kit RPC will still mutate the stage).

**E5: Network drops during POST.** `service.send_message` raises; `_handle_service_request` catches in `try/finally` and resets button to idle. User loses the turn's reply but the live strip showed what ran. Consider a "Connection lost — retry?" snackbar (out of v1 scope).

**E6: `tool_call_finished` event missed (SSE drop between started and finished).** Row stays in `running` state forever. Mitigation: in `_collapse_live_strip` (called on `agent_reply`), force any still-running rows to `done_unknown` state with `?` glyph in dim grey. Alternative: in `_tick_loop`, mark rows as stale after 60s without a finish event.

**E7: Two tools with the same `tc_id` from server.** Should not happen but defensive: `_make_live_row` overwrites the entry in `_live_rows` — old widget is orphaned but harmless.

**E8: Tool args contain non-JSON-serializable content.** `json.dumps(args_full, default=str)` falls back to repr. Tooltip may be ugly but never crashes.

**E9: Very long arg values in tooltip.** Truncated to 500 chars in `_make_live_row`.

**E10: Cancel sent before turn starts.** Orchestrator hasn't entered round loop yet; flag is set; on entry, immediately exits and returns canned reply. UI sees `cancel_acknowledged` then `agent_reply` with stub text.

**E11: `_pending_diff` never arrives.** No diff chip shown. Acceptable.

**E12: Multiple ChatViewWindows opened.** Each generates its own session ID and SSE stream. ✓.

**E13: Undo while another turn is in flight.** `_on_undo_clicked` checks `_turn_active` first and ignores the click. Button remains visible (no disabled-state flicker) but inert. Once turn ends, undo works again.

**E14: Undo via slash command (`/undo` typed in chat) drains UI's undo stack out-of-band.** Server emits `undo_applied` via SSE either way; UI handler pops `_undoable_bubbles` regardless of trigger source. Stays in sync.

**E15: User undoes a turn whose snapshot was deleted by another process.** `turn_snapshot.restore` returns `{ok: False, error: ...}`; server emits `undo_failed`; UI shows red error on button + live strip; button stays clickable for retry. The "no snapshots" case becomes "User → Stage was reset elsewhere, please re-prompt."

**E16: Snapshot restore succeeds but Hydra fails to recompose.** turn_snapshot.restore returns ok=True but viewport doesn't update visually. Out of UI's control. Mitigation: include a "Press F5 to refresh viewport" hint in undo's success tooltip (low priority, defer).

**E17: Clear chat fires while a turn is in flight.** Same as New: button is enabled but probably shouldn't be — add `if self._turn_active: return` to `_on_clear_chat_clicked` to ignore. Same for undo (already covered in E13).

**E18: User undoes the only mutating turn, then sends a new mutating turn.** `_undoable_bubbles` is empty when the new turn lands; new bubble becomes the sole undoable one and gets the `↶`. ✓.

**E19: Many consecutive undos.** Each undo emits `undo_applied`; UI pops one at a time. After all are consumed, server returns `{ok: False, error: "no snapshots"}` → UI disables `↶` on remaining bubbles? **Decision:** simpler to only show `↶` on the one bubble at the end of `_undoable_bubbles`, which empties naturally as undos consume.

**E20: Scale change during live SSE-driven progress (no active POST but tools mid-execution from a prior turn).** `_turn_active` is the source of truth — gates scale change. If somehow scale change fires anyway (race), rebuild aborts with a logged warning rather than corrupting widget refs.

**E21: Carb settings unavailable** (Kit subsystem not initialized at extension startup): scale falls back to default, persistence silently no-ops. Re-attempt on first scale change.

**E22: Keyboard shortcut conflicts with another extension** (e.g., a code editor extension also wants `Ctrl+=`): omni's input subscription is FIFO — the LAST registered handler wins. Scope our handler to "only when chat window has focus" via `_window_has_focus` check, so other extensions get the shortcut when chat isn't focused. Verify the focus API on first run.

**E23: `_chat_history` grows unbounded** in long sessions: cap at last 200 entries. Older entries dropped silently — chat scroll just shows what's in memory. (Same pattern as terminal scrollback.)

**E24: Scale rebuild loses an in-flight live row's spinner state.** Forbidden by `_turn_active` gate; rebuild can only happen when no row is `running`. ✓.

**E25: User dragged the chat window to a different size, then changes scale.** Window dimensions are persisted by Kit independently of scale; rebuild respects current window size. Layout reflows naturally.

---

## Acceptance tests

Each phase has a verifiable test. Run before moving to the next phase.

### Phase 0: Tool descriptions

```bash
cd /home/anton/projects/Omniverse_Nemotron_Ext
export ANTHROPIC_API_KEY=...
python scripts/regenerate_descriptions.py
```

Verify:

- [ ] `service/isaac_assist_service/chat/tools/descriptions.py` exists
- [ ] `_GENERATED` contains an entry for every tool in `tool_executor.py` (count matches)
- [ ] Sample 5 random entries — each is one line, plain English, 10-15 words
- [ ] `_OVERRIDES` exists and is an empty dict (preserved on subsequent runs)
- [ ] Re-running the script reports "Up to date — N descriptions, no changes needed."

### Phase 1: Backend pub/sub + cancel

```bash
# Terminal 1 — start uvicorn (must restart after orchestrator/session_trace edits)
cd /home/anton/projects/Omniverse_Nemotron_Ext
./launch_service.sh

# Terminal 2 — open SSE stream
curl -N http://localhost:8000/api/v1/chat/stream/test_sid

# Terminal 3 — send a message
curl -X POST http://localhost:8000/api/v1/chat/message \
  -H "Content-Type: application/json" \
  -d '{"session_id":"test_sid","message":"create a cube at /World/TestCube"}'

# Expected on terminal 2:
#   event: connected
#   event: turn_started
#   event: tool_call_started
#   event: tool_call_finished
#   event: turn_diff_computed
#   event: agent_reply
```

```bash
# Cancel test
# Terminal 2: still listening
# Terminal 3:
curl -X POST http://localhost:8000/api/v1/chat/message \
  -d '{"session_id":"test_sid","message":"create 50 cubes scattered randomly"}' &
sleep 1
curl -X POST http://localhost:8000/api/v1/chat/cancel \
  -d '{"session_id":"test_sid"}'

# Expected on terminal 2: tool_call events stop after the next finish,
# then cancel_acknowledged, then agent_reply with stub text.
```

### Phase 2: Service client (standalone)

```bash
cd /home/anton/projects/Omniverse_Nemotron_Ext
python -c "
import asyncio
from exts.isaac_6_0.omni.isaac.assist.omni.isaac.assist.service_client import AssistServiceClient

async def main():
    c = AssistServiceClient(session_id='test_phase2')
    events = []
    c.start_stream(lambda t,p,r: events.append((t, p)))
    await c.send_message('create a cube')
    await asyncio.sleep(2)
    print('\n'.join(f'{t}: {p}' for t,p in events))

asyncio.run(main())
"
# Expected: turn_started, tool_call_started, tool_call_finished, agent_reply printed
```

### Phase 3: UI (live strip visible)

In Isaac Sim: open the Isaac Assist window. Type "create a cube". Verify:

- [ ] User bubble appears within 200ms
- [ ] Live strip slides in showing `⠋ Thinking…`
- [ ] After ~3s, `Thinking…` is replaced by tool rows
- [ ] Spinner glyph cycles smoothly (~200ms cadence)
- [ ] Elapsed time counter ticks
- [ ] On completion: spinner becomes ✓, color fades from green to grey over ~400ms
- [ ] Assistant bubble appears with text
- [ ] Live strip clears

### Phase 4: Cancel + state machine

In the UI:

- [ ] Type "create 50 cubes scattered". Click Send. Wait for first tool to fire.
- [ ] Click Stop. Button changes to "Stopping…", disabled.
- [ ] Within ~5s, `■ Stopped by user` row appears. Stub assistant reply renders.
- [ ] Button returns to Send.

### Phase 5: Polish

- [ ] Diff chip appears below assistant bubble after a scene-mutating turn
- [ ] Connection dot is green during normal operation
- [ ] Kill uvicorn → connection dot turns amber and slow-pulses
- [ ] Restart uvicorn → connection dot returns to green
- [ ] Suggestion chips visible on first launch; disappear after first message
- [ ] Click New → button shows "Confirm?" for 3s; click again → wipe; wait 3s → reverts
- [ ] Click ↻ on a previous user bubble → input field populated
- [ ] Tool with elapsed >10s: spinner shifts to dim amber. >20s: full amber.
- [ ] Same tool failing 3x: shows as ONE row with `×3` counter, not 3 stacked rows

### Phase 6: Undo & Clear chat

Backend smoke test (no UI):

```bash
# After a mutating turn has run:
curl http://localhost:8000/api/v1/chat/stream/test_phase6 &  # listen
curl -X POST http://localhost:8000/api/v1/chat/message \
  -d '{"session_id":"test_phase6","message":"create a cube at /World/UndoTest"}'
# wait for completion
curl -X POST http://localhost:8000/api/v1/chat/undo \
  -d '{"session_id":"test_phase6","steps":1}'
# Expect: undo_started, undo_applied events on the SSE stream.
# Verify in viewport: cube is gone.

# Clear chat:
curl -X POST http://localhost:8000/api/v1/chat/clear_chat \
  -d '{"session_id":"test_phase6"}'
# Expect: chat_cleared event; stage UNCHANGED.
```

UI test in Isaac Sim:

- [ ] Send a mutating prompt; after agent_reply, `↶` appears next to the diff chip
- [ ] Click `↶` → button text becomes `Undo?` in amber; wait 3s → reverts to `↶`
- [ ] Click `↶` twice quickly → live strip shows `⠋ Reverting…` → bubble dims to ~50%, header shows "Isaac Assist (undone)", `↶` removed
- [ ] If a previous mutating bubble exists, it now has the `↶` button
- [ ] Click `↶` on the new latest, repeat — chains backward correctly
- [ ] Send a new mutating prompt after some undos — new bubble gets the `↶`, old undone bubbles stay dimmed
- [ ] Click Clear → "Confirm?" → confirm → chat history wipes, viewport stage unchanged
- [ ] After Clear, send a new prompt — chips reappear on first launch only (we just cleared, they should re-show)
- [ ] Type `/undo` directly in chat input → server runs the slash command → UI also dims the latest bubble (proves SSE-driven sync works regardless of trigger)
- [ ] During an in-flight turn, clicking `↶` on an older bubble does nothing (button visible but inert)

### Phase 7: Text/UI scaling

- [ ] `Aa` button visible in header; click opens popup
- [ ] In popup: `[A−] 100% [A+] [Reset]` — clicking `A+` advances scale, label updates immediately, UI rebuilds with larger fonts/buttons
- [ ] `Ctrl+=` while chat focused: scale advances one step
- [ ] `Ctrl+-` while chat focused: scale retreats one step
- [ ] `Ctrl+0` while chat focused: scale resets to 100%
- [ ] Right-click in chat area: context menu with Zoom in / Zoom out / Reset; clicking each works
- [ ] At 80% scale: text readable, buttons clickable, no clipping
- [ ] At 175% scale: header buttons may compress but no overflow off the window edge
- [ ] Send a few messages, change scale → all chat history re-renders at new scale, scroll position preserved
- [ ] Close Isaac Sim, reopen: scale persists at last value
- [ ] Manually corrupt the carb setting (`carb.settings.set(SCALE_SETTING_KEY, 99)`) and reopen: falls back to default index without crashing
- [ ] Start a turn, click `Aa` button → popup shows but `A−`/`A+` are inert; `Ctrl+=` no-ops
- [ ] After turn ends, scale controls work again
- [ ] Close window with custom scale, reopen: scale loads correctly
- [ ] Undo a previous turn, then change scale: undone bubble stays dimmed after rebuild (state preserved through history list)

---

## Implementation order

Each phase ships independently and can be reviewed/tested before the next.

**Phase 0 — Tool descriptions** (one-shot, ~10 minutes)

0a. Create `scripts/regenerate_descriptions.py` (~120 lines)
0b. Run it once: `python scripts/regenerate_descriptions.py` — generates `chat/tools/descriptions.py` with ~344 entries
0c. Spot-check the output. If any descriptions read poorly, add to `_OVERRIDES` (won't be touched on regenerate)
0d. Optional: install the pre-commit hook so descriptions stay in sync

**Phase 1 — Backend pub/sub + cancel** (server-only, no UI changes)

1. Create `chat/cancel_registry.py` (new, ~20 lines)
2. Modify `chat/session_trace.py` — add `subscribe`/`unsubscribe` and queue fan-out in `emit()` (~25 lines)
3. Modify `chat/orchestrator.py`:
   - Import `_t` (time) and `from .tools.descriptions import describe`
   - Add `_summarize_args` helper at module top
   - Emit `turn_started` after existing `user_msg` emit (~3 lines)
   - Wrap `await execute_tool_call` with `tool_call_started`/`tool_call_finished`, including `description` in the started payload (~14 lines)
   - Add cancel checks in round loop and tool inner loop (~6 lines)
   - Add early-exit cancel reply path (~10 lines)
   - Augment `turn_diff_computed` payload with path lists (~5 lines)
4. Modify `chat/routes.py` — add SSE endpoint and cancel endpoint (~50 lines)
5. **Restart uvicorn** (`pkill -f uvicorn && ./launch_service.sh`)
6. Run Phase 1 acceptance tests

**Phase 2 — Service client** (client-only, no UI changes)

7. Modify `service_client.py`:
   - Add `session_id` param with UUID default
   - Add `cancel_turn` method
   - Add `start_stream`/`stop_stream`/`_stream_loop` (~80 lines)
8. Run Phase 2 standalone test

**Phase 3 — UI infrastructure** (visible UI changes begin)

9. Create `ui/animations.py` (~70 lines)
10. Create `ui/verbs.py` (~150 lines)
11. Rewrite `ui/chat_view.py` — header, scroll, live strip, basic input, SSE wiring, tick loop (~250 lines so far)
12. Modify `extension.py` — pass title only; wider default size
13. **Reload extension in Isaac Sim** (Window → Extensions → Reload)
14. Run Phase 3 acceptance tests

**Phase 4 — Cancel + state machine**

15. Add Send/Stop/Stopping button state machine to `chat_view.py`
16. Wire `_on_send_or_stop`, `_on_cancel_ack` handlers
17. Run Phase 4 acceptance tests

**Phase 5 — Polish**

18. Diff chip rendering
19. Connection dot + state transitions
20. Empty-state suggestion chips
21. Soft confirm on New Scene
22. Re-run button on user bubbles
23. Run Phase 5 acceptance tests

**Phase 6 — Undo & Clear chat** (snapshot infra already exists)

24. Modify `chat/orchestrator.py` — capture `_snapshot_result` from existing `turn_snapshot.capture()` call; include `has_snapshot` in `agent_reply` payload (~3 lines)
25. Modify `chat/routes.py` — add `/undo` and `/clear_chat` endpoints; add 4 event types to `_USER_VISIBLE_EVENTS` (~50 lines)
26. Modify `service_client.py` — add `undo_turn` and `clear_chat` methods (~30 lines)
27. Modify `chat_view.py` — add Clear header button; add undo state init; `_attach_undo_button`/`_remove_undo_button`/`_on_undo_clicked`/`_on_undo_started`/`_on_undo_applied`/`_on_undo_failed`/`_dim_bubble_as_undone`/`_do_undo`; `_on_clear_chat_clicked`/`_do_clear_chat`; route 4 SSE event types; expose extra refs (`inner_bg_rect`, `header_lbl`, `body_lbl`, `diff_chip_lbl`) on assistant bubbles for dimming (~200 lines)
28. **Restart uvicorn** (orchestrator + routes both changed)
29. **Reload extension in Isaac Sim**
30. Run Phase 6 acceptance tests (backend curl + UI checklist)

**Phase 7 — Text/UI scaling**

31. Refactor bubble construction to data-driven: rename `_add_user_bubble` → `_render_user_bubble(text, record=True)`, `_add_assistant_bubble` → `_render_assistant_bubble(..., record=True)`, add `_record_history` and `_render_history_entry`; populate `self._chat_history` on every render (~50 lines)
32. Add scale state, helpers (`_sz`/`_px`/`_ss`), persistence load/save via carb settings (~30 lines)
33. Add `Aa` button to header between Clear and Vision; add `_open_scale_popup` and the popup window (~40 lines)
34. Add right-click context menu on chat scroll area (`_on_chat_mouse_pressed`, `_show_zoom_menu`) (~20 lines)
35. Add keyboard shortcut registration via carb input (`_register_keyboard_shortcuts`); release subscription in `destroy` (~30 lines)
36. Implement `_change_scale` (gates on `_turn_active`, clamps, persists, calls `_rebuild_ui`) and `_rebuild_ui` (preserve scroll/input/history, tear down, rebuild, restore) (~50 lines)
37. **Mechanical conversion** — wrap every hardcoded size in every `_build_*` and `_render_*` method with `_sz`/`_px`/`_ss` (~80 lines diff)
38. **Reload extension in Isaac Sim**
39. Run Phase 7 acceptance tests

**Total estimate: ~1400-1500 lines of new code (including ~344 generated descriptions, ~200 lines of undo/clear UI, and ~300 lines of scaling subsystem + mechanical conversion). No new runtime dependencies. `anthropic` SDK only required for the regeneration script.**

---

## Risk register

| Risk                                                            | Mitigation                                                              |
|-----------------------------------------------------------------|-------------------------------------------------------------------------|
| `omni.ui` tooltips don't work in Isaac 6.0                      | Verify on first widget; if not, fall back to a header label showing focused row's tool name |
| `omni.ui.Button.set_clicked_fn` doesn't exist (older API)       | Use lambda capture in constructor; can't change handler after — restructure if needed |
| `_tick_loop` starves under heavy Kit load                       | Test in a real scene with active simulation; back off to 300ms if needed |
| SSE through Isaac Sim's bundled aiohttp version is buggy        | Have a polling fallback ready (`GET /chat/events?since={ts}`)           |
| Orchestrator changes break a non-streaming code path            | Gate trace emits behind `os.environ.get("STREAM_PROGRESS", "1") == "1"` for first iteration |
| Two extensions on same uvicorn collide                           | Per-extension UUID session_id (already in spec)                         |
| Memory leak from never-unsubscribed queues                      | `finally: unsubscribe` in SSE generator (already in spec)               |
| Undo restore is slow on large stages (>5s)                      | Live strip shows `⠋ Reverting…` with elapsed time; user knows it's working. If consistently slow in practice, switch to `omni.kit.undo` integration as a follow-up |
| Undo state desync between UI bubble list and disk snapshot stack | `undo_failed` with "no snapshots" silently removes UI's stale ↶ button; SSE-driven `undo_applied` always pops UI list regardless of trigger source |
| User confused that `↶` only on latest bubble (not every bubble)  | Tooltip explains; matches Ctrl+Z mental model; if user feedback shows confusion, add a subtle "earlier turns can be undone by undoing this one first" hint |
| Snapshot file disk usage grows unbounded                         | turn_snapshot.py's docstring notes "could prune after 100 turns; not worth building yet"; revisit if it bites |
| `carb.input` keyboard subscription API differs across Isaac 5.1 / 6.0 | Wrap registration in try/except; `_kbd_sub = None` on failure means scale still works via button + menu, just without shortcuts |
| Scale rebuild visual flicker is too noticeable                   | If user feedback says so, transition by lerping a black overlay alpha for 100ms — but try without first; one-frame snap is acceptable for an explicit user action |
| `omni.ui.Window` `focused` property doesn't exist on Isaac 6.0   | Verify; if not, fall back to: shortcuts always active when extension is loaded (matches VS Code behavior; minor scope bleed acceptable) |
| `_chat_history` data structure drifts as bubble rendering evolves | Type-hint the entry shape (`TypedDict`) and validate in `_render_history_entry`; drop entries that don't match the schema |

---

## Future work (explicitly out of scope)

- **Audio chime on turn complete** (opt-in, settings flag).
- **Token-level streaming** of assistant text (requires LLM provider refactor).
- **Persistent chat history across window restarts.** The data-driven history list from Phase 7 is half the work; serialize `self._chat_history` to disk on destroy, reload on init. Worth it once Phase 7 lands.
- **Markdown rendering** in bubbles (code blocks, bold, lists).
- **Hover-only re-run button** (requires `mouse_hovered_fn` verification on omni.ui 6.0).
- **Inline diff viewer** when clicking the diff chip (currently shows full paths in tooltip).
- **Server-side event replay** on reconnect (read JSONL tail).
- **Approve/Reject UI** for `actions_to_approve` (currently silently dropped — separate spec).
- **Redo.** Once undone, snapshots are deleted by `turn_snapshot.restore`; redo would require keeping them and tracking a separate position cursor. Most users will just re-prompt.
- **Snapshot autoprune.** turn_snapshot.py notes this could be added after ~100 turns. Currently disk grows unbounded across long sessions.
- **Undo via Ctrl+Z keyboard shortcut.** Conflicts with Isaac's own Ctrl+Z (which acts on Kit's undo stack — different thing). Skipping to avoid confusion.

---

## Why this design is the right one

1. **Reuses existing infrastructure.** session_trace already emits ~20 event types; we add 4 and route them. No new event taxonomy to maintain.
2. **Two-channel design degrades gracefully.** SSE is enhancement, POST is canonical. If SSE breaks, the chat still works at current quality.
3. **Cancel granularity is honest.** "After current tool finishes" is achievable; "abort current tool" requires Kit-side changes we're not doing. We tell the user "Stopping…" not "Stopped" until the orchestrator returns.
4. **Animation discipline.** Four animations only, each tied to a state transition. No decorative motion.
5. **Verb mapping is finite work.** ~120 lines of override + ~80 lines of conjugation covers the long tail. Auto-derivation handles new tools without code changes.
6. **Tool descriptions are LLM-generated, not hand-written.** Generating 344 entries by hand would be days of work that goes stale; a one-shot Claude pass over `tool_executor.py` produces consistent-voice user-facing descriptions in ~10 minutes. The diff-aware regenerator + pre-commit hook keeps the dict in sync as new tools land — zero ongoing human work. `_OVERRIDES` exists for the rare cases where the generated description reads poorly.
7. **Undo reuses what already exists.** `turn_snapshot.py` was built for a `/undo` slash command and already auto-prunes consumed snapshots — there's no separate stack to maintain. Phase 6 is a UI surface plus 50 lines of REST glue, not an architectural addition. SSE-driven sync means slash-command undo and button undo update the same UI state without divergence.
8. **Latest-only undo button matches Ctrl+Z mental model.** Per-bubble buttons everywhere would imply branching history (which would create state-coherence bugs when middle turns are undone while later turns referenced the same prims). One button on the latest mutating bubble forces linear semantics — chain backwards by clicking repeatedly.
9. **Visible "(undone)" bubbles are a feature, not a bug.** Removing undone turns from the chat would lose provenance — the user wouldn't see what they tried. Dimming preserves the record and naturally pairs with the user's next re-prompt to form a coherent narrative.
10. **Scaling rebuilds rather than mutates.** Mutating ~30 widget refs across a scale change is fragile (orphaned refs in `_undoable_bubbles` and `_live_rows`, omni.ui's reflow quirks on width/height mutation). Rebuild has one clear failure mode (forbidden during `_turn_active`) instead of many subtle ones — and the data-driven chat history list it forces *also* unlocks future persistence and export.
11. **Discrete scale steps + multi-surface controls** match every familiar app (VS Code, Slack, browsers): keyboard shortcut for power users, header button for discoverability, context menu for muscle memory. Carb settings persistence is the Omniverse-native path — no custom file formats, auto-saves on shutdown.
12. **Each phase is independently shippable.** If we run out of time at Phase 3, the user has a working live strip without cancel. If we stop at Phase 5, they have everything except undo + scaling.
13. **Failure modes are pre-thought.** The 25 edge cases above are not after-the-fact patches; they shape the design.
