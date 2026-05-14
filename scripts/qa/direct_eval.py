"""
Direct-mode evaluation — no persona, no subprocess. Just POST the task goal
as a single user message to Isaac Assist and snapshot scene-state.

Cheaper + faster than persona harness; measures core tool-chain reliability
instead of emergent persona-level behavior. Per-task cost ~ $0.05 vs ~$1.
Inherits the same pre-session-setup hook from the task spec .md.

Usage:
    python -m scripts.qa.direct_eval --task G-01
    python -m scripts.qa.direct_eval --all
    python -m scripts.qa.direct_eval --tasks G-01,G-02,G-03,FX-01

WARNING — DO NOT RUN TWO INSTANCES IN PARALLEL against the same Kit RPC
server. Kit holds a single shared stage and each direct_eval resets +
seeds it per task. Concurrent runs race on _reset_stage + pre-session
setup → task A's seed leaks into task B's snapshot → false fabrication
verdicts. Observed 2026-04-18: running AD-06 concurrently with a 20-task
canary caused FX-05 to see /World/TestCube (AD-06's fixture) and judge
it as a task failure. Run sequentially, or spawn separate Kit instances.
"""
from __future__ import annotations
import argparse
import json
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import httpx

from scripts.qa.multi_turn_session import (
    _reset_stage, _apply_pre_session_setup, _snapshot_stage,
    ISAAC_ASSIST_URL, REPO_ROOT,
)


def _task_goal_query(task_id: str) -> Optional[str]:
    """Extract the Goal section from the task .md and format as a direct user query.

    Uses the full goal text (trimmed of whitespace, capped at MAX_QUERY_CHARS).
    Earlier versions took only the first sentence, but many task goals open
    with a context sentence (e.g. 'A cube already exists at /World/Anchor')
    and put the actual ask in sentence 2+. First-sentence truncation dropped
    the ask entirely and reduced the query to scene-state acknowledgement.
    """
    p = REPO_ROOT / "docs" / "qa" / "tasks" / f"{task_id}.md"
    if not p.exists():
        return None
    text = p.read_text()
    import re
    m = re.search(r"\*\*Goal:\*\*\s*(.*?)(?=\n\*\*|\n##|\n\Z)", text, re.S)
    if not m:
        return None
    goal = " ".join(m.group(1).split()).strip()  # collapse whitespace
    MAX_QUERY_CHARS = 600
    return goal[:MAX_QUERY_CHARS]


_CONTINUATION_PROMPT = (
    "Please continue and make sure every part of my original request is covered — "
    "if you left any sub-task unfinished (code example, specific tool name, exact "
    "constraint values, cite-able statement), complete it now. If you are truly "
    "done and every sub-task is addressed, reply with 'All sub-tasks covered.'"
)


def _extract_scripted_followups(task_id: str) -> list[str]:
    """Read the '## Scripted followups' YAML-list block from a task spec.

    Used by T4 (high-level intent) tasks where the agent is expected to
    ask 1-3 clarifying questions before building. Each followup is the
    canned user response applied turn-by-turn (no keyword matching —
    just next-in-list).

    Format expected in task .md:

        ## Scripted followups

        - "First answer text"
        - "Second answer text"

    Returns [] when the section is absent (single-turn task — current
    direct_eval default behavior).
    """
    p = REPO_ROOT / "docs" / "qa" / "tasks" / f"{task_id}.md"
    if not p.exists():
        return []
    text = p.read_text()
    import re
    m = re.search(
        r"##\s*Scripted\s*followups\s*\n+(.*?)(?=\n##|\n\Z)",
        text, re.S | re.I,
    )
    if not m:
        return []
    block = m.group(1)
    # Match leading-dash list items, accept both quoted and unquoted text
    items = []
    for line in block.splitlines():
        line = line.strip()
        if not line or not line.startswith("-"):
            continue
        body = line[1:].strip()
        # Strip surrounding quotes if present
        if (body.startswith('"') and body.endswith('"')) or \
           (body.startswith("'") and body.endswith("'")):
            body = body[1:-1]
        if body:
            items.append(body)
    return items


def _agent_asking_clarification(reply_text: str, tool_calls: list, intent: str) -> bool:
    """Detect whether the agent's reply is asking for clarification rather
    than acting. Used in the multi-turn loop to decide whether to send
    the next scripted_followup (yes — agent is waiting on user) vs end
    the dialog (agent has built / answered).

    Heuristics (in order):
      1. Intent == 'negotiation_clarification' (negotiator gate fired)
      2. Reply contains a '?' AND no tool calls were made
      3. Reply ENDS with a '?' (or '?' + trailing whitespace) — covers
         the "did work AND asked next" pattern that VR-15 hit, where a
         multi-turn refinement task creates the cube on turn 1, then
         asks 'what next?' — Rule 2 misses it because tool_calls is
         non-empty, but the trailing question mark is unambiguous.
      4. Reply mentions the canonical clarification phrase pattern
    Otherwise — agent is acting / done.
    """
    if intent == "negotiation_clarification":
        return True
    if not tool_calls and "?" in reply_text:
        return True
    # Tool calls + reply ending in a question = "did some work, now waiting".
    # Handle Markdown trailing chars too.
    stripped = reply_text.rstrip().rstrip("`*_)\"'")
    if stripped.endswith("?"):
        return True
    # Phrases the negotiator's format_clarification_reply uses or
    # template-driven assistants commonly emit
    asking_phrases = [
        "before i start",
        "could you tell",
        "could you specify",
        "could you confirm",
        "what would you like",
        "please provide",
        "please confirm",
        "should i use",
    ]
    rl = reply_text.lower()
    return any(p in rl for p in asking_phrases) and not tool_calls


def run_direct(task_id: str, runs_dir: Path, timeout_s: int = 600,
               followup: bool = False, multi_turn: bool = True,
               max_turns: int = 5) -> Dict:
    """Run a direct-mode eval on `task_id`.

    When `followup=True`, send one additional "are you really done?" turn
    after the first reply. (Legacy probe for output-length-bound failures.)

    When `multi_turn=True` (default for tasks with `## Scripted followups`):
      Loop up to `max_turns` turns. After each agent reply:
        - If agent appears to be asking for clarification AND a scripted
          followup is available, send the next followup as the user's
          turn-N+1 message.
        - Otherwise, end the dialog and snapshot/judge against the final
          stage state.
      This unblocks T4 (high-level intent) tasks where the agent is
      *expected* to ask 1-3 clarifying questions before building.
      Single-shot direct_eval can never pass these: agent asks → no answer
      → session ends. Scripted followups give the agent the missing inputs
      so the actual capability (turn-2/3 building) gets exercised.
    """
    run_id = datetime.now().strftime("run_direct_%Y%m%dT%H%M%S") + f"_{uuid.uuid4().hex[:6]}"
    out_dir = runs_dir / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    transcript = out_dir / f"{task_id}_direct.jsonl"

    def log(event: Dict) -> None:
        event["ts"] = time.time()
        with transcript.open("a") as f:
            f.write(json.dumps(event) + "\n")

    query = _task_goal_query(task_id)
    if not query:
        log({"event": "error", "message": f"no goal found for {task_id}"})
        return {"task": task_id, "transcript": str(transcript), "error": "no goal"}

    log({"event": "direct_eval_start", "task": task_id, "query": query,
         "followup_enabled": followup})
    reset_result = _reset_stage()
    log({"event": "stage_reset", "result": reset_result})
    pre = _apply_pre_session_setup(task_id)
    if pre.get("applied"):
        log({"event": "pre_session_setup", "result": pre})
    log({"event": "stage_snapshot", "when": "initial", "snapshot": _snapshot_stage()})

    session_id = f"direct_{run_id}"
    total_tool_calls = 0
    total_chars = 0
    try:
        with httpx.Client(timeout=timeout_s) as client:
            r = client.post(ISAAC_ASSIST_URL, json={"session_id": session_id, "message": query})
            r.raise_for_status()
            reply = r.json()
    except Exception as e:
        log({"event": "isaac_assist_error", "error": str(e)})
        return {"task": task_id, "transcript": str(transcript), "error": str(e)}

    assistant_msg = "\n".join(m.get("content", "") for m in reply.get("response_messages", [])).strip()
    log({"event": "isaac_assist_reply", "turn": 1, "text": assistant_msg,
         "intent": reply.get("intent"),
         "tool_calls": reply.get("tool_calls", [])})
    total_tool_calls += len(reply.get("tool_calls", []))
    total_chars += len(assistant_msg)

    # ── Multi-turn loop with scripted followups ────────────────────────────
    # Pull task-declared followups; if none present, behave as single-turn.
    scripted_followups = _extract_scripted_followups(task_id) if multi_turn else []
    followup_index = 0
    last_reply = reply
    last_assistant_msg = assistant_msg
    turn = 1
    while multi_turn and followup_index < len(scripted_followups) and turn < max_turns:
        last_tool_calls = last_reply.get("tool_calls", []) or []
        last_intent = last_reply.get("intent", "")
        agent_asked = _agent_asking_clarification(last_assistant_msg, last_tool_calls, last_intent)
        agent_acted = bool(last_tool_calls)
        # Continue the dialog when the agent either asked a clarifying
        # question (waiting on user) OR actually did work and the test
        # has more refinement followups queued. Iterative-refinement
        # tasks like VR-15 ('create cube' → 'make it bigger' → 'move
        # it') don't require the agent to ask between turns — the user
        # drives the iteration. Without this 'agent_acted'-also-OK
        # branch, the second prompt never fires and refinement steps
        # silently drop.
        if not agent_asked and not agent_acted:
            # Agent neither asked nor did anything — pointless to push
            # more followups at it, end the dialog.
            break
        # Send the next scripted followup as the user's turn-N+1 message
        followup_text = scripted_followups[followup_index]
        followup_index += 1
        turn += 1
        log({"event": "stage_snapshot", "when": f"after_turn_{turn-1}",
             "snapshot": _snapshot_stage()})
        log({"event": "scripted_followup", "turn": turn,
             "text": followup_text, "followup_index": followup_index})
        try:
            with httpx.Client(timeout=timeout_s) as client:
                r_next = client.post(ISAAC_ASSIST_URL, json={
                    "session_id": session_id, "message": followup_text,
                })
                r_next.raise_for_status()
                last_reply = r_next.json()
        except Exception as e:
            log({"event": "isaac_assist_error", "error": str(e), "turn": turn})
            break
        last_assistant_msg = "\n".join(
            m.get("content", "") for m in last_reply.get("response_messages", [])
        ).strip()
        log({"event": "isaac_assist_reply", "turn": turn, "text": last_assistant_msg,
             "intent": last_reply.get("intent"),
             "tool_calls": last_reply.get("tool_calls", [])})
        total_tool_calls += len(last_reply.get("tool_calls", []))
        total_chars += len(last_assistant_msg)

    # ── Legacy single-followup mode (--followup CLI flag) ──────────────────
    if followup and not scripted_followups:
        log({"event": "stage_snapshot", "when": "after_turn_1", "snapshot": _snapshot_stage()})
        log({"event": "persona_message", "turn": 2, "text": _CONTINUATION_PROMPT})
        try:
            with httpx.Client(timeout=timeout_s) as client:
                r2 = client.post(ISAAC_ASSIST_URL, json={
                    "session_id": session_id, "message": _CONTINUATION_PROMPT
                })
                r2.raise_for_status()
                reply2 = r2.json()
        except Exception as e:
            log({"event": "isaac_assist_error", "error": str(e), "turn": 2})
            reply2 = None
        if reply2 is not None:
            assistant_msg_2 = "\n".join(
                m.get("content", "") for m in reply2.get("response_messages", [])
            ).strip()
            log({"event": "isaac_assist_reply", "turn": 2, "text": assistant_msg_2,
                 "intent": reply2.get("intent"),
                 "tool_calls": reply2.get("tool_calls", [])})
            total_tool_calls += len(reply2.get("tool_calls", []))
            total_chars += len(assistant_msg_2)

    log({"event": "stage_snapshot", "when": "after_direct", "snapshot": _snapshot_stage()})
    log({"event": "direct_eval_end", "n_turns": turn,
         "n_followups_used": followup_index})
    return {"task": task_id, "transcript": str(transcript),
            "tool_calls": total_tool_calls,
            "reply_chars": total_chars}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--task")
    p.add_argument("--tasks", help="comma-separated")
    p.add_argument("--all", action="store_true")
    p.add_argument("--runs-dir", default=str(REPO_ROOT / "workspace" / "qa_runs"))
    p.add_argument("--followup", action="store_true",
                   help="Add one 'are you done?' continuation turn — probes whether "
                        "the agent's truncation-shaped failures are fixable with a "
                        "second shot, or are a true capability ceiling.")
    args = p.parse_args()
    runs_dir = Path(args.runs_dir)

    if args.task:
        task_ids = [args.task]
    elif args.tasks:
        task_ids = [t.strip() for t in args.tasks.split(",") if t.strip()]
    elif args.all:
        tasks_dir = REPO_ROOT / "docs" / "qa" / "tasks"
        task_ids = sorted(p.stem for p in tasks_dir.glob("*.md"))
    else:
        p.print_help()
        return

    tag = "direct2" if args.followup else "direct"
    summary = runs_dir / f"campaign_{tag}_{time.strftime('%Y%m%dT%H%M%S')}.jsonl"
    print(f"Campaign: {summary.name}", flush=True)
    for i, tid in enumerate(task_ids, 1):
        t0 = time.time()
        print(f"[{i}/{len(task_ids)}] {tid}", flush=True)
        res = run_direct(tid, runs_dir, followup=args.followup)
        elapsed = time.time() - t0
        print(f"  done tool_calls={res.get('tool_calls',0)} chars={res.get('reply_chars',0)} time={elapsed:.0f}s err={res.get('error','')}", flush=True)
        with summary.open('a') as f:
            f.write(json.dumps({**res, "elapsed_s": elapsed, "task_id": tid}) + "\n")


if __name__ == "__main__":
    main()
