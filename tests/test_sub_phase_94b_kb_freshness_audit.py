"""Tests for Phase 94b — KB freshness audit.

Uses tmp_path + os.utime to backdate files so every test is deterministic.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.l0

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_ONE_DAY_S = 86_400  # seconds


def _backdate(path: Path, days_ago: int) -> None:
    """Set *path* mtime to *days_ago* days in the past."""
    ts = time.time() - days_ago * _ONE_DAY_S
    os.utime(path, (ts, ts))


def _make_kb_file(directory: Path, stem: str, ext: str = ".json") -> Path:
    """Create an empty KB file and return its path."""
    p = directory / f"{stem}{ext}"
    p.write_text("{}", encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestPhaseMetadata:
    def test_metadata(self):
        from service.isaac_assist_service.multimodal.sub_phase_94b_kb_freshness_audit import (
            get_phase_metadata,
        )

        md = get_phase_metadata()
        assert md["phase"] == "94b"
        assert md["status"] == "landed"
        assert "spec_ref" in md


class TestCleanDir:
    """5 fresh files → 0 stale."""

    def test_zero_stale(self, tmp_path: Path):
        from service.isaac_assist_service.multimodal.sub_phase_94b_kb_freshness_audit import (
            KBFreshnessAuditor,
        )

        for i in range(5):
            p = _make_kb_file(tmp_path, f"fresh_{i}")
            _backdate(p, days_ago=10)  # well within default 90-day threshold

        auditor = KBFreshnessAuditor(tmp_path)
        report = auditor.audit()

        assert report.total_docs == 5
        assert report.stale_docs == 0
        assert report.fresh_docs == 5


class TestMixedFreshnessReport:
    """3 fresh + 4 stale → report counts correct."""

    def test_counts(self, tmp_path: Path):
        from service.isaac_assist_service.multimodal.sub_phase_94b_kb_freshness_audit import (
            KBFreshnessAuditor,
        )

        for i in range(3):
            p = _make_kb_file(tmp_path, f"fresh_{i}")
            _backdate(p, days_ago=30)

        for i in range(4):
            p = _make_kb_file(tmp_path, f"stale_{i}")
            _backdate(p, days_ago=120)

        auditor = KBFreshnessAuditor(tmp_path)
        report = auditor.audit()

        assert report.total_docs == 7
        assert report.fresh_docs == 3
        assert report.stale_docs == 4
        assert report.stale_threshold_days == 90


class TestOldestDocIdentification:
    """oldest_doc_id identifies the oldest file."""

    def test_oldest_doc_id(self, tmp_path: Path):
        from service.isaac_assist_service.multimodal.sub_phase_94b_kb_freshness_audit import (
            KBFreshnessAuditor,
        )

        p_medium = _make_kb_file(tmp_path, "medium_doc")
        _backdate(p_medium, days_ago=100)

        p_oldest = _make_kb_file(tmp_path, "oldest_doc")
        _backdate(p_oldest, days_ago=400)

        p_recent = _make_kb_file(tmp_path, "recent_doc")
        _backdate(p_recent, days_ago=5)

        auditor = KBFreshnessAuditor(tmp_path)
        report = auditor.audit()

        assert report.oldest_doc_id == "oldest_doc"
        assert report.oldest_age_days >= 400


class TestCustomThreshold:
    """Custom threshold of 30 days marks 60-day files stale; default 90 would not."""

    def test_custom_threshold_marks_stale(self, tmp_path: Path):
        from service.isaac_assist_service.multimodal.sub_phase_94b_kb_freshness_audit import (
            KBFreshnessAuditor,
        )

        p = _make_kb_file(tmp_path, "borderline")
        _backdate(p, days_ago=60)

        # With default 90-day threshold → fresh
        default_auditor = KBFreshnessAuditor(tmp_path, stale_threshold_days=90)
        default_report = default_auditor.audit()
        assert default_report.stale_docs == 0

        # With 30-day threshold → stale
        strict_auditor = KBFreshnessAuditor(tmp_path, stale_threshold_days=30)
        strict_report = strict_auditor.audit()
        assert strict_report.stale_docs == 1


class TestEmptyDir:
    """Empty directory handled gracefully (total=0, stale=0)."""

    def test_empty_dir(self, tmp_path: Path):
        from service.isaac_assist_service.multimodal.sub_phase_94b_kb_freshness_audit import (
            KBFreshnessAuditor,
        )

        auditor = KBFreshnessAuditor(tmp_path)
        report = auditor.audit()

        assert report.total_docs == 0
        assert report.stale_docs == 0
        assert report.fresh_docs == 0
        assert report.oldest_doc_id is None
        assert report.oldest_age_days == 0

    def test_missing_dir(self, tmp_path: Path):
        from service.isaac_assist_service.multimodal.sub_phase_94b_kb_freshness_audit import (
            KBFreshnessAuditor,
        )

        nonexistent = tmp_path / "does_not_exist"
        auditor = KBFreshnessAuditor(nonexistent)
        report = auditor.audit()

        assert report.total_docs == 0
        assert report.stale_docs == 0


class TestListStale:
    """list_stale returns only entries past threshold."""

    def test_returns_only_stale(self, tmp_path: Path):
        from service.isaac_assist_service.multimodal.sub_phase_94b_kb_freshness_audit import (
            KBFreshnessAuditor,
        )

        fresh_paths = []
        for i in range(3):
            p = _make_kb_file(tmp_path, f"fresh_{i}")
            _backdate(p, days_ago=20)
            fresh_paths.append(p)

        stale_stems = set()
        for i in range(2):
            p = _make_kb_file(tmp_path, f"stale_{i}")
            _backdate(p, days_ago=200)
            stale_stems.add(f"stale_{i}")

        auditor = KBFreshnessAuditor(tmp_path)
        stale = auditor.list_stale()

        assert len(stale) == 2
        returned_ids = {e.doc_id for e in stale}
        assert returned_ids == stale_stems
        # All entries must have stale=True
        assert all(e.stale for e in stale)

    def test_list_stale_empty_when_all_fresh(self, tmp_path: Path):
        from service.isaac_assist_service.multimodal.sub_phase_94b_kb_freshness_audit import (
            KBFreshnessAuditor,
        )

        for i in range(4):
            p = _make_kb_file(tmp_path, f"fresh_{i}")
            _backdate(p, days_ago=5)

        auditor = KBFreshnessAuditor(tmp_path)
        assert auditor.list_stale() == []


class TestListAll:
    """list_all returns every entry, fresh + stale, sorted oldest-first."""

    def test_list_all_covers_all_docs(self, tmp_path: Path):
        from service.isaac_assist_service.multimodal.sub_phase_94b_kb_freshness_audit import (
            KBFreshnessAuditor,
        )

        stems = set()
        for i, age in enumerate([5, 50, 95, 150]):
            stem = f"doc_{i}"
            p = _make_kb_file(tmp_path, stem)
            _backdate(p, days_ago=age)
            stems.add(stem)

        auditor = KBFreshnessAuditor(tmp_path)
        all_entries = auditor.list_all()

        assert len(all_entries) == 4
        assert {e.doc_id for e in all_entries} == stems
        # Sorted oldest-first (descending age)
        ages = [e.age_days for e in all_entries]
        assert ages == sorted(ages, reverse=True)


class TestMarkdownFiles:
    """Auditor picks up .md files as well as .json."""

    def test_md_files_counted(self, tmp_path: Path):
        from service.isaac_assist_service.multimodal.sub_phase_94b_kb_freshness_audit import (
            KBFreshnessAuditor,
        )

        p_json = _make_kb_file(tmp_path, "doc_json", ext=".json")
        _backdate(p_json, days_ago=10)

        p_md = _make_kb_file(tmp_path, "doc_md", ext=".md")
        _backdate(p_md, days_ago=200)

        auditor = KBFreshnessAuditor(tmp_path)
        report = auditor.audit()

        assert report.total_docs == 2
        assert report.stale_docs == 1


class TestConvenienceWrapper:
    """audit_default_kb_dir works with an explicit kb_dir argument."""

    def test_wrapper_uses_provided_dir(self, tmp_path: Path):
        from service.isaac_assist_service.multimodal.sub_phase_94b_kb_freshness_audit import (
            audit_default_kb_dir,
        )

        p = _make_kb_file(tmp_path, "entry")
        _backdate(p, days_ago=10)

        report = audit_default_kb_dir(kb_dir=tmp_path, threshold_days=90)
        assert report.total_docs == 1
        assert report.stale_docs == 0

    def test_wrapper_empty_dir(self, tmp_path: Path):
        from service.isaac_assist_service.multimodal.sub_phase_94b_kb_freshness_audit import (
            audit_default_kb_dir,
        )

        report = audit_default_kb_dir(kb_dir=tmp_path)
        assert report.total_docs == 0
