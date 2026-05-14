"""
analyze_provider_incidents.py — companion to llm_gemini's 9.1 logging.

Reads workspace/qa_runs/provider_incidents.jsonl (populated by
_persist_provider_incident in service/isaac_assist_service/chat/llm_gemini.py)
and emits a markdown report breaking down 503/429/etc incidents by:

- status code (429 vs 503 vs 502 vs 504 — different root causes)
- payload size correlation (does bytes correlate with status?)
- time-of-day distribution (did everything fail in one window?)
- time-since-last-200 (rate-limit pattern: long gaps → success, short → fail)
- response_excerpt patterns (provider's own message)

Goal: replace anecdote ("Gemini 503'd today") with measurement
("47 503s observed, all when payload >800KB and within 60s of prior call,
zero when payload <500KB — root cause: rate-limit per-minute bucket").

Until 9.1 fires N>10 times, this report is sparse. It self-explains
when data is insufficient.

Usage:
  python scripts/qa/analyze_provider_incidents.py
  python scripts/qa/analyze_provider_incidents.py --status 503
"""
from __future__ import annotations

import argparse
import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, List, Tuple

REPO_ROOT = Path(__file__).resolve().parents[2]
INCIDENT_LOG = REPO_ROOT / "workspace" / "qa_runs" / "provider_incidents.jsonl"
REPORT_PATH = REPO_ROOT / "workspace" / "qa_runs" / "provider_incidents_analysis.md"


def fmt_bytes(n: int) -> str:
    if n is None:
        return "?"
    if n < 1024:
        return f"{n}B"
    if n < 1024 * 1024:
        return f"{n/1024:.1f}KB"
    return f"{n/1024/1024:.2f}MB"


def percentile(vals: List[float], p: float) -> float:
    if not vals:
        return 0.0
    s = sorted(vals)
    idx = min(int(p * len(s)), len(s) - 1)
    return s[idx]


def load_incidents(status_filter: int | None = None) -> List[Dict]:
    if not INCIDENT_LOG.exists():
        return []
    incidents: List[Dict] = []
    with open(INCIDENT_LOG) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
                if status_filter is not None and d.get("status") != status_filter:
                    continue
                incidents.append(d)
            except json.JSONDecodeError:
                continue
    return incidents


def render_report(incidents: List[Dict]) -> str:
    lines: List[str] = []
    add = lines.append
    add("# Provider incident analysis — Track C 9.1 readout")
    add("")
    if not incidents:
        add("No incidents recorded yet. Either:")
        add("- The instrumentation in `llm_gemini.py` hasn't been exercised with")
        add("  a non-200 response since deploy.")
        add("- `workspace/qa_runs/provider_incidents.jsonl` doesn't exist or is")
        add("  empty.")
        add("")
        add("Run live traffic against Gemini and re-run this analyzer when")
        add("incidents accumulate (≥10 recommended for meaningful percentiles).")
        return "\n".join(lines)

    n = len(incidents)
    add(f"Total incidents: **{n}**")
    add("")
    if INCIDENT_LOG.exists():
        first_ts = incidents[0].get("ts_iso", "?")
        last_ts = incidents[-1].get("ts_iso", "?")
        add(f"Time range: {first_ts} → {last_ts}")
        add("")

    # ── Status distribution ──
    add("## Status distribution")
    add("")
    statuses = Counter(d.get("status") for d in incidents)
    add("| Status | Count | Pct |")
    add("|-------:|------:|----:|")
    for status, count in statuses.most_common():
        add(f"| {status} | {count} | {100*count/n:.0f}% |")
    add("")

    # ── Per-status drill-down ──
    for status_code, _count in statuses.most_common(3):
        sub = [d for d in incidents if d.get("status") == status_code]
        if not sub:
            continue
        add(f"## Status {status_code} ({len(sub)} incidents)")
        add("")
        # Payload size dist
        sizes = [d["payload_bytes"] for d in sub if d.get("payload_bytes", -1) > 0]
        if sizes:
            add(f"**Payload size (n={len(sizes)}):**")
            add(f"- median: {fmt_bytes(int(statistics.median(sizes)))}")
            add(f"- p90: {fmt_bytes(int(percentile(sizes, 0.9)))}")
            add(f"- p99: {fmt_bytes(int(percentile(sizes, 0.99)))}")
            add(f"- max: {fmt_bytes(max(sizes))}")
            add("")
        # Time since last 200
        gaps = [d["since_last_200_s"] for d in sub
                if d.get("since_last_200_s") is not None]
        if gaps:
            add(f"**Time since last 200 (n={len(gaps)}):**")
            add(f"- median: {statistics.median(gaps):.1f}s")
            add(f"- p90: {percentile(gaps, 0.9):.1f}s")
            add(f"- max: {max(gaps):.1f}s")
            add(f"- min: {min(gaps):.1f}s")
            add("")
        # Attempt distribution (1=first attempt, 2-4=retries)
        attempts = Counter(d.get("attempt") for d in sub)
        add(f"**Attempt distribution:**")
        for att, c in sorted(attempts.items()):
            add(f"- attempt {att}: {c}")
        add("")
        # Response excerpts (top 3 most frequent)
        excerpts = Counter(d.get("response_excerpt", "")[:120] for d in sub)
        if excerpts:
            add("**Top response excerpts (truncated to 120 chars):**")
            for excerpt, c in excerpts.most_common(3):
                if not excerpt:
                    continue
                add(f"- ({c}x) `{excerpt}`")
            add("")
        # retry-after presence
        retry_after_count = sum(
            1 for d in sub
            if (d.get("response_headers") or {}).get("retry-after") is not None
        )
        if retry_after_count > 0:
            add(f"**`retry-after` header present**: {retry_after_count}/{len(sub)} "
                f"({100*retry_after_count/len(sub):.0f}%) — provider explicitly "
                f"signals rate-limit, supports H2 (RPM-bound) over H1 (payload).")
            add("")

    # ── Hypothesis check ──
    add("## Hypothesis evaluation (data-driven)")
    add("")

    # H1: payload-size driven
    by_status_sizes = defaultdict(list)
    for d in incidents:
        if d.get("payload_bytes", -1) > 0:
            by_status_sizes[d["status"]].append(d["payload_bytes"])

    if 200 in by_status_sizes:
        # We don't actually log 200s, but symmetric — if 503-payloads are
        # systematically larger than baseline, H1 strengthens.
        pass

    # Across all incidents, do larger payloads hit different statuses?
    payload_status_corr = "ambiguous"
    if len(by_status_sizes) >= 2:
        med_per = {s: statistics.median(v) for s, v in by_status_sizes.items() if v}
        if len(med_per) >= 2:
            spread = max(med_per.values()) - min(med_per.values())
            if spread > 100_000:  # 100KB spread between statuses
                payload_status_corr = (
                    f"PAYLOAD VARIES BY STATUS — medians: " +
                    ", ".join(f"{s}: {fmt_bytes(int(v))}" for s, v in med_per.items())
                )
            else:
                payload_status_corr = (
                    "PAYLOAD INVARIANT — medians within 100KB across statuses, "
                    "suggests payload is NOT primary driver."
                )

    add(f"**H1 — payload size drives status:** {payload_status_corr}")
    add("")

    # H2: rate-limit (short time-since-last-200 should correlate with failure)
    gaps_all = [d["since_last_200_s"] for d in incidents
                if d.get("since_last_200_s") is not None]
    if gaps_all:
        median_gap = statistics.median(gaps_all)
        short_gap_pct = sum(1 for g in gaps_all if g < 30) / len(gaps_all)
        add(f"**H2 — rate-limit (RPM/TPM):** "
            f"median time-since-last-200 = {median_gap:.1f}s; "
            f"{100*short_gap_pct:.0f}% of incidents within 30s of prior success.")
        if short_gap_pct > 0.5:
            add("→ Many incidents cluster close to a prior success — "
                "PLAUSIBLE rate-limit signature.")
        elif short_gap_pct < 0.2:
            add("→ Few incidents cluster close to a prior success — "
                "rate-limit UNLIKELY; provider instability or bursts more likely.")
        add("")

    # H3: provider instability (random distribution, retry-after often absent)
    retry_after_pct = sum(
        1 for d in incidents
        if (d.get("response_headers") or {}).get("retry-after") is not None
    ) / max(1, n)
    add(f"**H3 — provider instability:** `retry-after` present in "
        f"{100*retry_after_pct:.0f}% of incidents.")
    if retry_after_pct < 0.1:
        add("→ Provider rarely signals retry — likely transient instability "
            "(not rate-limit bound). Mitigation: jittered exponential backoff.")
    elif retry_after_pct > 0.5:
        add("→ Provider consistently signals retry-after — rate-limit-driven, "
            "respect the header (we currently don't).")
    add("")

    add("---")
    add("")
    add("*Generated by `scripts/qa/analyze_provider_incidents.py`*")
    return "\n".join(lines)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--status", type=int, default=None,
                   help="Filter to a single HTTP status code (e.g. 503).")
    p.add_argument("--out", default=str(REPORT_PATH))
    args = p.parse_args()

    incidents = load_incidents(status_filter=args.status)
    print(f"[analyze] loaded {len(incidents)} incidents from {INCIDENT_LOG}")
    if args.status is not None:
        print(f"[analyze] filter: status={args.status}")

    report = render_report(incidents)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(report, encoding="utf-8")
    print(f"[analyze] wrote {args.out} ({len(report)} chars)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
