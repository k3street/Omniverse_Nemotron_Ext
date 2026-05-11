"""
Stage-diff judge — scores Phase 12 sessions on ACTUAL scene changes, not LLM prose.

Reads a transcript JSONL and computes:
  - prims added/removed between snapshots (real scene delta)
  - whether Assist's claimed changes are visible in the stage
  - whether the task's success criterion was met (by prim-path/type matching)

Ground truth = stage_snapshot events logged by the harness via Kit RPC
introspection. These cannot be fabricated by the LLM — they come from
Python reading the actual USD stage.

Usage:
  python -m scripts.qa.stage_diff_judge <transcript.jsonl>
  python -m scripts.qa.stage_diff_judge --campaign <summary.jsonl>
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

REPO_ROOT = Path(__file__).resolve().parents[2]
TASKS_DIR = REPO_ROOT / "docs" / "qa" / "tasks"


def _prim_set(snapshot: Dict[str, Any]) -> Set[str]:
    if not snapshot or "prims" not in snapshot:
        return set()
    return {p["path"] for p in snapshot.get("prims", [])}


def _parse_transcript(path: Path) -> Dict[str, Any]:
    lines = path.read_text().splitlines()
    persona_msgs = []
    assistant_msgs = []
    tool_calls_per_turn: List[List[Dict]] = []
    snapshots: List[Dict] = []  # ordered: initial + after_turn_N
    end_reason = None
    persona = task = None
    modifiers = {}

    for l in lines:
        try:
            d = json.loads(l)
        except json.JSONDecodeError:
            continue
        e = d.get("event")
        if e == "session_start":
            persona = d.get("persona"); task = d.get("task")
            modifiers = d.get("modifiers", {})
        elif e == "persona_message":
            persona_msgs.append(d.get("text", ""))
        elif e == "isaac_assist_reply":
            assistant_msgs.append(d.get("text", ""))
            tool_calls_per_turn.append(d.get("tool_calls", []))
        elif e == "stage_snapshot":
            snapshots.append({"when": d.get("when"), "snapshot": d.get("snapshot", {})})
        elif e == "session_end":
            end_reason = d.get("reason")
        elif e == "session_summary" and not end_reason:
            end_reason = "complete"

    return {
        "persona": persona, "task": task, "modifiers": modifiers,
        "persona_messages": persona_msgs,
        "assistant_messages": assistant_msgs,
        "tool_calls_per_turn": tool_calls_per_turn,
        "snapshots": snapshots,
        "end_reason": end_reason,
        "turns": len(persona_msgs),
    }


def _parse_task_success(task_id: str) -> str:
    p = TASKS_DIR / f"{task_id}.md"
    if not p.exists():
        return ""
    text = p.read_text()
    m = re.search(r'\*\*Success criterion[^*]*\*\*\s*\n(.*?)(?=\n\*\*|\n\Z)', text, re.S)
    return m.group(1).strip() if m else ""


def _extract_prim_paths_from_text(text: str) -> Set[str]:
    """Find all /World/... prim paths mentioned in assistant text."""
    return set(re.findall(r'/World/[A-Za-z_][\w/]*', text))


def judge(tx: Dict[str, Any]) -> Dict[str, Any]:
    snapshots = tx["snapshots"]
    tool_calls_per_turn = tx["tool_calls_per_turn"]
    assistant_msgs = tx["assistant_messages"]

    # Real scene deltas
    initial = snapshots[0]["snapshot"] if snapshots else {}
    final = snapshots[-1]["snapshot"] if snapshots else {}
    initial_prims = _prim_set(initial)
    final_prims = _prim_set(final)
    added = sorted(final_prims - initial_prims)
    removed = sorted(initial_prims - final_prims)

    # Per-turn delta
    per_turn_deltas = []
    prev = initial_prims
    for snap in snapshots[1:]:
        cur = _prim_set(snap["snapshot"])
        per_turn_deltas.append({
            "when": snap["when"],
            "added": sorted(cur - prev),
            "removed": sorted(prev - cur),
            "total_after": len(cur),
        })
        prev = cur

    # Tools: real vs fabricated
    tools_ran = 0
    tools_ok = 0
    for round_ in tool_calls_per_turn:
        for t in round_:
            r = t.get("result", {})
            if r.get("executed"):
                tools_ran += 1
                if r.get("success"):
                    tools_ok += 1

    # Fabrication check: does assistant text claim prims exist that AREN'T in stage?
    claimed_paths: Set[str] = set()
    for m in assistant_msgs:
        claimed_paths |= _extract_prim_paths_from_text(m)
    # Filter out paths that aren't specific prim paths (too generic like /World)
    claimed_paths = {p for p in claimed_paths if p.count("/") >= 2}
    verified_claims = claimed_paths & final_prims
    fabricated_claims = claimed_paths - final_prims

    # Score components
    # 1. Real scene change (did anything happen?)
    scene_changed = len(added) + len(removed) > 0
    # 2. Tools actually ran
    tools_executed = tools_ok > 0
    # 3. Claims grounded (no fabricated paths)
    claim_accuracy = (len(verified_claims) / len(claimed_paths)) if claimed_paths else None
    # 4. Persona not-gave-up
    completed = tx["end_reason"] != "persona_gave_up"

    # Binary verdict
    real_success = scene_changed and tools_executed and (claim_accuracy is None or claim_accuracy >= 0.5)

    return {
        "persona": tx["persona"],
        "task": tx["task"],
        "turns": tx["turns"],
        "end_reason": tx["end_reason"],
        # Real scene changes
        "scene_delta": {
            "prims_added": added,
            "prims_removed": removed,
            "delta_count": len(added) + len(removed),
        },
        "per_turn_deltas": per_turn_deltas,
        # Tool execution (factual from Kit RPC)
        "tools_executed": tools_ran,
        "tools_succeeded": tools_ok,
        # Fabrication detection
        "claimed_prim_paths": sorted(claimed_paths),
        "verified_claims": sorted(verified_claims),
        "fabricated_claims": sorted(fabricated_claims),
        "claim_accuracy": claim_accuracy,
        # Binary real-success verdict
        "real_success": real_success,
        "signals": {
            "scene_changed": scene_changed,
            "tools_executed": tools_executed,
            "completed": completed,
        },
    }


def _judge_one(path: Path) -> Dict[str, Any]:
    tx = _parse_transcript(path)
    return {"transcript": str(path), **judge(tx)}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("transcript", nargs="?")
    p.add_argument("--campaign", help="path to campaign summary JSONL")
    p.add_argument("--out")
    args = p.parse_args()

    if args.campaign:
        summary = Path(args.campaign)
        results = []
        for l in summary.read_text().splitlines():
            e = json.loads(l)
            tr = e.get("transcript")
            if tr:
                results.append(_judge_one(Path(tr)))
        out = args.out or str(summary).replace(".jsonl", "_stagediff.jsonl")
        Path(out).write_text("\n".join(json.dumps(r) for r in results))
        n_real = sum(1 for r in results if r["real_success"])
        print(f"Judged {len(results)} sessions. Real success (scene changed + tools ran + ≥50% claims grounded): {n_real}/{len(results)}")
        for r in results:
            status = "✓" if r["real_success"] else "✗"
            fab = len(r["fabricated_claims"])
            print(f"  {status} {r['persona']}×{r['task']}: +{len(r['scene_delta']['prims_added'])} prims, "
                  f"{r['tools_succeeded']} tools_ok, "
                  f"{fab} fabricated path claims")
        print(f"\nDetails: {out}")
    elif args.transcript:
        print(json.dumps(_judge_one(Path(args.transcript)), indent=2, default=str))
    else:
        p.print_help()


if __name__ == "__main__":
    main()
