"""
analyze_multimodal_usage.py — telemetry aggregator per spec §17.2.

Reads from MultimodalStore (sqlite) and summarizes:
- Modality usage breakdown
- T1 fire-rate per session (Open Q A baseline)
- Ratify success rate per modality
- Build-failure modes by tool
- Per-feature verifier check pass rate
- Agent proposal acceptance rate

Usage:
    python scripts/qa/analyze_multimodal_usage.py [--db PATH] [--since DAYS]
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional

# Make sure the service package is importable
REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))


def _load_events(db_path: Optional[Path], limit: int) -> List[Dict[str, Any]]:
    from service.isaac_assist_service.multimodal.persistence import MultimodalStore

    store = MultimodalStore(db_path) if db_path else MultimodalStore()
    return store.list_events(limit=limit)


def modality_breakdown(events: List[Dict[str, Any]]) -> Dict[str, int]:
    """Count events by modality (from modality_invoked + intent_extracted)."""
    c: Counter = Counter()
    for e in events:
        if e["event_type"] in ("modality_invoked", "intent_extracted"):
            m = e["payload"].get("modality")
            if m:
                c[m] += 1
    return dict(c)


def t1_fire_rate(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    """T1 fire-rate per session = retrieval events with tier=T1 / all retrievals."""
    total = 0
    t1 = 0
    per_session_total: Counter = Counter()
    per_session_t1: Counter = Counter()
    for e in events:
        if e["event_type"] == "retrieval_completed":
            sid = e["session_id"]
            total += 1
            per_session_total[sid] += 1
            if e["payload"].get("tier") == "T1":
                t1 += 1
                per_session_t1[sid] += 1
    rate = (t1 / total) if total else 0.0
    by_session = {
        sid: (per_session_t1[sid] / per_session_total[sid])
        for sid in per_session_total
    }
    return {
        "total_retrievals": total,
        "t1_retrievals": t1,
        "overall_rate": round(rate, 3),
        "per_session_rate": {k: round(v, 3) for k, v in by_session.items()},
    }


def ratify_success_per_modality(
    events: List[Dict[str, Any]],
) -> Dict[str, Dict[str, int]]:
    """Pair ratify_completed events with the preceding intent_extracted event
    on the same session to attribute status to modality."""
    # session → last-seen modality
    last_modality: Dict[str, str] = {}
    per_modality: Dict[str, Counter] = defaultdict(Counter)
    # Iterate strictly oldest→newest by event_id. SQLite timestamp has only
    # second resolution; event_id is monotonic and ties-break deterministically.
    for e in sorted(events, key=lambda x: x.get("event_id", 0)):
        sid = e["session_id"]
        if e["event_type"] == "intent_extracted":
            m = e["payload"].get("modality")
            if m:
                last_modality[sid] = m
        elif e["event_type"] == "ratify_completed":
            m = last_modality.get(sid, "unknown")
            status = e["payload"].get("status", "unknown")
            per_modality[m][status] += 1
    return {m: dict(c) for m, c in per_modality.items()}


def build_failure_modes(
    events: List[Dict[str, Any]],
) -> Dict[str, int]:
    """Tool name → failure count, from build_progress events with status≠ok."""
    c: Counter = Counter()
    for e in events:
        if e["event_type"] == "build_progress":
            status = e["payload"].get("status")
            tool = e["payload"].get("tool")
            if status not in ("ok", "success", "completed") and tool:
                c[tool] += 1
    return dict(c)


def verifier_check_pass_rate(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    """check_id → {runs, pass, fail, skip, pass_rate}."""
    summary: Dict[str, Dict[str, int]] = defaultdict(lambda: Counter())
    for e in events:
        if e["event_type"] == "verify_check_run":
            check = e["payload"].get("check_id")
            status = e["payload"].get("status")
            if check and status:
                summary[check][status] += 1
                summary[check]["_runs"] += 1
    out: Dict[str, Dict[str, Any]] = {}
    for check, c in summary.items():
        runs = c.get("_runs", 0)
        pass_n = c.get("pass", 0)
        out[check] = {
            "runs": runs,
            "pass": pass_n,
            "fail": c.get("fail", 0),
            "skip": c.get("skip", 0) + c.get("skipped", 0),
            "pass_rate": round(pass_n / runs, 3) if runs else 0.0,
        }
    return out


def proposal_acceptance(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Agent proposal acceptance from canvas_proposed_resolved actions."""
    c: Counter = Counter()
    for e in events:
        if e["event_type"] == "canvas_proposed_resolved":
            act = e["payload"].get("action")
            if act:
                c[act] += 1
    total = sum(c.values())
    rate = (c.get("accept", 0) / total) if total else 0.0
    return {
        "totals": dict(c),
        "acceptance_rate": round(rate, 3),
    }


def aggregate(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Run all aggregations and return one report dict."""
    return {
        "n_events": len(events),
        "modality_breakdown": modality_breakdown(events),
        "t1_fire_rate": t1_fire_rate(events),
        "ratify_success_per_modality": ratify_success_per_modality(events),
        "build_failure_modes": build_failure_modes(events),
        "verifier_check_pass_rate": verifier_check_pass_rate(events),
        "proposal_acceptance": proposal_acceptance(events),
    }


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--db", type=Path, default=None, help="Path to state.db")
    p.add_argument("--limit", type=int, default=10000)
    p.add_argument("--out", type=Path, default=None,
                   help="Write JSON report here; default stdout")
    args = p.parse_args()

    events = _load_events(args.db, args.limit)
    report = aggregate(events)
    txt = json.dumps(report, indent=2, sort_keys=True)
    if args.out:
        args.out.write_text(txt)
        print(f"wrote {args.out}")
    else:
        print(txt)
    return 0


if __name__ == "__main__":
    sys.exit(main())
