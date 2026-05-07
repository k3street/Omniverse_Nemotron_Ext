"""
replay_vault_simulation.py — Layer 1 from kcode-spec sec 10.1.

Pass criterion: vault would actually hit ≥30% of large tool_result
events. Otherwise, halt evaluation (per spec sec 10.7 rollback trigger).

What "vault hit" means here:

Scenario A — exact duplicate: same tool called with same args twice
  in one session, returns identical content. Vault would auto-dedup.
Scenario B — content-equivalent: different tool/args but identical
  content bytes. Vault hash-keys on content so still dedups.
Scenario C — within-session repeat regardless of args: less strict —
  same tool called N times produces results that are mostly identical
  except minor fields (timestamps, IDs). Vault dedups by content hash;
  these are NOT hits.

We measure A+B (content-hash-based dedup, what an actual vault would
catch) and report Scenario C as auxiliary signal.

Why this matters:
- P3/P4 (vault) only worth building if dedup-rate is meaningful
- If <30%, halt evaluation per spec; focus on P1+P2 instead
- Pure-additive analysis — no Kit, no provider, no risk

Usage:
  python scripts/qa/replay_vault_simulation.py
  python scripts/qa/replay_vault_simulation.py --threshold 5000
"""
from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, List, Tuple

REPO_ROOT = Path(__file__).resolve().parents[2]
QA_RUNS = REPO_ROOT / "workspace" / "qa_runs"
REPORT_PATH = QA_RUNS / "vault_replay_layer1.md"


def fmt_bytes(n: int) -> str:
    if n < 1024:
        return f"{n}B"
    if n < 1024 * 1024:
        return f"{n/1024:.1f}KB"
    return f"{n/1024/1024:.2f}MB"


def normalize_for_hash(result: object) -> bytes:
    """Hash-key for vault dedup. Use canonical JSON so reordering doesn't
    create false misses. Stringify non-JSON-serializable types."""
    return json.dumps(result, sort_keys=True, default=str).encode("utf-8")


def collect_session_data(threshold_bytes: int) -> Dict:
    """Walk run_direct/*.jsonl. Per session, collect tool_result entries
    above threshold and compute dedup statistics."""
    runs = sorted(QA_RUNS.glob("run_direct_*"))

    # Aggregates
    total_large_results = 0
    total_distinct_hashes_per_session: List[int] = []
    total_repeats_per_session: List[int] = []
    repeat_rate_per_session: List[float] = []
    sessions_with_any_large = 0
    sessions_with_any_repeat = 0

    # Per-tool stats
    per_tool_count: Counter = Counter()
    per_tool_repeats: Counter = Counter()

    # Within-session same-args call repeats (Scenario C)
    same_arg_repeat_calls = 0
    total_calls = 0

    # Top dedup wins (per content hash, count occurrences)
    cross_session_hash_count: Counter = Counter()

    for run in runs:
        files = list(run.glob("*_direct.jsonl"))
        if not files:
            continue
        f = files[0]
        try:
            session_hashes: List[Tuple[str, int, str]] = []  # (hash, size, tool)
            session_arg_keys: List[Tuple[str, str]] = []  # (tool, args_hash)
            with open(f) as fh:
                for line in fh:
                    try:
                        d = json.loads(line)
                    except Exception:
                        continue
                    if d.get("event") != "isaac_assist_reply":
                        continue
                    for tc in d.get("tool_calls") or []:
                        result = tc.get("result")
                        if result is None:
                            continue
                        total_calls += 1
                        tool_name = tc.get("tool") or tc.get("name") or "?"

                        # Args fingerprint for Scenario C
                        args_blob = json.dumps(
                            tc.get("arguments") or {}, sort_keys=True, default=str
                        )
                        args_hash = hashlib.sha256(args_blob.encode()).hexdigest()[:16]
                        if (tool_name, args_hash) in session_arg_keys:
                            same_arg_repeat_calls += 1
                        session_arg_keys.append((tool_name, args_hash))

                        # Content fingerprint for Scenario A+B
                        blob = normalize_for_hash(result)
                        sz = len(blob)
                        if sz < threshold_bytes:
                            continue
                        h = hashlib.sha256(blob).hexdigest()[:16]
                        session_hashes.append((h, sz, tool_name))
                        cross_session_hash_count[h] += 1

            # Per-session stats
            if session_hashes:
                sessions_with_any_large += 1
                total_large_results += len(session_hashes)

                hash_counter = Counter(h for h, _, _ in session_hashes)
                distinct = len(hash_counter)
                repeats = sum(c - 1 for c in hash_counter.values() if c > 1)
                total_distinct_hashes_per_session.append(distinct)
                total_repeats_per_session.append(repeats)
                repeat_rate_per_session.append(
                    repeats / len(session_hashes) if session_hashes else 0.0
                )
                if repeats > 0:
                    sessions_with_any_repeat += 1

                for _, sz, tool in session_hashes:
                    per_tool_count[tool] += 1
                # Within-session repeats per tool
                seen_in_session = set()
                for h, _, tool in session_hashes:
                    if h in seen_in_session:
                        per_tool_repeats[tool] += 1
                    seen_in_session.add(h)
        except Exception:
            continue

    return {
        "total_large_results": total_large_results,
        "sessions_with_any_large": sessions_with_any_large,
        "sessions_with_any_repeat": sessions_with_any_repeat,
        "repeat_rate_per_session": repeat_rate_per_session,
        "per_tool_count": dict(per_tool_count),
        "per_tool_repeats": dict(per_tool_repeats),
        "same_arg_repeat_calls": same_arg_repeat_calls,
        "total_calls": total_calls,
        "cross_session_hash_count": cross_session_hash_count,
    }


def render_report(data: Dict, threshold_bytes: int) -> str:
    lines = ["# Vault replay simulation — Layer 1 (kcode-spec sec 10.1)"]
    lines.append("")
    lines.append("Walks `workspace/qa_runs/run_direct_*/*.jsonl` and computes "
                 "what fraction of large tool_result events are duplicates that "
                 "a hash-keyed vault would dedup. **Pass criterion: ≥30% repeat-"
                 "rate** — otherwise halt vault evaluation per spec sec 10.7.")
    lines.append("")
    lines.append(f"**Threshold for \"large\":** {fmt_bytes(threshold_bytes)}")
    lines.append("")

    n_large = data["total_large_results"]
    n_sessions_large = data["sessions_with_any_large"]
    n_sessions_repeat = data["sessions_with_any_repeat"]
    rates = data["repeat_rate_per_session"]
    same_arg = data["same_arg_repeat_calls"]
    total_calls = data["total_calls"]

    lines.append("## Aggregate")
    lines.append("")
    lines.append(f"- Total large tool_results observed: **{n_large}**")
    lines.append(f"- Sessions with ≥1 large result: **{n_sessions_large}**")
    lines.append(f"- Sessions with ≥1 within-session repeat: **{n_sessions_repeat}** "
                 f"({100*n_sessions_repeat/max(1,n_sessions_large):.0f}% of sessions with large)")
    lines.append(f"- Total tool calls (all sizes): **{total_calls}**")
    lines.append(f"- Same-tool-same-args repeat calls (Scenario C, regardless of size): "
                 f"**{same_arg}** ({100*same_arg/max(1,total_calls):.1f}% of all calls)")
    lines.append("")

    if rates:
        lines.append(f"### Per-session within-session repeat rate "
                     f"(n={len(rates)} sessions)")
        lines.append("")
        sorted_rates = sorted(rates)
        median = sorted_rates[len(sorted_rates)//2]
        p90 = sorted_rates[int(0.9*len(sorted_rates))]
        p99 = sorted_rates[int(0.99*len(sorted_rates))]
        max_r = sorted_rates[-1]
        mean = sum(rates) / len(rates)
        lines.append(f"- median: {100*median:.0f}%")
        lines.append(f"- mean:   {100*mean:.0f}%")
        lines.append(f"- p90:    {100*p90:.0f}%")
        lines.append(f"- p99:    {100*p99:.0f}%")
        lines.append(f"- max:    {100*max_r:.0f}%")
        lines.append("")

    # Cross-session bonus: how often does the SAME content (across sessions)
    # repeat? Useful to know but not the primary metric since vault is
    # per-session typically.
    cross = data["cross_session_hash_count"]
    if cross:
        n_distinct = len(cross)
        n_total = sum(cross.values())
        cross_dups = sum(c - 1 for c in cross.values() if c > 1)
        lines.append(f"### Cross-session content dedup (auxiliary)")
        lines.append("")
        lines.append(f"- Distinct content hashes: {n_distinct}")
        lines.append(f"- Total occurrences: {n_total}")
        lines.append(f"- Cross-session dedups possible: {cross_dups} "
                     f"({100*cross_dups/max(1,n_total):.0f}% of all large results)")
        # Top reused content
        top = sorted(cross.items(), key=lambda kv: -kv[1])[:5]
        if top and top[0][1] > 1:
            lines.append("")
            lines.append("Top 5 most-reused content blobs:")
            for h, c in top:
                lines.append(f"  - {h[:8]}... seen {c}x")
        lines.append("")

    # Per-tool distribution
    per_tool = data["per_tool_count"]
    per_tool_rep = data["per_tool_repeats"]
    if per_tool:
        lines.append("### Per-tool large-result counts + within-session repeats")
        lines.append("")
        lines.append("| Tool | Large calls | Repeats | Repeat % |")
        lines.append("|------|-----------:|--------:|---------:|")
        rows = []
        for tool, cnt in per_tool.items():
            rep = per_tool_rep.get(tool, 0)
            pct = 100 * rep / cnt if cnt else 0
            rows.append((tool, cnt, rep, pct))
        rows.sort(key=lambda r: -r[1])
        for tool, cnt, rep, pct in rows[:15]:
            lines.append(f"| `{tool}` | {cnt} | {rep} | {pct:.0f}% |")
        if len(rows) > 15:
            lines.append(f"| ... ({len(rows)-15} more) | | | |")
        lines.append("")

    # Verdict
    lines.append("## Verdict")
    lines.append("")
    if rates:
        # Two ways to read pass criterion:
        #   A) median session repeat rate ≥ 30%
        #   B) any session has ≥ 30%, weighted by call count
        # Spec is ambiguous. Compute both.
        median = sorted(rates)[len(rates)//2]
        # Weighted: total repeats / total large
        per_tool_total_reps = sum(per_tool_rep.values())
        weighted = per_tool_total_reps / max(1, n_large)
        lines.append(f"- **Median session within-session repeat rate**: {100*median:.0f}%")
        lines.append(f"- **Weighted (total repeats / total large results)**: {100*weighted:.0f}%")
        lines.append("")
        verdict = (
            "**PASS** — vault dedup-rate exceeds 30% threshold; "
            "vault evaluation can proceed."
            if (weighted >= 0.30 or median >= 0.30) else
            "**HALT** — vault dedup-rate below 30% threshold. "
            "Per spec sec 10.7, focus on P1+P2 (per-tool size cap, "
            "request budget) instead. Vault would be dead weight."
        )
        lines.append(verdict)
        lines.append("")
        if weighted < 0.30 and median < 0.30:
            lines.append("Interpretation: the typical large tool_result is NOT a "
                         "duplicate within its session. A content-addressed vault "
                         "with auto-dedup would rarely fire — the storage and "
                         "round-trip cost would dominate any savings. The "
                         "kcode-style vault premise (\"context grows huge because "
                         "of repeated identical content\") is not supported by "
                         "this codebase's actual session shape.")
    else:
        lines.append("**INSUFFICIENT DATA** — no sessions with large tool_results "
                     "observed. Either threshold is too high or workload is "
                     "small-result-dominated.")

    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("*Generated by `scripts/qa/replay_vault_simulation.py`*")
    return "\n".join(lines)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--threshold", type=int, default=5000,
                   help='"Large" threshold in bytes (default 5000)')
    p.add_argument("--out", default=str(REPORT_PATH))
    args = p.parse_args()

    print(f"[replay] threshold={args.threshold}B, scanning {QA_RUNS}/run_direct_*")
    data = collect_session_data(threshold_bytes=args.threshold)

    n_large = data["total_large_results"]
    rates = data["repeat_rate_per_session"]
    print(f"[replay] {n_large} large results across {data['sessions_with_any_large']} "
          f"sessions; {data['sessions_with_any_repeat']} have ≥1 within-session repeat")
    if rates:
        median = sorted(rates)[len(rates)//2]
        per_tool_rep = sum(data["per_tool_repeats"].values())
        weighted = per_tool_rep / max(1, n_large)
        print(f"[replay] median repeat-rate: {100*median:.0f}%; weighted: {100*weighted:.0f}%")

    report = render_report(data, threshold_bytes=args.threshold)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(report, encoding="utf-8")
    print(f"[replay] wrote {args.out} ({len(report)} chars)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
