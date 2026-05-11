"""
analyze_tool_result_sizes.py — Track C 9.2 analysis.

Replays existing run_direct/*.jsonl + session_traces to measure the
empirical distribution of:
  - tool_result individual sizes (per call, per tool name)
  - cumulative payload size over a session (running sum, after each call)
  - which tools dominate growth

Output: a markdown summary in workspace/qa_runs/tool_result_size_analysis.md
plus stdout one-line digest.

This is read-only: it does NOT write to source code, only emits a report.
Used to validate hypotheses about Gemini 503:
  H1: large payload (>1MB) → server rejects
  H2: per-tool outliers (e.g. find_prims_by_schema, scene_summary, etc)
  H3: linear growth dominates (every tool small, accumulation blows up)

Usage:
  python scripts/qa/analyze_tool_result_sizes.py
  python scripts/qa/analyze_tool_result_sizes.py --task VR-19
  python scripts/qa/analyze_tool_result_sizes.py --top 30
"""
from __future__ import annotations

import argparse
import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, List, Tuple

REPO_ROOT = Path(__file__).resolve().parents[2]
QA_RUNS = REPO_ROOT / "workspace" / "qa_runs"
SESSION_TRACES = REPO_ROOT / "workspace" / "session_traces"
REPORT_PATH = QA_RUNS / "tool_result_size_analysis.md"


def percentile(sorted_vals: List[int], p: float) -> int:
    if not sorted_vals:
        return 0
    idx = min(int(p * len(sorted_vals)), len(sorted_vals) - 1)
    return sorted_vals[idx]


def fmt_bytes(n: int) -> str:
    if n < 1024:
        return f"{n}B"
    if n < 1024 * 1024:
        return f"{n/1024:.1f}KB"
    return f"{n/1024/1024:.2f}MB"


def collect_run_direct_data(task_filter: str | None = None) -> Dict:
    """Walk run_direct_*/{TASK}_direct.jsonl files. Each isaac_assist_reply
    carries a tool_calls array; each entry has 'tool', 'arguments',
    optionally 'result'. We measure result-size.

    NOTE: stage_snapshots in these files are harness-side events (written
    by direct_eval.py for offline analysis); they do NOT enter the LLM
    payload. Cumulative size below excludes them — only tool_calls (which
    DO appear in the orchestrator's messages history) count toward the
    LLM-payload estimate.
    """
    runs = sorted(QA_RUNS.glob("run_direct_*"))

    # Per individual tool_result
    per_tool_sizes: Dict[str, List[int]] = defaultdict(list)
    # Per-session cumulative growth — TOOL CALLS ONLY (LLM-payload proxy)
    session_growth: List[Dict] = []
    # Stage_snapshot sizes — for context only; NOT in LLM payload
    stage_snapshot_sizes: List[int] = []
    # Top single biggest results we ever saw
    biggest_results: List[Tuple[int, str, str]] = []  # (size, tool, run_name)

    for run in runs:
        files = list(run.glob("*_direct.jsonl"))
        if not files:
            continue
        f = files[0]
        if task_filter and task_filter not in f.name:
            continue

        cumulative = 0  # tool_call results only — proxy for LLM-payload
        tool_count = 0
        biggest_in_run = 0
        biggest_in_run_name = ""
        try:
            with open(f) as fh:
                for line in fh:
                    try:
                        d = json.loads(line)
                    except Exception:
                        continue
                    ev = d.get("event")

                    if ev == "stage_snapshot":
                        # Harness-only; tracked for context but excluded
                        # from the LLM-payload cumulative.
                        snap = d.get("snapshot") or {}
                        sz = len(json.dumps(snap, default=str))
                        stage_snapshot_sizes.append(sz)

                    elif ev == "isaac_assist_reply":
                        # tool_calls is a list; each call's result contributes
                        for call in d.get("tool_calls") or []:
                            tool_name = call.get("tool") or call.get("name") or "?"
                            result = call.get("result")
                            if result is None:
                                # Sometimes 'output' or just 'success'
                                result = call.get("output") or call.get("success")
                            sz = len(json.dumps(result, default=str)) if result is not None else 0
                            per_tool_sizes[tool_name].append(sz)
                            cumulative += sz
                            tool_count += 1
                            if sz > biggest_in_run:
                                biggest_in_run = sz
                                biggest_in_run_name = tool_name
                            biggest_results.append((sz, tool_name, run.name))
        except Exception:
            continue

        if tool_count > 0:
            session_growth.append({
                "run": run.name,
                "task": f.name.replace("_direct.jsonl", ""),
                "tool_count": tool_count,
                "cumulative_bytes": cumulative,
                "biggest_single": biggest_in_run,
                "biggest_single_tool": biggest_in_run_name,
            })

    return {
        "per_tool_sizes": dict(per_tool_sizes),
        "session_growth": session_growth,
        "stage_snapshot_sizes": stage_snapshot_sizes,
        "biggest_results": sorted(biggest_results, reverse=True)[:50],
    }


def render_report(data: Dict, top_k: int = 20) -> str:
    """Render markdown report from collected data."""
    lines: List[str] = []
    add = lines.append

    add("# Tool result size analysis — Track C 9.2")
    add("")
    add("Empirical distribution of **tool result** byte sizes")
    add("from `workspace/qa_runs/run_direct_*/*.jsonl`. Used to evaluate")
    add("Gemini 503 hypotheses (cf. `2026-05-08-kcode-research-and-vault-spec.md`).")
    add("")
    add("**Scope & limitations:** This is a *lower bound* on LLM payload —")
    add("we sum only `tool_call.result` bytes recorded in the harness logs.")
    add("The actual Gemini request also includes:")
    add("")
    add("- system prompt (~few KB)")
    add("- `functionDeclarations`: **321.5 KB** for the full 374-tool")
    add("  registry, **17.4 KB** when `distill_context` selects ~30 tools")
    add("  (measured via `ISAAC_SIM_TOOLS` direct dump on 2026-05-07)")
    add("- prior assistant `text` and `tool_calls` blocks")
    add("- user-message turns")
    add("")
    add("Real payload ≈ `cumulative_below + 17-321KB schemas + system + history`.")
    add("Stage_snapshot events are harness-only and excluded from cumulative.")
    add("")
    add("For ground-truth payload at 503-time, see `provider_incidents.jsonl`")
    add("populated by Track C 9.1 instrumentation in `llm_gemini.py`.")
    add("")

    # ── Per-tool size distribution ──
    add("## Per-tool result size distribution")
    add("")
    per_tool = data["per_tool_sizes"]
    total_calls = sum(len(v) for v in per_tool.values())
    add(f"Tools observed: {len(per_tool)}; total result samples: {total_calls}")
    add("")
    add("| Tool | n | median | p90 | p99 | max | total |")
    add("|------|---|-------:|----:|----:|----:|------:|")
    rows = []
    for tool, sizes in per_tool.items():
        s = sorted(sizes)
        rows.append((
            tool, len(s), s[len(s)//2] if s else 0,
            percentile(s, 0.9), percentile(s, 0.99),
            max(s) if s else 0, sum(s),
        ))
    # Sort by total bytes — biggest payload contributors first
    rows.sort(key=lambda r: -r[6])
    for tool, n, med, p90, p99, mx, tot in rows[:top_k]:
        add(f"| `{tool}` | {n} | {fmt_bytes(med)} | {fmt_bytes(p90)} | "
            f"{fmt_bytes(p99)} | {fmt_bytes(mx)} | {fmt_bytes(tot)} |")
    if len(rows) > top_k:
        add(f"| ... ({len(rows)-top_k} more truncated) | | | | | | |")
    add("")

    # ── Per-session cumulative ──
    add("## Per-session cumulative payload growth")
    add("")
    growth = data["session_growth"]
    if growth:
        cums = sorted([g["cumulative_bytes"] for g in growth])
        tcs = sorted([g["tool_count"] for g in growth])
        add(f"Sessions with ≥1 tool call: {len(growth)}")
        add("")
        add("| Statistic | tool_count | cumulative_bytes |")
        add("|-----------|-----------:|-----------------:|")
        add(f"| median | {tcs[len(tcs)//2]} | {fmt_bytes(cums[len(cums)//2])} |")
        add(f"| p90    | {percentile(tcs, 0.9)} | {fmt_bytes(percentile(cums, 0.9))} |")
        add(f"| p99    | {percentile(tcs, 0.99)} | {fmt_bytes(percentile(cums, 0.99))} |")
        add(f"| max    | {tcs[-1]} | {fmt_bytes(cums[-1])} |")
        add("")

        # Top 10 biggest sessions
        add("### Top 10 biggest cumulative sessions")
        add("")
        add("| Run | Task | Tool calls | Cumulative |")
        add("|-----|------|-----------:|-----------:|")
        for g in sorted(growth, key=lambda x: -x["cumulative_bytes"])[:10]:
            add(f"| `{g['run'][:40]}` | {g['task']} | {g['tool_count']} | "
                f"{fmt_bytes(g['cumulative_bytes'])} |")
        add("")

    # ── Stage snapshots ──
    add("## Stage-snapshot sizes")
    add("")
    snaps = sorted(data["stage_snapshot_sizes"])
    if snaps:
        add(f"Snapshots observed: {len(snaps)}")
        add(f"- median: {fmt_bytes(snaps[len(snaps)//2])}")
        add(f"- p90: {fmt_bytes(percentile(snaps, 0.9))}")
        add(f"- p99: {fmt_bytes(percentile(snaps, 0.99))}")
        add(f"- max: {fmt_bytes(snaps[-1])}")
        add("")
    else:
        add("No stage_snapshots observed.")
        add("")

    # ── Top single biggest ──
    add("## Top 20 single-call biggest results")
    add("")
    add("| Size | Tool | Run |")
    add("|-----:|------|-----|")
    for sz, tool, run_name in data["biggest_results"][:20]:
        add(f"| {fmt_bytes(sz)} | `{tool}` | `{run_name[:40]}` |")
    add("")

    # ── Hypothesis evaluation ──
    add("## Hypothesis check (vs. observed data)")
    add("")
    if growth:
        cums = [g["cumulative_bytes"] for g in growth]
        max_cum = max(cums)
        n_over_500kb = sum(1 for c in cums if c > 500_000)
        n_over_1mb = sum(1 for c in cums if c > 1_000_000)
        add(f"- Sessions exceeding 500KB cumulative tool-results: "
            f"{n_over_500kb}/{len(growth)} ({100*n_over_500kb/len(growth):.0f}%)")
        add(f"- Sessions exceeding 1MB cumulative tool-results: "
            f"{n_over_1mb}/{len(growth)} ({100*n_over_1mb/len(growth):.0f}%)")
        add(f"- Largest observed cumulative tool-results: {fmt_bytes(max_cum)}")
        add("")
        add("Adding measured schema overhead:")
        add(f"- Worst case with FULL schemas (no distill): "
            f"{fmt_bytes(max_cum + 321_500)} (still under 1MB)")
        add(f"- Worst case with DISTILLED schemas: "
            f"{fmt_bytes(max_cum + 17_400)}")
        add("")
        add("**H1 (payload >1MB → 503)**: " + (
            "PLAUSIBLE — sessions plus full schemas reach this range."
            if (max_cum + 321_500) > 1_000_000 else
            "**CURRENTLY UNSUPPORTED** — even worst-case "
            f"({fmt_bytes(max_cum + 321_500)} tool-results + full schemas) "
            "stays under 1MB. If 503s fire often, the cause is more likely "
            "rate-limit (RPM/TPM), provider instability, or specific request "
            "shape — not raw payload size. Track C 9.1 instrumentation will "
            "confirm at 503-time."
        ))
        add("")
        # H2: outliers
        if per_tool:
            outliers = [(t, max(s)) for t, s in per_tool.items() if s and max(s) > 50_000]
            outliers.sort(key=lambda x: -x[1])
            if outliers:
                add("**H2 (per-tool outliers)**: tools producing >50KB single results:")
                for t, mx in outliers[:8]:
                    add(f"  - `{t}`: max {fmt_bytes(mx)}")
            else:
                add("**H2 (per-tool outliers)**: no tool produces >50KB single results in observed data.")
            add("")
        # H3: linear growth
        avg_per_call = sum(g["cumulative_bytes"] for g in growth) / max(1, sum(g["tool_count"] for g in growth))
        add(f"**H3 (linear growth dominates)**: avg bytes per tool call = {fmt_bytes(int(avg_per_call))}.")
        add("")

    add("---")
    add("")
    add("*Generated by `scripts/qa/analyze_tool_result_sizes.py`*")
    return "\n".join(lines)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--task", default=None,
                   help="Filter to a specific task ID (substring match).")
    p.add_argument("--top", type=int, default=20,
                   help="Top-K rows in per-tool table.")
    p.add_argument("--out", default=str(REPORT_PATH))
    args = p.parse_args()

    print(f"[analyze] scanning {QA_RUNS}/run_direct_*  task_filter={args.task or 'ALL'}")
    data = collect_run_direct_data(task_filter=args.task)

    # One-line digest
    pt = data["per_tool_sizes"]
    growth = data["session_growth"]
    n_calls = sum(len(v) for v in pt.values())
    n_sessions = len(growth)
    if growth:
        max_cum = max(g["cumulative_bytes"] for g in growth)
        med_cum = sorted(g["cumulative_bytes"] for g in growth)[n_sessions//2]
    else:
        max_cum = med_cum = 0
    print(f"[analyze] sessions={n_sessions}  total_calls={n_calls}  "
          f"med_cum={fmt_bytes(med_cum)}  max_cum={fmt_bytes(max_cum)}")

    report = render_report(data, top_k=args.top)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(report, encoding="utf-8")
    print(f"[analyze] wrote {args.out}  ({len(report)} chars)")


if __name__ == "__main__":
    main()
