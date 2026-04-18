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


def run_direct(task_id: str, runs_dir: Path, timeout_s: int = 600,
               followup: bool = False) -> Dict:
    """Run a direct-mode eval on `task_id`.

    When `followup=True`, send one additional "are you really done?" turn
    after the first reply. This probes whether LLM truncation is what's
    costing us the ~2 failures on the 20-task suite (T-13, C-03) — if the
    agent CAN produce the full answer given a second shot, the failure was
    output-length-bound rather than capability-bound.
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

    if followup:
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
    log({"event": "direct_eval_end"})
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
