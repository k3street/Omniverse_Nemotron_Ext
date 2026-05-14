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


# ── Kit Supervisor dashboards (spec 2026-05-11 v2 §9.3) ───────────────────


def supervisor_health_summary(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Drift events, restart count, CPs per restart, abort rate."""
    drift_events = sum(1 for e in events if e["event_type"] == "supervisor_drift_detected")
    restart_completed = sum(
        1 for e in events if e["event_type"] == "supervisor_restart_completed"
    )
    restart_failed = sum(
        1 for e in events if e["event_type"] == "supervisor_restart_failed"
    )
    aborts = sum(1 for e in events if e["event_type"] == "supervisor_abort")
    soft_resets = sum(1 for e in events if e["event_type"] == "supervisor_soft_reset")
    total_classifications = sum(
        1 for e in events if e["event_type"] == "supervisor_drift_classification"
    )

    cps_per_restart = (
        total_classifications / max(restart_completed, 1)
        if restart_completed > 0
        else None
    )

    return {
        "total_classifications": total_classifications,
        "drift_events": drift_events,
        "restart_completed": restart_completed,
        "restart_failed": restart_failed,
        "aborts": aborts,
        "soft_resets": soft_resets,
        "cps_per_restart": round(cps_per_restart, 1) if cps_per_restart else None,
        "abort_rate": round(aborts / max(restart_completed + aborts, 1), 3),
    }


def supervisor_drift_precision(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    """% of drift detections that resulted in a successful retry on fresh Kit.

    A "precise" drift detection is one where:
    1. supervisor_drift_detected fires for CP-X
    2. supervisor_restart_completed fires next
    3. supervisor_drift_classification fires for CP-X with level=ok or warn
       (i.e. the retry succeeded)

    Strict per-CP pairing via event_id order.
    """
    ordered = sorted(events, key=lambda e: e.get("event_id", 0))
    # cp -> list of (drift_idx, retry_outcome) pairs
    drift_by_cp: Dict[str, list] = defaultdict(list)
    last_drift_for_cp: Dict[str, int] = {}

    for i, e in enumerate(ordered):
        cp = (e["payload"] or {}).get("cp")
        if not cp:
            continue
        if e["event_type"] == "supervisor_drift_detected":
            last_drift_for_cp[cp] = i
            drift_by_cp[cp].append({"drift_idx": i, "retry_outcome": None})
        elif e["event_type"] == "supervisor_drift_classification":
            # Retry classification: cp matches AND comes after a drift
            if e["payload"].get("retry") is True:
                pending = drift_by_cp.get(cp, [])
                if pending and pending[-1]["retry_outcome"] is None:
                    pending[-1]["retry_outcome"] = e["payload"].get("level")

    n_drifts = sum(len(p) for p in drift_by_cp.values())
    n_recovered = sum(
        1
        for plist in drift_by_cp.values()
        for p in plist
        if p["retry_outcome"] in ("ok", "warn")
    )
    return {
        "drift_events": n_drifts,
        "recovered_on_retry": n_recovered,
        "precision": round(n_recovered / n_drifts, 3) if n_drifts else 0.0,
        "by_cp": {cp: len(plist) for cp, plist in drift_by_cp.items()},
    }


def supervisor_per_cp_baselines(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Per-CP elapsed_s distributions for calibration.

    Walks supervisor_drift_classification events with level=ok, groups by cp,
    returns {cp: {n, min, max, p50, p95}}.
    """
    by_cp: Dict[str, list] = defaultdict(list)
    for e in events:
        if e["event_type"] != "supervisor_drift_classification":
            continue
        p = e["payload"] or {}
        if p.get("level") not in ("ok", "warn"):
            continue
        cp = p.get("cp")
        elapsed = p.get("elapsed_s")
        if cp and isinstance(elapsed, (int, float)):
            by_cp[cp].append(float(elapsed))

    out: Dict[str, Dict[str, Any]] = {}
    for cp, samples in by_cp.items():
        if not samples:
            continue
        srt = sorted(samples)
        n = len(srt)
        p50 = srt[n // 2]
        p95_idx = min(int(n * 0.95), n - 1)
        p95 = srt[p95_idx]
        out[cp] = {
            "n": n,
            "min": round(srt[0], 3),
            "max": round(srt[-1], 3),
            "p50": round(p50, 3),
            "p95": round(p95, 3),
        }
    return out


def compliance_usage_breakdown(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Aggregate compliance controller lifecycle events (CRM spec §8).

    Args:
        events: Flat list of telemetry event dicts, each with keys
            ``event_type``, ``payload`` (dict), and ``session_id``.

    Returns:
        A structured dict with keys:

        - ``by_mode`` (dict[str, int]): count of ``compliance_installed``
          events per compliance mode (e.g. ``admittance``, ``impedance``).
        - ``params_updates`` (int): count of ``compliance_params_updated``
          events.
        - ``releases`` (int): count of ``compliance_released`` events.
        - ``active_now`` (dict[str, str]): robot_path → mode for robots that
          have been installed but not yet released (install minus release,
          last-write-wins on repeated installs for the same robot_path).
    """
    from service.isaac_assist_service.multimodal.telemetry import (
        EVENT_COMPLIANCE_INSTALLED,
        EVENT_COMPLIANCE_PARAMS_UPDATED,
        EVENT_COMPLIANCE_RELEASED,
    )

    by_mode: Counter = Counter()
    params_updates: int = 0
    releases: int = 0
    # robot_path → mode for currently-installed controllers
    active: Dict[str, str] = {}

    for e in events:
        et = e.get("event_type")
        payload = e.get("payload") or {}

        if et == EVENT_COMPLIANCE_INSTALLED:
            mode = payload.get("mode", "unknown")
            by_mode[mode] += 1
            robot_path = payload.get("robot_path")
            if robot_path:
                active[robot_path] = mode

        elif et == EVENT_COMPLIANCE_PARAMS_UPDATED:
            params_updates += 1

        elif et == EVENT_COMPLIANCE_RELEASED:
            releases += 1
            robot_path = payload.get("robot_path")
            if robot_path and robot_path in active:
                del active[robot_path]

    return {
        "by_mode": dict(by_mode),
        "params_updates": params_updates,
        "releases": releases,
        "active_now": dict(active),
    }


def contact_phase_success_rate(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Aggregate contact-phase and insertion outcome events (CRM spec §8).

    Args:
        events: Flat list of telemetry event dicts.

    Returns:
        A structured dict with keys:

        - ``phases_entered`` (int): count of ``contact_phase_entered`` events.
        - ``phases_exited`` (int): count of ``contact_phase_exited`` events.
        - ``insertion_succeeded`` (int): count of ``insertion_succeeded`` events.
        - ``insertion_failed`` (int): count of ``insertion_failed`` events.
        - ``success_rate`` (float): ``succeeded / (succeeded + failed)``; 0.0
          when no insertion attempts have been recorded.
    """
    from service.isaac_assist_service.multimodal.telemetry import (
        EVENT_CONTACT_PHASE_ENTERED,
        EVENT_CONTACT_PHASE_EXITED,
        EVENT_INSERTION_SUCCEEDED,
        EVENT_INSERTION_FAILED,
    )

    phases_entered: int = 0
    phases_exited: int = 0
    insertion_succeeded: int = 0
    insertion_failed: int = 0

    for e in events:
        et = e.get("event_type")
        if et == EVENT_CONTACT_PHASE_ENTERED:
            phases_entered += 1
        elif et == EVENT_CONTACT_PHASE_EXITED:
            phases_exited += 1
        elif et == EVENT_INSERTION_SUCCEEDED:
            insertion_succeeded += 1
        elif et == EVENT_INSERTION_FAILED:
            insertion_failed += 1

    total_insertions = insertion_succeeded + insertion_failed
    success_rate = (insertion_succeeded / total_insertions) if total_insertions > 0 else 0.0

    return {
        "phases_entered": phases_entered,
        "phases_exited": phases_exited,
        "insertion_succeeded": insertion_succeeded,
        "insertion_failed": insertion_failed,
        "success_rate": round(success_rate, 3),
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
        # Kit Supervisor dashboards (spec 2026-05-11 v2 §9.3)
        "supervisor_health": supervisor_health_summary(events),
        "supervisor_drift_precision": supervisor_drift_precision(events),
        "supervisor_per_cp_baselines": supervisor_per_cp_baselines(events),
        # CRM compliance dashboards (CRM spec §8)
        "compliance_usage_breakdown": compliance_usage_breakdown(events),
        "contact_phase_success_rate": contact_phase_success_rate(events),
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
