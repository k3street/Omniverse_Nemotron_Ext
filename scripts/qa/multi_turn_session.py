"""Run one multi-turn QA session: persona (Claude Code subprocess) ↔ Isaac Assist (FastAPI).

Usage:
    python -m scripts.qa.multi_turn_session --persona 01_maya --task M-01

Writes JSONL transcript to workspace/qa_runs/<run_id>/<persona>__<task>.jsonl.
Each message (persona out, assistant back) is a JSONL event. End conditions:
  - persona emits a give-up phrase
  - MAX_TURNS hit
  - persona subprocess fails repeatedly
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

from scripts.qa.build_session_prompt import (
    REPO_ROOT,
    build_session_prompt,
    random_modifiers,
    Modifiers,
)

ISAAC_ASSIST_URL = "http://127.0.0.1:8000/api/v1/chat/message"
KIT_RPC_EXEC = "http://127.0.0.1:8001/exec_sync"
MAX_TURNS = 20
CLAUDE_BIN = os.environ.get("CLAUDE_BIN", "claude")


def _reset_stage() -> Dict[str, Any]:
    """Open a fresh, empty stage in Isaac Sim before starting a session."""
    code = (
        "import omni.usd\n"
        "ctx = omni.usd.get_context()\n"
        "ctx.new_stage()\n"
        "stage = ctx.get_stage()\n"
        "print('stage_reset prims=', len(list(stage.Traverse())))\n"
    )
    try:
        with httpx.Client(timeout=30) as client:
            r = client.post(KIT_RPC_EXEC, json={"code": code})
            r.raise_for_status()
            return r.json()
    except Exception as e:
        return {"success": False, "output": f"stage_reset_failed: {e}"}

# Give-up phrases / stage directions indicating the persona has disengaged.
import re as _qa_re
GIVE_UP_PATTERNS = [
    "i'll try the docs", "i'll try the forum", "i'll try discord",
    "i'll ask a colleague", "this isn't working", "not worth my time",
    "going to the docs", "going to read the docs", "docs it is",
    "pull the 5.x", "i'll pull the",
    "i'm out", "bye.", "walked away", "closing the chat", "no response",
    "session ended", "session's done", "session is done",
    # Success closers — the persona has what they need and is disengaging
    "got what i needed", "that's what i needed",
    # Casual farewells when they clearly close the conversation
    "later 👋", "later.", "cya", "peace out", "thanks bye",
]
# A bracketed or asterisked stage direction alone — e.g. "[session ended]", "*closes tab*"
_STAGE_DIRECTION_RE = _qa_re.compile(r"^\s*[\[\*][^\]\*]{1,200}[\]\*]\s*$")


def _is_give_up(text: str) -> bool:
    t = text.lower().strip()
    if _STAGE_DIRECTION_RE.match(t):
        return True
    return any(p in t for p in GIVE_UP_PATTERNS)


def _persona_next_message(prompt: str, timeout_s: int = 120) -> Dict[str, Any]:
    """Invoke `claude -p` and return parsed JSON result dict."""
    cmd = [CLAUDE_BIN, "-p", prompt, "--output-format", "json"]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s, check=False)
    try:
        data = json.loads(proc.stdout or "{}")
        return {"ok": proc.returncode == 0, "result": data.get("result", ""), "cost": data.get("total_cost_usd", 0.0), "raw": data}
    except json.JSONDecodeError:
        return {"ok": False, "result": proc.stdout, "cost": 0.0, "raw": None, "error": "json_decode"}


def _ask_isaac_assist(session_id: str, message: str, timeout_s: int = 600) -> Dict[str, Any]:
    with httpx.Client(timeout=timeout_s) as client:
        r = client.post(ISAAC_ASSIST_URL, json={"session_id": session_id, "message": message})
        r.raise_for_status()
        return r.json()


def _build_next_persona_prompt(base_prompt: str, conversation: List[Dict[str, str]]) -> str:
    """Append conversation history and request next persona message."""
    lines = [base_prompt, "\n=== Conversation so far ===\n"]
    for turn in conversation:
        role = "You (persona)" if turn["role"] == "user" else "Isaac Assist"
        lines.append(f"{role}: {turn['content']}\n")
    lines.append(
        "\n=== Your next message ===\n"
        "Write your next message to Isaac Assist. Stay in character. One message only. "
        "If the task succeeded or you've given up, emit your final in-character line "
        "(e.g. 'ok this isn't working, I'll try the docs')."
    )
    return "\n".join(lines)


def run_session(persona: str, task: str, runs_dir: Path, seed: Optional[int] = None) -> Dict[str, Any]:
    run_id = datetime.now().strftime("run_%Y%m%dT%H%M%S") + f"_{uuid.uuid4().hex[:6]}"
    out_dir = runs_dir / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    transcript_path = out_dir / f"{persona}__{task}.jsonl"

    if seed is not None:
        import random as _random
        _random.seed(seed)
    mods = random_modifiers(persona)
    base_prompt = build_session_prompt(persona_id=persona, task_id=task, modifiers=mods)

    def log(event: Dict[str, Any]) -> None:
        event["ts"] = time.time()
        with transcript_path.open("a") as f:
            f.write(json.dumps(event) + "\n")

    reset_result = _reset_stage()
    log({"event": "stage_reset", "result": reset_result})
    log({"event": "session_start", "persona": persona, "task": task, "modifiers": mods.as_dict()})

    session_id = f"qa_{run_id}"
    conversation: List[Dict[str, str]] = []
    total_cost = 0.0
    turn = 0

    while turn < MAX_TURNS:
        turn += 1
        prompt = base_prompt if turn == 1 else _build_next_persona_prompt(base_prompt, conversation)

        pres = _persona_next_message(prompt)
        total_cost += pres.get("cost", 0.0)
        persona_msg = (pres.get("result") or "").strip()
        log({"event": "persona_message", "turn": turn, "text": persona_msg, "cost": pres.get("cost", 0.0)})

        if not persona_msg:
            log({"event": "abort", "reason": "empty persona message"})
            break

        conversation.append({"role": "user", "content": persona_msg})

        if _is_give_up(persona_msg):
            log({"event": "session_end", "reason": "persona_gave_up", "turn": turn})
            break

        try:
            aa_reply = _ask_isaac_assist(session_id, persona_msg)
        except Exception as e:
            log({"event": "isaac_assist_error", "turn": turn, "error": str(e)})
            break

        content_parts = []
        for msg in aa_reply.get("response_messages", []):
            content_parts.append(msg.get("content", ""))
        assistant_msg = "\n".join(content_parts).strip()

        log({"event": "isaac_assist_reply", "turn": turn, "text": assistant_msg,
             "intent": aa_reply.get("intent"),
             "tool_calls": aa_reply.get("tool_calls", []),
             "actions_to_approve": aa_reply.get("actions_to_approve"),
             "sources_consulted": aa_reply.get("sources_consulted", [])})

        conversation.append({"role": "assistant", "content": assistant_msg})

    else:
        log({"event": "session_end", "reason": "max_turns_hit", "turn": turn})

    log({"event": "session_summary", "turns": turn, "total_persona_cost_usd": total_cost,
         "transcript": str(transcript_path)})
    return {"run_id": run_id, "transcript": str(transcript_path), "turns": turn, "cost": total_cost}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--persona", required=True)
    p.add_argument("--task", required=True)
    p.add_argument("--runs-dir", default=str(REPO_ROOT / "workspace" / "qa_runs"))
    p.add_argument("--seed", type=int, default=None)
    args = p.parse_args()

    res = run_session(args.persona, args.task, Path(args.runs_dir), seed=args.seed)
    print(json.dumps(res, indent=2))


if __name__ == "__main__":
    main()
