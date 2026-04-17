"""
Ground-truth judge — scores sessions using Kit RPC stage snapshots as fact.

Key difference from auto_judge.py and stage_diff_judge.py: this one uses an
LLM judge with INJECTED ground-truth data from snapshots. The LLM never gets
to trust the persona or Assist's text claims about scene state; it sees the
actual prim list, joint positions, timeline state.

Usage:
  python -m scripts.qa.ground_truth_judge <transcript.jsonl>
  python -m scripts.qa.ground_truth_judge --campaign <summary.jsonl> [--out <file>]
"""
from __future__ import annotations
import argparse
import asyncio
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parents[2]
TASKS_DIR = REPO_ROOT / "docs" / "qa" / "tasks"


def _parse_transcript(path: Path) -> Dict[str, Any]:
    lines = path.read_text().splitlines()
    ev = []
    persona = task = end_reason = None
    snapshots = []
    for l in lines:
        try:
            d = json.loads(l)
        except: continue
        e = d.get("event")
        if e == "session_start":
            persona = d.get("persona"); task = d.get("task")
        elif e == "stage_snapshot":
            snapshots.append({"when": d.get("when"), "snapshot": d.get("snapshot", {})})
        elif e == "session_end":
            end_reason = d.get("reason")
        elif e == "session_summary" and not end_reason:
            end_reason = "complete"
        if e in ("persona_message", "isaac_assist_reply"):
            ev.append(d)
    return {
        "persona": persona, "task": task, "end_reason": end_reason,
        "events": ev, "snapshots": snapshots, "turns": sum(1 for x in ev if x.get("event") == "persona_message"),
    }


def _parse_task_sections(task_id: str) -> Dict[str, str]:
    p = TASKS_DIR / f"{task_id}.md"
    if not p.exists():
        return {}
    text = p.read_text()
    sections = {}
    for sec in ["Goal", "Success criterion", "Expected tool chain", "Friction points"]:
        m = re.search(rf'\*\*{re.escape(sec)}[^*]*\*\*\s*\n(.*?)(?=\n\*\*|\n\Z)', text, re.S)
        if m:
            sections[sec] = m.group(1).strip()
    return sections


def _snapshot_summary(snap: Dict) -> str:
    if not snap or snap.get("error"):
        return f"(no snapshot: {snap.get('error','unknown')})"
    prims = snap.get("prims", [])
    user_prims = [p for p in prims if not p["path"].startswith(("/Render", "/OmniverseKit", "/Environment"))]
    lines = [f"prim_count={snap.get('prim_count')} (user_prims={len(user_prims)})"]
    for p in user_prims[:15]:
        apis = p.get("apis", [])
        apis_short = (" [" + ",".join(apis[:3]) + "]") if apis else ""
        lines.append(f"  {p['path']} ({p['type']}){apis_short}")
    if len(user_prims) > 15:
        lines.append(f"  ... and {len(user_prims)-15} more")
    jp = snap.get("joint_positions", {})
    if jp:
        lines.append(f"joint_positions: {dict(list(jp.items())[:5])}")
    tl = snap.get("timeline", {})
    if tl:
        lines.append(f"timeline: playing={tl.get('playing')} t={tl.get('current_time')}")
    cam = snap.get("active_camera")
    if cam:
        lines.append(f"active_camera: {cam}")
    return "\n".join(lines)


def _tool_summary(ev: Dict) -> str:
    tc = ev.get("tool_calls", [])
    if not tc:
        return "(no tools)"
    parts = []
    for t in tc:
        r = t.get("result", {})
        if r.get("executed"):
            tag = "✓" if r.get("success") else "✗"
        else:
            tag = "·"
        parts.append(f"{tag}{t.get('tool','?')}")
    return " ".join(parts)


async def judge_with_gemini(tx: Dict, task_sections: Dict) -> Dict[str, Any]:
    """Call Gemini with task criteria + ground-truth snapshots."""
    sys.path.insert(0, str(REPO_ROOT))
    from service.isaac_assist_service.chat.provider_factory import get_llm_provider
    provider = get_llm_provider()

    # Build a compact transcript + snapshot timeline
    lines = ["=== TASK SPEC ==="]
    lines.append(f"Goal: {task_sections.get('Goal','')[:400]}")
    lines.append(f"Success criterion:\n{task_sections.get('Success criterion','')[:800]}")
    lines.append(f"Expected tool chain:\n{task_sections.get('Expected tool chain','')[:400]}")
    lines.append("\n=== INITIAL STAGE (ground truth) ===")
    if tx["snapshots"]:
        lines.append(_snapshot_summary(tx["snapshots"][0]["snapshot"]))
    lines.append("\n=== SESSION ===")

    snap_idx = 1  # after_turn_N snapshots follow initial
    for i, e in enumerate(tx["events"]):
        et = e.get("event")
        if et == "persona_message":
            lines.append(f"\n--- Persona T{e.get('turn','?')} ---")
            lines.append(e.get("text", "")[:500])
        elif et == "isaac_assist_reply":
            lines.append(f"\n--- Assist T{e.get('turn','?')} [{_tool_summary(e)}] ---")
            lines.append(e.get("text", "")[:500] or "(EMPTY REPLY — Assist produced no text)")
            # Inject snapshot AFTER this turn
            if snap_idx < len(tx["snapshots"]):
                s = tx["snapshots"][snap_idx]["snapshot"]
                lines.append(f"[GROUND TRUTH after T{e.get('turn','?')}]:")
                lines.append(_snapshot_summary(s))
                snap_idx += 1

    convo = "\n".join(lines)[:8000]

    prompt = f"""You are a STRICT QA judge. Score the session based ONLY on:
1. Did the task's success criterion actually get met — verified in the GROUND TRUTH snapshots?
2. Did Assist's text claims match what the snapshot shows, or did it fabricate?

NEVER trust Assist's or the persona's words about scene state. Only trust the GROUND TRUTH snapshot lines.

{convo}

END REASON: {tx.get('end_reason')}

Respond with ONLY this JSON (no markdown fences):
{{
  "real_success": true|false,
  "scene_matched_criterion": true|false,
  "evidence_for_success": "short quote from GROUND TRUTH showing the match (or empty)",
  "fabricated_claims": ["quote of an assist claim that's NOT backed by snapshot", ...],
  "unmatched_criteria": ["criterion text that snapshot doesn't confirm", ...],
  "notes": "one-sentence summary"
}}"""
    resp = await provider.complete(
        [{"role": "user", "content": prompt}],
        {"system_override": "You are a strict QA judge. Return valid JSON only, no markdown."},
    )
    raw = (resp.text or "").strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"): raw = raw[4:]
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        return {"parse_error": str(e), "raw": raw[:600]}


def _judge_one(path: Path) -> Dict[str, Any]:
    tx = _parse_transcript(path)
    sections = _parse_task_sections(tx.get("task") or "")
    try:
        verdict = asyncio.run(judge_with_gemini(tx, sections))
    except Exception as e:
        verdict = {"error": str(e)}
    return {
        "transcript": str(path),
        "persona": tx.get("persona"),
        "task": tx.get("task"),
        "turns": tx.get("turns"),
        "end_reason": tx.get("end_reason"),
        "n_snapshots": len(tx.get("snapshots", [])),
        "verdict": verdict,
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("transcript", nargs="?")
    p.add_argument("--campaign")
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
        out = args.out or str(summary).replace(".jsonl", "_groundtruth.jsonl")
        Path(out).write_text("\n".join(json.dumps(r) for r in results))
        n_real = sum(1 for r in results if r["verdict"].get("real_success"))
        print(f"Judged {len(results)} sessions. Real success per ground truth: {n_real}/{len(results)}")
        for r in results:
            v = r["verdict"]
            tag = "✓" if v.get("real_success") else "✗"
            fab = len(v.get("fabricated_claims", []))
            print(f"  {tag} {r['persona']}×{r['task']}: snapshots={r['n_snapshots']}, fabricated={fab}")
            note = v.get("notes", "")
            if note: print(f"     {note}")
        print(f"\nDetails: {out}")
    elif args.transcript:
        print(json.dumps(_judge_one(Path(args.transcript)), indent=2, default=str))
    else:
        p.print_help()


if __name__ == "__main__":
    main()
