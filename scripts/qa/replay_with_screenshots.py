"""Replay a canary task and capture viewport screenshots before/after.

Produces a small artifact bundle per task:
    /tmp/canary_replays/<task_id>/
        before.png    — viewport BEFORE the task runs (post-reset, post-setup)
        after.png     — viewport AFTER the task completes
        prompt.txt    — exact user query sent to the agent
        reply.txt     — agent's text reply
        tool_calls.json — tool calls made by the agent (names + args + errors)
        verdict.txt   — judge result if available, "(pending)" otherwise

Lets a human eyeball "did the scene change in the way the agent claimed?"
without having to scrape transcripts. Companion to mark_verified.py for
spot-checking that ChromaDB metadata matches reality.

Usage:
    python -m scripts.qa.replay_with_screenshots --tasks G-04,AD-14,AD-05,FX-05

Sequential only — runs task by task. Do not run alongside another canary.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

import httpx

from scripts.qa.multi_turn_session import (
    _reset_stage,
    _apply_pre_session_setup,
    ISAAC_ASSIST_URL,
    REPO_ROOT,
)
from scripts.qa.direct_eval import _task_goal_query

KIT_URL = "http://127.0.0.1:8001/exec_sync"
OUT_ROOT = Path("/tmp/canary_replays")


KIT_CAPTURE_URL = "http://127.0.0.1:8001/capture"


def kit_capture_viewport_png(out_path: Path, max_dim: int = 1280) -> bool:
    """Capture viewport via Kit's /capture HTTP endpoint and write PNG.

    Kit serves /capture directly and handles the async GPU readback for us;
    much simpler than driving omni.kit.viewport.utility ourselves over RPC.
    """
    try:
        r = httpx.get(KIT_CAPTURE_URL, params={"max_dim": str(max_dim)}, timeout=30.0)
        if r.status_code != 200:
            print(f"  /capture HTTP {r.status_code}", file=sys.stderr)
            return False
        d = r.json()
        b64 = d.get("image_b64")
        if not b64:
            print(f"  /capture missing image_b64: {list(d.keys())}", file=sys.stderr)
            return False
        out_path.write_bytes(base64.b64decode(b64))
        return True
    except Exception as e:
        print(f"  capture failed: {e}", file=sys.stderr)
        return False


def replay_task(task_id: str) -> dict:
    """Reset stage, apply setup, capture before, run task, capture after."""
    out_dir = OUT_ROOT / task_id
    out_dir.mkdir(parents=True, exist_ok=True)

    query = _task_goal_query(task_id)
    if not query:
        return {"ok": False, "error": f"task {task_id}: no Goal in spec"}
    (out_dir / "prompt.txt").write_text(query)

    session_id = f"replay_{task_id}_{uuid.uuid4().hex[:6]}"

    # 1. Reset stage + apply pre-session-setup from the task spec md
    _reset_stage()
    _apply_pre_session_setup(task_id)

    # 2. Capture BEFORE
    before_ok = kit_capture_viewport_png(out_dir / "before.png")

    # 3. Send the prompt to the chat service
    t0 = time.time()
    r = httpx.post(
        ISAAC_ASSIST_URL,
        json={"session_id": session_id, "message": query},
        timeout=600.0,
    )
    elapsed = time.time() - t0

    reply_text = ""
    tool_calls: list = []
    if r.status_code == 200:
        body = r.json()
        msgs = body.get("response_messages", []) or []
        reply_text = "\n\n".join(
            m.get("content", "") for m in msgs
            if m.get("message_type") == "text"
        ).strip()
        tool_calls = body.get("tool_calls", []) or []
    else:
        reply_text = f"[HTTP {r.status_code}: {r.text[:300]}]"

    (out_dir / "reply.txt").write_text(reply_text)
    (out_dir / "tool_calls.json").write_text(json.dumps(tool_calls, indent=2, default=str))

    # 4. Capture AFTER
    after_ok = kit_capture_viewport_png(out_dir / "after.png")

    return {
        "ok": True,
        "task_id": task_id,
        "out_dir": str(out_dir),
        "before_png": before_ok,
        "after_png": after_ok,
        "elapsed_s": round(elapsed, 1),
        "tool_call_count": len(tool_calls),
        "reply_chars": len(reply_text),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tasks", required=True, help="comma-separated task ids")
    args = ap.parse_args()

    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    tasks = [t.strip() for t in args.tasks.split(",") if t.strip()]
    results = []
    for tid in tasks:
        print(f"=== Replay {tid} ===")
        try:
            r = replay_task(tid)
            print(f"  ok={r['ok']} before={r.get('before_png')} after={r.get('after_png')} "
                  f"reply={r.get('reply_chars')}c tools={r.get('tool_call_count')} "
                  f"elapsed={r.get('elapsed_s')}s")
        except Exception as e:
            r = {"ok": False, "task_id": tid, "error": str(e)}
            print(f"  FAILED: {e}")
        results.append(r)

    summary_path = OUT_ROOT / "summary.json"
    summary_path.write_text(json.dumps(results, indent=2))
    print(f"\nArtifacts: {OUT_ROOT}")
    print(f"Summary:   {summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
