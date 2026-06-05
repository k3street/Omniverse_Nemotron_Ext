"""Phase 94 — Knowledge base feedback loop tests.

Gate: pytest — feedback append + KB index refresh.
7+ tests using tmp_path covering all public contracts.
"""
from __future__ import annotations

import json

import pytest

pytestmark = pytest.mark.l0


# ---------------------------------------------------------------------------
# Imports (deferred inside each test for fast collection)
# ---------------------------------------------------------------------------


def _imports():
    from service.isaac_assist_service.multimodal.kb_feedback_loop import (
        KBFeedbackEntry,
        KBFeedbackWriter,
        KBIndexRefresher,
        get_phase_metadata,
    )
    return KBFeedbackEntry, KBFeedbackWriter, KBIndexRefresher, get_phase_metadata


# ---------------------------------------------------------------------------
# 1. Metadata contract
# ---------------------------------------------------------------------------


def test_phase_94_metadata():
    _, _, _, get_phase_metadata = _imports()
    md = get_phase_metadata()
    assert md["phase"] == 94
    assert md["status"] == "landed"
    assert "spec_ref" in md


# ---------------------------------------------------------------------------
# 2. append + read_all roundtrip
# ---------------------------------------------------------------------------


def test_append_and_read_all_roundtrip(tmp_path):
    KBFeedbackEntry, KBFeedbackWriter, _, _ = _imports()

    writer = KBFeedbackWriter(tmp_path / "feedback")
    entry = KBFeedbackEntry(
        kb_doc_id="doc-001",
        feedback_kind="correction",
        note="Mass value is incorrect; should be 1.2 kg not 2.1 kg.",
        submitter="alice",
    )
    eid = writer.append(entry)

    assert eid == entry.entry_id  # returned id matches

    retrieved = writer.read_all("doc-001")
    assert len(retrieved) == 1
    r = retrieved[0]
    assert r.entry_id == entry.entry_id
    assert r.kb_doc_id == "doc-001"
    assert r.feedback_kind == "correction"
    assert r.note == "Mass value is incorrect; should be 1.2 kg not 2.1 kg."
    assert r.submitter == "alice"
    assert r.timestamp  # non-empty ISO string


# ---------------------------------------------------------------------------
# 3. NDJSON validity — each line is valid JSON, one per entry
# ---------------------------------------------------------------------------


def test_ndjson_validity(tmp_path):
    KBFeedbackEntry, KBFeedbackWriter, _, _ = _imports()

    writer = KBFeedbackWriter(tmp_path / "feedback")
    for i in range(3):
        writer.append(KBFeedbackEntry(
            kb_doc_id="doc-002",
            feedback_kind="endorsement",
            note=f"Looks good #{i}",
        ))

    ndjson_file = tmp_path / "feedback" / "doc-002.ndjson"
    assert ndjson_file.exists()

    lines = [l for l in ndjson_file.read_text().splitlines() if l.strip()]
    assert len(lines) == 3
    for line in lines:
        record = json.loads(line)  # must not raise
        assert "entry_id" in record
        assert "kb_doc_id" in record
        assert "feedback_kind" in record
        assert "timestamp" in record


# ---------------------------------------------------------------------------
# 4. Multiple documents are isolated (separate files)
# ---------------------------------------------------------------------------


def test_multiple_docs_isolated(tmp_path):
    KBFeedbackEntry, KBFeedbackWriter, _, _ = _imports()

    writer = KBFeedbackWriter(tmp_path / "feedback")
    writer.append(KBFeedbackEntry(kb_doc_id="alpha", feedback_kind="addition", note="extra A"))
    writer.append(KBFeedbackEntry(kb_doc_id="beta", feedback_kind="deprecation", note="removed B"))
    writer.append(KBFeedbackEntry(kb_doc_id="alpha", feedback_kind="correction", note="fix A2"))

    alpha_entries = writer.read_all("alpha")
    beta_entries = writer.read_all("beta")

    assert len(alpha_entries) == 2
    assert len(beta_entries) == 1
    assert all(e.kb_doc_id == "alpha" for e in alpha_entries)
    assert beta_entries[0].kb_doc_id == "beta"

    # Physical files are separate
    assert (tmp_path / "feedback" / "alpha.ndjson").exists()
    assert (tmp_path / "feedback" / "beta.ndjson").exists()


# ---------------------------------------------------------------------------
# 5. refresh() produces correct aggregate
# ---------------------------------------------------------------------------


def test_refresh_produces_correct_aggregate(tmp_path):
    KBFeedbackEntry, KBFeedbackWriter, KBIndexRefresher, _ = _imports()

    fb_dir = tmp_path / "feedback"
    index_path = tmp_path / "kb_index.json"
    writer = KBFeedbackWriter(fb_dir)

    # doc-A: 2 corrections, 1 endorsement
    writer.append(KBFeedbackEntry(kb_doc_id="doc-A", feedback_kind="correction", note="fix 1"))
    writer.append(KBFeedbackEntry(kb_doc_id="doc-A", feedback_kind="correction", note="fix 2"))
    writer.append(KBFeedbackEntry(kb_doc_id="doc-A", feedback_kind="endorsement", note="looks good"))

    # doc-B: 1 addition
    writer.append(KBFeedbackEntry(kb_doc_id="doc-B", feedback_kind="addition", note="new info"))

    refresher = KBIndexRefresher(fb_dir, index_path)
    index = refresher.refresh()

    assert "doc-A" in index
    assert index["doc-A"]["n_feedback"] == 3
    assert index["doc-A"]["by_kind"]["correction"] == 2
    assert index["doc-A"]["by_kind"]["endorsement"] == 1
    assert index["doc-A"]["by_kind"]["addition"] == 0
    assert index["doc-A"]["by_kind"]["deprecation"] == 0
    assert index["doc-A"]["last_update"]  # non-empty

    assert "doc-B" in index
    assert index["doc-B"]["n_feedback"] == 1
    assert index["doc-B"]["by_kind"]["addition"] == 1

    # Index file on disk matches returned dict
    on_disk = json.loads(index_path.read_text())
    assert on_disk == index


# ---------------------------------------------------------------------------
# 6. corrections_for filters by kind
# ---------------------------------------------------------------------------


def test_corrections_for_filters_by_kind(tmp_path):
    KBFeedbackEntry, KBFeedbackWriter, _, _ = _imports()

    writer = KBFeedbackWriter(tmp_path / "feedback")
    writer.append(KBFeedbackEntry(kb_doc_id="doc-C", feedback_kind="correction", note="bad value"))
    writer.append(KBFeedbackEntry(kb_doc_id="doc-C", feedback_kind="addition", note="extra info"))
    writer.append(KBFeedbackEntry(kb_doc_id="doc-C", feedback_kind="correction", note="another fix"))
    writer.append(KBFeedbackEntry(kb_doc_id="doc-C", feedback_kind="endorsement", note="great"))

    corrections = writer.corrections_for("doc-C")
    assert len(corrections) == 2
    assert all(e.feedback_kind == "correction" for e in corrections)


# ---------------------------------------------------------------------------
# 7. Empty feedback dir handled gracefully
# ---------------------------------------------------------------------------


def test_empty_feedback_dir_handled_gracefully(tmp_path):
    _, KBFeedbackWriter, KBIndexRefresher, _ = _imports()

    fb_dir = tmp_path / "feedback"
    index_path = tmp_path / "kb_index.json"

    # read_all on a doc that has no file
    writer = KBFeedbackWriter(fb_dir)
    assert writer.read_all("nonexistent-doc") == []
    assert writer.corrections_for("nonexistent-doc") == []

    # refresh on empty dir produces empty index
    refresher = KBIndexRefresher(fb_dir, index_path)
    index = refresher.refresh()
    assert index == {}
    assert index_path.exists()
    assert json.loads(index_path.read_text()) == {}


# ---------------------------------------------------------------------------
# 8. KBFeedbackEntry auto-generates entry_id and timestamp
# ---------------------------------------------------------------------------


def test_entry_auto_fields():
    KBFeedbackEntry, _, _, _ = _imports()

    e1 = KBFeedbackEntry(kb_doc_id="x", feedback_kind="addition", note="n")
    e2 = KBFeedbackEntry(kb_doc_id="x", feedback_kind="addition", note="n")

    # entry_id should be a non-empty UUID string
    assert e1.entry_id
    # Two entries get distinct IDs
    assert e1.entry_id != e2.entry_id
    # timestamp is a non-empty string that looks like ISO-8601
    assert "T" in e1.timestamp or "-" in e1.timestamp

    # Default submitter
    assert e1.submitter == "anonymous"


# ---------------------------------------------------------------------------
# 9. to_dict / from_dict roundtrip
# ---------------------------------------------------------------------------


def test_entry_serialisation_roundtrip():
    KBFeedbackEntry, _, _, _ = _imports()

    original = KBFeedbackEntry(
        kb_doc_id="doc-99",
        feedback_kind="deprecation",
        note="This section is outdated.",
        submitter="bob",
    )
    d = original.to_dict()
    restored = KBFeedbackEntry.from_dict(d)

    assert restored.entry_id == original.entry_id
    assert restored.kb_doc_id == original.kb_doc_id
    assert restored.feedback_kind == original.feedback_kind
    assert restored.note == original.note
    assert restored.submitter == original.submitter
    assert restored.timestamp == original.timestamp


# ---------------------------------------------------------------------------
# 10. refresh() without a pre-existing feedback dir handles gracefully
# ---------------------------------------------------------------------------


def test_refresh_nonexistent_dir_handles_gracefully(tmp_path):
    _, _, KBIndexRefresher, _ = _imports()

    fb_dir = tmp_path / "does_not_exist"
    index_path = tmp_path / "index.json"

    refresher = KBIndexRefresher(fb_dir, index_path)
    index = refresher.refresh()
    assert index == {}
    assert index_path.exists()
