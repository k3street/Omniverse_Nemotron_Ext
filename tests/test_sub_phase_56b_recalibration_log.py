"""Phase 56b contract tests — recalibration log.

Gate: log writes NDJSON, queryable by dimension.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.l0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_event(dimension: str = "depth", trigger: str = "systematic_bias",
                mean_delta: float = 0.05, n_samples: int = 100,
                old: dict | None = None, new: dict | None = None,
                notes: str = "") -> "RecalibrationEvent":
    from service.isaac_assist_service.multimodal.sub_phase_56b_recalibration_log import (
        RecalibrationEvent,
    )
    return RecalibrationEvent(
        dimension=dimension,
        old_params=old or {"scale": 1.0, "offset": 0.0},
        new_params=new or {"scale": 0.95, "offset": 0.01},
        mean_delta=mean_delta,
        n_samples=n_samples,
        trigger=trigger,
        notes=notes,
    )


# ---------------------------------------------------------------------------
# T1 — metadata
# ---------------------------------------------------------------------------

def test_phase_56b_metadata():
    from service.isaac_assist_service.multimodal.sub_phase_56b_recalibration_log import (
        get_phase_metadata,
    )
    md = get_phase_metadata()
    assert md["phase"] == "56b"
    assert md["status"] == "landed"
    assert "spec_ref" in md


# ---------------------------------------------------------------------------
# T2 — record + roundtrip (NDJSON parseable)
# ---------------------------------------------------------------------------

def test_record_and_roundtrip(tmp_path: Path):
    from service.isaac_assist_service.multimodal.sub_phase_56b_recalibration_log import (
        RecalibrationLog,
    )

    log = RecalibrationLog(tmp_path / "recalib.ndjson")
    event = _make_event(notes="first run")
    returned_id = log.record(event)

    assert returned_id == event.event_id

    # The file must exist and every line must be valid JSON
    lines = (tmp_path / "recalib.ndjson").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    parsed = json.loads(lines[0])
    assert parsed["event_id"] == event.event_id
    assert parsed["dimension"] == "depth"
    assert parsed["trigger"] == "systematic_bias"
    assert parsed["notes"] == "first run"
    assert isinstance(parsed["old_params"], dict)
    assert isinstance(parsed["new_params"], dict)


# ---------------------------------------------------------------------------
# T3 — for_dimension filter
# ---------------------------------------------------------------------------

def test_for_dimension_filter(tmp_path: Path):
    from service.isaac_assist_service.multimodal.sub_phase_56b_recalibration_log import (
        RecalibrationLog,
    )

    log = RecalibrationLog(tmp_path / "recalib.ndjson")
    log.record(_make_event(dimension="depth"))
    log.record(_make_event(dimension="color"))
    log.record(_make_event(dimension="depth"))
    log.record(_make_event(dimension="ir"))

    depth_events = log.for_dimension("depth")
    assert len(depth_events) == 2
    assert all(e.dimension == "depth" for e in depth_events)

    color_events = log.for_dimension("color")
    assert len(color_events) == 1

    missing = log.for_dimension("nonexistent")
    assert missing == []


# ---------------------------------------------------------------------------
# T4 — latest_for_dimension ordering
# ---------------------------------------------------------------------------

def test_latest_for_dimension_ordering(tmp_path: Path):
    from service.isaac_assist_service.multimodal.sub_phase_56b_recalibration_log import (
        RecalibrationLog,
    )

    log = RecalibrationLog(tmp_path / "recalib.ndjson")
    e1 = _make_event(dimension="depth", mean_delta=0.1)
    e2 = _make_event(dimension="depth", mean_delta=0.2)
    log.record(e1)
    log.record(e2)

    latest = log.latest_for_dimension("depth")
    assert latest is not None
    assert latest.event_id == e2.event_id
    assert latest.mean_delta == pytest.approx(0.2)


def test_latest_for_dimension_none_when_empty(tmp_path: Path):
    from service.isaac_assist_service.multimodal.sub_phase_56b_recalibration_log import (
        RecalibrationLog,
    )

    log = RecalibrationLog(tmp_path / "recalib.ndjson")
    assert log.latest_for_dimension("depth") is None


# ---------------------------------------------------------------------------
# T5 — all_events count
# ---------------------------------------------------------------------------

def test_all_events_count(tmp_path: Path):
    from service.isaac_assist_service.multimodal.sub_phase_56b_recalibration_log import (
        RecalibrationLog,
    )

    log = RecalibrationLog(tmp_path / "recalib.ndjson")
    for dim in ["depth", "color", "ir", "depth", "color"]:
        log.record(_make_event(dimension=dim))

    events = log.all_events()
    assert len(events) == 5
    # Order preserved (oldest first)
    assert events[0].dimension == "depth"
    assert events[-1].dimension == "color"


# ---------------------------------------------------------------------------
# T6 — summary aggregation
# ---------------------------------------------------------------------------

def test_summary_aggregation(tmp_path: Path):
    from service.isaac_assist_service.multimodal.sub_phase_56b_recalibration_log import (
        RecalibrationLog,
    )

    log = RecalibrationLog(tmp_path / "recalib.ndjson")
    for dim in ["depth", "depth", "depth", "color", "ir", "ir"]:
        log.record(_make_event(dimension=dim))

    s = log.summary()
    assert s["depth"] == 3
    assert s["color"] == 1
    assert s["ir"] == 2
    assert set(s.keys()) == {"depth", "color", "ir"}


def test_summary_empty_log(tmp_path: Path):
    from service.isaac_assist_service.multimodal.sub_phase_56b_recalibration_log import (
        RecalibrationLog,
    )

    log = RecalibrationLog(tmp_path / "recalib.ndjson")
    assert log.summary() == {}


# ---------------------------------------------------------------------------
# T7 — NDJSON file is multi-line parseable (one object per line)
# ---------------------------------------------------------------------------

def test_ndjson_multi_line_parseable(tmp_path: Path):
    from service.isaac_assist_service.multimodal.sub_phase_56b_recalibration_log import (
        RecalibrationLog,
    )

    log = RecalibrationLog(tmp_path / "recalib.ndjson")
    ids = [log.record(_make_event(dimension=f"dim{i}")) for i in range(5)]

    raw = (tmp_path / "recalib.ndjson").read_text(encoding="utf-8")
    lines = [ln for ln in raw.splitlines() if ln.strip()]
    assert len(lines) == 5
    parsed_ids = [json.loads(ln)["event_id"] for ln in lines]
    assert parsed_ids == ids


# ---------------------------------------------------------------------------
# T8 — auto-assigned fields (event_id UUID, timestamp ISO)
# ---------------------------------------------------------------------------

def test_auto_fields():
    import re
    from service.isaac_assist_service.multimodal.sub_phase_56b_recalibration_log import (
        RecalibrationEvent,
    )

    e = _make_event()
    # event_id must look like a UUID4
    assert re.fullmatch(
        r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}",
        e.event_id,
    )
    # timestamp must be ISO-8601 with timezone offset
    assert "T" in e.timestamp
    assert e.timestamp.endswith("+00:00")


# ---------------------------------------------------------------------------
# T9 — parent directories created automatically
# ---------------------------------------------------------------------------

def test_parent_dirs_created(tmp_path: Path):
    from service.isaac_assist_service.multimodal.sub_phase_56b_recalibration_log import (
        RecalibrationLog,
    )

    deep_path = tmp_path / "a" / "b" / "c" / "recalib.ndjson"
    log = RecalibrationLog(deep_path)
    log.record(_make_event())
    assert deep_path.exists()
