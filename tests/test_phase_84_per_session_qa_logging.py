"""Phase 84 — Per-session QA logging: contract tests.

Gate criteria:
  * log writes ndjson (each line is valid JSON)
  * per-session ring buffer caps at max_events (drops oldest)
  * get_recent returns newest-last (chronological)
  * multi-session isolation (separate files, separate buffers)
  * payload merge semantics (envelope fields overwrite payload keys)
  * metadata status == "landed"
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.l0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _read_ndjson(path: Path) -> list[dict]:
    """Parse all lines of an ndjson file into a list of dicts."""
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_phase_84_metadata():
    """Metadata reflects landed status."""
    from service.isaac_assist_service.multimodal.per_session_qa_logging import get_phase_metadata
    md = get_phase_metadata()
    assert md["phase"] == 84
    assert md["status"] == "landed"


def test_log_writes_ndjson(tmp_path: Path):
    """Each logged event is written as a valid JSON line to the ndjson file."""
    from service.isaac_assist_service.multimodal.per_session_qa_logging import SessionQALogger

    logger = SessionQALogger("sess-001", tmp_path)
    logger.log_event("tool_call", {"tool": "get_attribute", "prim": "/World/Cube"})
    logger.log_event("assertion_pass", {"check": "prim_exists"})
    logger.flush()
    logger.close()

    log_file = tmp_path / "sess-001.ndjson"
    assert log_file.exists(), "ndjson file must be created"

    rows = _read_ndjson(log_file)
    assert len(rows) == 2

    first = rows[0]
    assert first["event_type"] == "tool_call"
    assert first["session_id"] == "sess-001"
    assert first["tool"] == "get_attribute"
    assert "timestamp" in first

    second = rows[1]
    assert second["event_type"] == "assertion_pass"
    assert second["check"] == "prim_exists"


def test_ring_buffer_caps_at_max_events(tmp_path: Path):
    """Ring buffer never exceeds max_events; oldest entries are dropped."""
    from service.isaac_assist_service.multimodal.per_session_qa_logging import SessionQALogger

    cap = 10
    logger = SessionQALogger("sess-cap", tmp_path, max_events=cap)
    for i in range(25):
        logger.log_event("tick", {"i": i})
    logger.close()

    # Buffer should hold exactly cap items
    recent = logger.get_recent(n=9999)
    assert len(recent) == cap

    # And they should be the LAST `cap` events (oldest dropped)
    values = [e["i"] for e in recent]
    assert values == list(range(25 - cap, 25))


def test_multi_session_isolation(tmp_path: Path):
    """Two sessions write to separate files and have independent buffers."""
    from service.isaac_assist_service.multimodal.per_session_qa_logging import SessionQALogger

    a = SessionQALogger("alpha", tmp_path)
    b = SessionQALogger("beta", tmp_path)

    a.log_event("ev", {"src": "alpha"})
    b.log_event("ev", {"src": "beta"})
    b.log_event("ev", {"src": "beta-2"})

    a.close()
    b.close()

    alpha_rows = _read_ndjson(tmp_path / "alpha.ndjson")
    beta_rows = _read_ndjson(tmp_path / "beta.ndjson")

    assert len(alpha_rows) == 1
    assert len(beta_rows) == 2

    assert all(r["session_id"] == "alpha" for r in alpha_rows)
    assert all(r["session_id"] == "beta" for r in beta_rows)

    # Buffers are independent
    assert len(a.get_recent()) == 1
    assert len(b.get_recent()) == 2


def test_ndjson_all_lines_parseable(tmp_path: Path):
    """Every line in the file is parseable JSON (no partial writes)."""
    from service.isaac_assist_service.multimodal.per_session_qa_logging import SessionQALogger

    logger = SessionQALogger("parseable", tmp_path)
    for i in range(50):
        logger.log_event("item", {"seq": i, "data": "x" * 80})
    logger.flush()
    logger.close()

    log_file = tmp_path / "parseable.ndjson"
    lines = [l for l in log_file.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(lines) == 50
    for ln in lines:
        parsed = json.loads(ln)  # raises if invalid
        assert "timestamp" in parsed
        assert "session_id" in parsed
        assert "event_type" in parsed


def test_get_recent_ordering(tmp_path: Path):
    """get_recent returns events in chronological order (oldest first, newest last)."""
    from service.isaac_assist_service.multimodal.per_session_qa_logging import SessionQALogger

    logger = SessionQALogger("order-test", tmp_path)
    for i in range(20):
        logger.log_event("tick", {"seq": i})
    logger.close()

    recent = logger.get_recent(n=10)
    assert len(recent) == 10
    # Should be the last 10 events in order: seq 10..19
    seqs = [e["seq"] for e in recent]
    assert seqs == list(range(10, 20))


def test_payload_merge_semantics(tmp_path: Path):
    """Envelope fields (timestamp, session_id, event_type) override payload keys."""
    from service.isaac_assist_service.multimodal.per_session_qa_logging import SessionQALogger

    logger = SessionQALogger("merge-test", tmp_path)
    # Attempt to override envelope fields via payload — they should be ignored
    logger.log_event(
        "injection",
        {
            "session_id": "EVIL",
            "event_type": "HIJACKED",
            "timestamp": "1970-01-01T00:00:00",
            "real_field": "present",
        },
    )
    logger.close()

    rows = _read_ndjson(tmp_path / "merge-test.ndjson")
    assert len(rows) == 1
    row = rows[0]

    # Envelope fields must reflect actual values, not attacker payload
    assert row["session_id"] == "merge-test"
    assert row["event_type"] == "injection"
    assert row["timestamp"] != "1970-01-01T00:00:00"
    # Non-conflicting payload key is preserved
    assert row["real_field"] == "present"
