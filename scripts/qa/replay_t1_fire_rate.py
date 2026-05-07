"""
replay_t1_fire_rate.py — Track C 9.3 retrospective measurement.

Live "Canonical match" log entries are too sparse (~9 fires, all from
test sessions) to estimate T1-fire-rate empirically. Instead, replay
each historical session's user_message through the template_retriever
and count how many WOULD have triggered hard-instantiate at current
thresholds (sim≥0.45 AND margin≥0.20).

Output: distribution of top_sim, margin, would_instantiate per session.
Aggregated: fraction of historical user prompts that would hit T1.

Why this matters:
- High T1-fire-rate (>30%) means hard-instantiate carries most of the
  agent's quality work — canonical coverage is the leverage axis
- Low T1-fire-rate (<10%) means we mostly fall back to LLM-iteration
  (existing path); canonical work is rare-case insurance
- This calibrates how much investment in canonical-coverage expansion
  vs. LLM-path quality is justified

Pure-additive, no Kit, no provider. Local ChromaDB + sentence-transformers.

Usage:
  python scripts/qa/replay_t1_fire_rate.py
  python scripts/qa/replay_t1_fire_rate.py --max-sessions 200  # quick sample
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path
from typing import Dict, List

REPO_ROOT = Path(__file__).resolve().parents[2]
QA_RUNS = REPO_ROOT / "workspace" / "qa_runs"
REPORT_PATH = QA_RUNS / "t1_fire_rate_layer1.md"


def percentile(vals: List[float], p: float) -> float:
    if not vals:
        return 0.0
    s = sorted(vals)
    idx = min(int(p * len(s)), len(s) - 1)
    return s[idx]


def collect_user_messages(max_sessions: int | None = None) -> List[Dict]:
    """Walk run_direct files; return [{run, task, query}] for sessions
    with a user_message we can replay."""
    out: List[Dict] = []
    runs = sorted(QA_RUNS.glob("run_direct_*"))
    if max_sessions:
        runs = runs[:max_sessions]
    for r in runs:
        files = list(r.glob("*_direct.jsonl"))
        if not files:
            continue
        f = files[0]
        try:
            with open(f) as fh:
                for line in fh:
                    try:
                        d = json.loads(line)
                    except Exception:
                        continue
                    if d.get("event") == "direct_eval_start":
                        q = d.get("query", "")
                        if q:
                            out.append({
                                "run": r.name,
                                "task": d.get("task", "?"),
                                "query": q,
                            })
                        break
        except Exception:
            continue
    return out


def _grid_sensitivity(
    results: List[Dict],
    sim_grid: List[float],
    margin_grid: List[float],
) -> List[Dict]:
    """For each (sim, margin) threshold pair, count fire-rate using
    cached top_sim/margin from results."""
    rows = []
    for sim in sim_grid:
        for margin in margin_grid:
            n_fire = sum(
                1 for r in results
                if "top_sim" in r
                and r["top_sim"] >= sim
                and r["margin"] >= margin
            )
            n = sum(1 for r in results if "top_sim" in r)
            rate = n_fire / n if n > 0 else 0
            rows.append({
                "sim": sim, "margin": margin,
                "fire_rate": rate, "n_fire": n_fire, "n_total": n,
            })
    return rows


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--max-sessions", type=int, default=None)
    p.add_argument("--sensitivity", action="store_true",
                   help="Also output a (sim, margin) sensitivity grid")
    p.add_argument("--out", default=str(REPORT_PATH))
    args = p.parse_args()

    # Read thresholds the orchestrator uses
    sim_thr = float(os.environ.get("CANONICAL_MIN_SIM", "0.45"))
    margin_thr = float(os.environ.get("CANONICAL_MIN_MARGIN", "0.20"))

    print(f"[t1-replay] thresholds: sim≥{sim_thr} AND margin≥{margin_thr}")
    sessions = collect_user_messages(args.max_sessions)
    print(f"[t1-replay] {len(sessions)} sessions with user_message to replay")

    if not sessions:
        print("[t1-replay] no data — bailing")
        return 1

    sys.path.insert(0, str(REPO_ROOT))
    from service.isaac_assist_service.chat.tools.template_retriever import (
        retrieve_templates_with_scores,
    )

    # Run retrieval on each
    results: List[Dict] = []
    for i, s in enumerate(sessions):
        if i % 100 == 0:
            print(f"[t1-replay] {i}/{len(sessions)}...")
        try:
            scored = retrieve_templates_with_scores(s["query"], top_k=3)
        except Exception as e:
            results.append({"task": s["task"], "error": str(e)[:80]})
            continue
        top_sim = scored[0]["similarity"] if scored else 0.0
        second_sim = scored[1]["similarity"] if len(scored) > 1 else 0.0
        margin = top_sim - second_sim
        would_fire = bool(scored and top_sim >= sim_thr and margin >= margin_thr)
        results.append({
            "task": s["task"],
            "top_id": scored[0]["template"].get("task_id") if scored else None,
            "top_sim": round(top_sim, 3),
            "margin": round(margin, 3),
            "would_fire": would_fire,
        })

    # Aggregate
    n = len(results)
    n_fire = sum(1 for r in results if r.get("would_fire"))
    fire_rate = n_fire / n if n > 0 else 0
    sims = [r["top_sim"] for r in results if "top_sim" in r]
    margins = [r["margin"] for r in results if "margin" in r]
    by_task = Counter(r["task"] for r in results if r.get("would_fire"))
    by_top = Counter(r["top_id"] for r in results if r.get("would_fire") and r.get("top_id"))

    # Render report
    lines = ["# T1-fire-rate retrospective replay (Track C 9.3)"]
    lines.append("")
    lines.append("Replays each historical session's first `user_message` "
                 "(from `direct_eval_start` events in `qa_runs/run_direct_*`) "
                 "through `template_retriever.retrieve_templates_with_scores` "
                 "and counts how many WOULD have triggered the hard-instantiate "
                 "path at production thresholds.")
    lines.append("")
    lines.append(f"**Thresholds**: top_sim ≥ {sim_thr} AND margin ≥ {margin_thr}")
    lines.append(f"**Sessions analyzed**: {n}")
    lines.append("")
    lines.append("## Aggregate")
    lines.append("")
    lines.append(f"- Sessions that WOULD fire hard-instantiate: "
                 f"**{n_fire}/{n} ({100*fire_rate:.0f}%)**")
    lines.append("")

    if sims:
        lines.append(f"### top_sim distribution (n={len(sims)})")
        lines.append(f"- median: {percentile(sims, 0.5):.3f}")
        lines.append(f"- p90: {percentile(sims, 0.9):.3f}")
        lines.append(f"- max: {max(sims):.3f}")
        lines.append(f"- min: {min(sims):.3f}")
        lines.append("")

    if margins:
        lines.append(f"### margin (top - second) distribution")
        lines.append(f"- median: {percentile(margins, 0.5):.3f}")
        lines.append(f"- p90: {percentile(margins, 0.9):.3f}")
        lines.append(f"- max: {max(margins):.3f}")
        lines.append("")

    if by_task:
        lines.append("### Tasks that would fire (top 10 by count)")
        lines.append("")
        lines.append("| Task | Sessions firing |")
        lines.append("|------|----------------:|")
        for t, c in by_task.most_common(10):
            lines.append(f"| `{t}` | {c} |")
        lines.append("")

    if by_top:
        lines.append("### Canonical templates that would be instantiated (top 10)")
        lines.append("")
        lines.append("| Template ID | Sessions matching |")
        lines.append("|-------------|------------------:|")
        for tid, c in by_top.most_common(10):
            lines.append(f"| `{tid}` | {c} |")
        lines.append("")

    # Verdict
    lines.append("## Interpretation")
    lines.append("")
    if fire_rate >= 0.30:
        verdict = (
            f"**HIGH T1-fire-rate ({100*fire_rate:.0f}%)** — canonical "
            "coverage is the dominant agent quality axis. Investment in "
            "expanding canonical templates (CP-06+, additional task families) "
            "has high leverage. The LLM-iteration path serves as fallback."
        )
    elif fire_rate >= 0.10:
        verdict = (
            f"**MODERATE T1-fire-rate ({100*fire_rate:.0f}%)** — hard-"
            "instantiate handles a meaningful subset; LLM-iteration handles "
            "the rest. Both axes warrant investment proportional to coverage. "
            "Adding canonicals for the long-tail tasks remains valuable but "
            "not dominant."
        )
    else:
        verdict = (
            f"**LOW T1-fire-rate ({100*fire_rate:.0f}%)** — historical sessions "
            "don't strongly resemble existing canonical templates. LLM-iteration "
            "path carries most of the agent's quality work. Two interpretations: "
            "(a) the canonical library covers narrow specific scenes that don't "
            "match the open-ended task distribution, or (b) thresholds are too "
            "strict. Either way, **canonical-coverage expansion is not the "
            "dominant leverage axis** — focus on LLM-path quality (model, "
            "prompts, retrieval, harness) instead."
        )
    lines.append(verdict)
    lines.append("")

    lines.append("## Caveats")
    lines.append("")
    lines.append("- The `direct_eval` task distribution is biased toward "
                 "evaluation-suite tasks, not the natural distribution of "
                 "user prompts in production chat. Real-world fire-rate may "
                 "differ.")
    lines.append("- Threshold-based gating: small sim threshold changes can "
                 "swing the rate substantially. Consider sensitivity analysis "
                 "if the fire-rate is near the borderline.")
    lines.append("- This counts \"would have fired\" — actual production fires "
                 "may be lower if the orchestrator's user_message extraction "
                 "differs from `direct_eval_start.query`.")
    lines.append("")
    # Sensitivity grid (when --sensitivity)
    if args.sensitivity:
        lines.append("## Sensitivity grid (sim threshold × margin threshold)")
        lines.append("")
        lines.append("Fire-rate at each threshold pair. Production = "
                     f"sim≥{sim_thr} AND margin≥{margin_thr} (highlighted).")
        lines.append("")
        sim_grid = [0.40, 0.45, 0.50, 0.55, 0.60, 0.65]
        margin_grid = [0.05, 0.10, 0.15, 0.20, 0.25, 0.30]
        grid = _grid_sensitivity(results, sim_grid, margin_grid)
        # Render as a table — rows = sim, cols = margin
        header = "| sim ↓ \\ margin → | " + " | ".join(
            f"≥{m:.2f}" for m in margin_grid
        ) + " |"
        sep = "|" + "---|" * (len(margin_grid) + 1)
        lines.append(header)
        lines.append(sep)
        for sim in sim_grid:
            cells = []
            for margin in margin_grid:
                row = next(
                    g for g in grid
                    if abs(g["sim"] - sim) < 1e-9
                    and abs(g["margin"] - margin) < 1e-9
                )
                marker = "**" if (
                    abs(sim - sim_thr) < 1e-9
                    and abs(margin - margin_thr) < 1e-9
                ) else ""
                cells.append(f"{marker}{100*row['fire_rate']:.0f}%{marker}")
            lines.append(f"| ≥{sim:.2f} | " + " | ".join(cells) + " |")
        lines.append("")
        # Identify nearest cells with target fire-rates
        targets = [0.20, 0.30, 0.50, 0.75]
        lines.append("### Nearest threshold pairs to common target rates")
        lines.append("")
        lines.append("| Target | Closest (sim, margin) | Actual rate |")
        lines.append("|--------|------------------------|------------:|")
        for tgt in targets:
            closest = min(grid, key=lambda g: abs(g["fire_rate"] - tgt))
            lines.append(
                f"| {100*tgt:.0f}% | sim≥{closest['sim']:.2f}, "
                f"margin≥{closest['margin']:.2f} | "
                f"{100*closest['fire_rate']:.0f}% |"
            )
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("*Generated by `scripts/qa/replay_t1_fire_rate.py`*")

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text("\n".join(lines), encoding="utf-8")
    print(f"\n[t1-replay] would-fire: {n_fire}/{n} ({100*fire_rate:.0f}%)")
    print(f"[t1-replay] median sim: {percentile(sims, 0.5):.3f}, "
          f"median margin: {percentile(margins, 0.5):.3f}")
    print(f"[t1-replay] wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
