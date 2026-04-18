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
    """Extract the Goal section from the task .md and format as a direct user query."""
    p = REPO_ROOT / "docs" / "qa" / "tasks" / f"{task_id}.md"
    if not p.exists():
        return None
    text = p.read_text()
    import re
    m = re.search(r"\*\*Goal:\*\*\s*(.*?)(?=\n\*\*|\n##|\n\Z)", text, re.S)
    if not m:
        return None
    goal = m.group(1).strip()
    # A real user wouldn't type a novel-length paragraph; trim to first sentence
    # + keep it under 300 chars. The intent survives, the verbosity doesn't.
    first_sentence = re.split(r"(?<=[.!?])\s", goal)[0]
    return (first_sentence if len(first_sentence) < 300 else goal[:300]).strip()


def run_direct(task_id: str, runs_dir: Path, timeout_s: int = 600) -> Dict:
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

    log({"event": "direct_eval_start", "task": task_id, "query": query})
    reset_result = _reset_stage()
    log({"event": "stage_reset", "result": reset_result})
    pre = _apply_pre_session_setup(task_id)
    if pre.get("applied"):
        log({"event": "pre_session_setup", "result": pre})
    log({"event": "stage_snapshot", "when": "initial", "snapshot": _snapshot_stage()})

    session_id = f"direct_{run_id}"
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
    log({"event": "stage_snapshot", "when": "after_direct", "snapshot": _snapshot_stage()})
    log({"event": "direct_eval_end"})
    return {"task": task_id, "transcript": str(transcript),
            "tool_calls": len(reply.get("tool_calls", [])),
            "reply_chars": len(assistant_msg)}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--task")
    p.add_argument("--tasks", help="comma-separated")
    p.add_argument("--all", action="store_true")
    p.add_argument("--runs-dir", default=str(REPO_ROOT / "workspace" / "qa_runs"))
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

    summary = runs_dir / f"campaign_direct_{time.strftime('%Y%m%dT%H%M%S')}.jsonl"
    print(f"Campaign: {summary.name}", flush=True)
    for i, tid in enumerate(task_ids, 1):
        t0 = time.time()
        print(f"[{i}/{len(task_ids)}] {tid}", flush=True)
        res = run_direct(tid, runs_dir)
        elapsed = time.time() - t0
        print(f"  done tool_calls={res.get('tool_calls',0)} chars={res.get('reply_chars',0)} time={elapsed:.0f}s err={res.get('error','')}", flush=True)
        with summary.open('a') as f:
            f.write(json.dumps({**res, "elapsed_s": elapsed, "task_id": tid}) + "\n")


if __name__ == "__main__":
    main()
