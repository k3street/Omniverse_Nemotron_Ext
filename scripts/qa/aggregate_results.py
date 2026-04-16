"""Aggregate judge verdicts across all sessions in a campaign.

Reads either:
  (a) a directory of `<persona>__<task>.judge.json` files, OR
  (b) a JSONL file with one verdict per line.

Produces a `report` dict (and optional human-readable text rollup) covering:

* completion rate (completed / total)
* mean and median weighted_total
* per-persona completion rates (lowest first)
* per-task completion rates (lowest first)
* top failure modes (count-sorted)
* most-requested missing tools (count-sorted)
* score distribution per criterion

CLI:
    python -m scripts.qa.aggregate_results --verdicts-dir workspace/qa_runs/run_X/verdicts
    python -m scripts.qa.aggregate_results --verdicts-jsonl all.jsonl --text
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from scripts.qa.judge_session import CRITERIA, VALID_COMPLETIONS

# ---------------------------------------------------------------------------
# Verdict loading
# ---------------------------------------------------------------------------


def load_verdicts_from_dir(verdicts_dir: Path) -> List[Dict[str, object]]:
    out: List[Dict[str, object]] = []
    for path in sorted(verdicts_dir.glob("*.json")):
        try:
            out.append(json.loads(path.read_text(encoding="utf-8")))
        except json.JSONDecodeError:
            continue
    return out


def load_verdicts_from_jsonl(path: Path) -> List[Dict[str, object]]:
    out: List[Dict[str, object]] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------


@dataclass
class CampaignReport:
    session_count: int
    completion_counts: Dict[str, int]
    completion_rate: float
    weighted_total_mean: float
    weighted_total_median: float
    per_persona: Dict[str, Dict[str, object]]
    per_task: Dict[str, Dict[str, object]]
    top_failure_modes: List[List[object]]
    top_missing_tools: List[List[object]]
    criterion_means: Dict[str, float]

    def as_dict(self) -> Dict[str, object]:
        return {
            "session_count": self.session_count,
            "completion_counts": self.completion_counts,
            "completion_rate": round(self.completion_rate, 4),
            "weighted_total_mean": round(self.weighted_total_mean, 2),
            "weighted_total_median": round(self.weighted_total_median, 2),
            "per_persona": self.per_persona,
            "per_task": self.per_task,
            "top_failure_modes": self.top_failure_modes,
            "top_missing_tools": self.top_missing_tools,
            "criterion_means": {k: round(v, 2) for k, v in self.criterion_means.items()},
        }


def _split_session_id(session_id: str) -> tuple[str, str]:
    if "__" not in session_id:
        return ("", session_id)
    persona, _, task = session_id.partition("__")
    return persona, task


def aggregate(verdicts: Iterable[Dict[str, object]]) -> CampaignReport:
    verdicts = list(verdicts)
    n = len(verdicts)

    completion_counts: Counter[str] = Counter()
    weighted_totals: List[float] = []
    failure_modes: Counter[str] = Counter()
    missing_tools: Counter[str] = Counter()
    per_persona_total: Dict[str, List[int]] = defaultdict(list)
    per_persona_completed: Dict[str, int] = defaultdict(int)
    per_task_total: Dict[str, List[int]] = defaultdict(list)
    per_task_completed: Dict[str, int] = defaultdict(int)
    crit_scores: Dict[str, List[int]] = {k: [] for k in CRITERIA}

    for v in verdicts:
        completion = v.get("completion", "partial")
        if completion not in VALID_COMPLETIONS:
            completion = "partial"
        completion_counts[completion] += 1

        wt = v.get("weighted_total")
        if isinstance(wt, (int, float)):
            weighted_totals.append(float(wt))

        scores = v.get("scores", {})
        if isinstance(scores, dict):
            for crit in CRITERIA:
                val = scores.get(crit)
                if isinstance(val, int) and 1 <= val <= 5:
                    crit_scores[crit].append(val)

        for fm in v.get("failure_modes", []) or []:
            if fm:
                failure_modes[str(fm)] += 1
        for mt in v.get("missing_tools", []) or []:
            if mt:
                missing_tools[str(mt)] += 1

        sid = str(v.get("session_id", ""))
        persona, task = _split_session_id(sid)
        if persona:
            if isinstance(wt, (int, float)):
                per_persona_total[persona].append(int(wt))
            if completion == "completed":
                per_persona_completed[persona] += 1
        if task:
            if isinstance(wt, (int, float)):
                per_task_total[task].append(int(wt))
            if completion == "completed":
                per_task_completed[task] += 1

    completion_rate = (completion_counts["completed"] / n) if n else 0.0

    def _persona_rollup(scores: List[int], completed: int, total: int) -> Dict[str, object]:
        return {
            "session_count": total,
            "completed": completed,
            "completion_rate": round(completed / total, 4) if total else 0.0,
            "mean_weighted_total": round(statistics.mean(scores), 2) if scores else None,
        }

    per_persona: Dict[str, Dict[str, object]] = {}
    persona_totals: Dict[str, int] = {}
    for v in verdicts:
        sid = str(v.get("session_id", ""))
        persona, _ = _split_session_id(sid)
        if persona:
            persona_totals[persona] = persona_totals.get(persona, 0) + 1
    for persona, total in persona_totals.items():
        per_persona[persona] = _persona_rollup(
            per_persona_total[persona],
            per_persona_completed[persona],
            total,
        )

    per_task: Dict[str, Dict[str, object]] = {}
    task_totals: Dict[str, int] = {}
    for v in verdicts:
        sid = str(v.get("session_id", ""))
        _, task = _split_session_id(sid)
        if task:
            task_totals[task] = task_totals.get(task, 0) + 1
    for task, total in task_totals.items():
        per_task[task] = _persona_rollup(
            per_task_total[task],
            per_task_completed[task],
            total,
        )

    return CampaignReport(
        session_count=n,
        completion_counts=dict(completion_counts),
        completion_rate=completion_rate,
        weighted_total_mean=statistics.mean(weighted_totals) if weighted_totals else 0.0,
        weighted_total_median=statistics.median(weighted_totals) if weighted_totals else 0.0,
        per_persona=per_persona,
        per_task=per_task,
        top_failure_modes=[[mode, count] for mode, count in failure_modes.most_common(10)],
        top_missing_tools=[[tool, count] for tool, count in missing_tools.most_common(10)],
        criterion_means={
            k: (statistics.mean(v) if v else 0.0) for k, v in crit_scores.items()
        },
    )


# ---------------------------------------------------------------------------
# Text rendering
# ---------------------------------------------------------------------------


def render_text(report: CampaignReport) -> str:
    lines: List[str] = []
    lines.append(f"Campaign: {report.session_count} sessions")
    lines.append(f"Completion rate: {report.completion_rate * 100:.0f}%")
    lines.append(
        f"Mean weighted total: {report.weighted_total_mean:.1f} "
        f"(median {report.weighted_total_median:.1f}, scale 0-100)"
    )
    lines.append("")
    lines.append("Criterion means (1-5):")
    for k in CRITERIA:
        lines.append(f"  {k:<24} {report.criterion_means[k]:.2f}")
    lines.append("")
    lines.append("Personas with lowest completion rate:")
    persona_sorted = sorted(
        report.per_persona.items(),
        key=lambda kv: (kv[1].get("completion_rate", 0.0), kv[0]),
    )
    for persona, stats in persona_sorted[:5]:
        rate = float(stats.get("completion_rate", 0.0)) * 100
        lines.append(f"  {persona:<16} {rate:>4.0f}% ({stats['completed']}/{stats['session_count']})")
    lines.append("")
    lines.append("Top failure modes:")
    for mode, count in report.top_failure_modes:
        lines.append(f"  {count:>3}x  {mode}")
    lines.append("")
    lines.append("Most-requested missing tools:")
    for tool, count in report.top_missing_tools:
        lines.append(f"  {count:>3}x  {tool}")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _cli(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description="Aggregate QA judge verdicts into a campaign report.")
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--verdicts-dir", type=Path, help="Directory of *.json verdict files")
    src.add_argument("--verdicts-jsonl", type=Path, help="JSONL file, one verdict per line")
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--text", action="store_true", help="Emit human-readable text rollup")
    args = parser.parse_args(argv)

    if args.verdicts_dir:
        verdicts = load_verdicts_from_dir(args.verdicts_dir)
    else:
        verdicts = load_verdicts_from_jsonl(args.verdicts_jsonl)

    report = aggregate(verdicts)
    out = render_text(report) if args.text else json.dumps(report.as_dict(), indent=2)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(out, encoding="utf-8")
    else:
        sys.stdout.write(out)
        if not out.endswith("\n"):
            sys.stdout.write("\n")
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry
    raise SystemExit(_cli())
