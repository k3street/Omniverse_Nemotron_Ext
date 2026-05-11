"""
replay_compression_effect.py — Track C 9.2 follow-up.

For each historical session in qa_runs/run_direct_*, reconstruct the
orchestrator's messages list as it grew turn-by-turn, and measure:

- payload size BEFORE compression at each turn
- payload size AFTER compression at each turn
- bytes saved per session (peak turn)
- which sessions would have benefited most

This complements analyze_tool_result_sizes.py (which sums bytes
without simulating compression) by quantifying the empirical savings
of the compression mechanism shipped in commit ec07469.

If compression saves <10% across all sessions, the mechanism's value
is marginal. If it saves >50% on tail sessions, it's a useful guard
even though Track C 9.2 showed H1 (payload→503) is currently
unsupported — long sessions could still grow toward the 1MB threshold
under different model/prompt distributions.

Read-only analysis; emits markdown to
workspace/qa_runs/compression_replay_analysis.md.

Usage:
  python scripts/qa/replay_compression_effect.py
  python scripts/qa/replay_compression_effect.py --keep-last 1
  python scripts/qa/replay_compression_effect.py --max-output-chars 200
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path
from typing import Dict, List, Tuple

REPO_ROOT = Path(__file__).resolve().parents[2]
QA_RUNS = REPO_ROOT / "workspace" / "qa_runs"
REPORT_PATH = QA_RUNS / "compression_replay_analysis.md"

# Import the actual production compression helpers — replay uses the
# same code path the live orchestrator does, so any divergence between
# replay and reality is in the run_direct → messages reconstruction
# (which we keep simple) not in the compression itself.
sys.path.insert(0, str(REPO_ROOT))
from service.isaac_assist_service.chat.orchestrator import (  # noqa: E402
    _compress_old_tool_results,
    _measure_messages_bytes,
)


def fmt_bytes(n: int) -> str:
    if n < 1024:
        return f"{n}B"
    if n < 1024 * 1024:
        return f"{n/1024:.1f}KB"
    return f"{n/1024/1024:.2f}MB"


def reconstruct_messages_per_turn(
    run_file: Path,
) -> List[Tuple[int, List[Dict]]]:
    """Walk one run_direct file. Each isaac_assist_reply event is one
    turn — we synthesize the messages list as it would have grown by
    that turn (user-msg + assistant text + tool_calls + tool_results).

    Returns: list of (turn_index, messages_so_far). Each entry shows
    what the orchestrator's history looked like just before the
    NEXT LLM call.
    """
    messages: List[Dict] = []
    snapshots: List[Tuple[int, List[Dict]]] = []
    turn = 0
    try:
        with open(run_file) as fh:
            for line in fh:
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                ev = d.get("event")
                if ev == "direct_eval_start":
                    # User-msg = the task query
                    messages.append({
                        "role": "user",
                        "content": d.get("query", ""),
                    })
                elif ev == "isaac_assist_reply":
                    turn += 1
                    text = d.get("text", "") or ""
                    tcs = d.get("tool_calls") or []
                    # Assistant text + each tool_call as one combined message
                    asst = {
                        "role": "assistant",
                        "content": text,
                        "tool_calls": [
                            {
                                "id": f"call_{turn}_{i}",
                                "function": {
                                    "name": tc.get("tool", "?"),
                                    "arguments": json.dumps(
                                        tc.get("arguments") or {}
                                    ),
                                },
                            }
                            for i, tc in enumerate(tcs)
                        ],
                    }
                    messages.append(asst)
                    # Each tool_call's result becomes a tool message
                    for i, tc in enumerate(tcs):
                        result = tc.get("result")
                        if result is None:
                            result = tc.get("output") or {}
                        messages.append({
                            "role": "tool",
                            "tool_call_id": f"call_{turn}_{i}",
                            "content": json.dumps(result, default=str),
                        })
                    # Snapshot: what would the LLM see at the START of
                    # the NEXT turn?
                    snapshots.append((turn, list(messages)))
    except Exception:
        return []
    return snapshots


def analyze_session(
    run_file: Path, keep_last_n: int = 3
) -> Dict | None:
    """For one session, compute pre/post bytes per turn + peak savings."""
    snapshots = reconstruct_messages_per_turn(run_file)
    if not snapshots:
        return None
    turns: List[Tuple[int, int, int]] = []  # (turn, pre_bytes, post_bytes)
    for turn_idx, msgs in snapshots:
        pre = _measure_messages_bytes(msgs)
        post = _measure_messages_bytes(
            _compress_old_tool_results(msgs, keep_last_n=keep_last_n)
        )
        turns.append((turn_idx, pre, post))
    if not turns:
        return None
    peak_pre = max(t[1] for t in turns)
    peak_post = max(t[2] for t in turns)
    final_pre = turns[-1][1]
    final_post = turns[-1][2]
    return {
        "run": run_file.parent.name,
        "task": run_file.stem.replace("_direct", ""),
        "n_turns": len(turns),
        "peak_pre": peak_pre,
        "peak_post": peak_post,
        "final_pre": final_pre,
        "final_post": final_post,
        "savings_at_peak": peak_pre - peak_post,
        "savings_at_final": final_pre - final_post,
    }


def render_report(results: List[Dict]) -> str:
    lines = ["# Compression replay analysis — Track C 9.2 follow-up"]
    lines.append("")
    lines.append(
        "Replays historical sessions in `workspace/qa_runs/run_direct_*` "
        "through the production compression helpers and reports per-session "
        "and aggregate savings. Pre = payload without compression; Post = "
        "with `_compress_old_tool_results` applied (production helper)."
    )
    lines.append("")
    lines.append(
        "**Note on \"turns\":** counts LLM-reply rounds (one `isaac_assist_reply` "
        "event = one turn). A single turn can contain many tool calls — that's "
        "where compression has the most impact (27 of 30 tool results compressed "
        "with default `keep_last_n=3`)."
    )
    lines.append("")

    if not results:
        lines.append("No sessions analyzed (no run_direct files found?).")
        return "\n".join(lines)

    n = len(results)
    lines.append(f"Sessions analyzed: **{n}**")
    lines.append("")

    # ── Aggregate stats ──
    lines.append("## Aggregate")
    lines.append("")
    pres = sorted(r["peak_pre"] for r in results)
    posts = sorted(r["peak_post"] for r in results)
    savings = sorted(r["savings_at_peak"] for r in results)
    pct_saved = [
        100 * r["savings_at_peak"] / r["peak_pre"]
        for r in results if r["peak_pre"] > 0
    ]
    pct_saved.sort()

    lines.append("| Metric | Median | p90 | p99 | Max |")
    lines.append("|--------|-------:|----:|----:|----:|")
    lines.append(f"| peak pre-compress | {fmt_bytes(pres[n//2])} | "
                 f"{fmt_bytes(pres[int(0.9*n)])} | "
                 f"{fmt_bytes(pres[int(0.99*n)])} | {fmt_bytes(pres[-1])} |")
    lines.append(f"| peak post-compress | {fmt_bytes(posts[n//2])} | "
                 f"{fmt_bytes(posts[int(0.9*n)])} | "
                 f"{fmt_bytes(posts[int(0.99*n)])} | {fmt_bytes(posts[-1])} |")
    lines.append(f"| savings at peak | {fmt_bytes(savings[n//2])} | "
                 f"{fmt_bytes(savings[int(0.9*n)])} | "
                 f"{fmt_bytes(savings[int(0.99*n)])} | {fmt_bytes(savings[-1])} |")
    if pct_saved:
        lines.append(f"| savings % at peak | {pct_saved[len(pct_saved)//2]:.0f}% | "
                     f"{pct_saved[int(0.9*len(pct_saved))]:.0f}% | "
                     f"{pct_saved[int(0.99*len(pct_saved))]:.0f}% | "
                     f"{pct_saved[-1]:.0f}% |")
    lines.append("")

    # ── Effect-size threshold ──
    n_savings_meaningful = sum(1 for s in savings if s > 10_000)  # >10KB saved
    n_savings_large = sum(1 for s in savings if s > 100_000)  # >100KB saved
    lines.append(f"- Sessions with >10KB savings at peak: {n_savings_meaningful}/{n} "
                 f"({100*n_savings_meaningful/n:.0f}%)")
    lines.append(f"- Sessions with >100KB savings at peak: {n_savings_large}/{n} "
                 f"({100*n_savings_large/n:.0f}%)")
    lines.append("")

    # ── Top biggest-savings sessions ──
    lines.append("## Top 10 sessions by savings-at-peak")
    lines.append("")
    lines.append("| Run | Task | Turns | Pre→Post (peak) | Saved |")
    lines.append("|-----|------|------:|----------------:|------:|")
    for r in sorted(results, key=lambda x: -x["savings_at_peak"])[:10]:
        lines.append(
            f"| `{r['run'][:32]}` | {r['task']} | {r['n_turns']} | "
            f"{fmt_bytes(r['peak_pre'])} → {fmt_bytes(r['peak_post'])} | "
            f"{fmt_bytes(r['savings_at_peak'])} |"
        )
    lines.append("")

    # ── Conclusion ──
    lines.append("## Conclusion")
    lines.append("")
    if pct_saved:
        median_pct = pct_saved[len(pct_saved)//2]
        if median_pct < 5:
            verdict = (
                f"Median savings is {median_pct:.0f}% — compression has "
                "negligible effect on the typical session. Most sessions are "
                "small enough that compression is a no-op."
            )
        elif median_pct < 20:
            verdict = (
                f"Median savings is {median_pct:.0f}% — modest. Compression "
                "helps long-tail sessions but typical case is unchanged."
            )
        else:
            verdict = (
                f"Median savings is {median_pct:.0f}% — substantial. "
                "Compression is worth keeping."
            )
        lines.append(verdict)
        lines.append("")
        lines.append(
            "Combined with Track C 9.2's finding that H1 (payload→503) is "
            "not supported by historical data, compression's value here is "
            "primarily token-cost reduction and defensive insurance against "
            "future longer sessions, not 503-mitigation."
        )

    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("*Generated by `scripts/qa/replay_compression_effect.py`*")
    return "\n".join(lines)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--keep-last", type=int, default=3,
                   help="keep_last_n for compression (default 3 = production)")
    p.add_argument("--max-runs", type=int, default=None,
                   help="Cap number of runs analyzed (debug; default: all)")
    p.add_argument("--out", default=str(REPORT_PATH))
    args = p.parse_args()

    runs = sorted(QA_RUNS.glob("run_direct_*"))
    if args.max_runs:
        runs = runs[:args.max_runs]
    print(f"[replay] {len(runs)} candidate runs")

    results: List[Dict] = []
    for r in runs:
        files = list(r.glob("*_direct.jsonl"))
        if not files:
            continue
        out = analyze_session(files[0], keep_last_n=args.keep_last)
        if out and out["n_turns"] > 0:
            results.append(out)

    print(f"[replay] {len(results)} sessions with ≥1 turn")
    if results:
        n = len(results)
        savings_sorted = sorted(r["savings_at_peak"] for r in results)
        print(f"[replay] median savings: {fmt_bytes(savings_sorted[n//2])}, "
              f"max: {fmt_bytes(savings_sorted[-1])}")

    report = render_report(results)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(report, encoding="utf-8")
    print(f"[replay] wrote {args.out} ({len(report)} chars)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
