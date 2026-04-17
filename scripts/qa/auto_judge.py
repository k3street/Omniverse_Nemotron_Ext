"""
Auto-judge a Phase 12 session transcript against its task spec.

Reads:
  - Transcript JSONL (persona_message, isaac_assist_reply, session_end events)
  - Task spec markdown (Success criterion, Expected tool chain, Friction points)

Writes:
  - verdict JSON: per-criterion pass/fail, overall score, evidence snippets

Judge model: uses the configured LLM provider with a rubric prompt. Falls back
to heuristic scoring if no LLM available.

Usage:
  python -m scripts.qa.auto_judge <transcript.jsonl>
  python -m scripts.qa.auto_judge --campaign <campaign_summary.jsonl>  # judge all
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parents[2]
TASKS_DIR = REPO_ROOT / "docs" / "qa" / "tasks"


# ── Rubric sections pulled from task files ─────────────────────────────────

def _parse_task(task_id: str) -> Dict[str, Any]:
    """Extract Success criterion, Expected tool chain, Friction points from task md."""
    path = TASKS_DIR / f"{task_id}.md"
    if not path.exists():
        return {"task_id": task_id, "error": f"task file not found: {path}"}
    text = path.read_text()

    def section(name: str) -> str:
        m = re.search(rf'\*\*{re.escape(name)}[^*]*\*\*\s*\n(.*?)(?=\n\*\*|\n\Z)', text, re.S)
        return m.group(1).strip() if m else ""

    expected_tools = re.findall(r'`([a-z_][a-z0-9_]*)`', section("Expected tool chain"))
    return {
        "task_id": task_id,
        "goal": section("Goal"),
        "success_criterion": section("Success criterion"),
        "expected_tools": expected_tools,
        "friction_points": section("Friction points"),
    }


# ── Transcript parsing ─────────────────────────────────────────────────────

def _parse_transcript(path: Path) -> Dict[str, Any]:
    lines = path.read_text().splitlines()
    persona_msgs: List[str] = []
    assistant_msgs: List[str] = []
    tool_calls: List[Dict[str, Any]] = []
    end_reason: Optional[str] = None
    session_meta: Dict[str, Any] = {}

    for l in lines:
        try:
            d = json.loads(l)
        except json.JSONDecodeError:
            continue
        e = d.get("event")
        if e == "session_start":
            session_meta = {
                "persona": d.get("persona"),
                "task": d.get("task"),
                "modifiers": d.get("modifiers", {}),
            }
        elif e == "persona_message":
            persona_msgs.append(d.get("text", ""))
        elif e == "isaac_assist_reply":
            assistant_msgs.append(d.get("text", ""))
            for t in d.get("tool_calls", []):
                r = t.get("result", {})
                tool_calls.append({
                    "tool": t.get("tool"),
                    "executed": r.get("executed"),
                    "success": r.get("success"),
                    "output_preview": (r.get("output", "") or "")[:200],
                })
        elif e == "session_end":
            end_reason = d.get("reason")
        elif e == "session_summary" and not end_reason:
            end_reason = "complete"

    return {
        **session_meta,
        "persona_messages": persona_msgs,
        "assistant_messages": assistant_msgs,
        "tool_calls": tool_calls,
        "end_reason": end_reason,
        "turns": len(persona_msgs),
    }


# ── Heuristic verdict (no LLM) ─────────────────────────────────────────────

def heuristic_verdict(tx: Dict[str, Any], task: Dict[str, Any]) -> Dict[str, Any]:
    """Fast, deterministic rubric. No LLM.

    Criteria (0-5 each):
      - engagement: persona didn't give up early
      - tool_execution: tools actually ran successfully
      - expected_tool_overlap: actual tools match expected
      - hallucination_flags: persona didn't call out hallucination
      - response_discipline: assistant didn't pad action requests
    """
    turns = tx.get("turns", 0)
    tool_calls = tx.get("tool_calls", [])
    end_reason = tx.get("end_reason", "")
    expected = set(task.get("expected_tools", []))
    actual = set(t["tool"] for t in tool_calls if t.get("tool"))

    n_ok = sum(1 for t in tool_calls if t.get("executed") and t.get("success"))
    n_fail = sum(1 for t in tool_calls if t.get("executed") and t.get("success") is False)

    persona_text = " ".join(tx.get("persona_messages", [])).lower()
    halluc_keywords = ["halluc", "making it up", "made that up", "made up", "hallucin", "didn't read what i wrote"]
    halluc_flags = sum(1 for kw in halluc_keywords if kw in persona_text)

    scores = {
        "engagement":            5 if turns >= 3 and end_reason != "persona_gave_up" else (3 if turns >= 2 else 1),
        "tool_execution":        min(5, n_ok) if n_ok > 0 else (1 if n_fail else 0),
        "expected_tool_overlap": round(5 * len(expected & actual) / max(len(expected), 1)) if expected else 3,
        "hallucination_flags":   5 if halluc_flags == 0 else max(0, 5 - halluc_flags * 2),
        "response_discipline":   3,  # heuristic can't judge this; neutral
    }
    total = sum(scores.values())
    return {
        "scores": scores,
        "total": total,
        "max": 25,
        "tools_ok": n_ok,
        "tools_fail": n_fail,
        "expected_tools": sorted(expected),
        "actual_tools": sorted(actual),
        "overlap": sorted(expected & actual),
        "missing_expected": sorted(expected - actual),
        "extra_actual": sorted(actual - expected),
        "hallucination_flags": halluc_flags,
        "end_reason": end_reason,
        "method": "heuristic",
    }


# ── LLM judge (optional, uses GeminiProvider) ──────────────────────────────

async def llm_verdict(tx: Dict[str, Any], task: Dict[str, Any]) -> Dict[str, Any]:
    """Use Gemini to score per-criterion with qualitative justification."""
    sys.path.insert(0, str(REPO_ROOT))
    from service.isaac_assist_service.config import config
    from service.isaac_assist_service.chat.provider_factory import get_provider
    provider = get_provider()

    rubric_path = REPO_ROOT / "docs" / "qa" / "judge_rubric.md"
    rubric = rubric_path.read_text() if rubric_path.exists() else "Score 0-5 per criterion: engagement, tool-execution quality, task success."

    # Build compact transcript summary
    lines = []
    for i, p in enumerate(tx.get("persona_messages", [])[:6]):
        lines.append(f"TURN {i+1} PERSONA: {p[:400]}")
        a = tx.get("assistant_messages", [i])[i] if i < len(tx.get("assistant_messages", [])) else ""
        lines.append(f"TURN {i+1} ASSISTANT: {a[:400]}")
    transcript_text = "\n".join(lines)

    prompt = f"""You are a QA judge scoring a persona-driven chat session with Isaac Assist (an AI embedded in NVIDIA Isaac Sim).

TASK SPEC:
Goal: {task.get('goal', '')[:300]}
Success criterion: {task.get('success_criterion', '')[:500]}
Expected tool chain: {', '.join(task.get('expected_tools', []))}

ACTUAL TOOLS CALLED: {', '.join(t['tool'] for t in tx.get('tool_calls', []) if t.get('tool'))}
END REASON: {tx.get('end_reason', 'unknown')}

TRANSCRIPT:
{transcript_text[:4000]}

Reply with ONLY a JSON object:
{{
  "success_criterion_met": true|false,
  "engagement_score": 0-5,
  "tool_execution_score": 0-5,
  "response_discipline_score": 0-5,
  "hallucination_flags": <int>,
  "key_findings": ["one sentence per finding, max 3"]
}}
"""
    response = await provider.complete(
        [{"role": "user", "content": prompt}],
        {"system_override": "You are a strict QA judge. Return JSON only."},
    )
    raw = (response.text or "").strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    try:
        verdict = json.loads(raw)
        verdict["method"] = "llm"
        return verdict
    except json.JSONDecodeError as e:
        return {"method": "llm", "parse_error": str(e), "raw": raw[:400]}


# ── CLI ────────────────────────────────────────────────────────────────────

def _judge_one(path: Path, use_llm: bool = False) -> Dict[str, Any]:
    tx = _parse_transcript(path)
    task = _parse_task(tx.get("task", ""))
    verdict = heuristic_verdict(tx, task)
    if use_llm:
        import asyncio
        try:
            verdict["llm"] = asyncio.run(llm_verdict(tx, task))
        except Exception as e:
            verdict["llm_error"] = str(e)
    return {
        "transcript": str(path),
        "persona": tx.get("persona"),
        "task": tx.get("task"),
        "turns": tx.get("turns"),
        "verdict": verdict,
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("transcript", nargs="?", help="path to JSONL transcript")
    p.add_argument("--campaign", help="path to campaign summary JSONL")
    p.add_argument("--llm", action="store_true", help="use LLM judge")
    p.add_argument("--out", help="write verdicts to this file")
    args = p.parse_args()

    if args.campaign:
        summary = Path(args.campaign)
        results = []
        for l in summary.read_text().splitlines():
            e = json.loads(l)
            tr = e.get("transcript")
            if tr:
                results.append(_judge_one(Path(tr), use_llm=args.llm))
        out = args.out or str(summary).replace(".jsonl", "_verdicts.jsonl")
        Path(out).write_text("\n".join(json.dumps(r) for r in results))
        # Summary print
        totals = [r["verdict"]["total"] for r in results if "total" in r.get("verdict", {})]
        print(f"Judged {len(results)} sessions. Avg heuristic score: {sum(totals)/max(len(totals),1):.1f}/25")
        print(f"Verdicts written: {out}")
    elif args.transcript:
        r = _judge_one(Path(args.transcript), use_llm=args.llm)
        print(json.dumps(r, indent=2))
    else:
        p.print_help()


if __name__ == "__main__":
    main()
