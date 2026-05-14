"""Tests for service/isaac_assist_service/multimodal/telemetry.py + aggregator.

Block 5 — telemetry per spec §17.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.l0

# Add repo root to path so scripts.qa.analyze_multimodal_usage imports
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from service.isaac_assist_service.multimodal import telemetry as tel
from service.isaac_assist_service.multimodal.persistence import MultimodalStore


@pytest.fixture
def store(tmp_path: Path) -> MultimodalStore:
    return MultimodalStore(tmp_path / "test.db")


# ── Generic emit ───────────────────────────────────────────────────────────


def test_emit_records_event(store: MultimodalStore):
    eid = tel.emit(store, "sess1", tel.EVENT_MODALITY_INVOKED,
                   modality="text", ms=12.3)
    assert eid is not None
    events = store.list_events(session_id="sess1")
    assert len(events) == 1
    assert events[0]["event_type"] == "modality_invoked"
    assert events[0]["payload"]["modality"] == "text"


def test_emit_unknown_event_type_still_records(store, caplog):
    """Unknown types are logged but not blocked — telemetry is best-effort."""
    eid = tel.emit(store, "sess1", "totally_made_up", anything="ok")
    assert eid is not None
    events = store.list_events(session_id="sess1")
    assert len(events) == 1


def test_emit_does_not_raise_when_store_broken():
    """A broken store must not crash the calling code path."""
    class BrokenStore:
        def append_event(self, *a, **k):
            raise RuntimeError("db gone")
    # No exception, returns None
    assert tel.emit(BrokenStore(), "s", tel.EVENT_BUILD_STARTED) is None


# ── Named emitters ─────────────────────────────────────────────────────────


def test_emit_modality_invoked(store):
    tel.emit_modality_invoked(store, "s", "text", 45.2, n_chars=100)
    e = store.list_events(session_id="s")[0]
    assert e["event_type"] == "modality_invoked"
    assert e["payload"]["modality"] == "text"
    assert e["payload"]["ms"] == 45.2
    assert e["payload"]["n_chars"] == 100


def test_emit_intent_extracted(store):
    tel.emit_intent_extracted(
        store, "s", "text", {"pattern_hint": "sort", "n_bins": 3}
    )
    e = store.list_events(session_id="s")[0]
    assert e["payload"]["intent_summary"]["pattern_hint"] == "sort"


def test_emit_retrieval_completed(store):
    tel.emit_retrieval_completed(
        store, "s",
        [{"task_id": "CP-01", "similarity": 0.92}],
        tier="T1",
    )
    e = store.list_events(session_id="s")[0]
    assert e["payload"]["tier"] == "T1"


def test_emit_ratify_completed(store):
    tel.emit_ratify_completed(store, "s", status="ok", n_diagnostics=3)
    e = store.list_events(session_id="s")[0]
    assert e["payload"]["status"] == "ok"


def test_emit_build_lifecycle(store):
    tel.emit_build_started(store, "s", "B-1", template_id="CP-01")
    tel.emit_build_progress(store, "s", "B-1", "create_prim", "ok", 12.0)
    tel.emit_build_completed(store, "s", "B-1", "success", 25)
    events = store.list_events(session_id="s")
    types = [e["event_type"] for e in events]
    assert "build_started" in types
    assert "build_progress" in types
    assert "build_completed" in types


def test_emit_verify_check_run(store):
    tel.emit_verify_check_run(store, "s", "verify:reach", "pass", 8.7)
    e = store.list_events(session_id="s")[0]
    assert e["payload"]["check_id"] == "verify:reach"
    assert e["payload"]["status"] == "pass"


def test_emit_canvas_proposed_resolved_invalid_action(store, caplog):
    """Invalid action logged, but event still recorded for telemetry honesty."""
    tel.emit_canvas_proposed_resolved(store, "s", "weird_action")
    e = store.list_events(session_id="s")[0]
    assert e["payload"]["action"] == "weird_action"


# ── Aggregator script ──────────────────────────────────────────────────────


def _build_corpus(store: MultimodalStore) -> None:
    """Seed a store with a varied event corpus for aggregator tests."""
    # session-A: text modality, T1 hit, ratify ok, build succeeds
    tel.emit_modality_invoked(store, "A", "text", 10.0)
    tel.emit_intent_extracted(store, "A", "text", {"pattern_hint": "pick_place"})
    tel.emit_retrieval_completed(
        store, "A", [{"task_id": "CP-01", "similarity": 0.9}], "T1",
    )
    tel.emit_ratify_completed(store, "A", "ok", 1)
    tel.emit_build_started(store, "A", "B-A", "CP-01")
    tel.emit_build_progress(store, "A", "B-A", "create_prim", "ok", 5.0)
    tel.emit_build_completed(store, "A", "B-A", "success", 10)
    tel.emit_verify_check_run(store, "A", "verify:reach", "pass", 3.0)
    tel.emit_canvas_proposed_resolved(store, "A", "accept")

    # session-B: voice modality, T2, ratify needs_choice, build fails
    tel.emit_intent_extracted(store, "B", "voice", {"pattern_hint": "sort"})
    tel.emit_retrieval_completed(
        store, "B", [{"task_id": "CP-03", "similarity": 0.7}], "T2",
    )
    tel.emit_ratify_completed(store, "B", "needs_choice", 2)
    tel.emit_build_progress(store, "B", "B-B", "robot_wizard", "fail", 2.0)
    tel.emit_verify_check_run(store, "B", "verify:reach", "fail", 4.0)
    tel.emit_canvas_proposed_resolved(store, "B", "reject")


def test_aggregator_modality_breakdown(store):
    _build_corpus(store)
    events = store.list_events(limit=1000)
    from scripts.qa.analyze_multimodal_usage import modality_breakdown
    b = modality_breakdown(events)
    assert b.get("text", 0) >= 1
    assert b.get("voice", 0) >= 1


def test_aggregator_t1_fire_rate(store):
    _build_corpus(store)
    events = store.list_events(limit=1000)
    from scripts.qa.analyze_multimodal_usage import t1_fire_rate
    r = t1_fire_rate(events)
    assert r["total_retrievals"] == 2
    assert r["t1_retrievals"] == 1
    assert r["overall_rate"] == 0.5


def test_aggregator_ratify_per_modality(store):
    _build_corpus(store)
    events = store.list_events(limit=1000)
    from scripts.qa.analyze_multimodal_usage import ratify_success_per_modality
    r = ratify_success_per_modality(events)
    assert r.get("text", {}).get("ok", 0) == 1
    assert r.get("voice", {}).get("needs_choice", 0) == 1


def test_aggregator_build_failures(store):
    _build_corpus(store)
    events = store.list_events(limit=1000)
    from scripts.qa.analyze_multimodal_usage import build_failure_modes
    f = build_failure_modes(events)
    assert f.get("robot_wizard", 0) >= 1


def test_aggregator_verifier_pass_rate(store):
    _build_corpus(store)
    events = store.list_events(limit=1000)
    from scripts.qa.analyze_multimodal_usage import verifier_check_pass_rate
    v = verifier_check_pass_rate(events)
    assert "verify:reach" in v
    assert v["verify:reach"]["pass_rate"] == 0.5


def test_aggregator_proposal_acceptance(store):
    _build_corpus(store)
    events = store.list_events(limit=1000)
    from scripts.qa.analyze_multimodal_usage import proposal_acceptance
    p = proposal_acceptance(events)
    assert p["acceptance_rate"] == 0.5


def test_aggregator_full(store):
    _build_corpus(store)
    events = store.list_events(limit=1000)
    from scripts.qa.analyze_multimodal_usage import aggregate
    a = aggregate(events)
    assert "modality_breakdown" in a
    assert "t1_fire_rate" in a
    assert "build_failure_modes" in a
    # round-trip through JSON
    assert json.loads(json.dumps(a)) == a


def test_all_event_types_named():
    """Every constant has a named emitter or is at least listed."""
    # Spec §17.1 defines 13 multimodal events; supervisor spec §9.1 adds 12;
    # CRM spec §8 adds 8 compliance events (CRM-D1).
    assert len(tel.ALL_EVENT_TYPES) == 33


# ── Kit Supervisor aggregator dashboards (spec v2 §9.3) ────────────────────


def _seed_supervisor_corpus(store):
    """Seed events emulating one supervisor session with drift + recovery."""
    tel.emit(store, "sup-1", tel.EVENT_SUPERVISOR_STARTED,
             baseline_rss_mb=1000, baseline_gpu_mb=4000, kit_pid=1234)
    # 3 normal CPs
    for i, cp in enumerate(("CP-01", "CP-02", "CP-03"), 1):
        tel.emit(store, "sup-1", tel.EVENT_SUPERVISOR_DRIFT_CLASSIFICATION,
                 cp=cp, level="ok", reason="", elapsed_s=70.0 + i,
                 baseline_elapsed_s=70.0)
    # 1 drift event for CP-04 with retry recovery
    tel.emit(store, "sup-1", tel.EVENT_SUPERVISOR_DRIFT_CLASSIFICATION,
             cp="CP-04", level="drift", reason="cube_position_absurd",
             elapsed_s=80.0, baseline_elapsed_s=None)
    tel.emit(store, "sup-1", tel.EVENT_SUPERVISOR_DRIFT_DETECTED,
             cp="CP-04", reason="cube_position_absurd", evidence={"cube": [1e8]})
    tel.emit(store, "sup-1", tel.EVENT_SUPERVISOR_RESTART_STARTED,
             cp="CP-04", kind="hard")
    tel.emit(store, "sup-1", tel.EVENT_SUPERVISOR_RESTART_COMPLETED,
             duration_ms=45000, new_baseline_rss_mb=1100,
             new_baseline_gpu_mb=4100)
    tel.emit(store, "sup-1", tel.EVENT_SUPERVISOR_DRIFT_CLASSIFICATION,
             cp="CP-04", level="ok", reason="", elapsed_s=72.0, retry=True)
    # 1 soft-reset
    tel.emit(store, "sup-1", tel.EVENT_SUPERVISOR_SOFT_RESET,
             cp="CP-05", actions=["stage_reset"], duration_ms=2000, errors=[])
    tel.emit(store, "sup-1", tel.EVENT_SUPERVISOR_STOPPED,
             total_restarts=1, total_drift_events=1)


def test_supervisor_health_summary(store):
    _seed_supervisor_corpus(store)
    events = store.list_events(limit=1000)
    from scripts.qa.analyze_multimodal_usage import supervisor_health_summary
    h = supervisor_health_summary(events)
    assert h["drift_events"] == 1
    assert h["restart_completed"] == 1
    assert h["soft_resets"] == 1
    assert h["total_classifications"] >= 4
    assert h["abort_rate"] == 0.0


def test_supervisor_drift_precision_recovery_counted(store):
    _seed_supervisor_corpus(store)
    events = store.list_events(limit=1000)
    from scripts.qa.analyze_multimodal_usage import supervisor_drift_precision
    p = supervisor_drift_precision(events)
    assert p["drift_events"] == 1
    assert p["recovered_on_retry"] == 1
    assert p["precision"] == 1.0


def test_supervisor_per_cp_baselines(store):
    _seed_supervisor_corpus(store)
    events = store.list_events(limit=1000)
    from scripts.qa.analyze_multimodal_usage import supervisor_per_cp_baselines
    b = supervisor_per_cp_baselines(events)
    # CP-01..CP-03 have one ok sample each
    assert "CP-01" in b
    assert b["CP-01"]["n"] == 1
    assert b["CP-01"]["p50"] == 71.0


def test_supervisor_aggregate_includes_supervisor_dashboards(store):
    _seed_supervisor_corpus(store)
    events = store.list_events(limit=1000)
    from scripts.qa.analyze_multimodal_usage import aggregate
    a = aggregate(events)
    assert "supervisor_health" in a
    assert "supervisor_drift_precision" in a
    assert "supervisor_per_cp_baselines" in a
