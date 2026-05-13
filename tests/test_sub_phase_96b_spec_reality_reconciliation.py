"""Phase 96b contract tests — Spec-reality reconciliation workflow.

Gate: reconciliation detects unreferenced files and orphan spec lines.

Tests use tmp_path with synthetic metadata YAML and scan directories so
they run fully offline without touching the live repo state.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.l0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_metadata(path: Path, phases: dict) -> Path:
    """Write a phase_metadata.yaml to *path* and return the path."""
    path.write_text(yaml.dump(phases), encoding="utf-8")
    return path


def _make_reconciler(metadata_path: Path):
    from service.isaac_assist_service.multimodal.sub_phase_96b_spec_reality_reconciliation import (
        SpecRealityReconciler,
    )
    return SpecRealityReconciler(metadata_path=metadata_path)


# ---------------------------------------------------------------------------
# 1. Metadata contract
# ---------------------------------------------------------------------------

def test_phase_96b_metadata():
    from service.isaac_assist_service.multimodal.sub_phase_96b_spec_reality_reconciliation import (
        get_phase_metadata,
        PHASE_STATUS,
    )
    md = get_phase_metadata()
    assert md["phase"] == "96b"
    assert md["status"] == "landed"
    assert "title" in md
    assert "spec_ref" in md
    assert PHASE_STATUS == "landed"


# ---------------------------------------------------------------------------
# 2. status_mismatch detected when "landed" phase points to non-existent file
# ---------------------------------------------------------------------------

def test_status_mismatch_detected_for_missing_files(tmp_path: Path):
    """A landed phase whose declared files do not exist produces a warning."""
    meta_file = tmp_path / "phase_metadata.yaml"
    # Declare a file that does not exist on disk.
    _write_metadata(meta_file, {
        "10": {
            "title": "Phantom phase",
            "status": "landed",
            "files": ["service/no_such_dir/phantom_module.py"],
        }
    })

    reconciler = _make_reconciler(meta_file)
    findings = reconciler.find_status_mismatches()

    assert len(findings) == 1
    f = findings[0]
    assert f.category == "status_mismatch"
    assert f.phase_id == "10"
    assert f.severity == "warn"
    assert "10" in f.detail
    assert "phantom_module.py" in f.detail


# ---------------------------------------------------------------------------
# 3. status_mismatch NOT raised when files= is empty list
# ---------------------------------------------------------------------------

def test_status_mismatch_not_raised_when_files_empty(tmp_path: Path):
    """Empty files list on a landed phase must NOT produce a mismatch finding."""
    meta_file = tmp_path / "phase_metadata.yaml"
    _write_metadata(meta_file, {
        "42": {
            "title": "Data-only phase",
            "status": "landed",
            "files": [],
        }
    })

    reconciler = _make_reconciler(meta_file)
    findings = reconciler.find_status_mismatches()

    assert findings == []


# ---------------------------------------------------------------------------
# 3b. status_mismatch NOT raised for scaffold phases with missing files
# ---------------------------------------------------------------------------

def test_status_mismatch_not_raised_for_scaffold_phases(tmp_path: Path):
    """Scaffold phases are not checked for file presence."""
    meta_file = tmp_path / "phase_metadata.yaml"
    _write_metadata(meta_file, {
        "77": {
            "title": "Future feature",
            "status": "scaffold",
            "files": ["service/some/future_module.py"],
        }
    })

    reconciler = _make_reconciler(meta_file)
    findings = reconciler.find_status_mismatches()

    assert findings == []


# ---------------------------------------------------------------------------
# 4. broken_blocker_chain detected when blocker ID missing
# ---------------------------------------------------------------------------

def test_broken_blocker_chain_detected(tmp_path: Path):
    """A phase that references a non-existent blocker ID emits an error."""
    meta_file = tmp_path / "phase_metadata.yaml"
    _write_metadata(meta_file, {
        "5": {
            "title": "Dependent phase",
            "status": "scaffold",
            "blocked_by": ["999"],  # 999 does not exist
        }
    })

    reconciler = _make_reconciler(meta_file)
    findings = reconciler.find_broken_blocker_chains()

    assert len(findings) == 1
    f = findings[0]
    assert f.category == "blocker_chain_broken"
    assert f.phase_id == "5"
    assert f.severity == "error"
    assert "999" in f.detail


# ---------------------------------------------------------------------------
# 5. blocker_chain ok when all blockers present
# ---------------------------------------------------------------------------

def test_blocker_chain_ok_when_all_blockers_present(tmp_path: Path):
    """No findings when every referenced blocker ID exists in metadata."""
    meta_file = tmp_path / "phase_metadata.yaml"
    _write_metadata(meta_file, {
        "1": {"title": "Base", "status": "landed"},
        "2": {"title": "Middle", "status": "landed", "blocked_by": ["1"]},
        "3": {"title": "Top", "status": "scaffold", "blocked_by": ["1", "2"]},
    })

    reconciler = _make_reconciler(meta_file)
    findings = reconciler.find_broken_blocker_chains()

    assert findings == []


# ---------------------------------------------------------------------------
# 6. file_orphan detected when phase_X.py exists but no phase references it
# ---------------------------------------------------------------------------

def test_file_orphan_detected_for_unreferenced_file(tmp_path: Path):
    """A phase_*.py file that no phase claims in its files list → file_orphan."""
    meta_file = tmp_path / "phase_metadata.yaml"
    # Metadata does NOT reference orphan_module.py
    _write_metadata(meta_file, {
        "1": {
            "title": "Actual phase",
            "status": "landed",
            "files": ["service/foo/other_module.py"],
        }
    })

    scan_dir = tmp_path / "modules"
    scan_dir.mkdir()
    orphan = scan_dir / "phase_orphan_module.py"
    orphan.write_text("# orphan\n", encoding="utf-8")

    reconciler = _make_reconciler(meta_file)
    findings = reconciler.find_file_orphans(scan_dir)

    assert len(findings) == 1
    f = findings[0]
    assert f.category == "file_orphan"
    assert f.phase_id is None
    assert f.severity == "info"
    assert "phase_orphan_module.py" in f.detail


# ---------------------------------------------------------------------------
# 7. file_orphan NOT raised when file is referenced
# ---------------------------------------------------------------------------

def test_file_orphan_not_raised_when_file_referenced(tmp_path: Path):
    """A sub_phase_*.py that appears in any phase's files list is not an orphan."""
    meta_file = tmp_path / "phase_metadata.yaml"
    # Declare the file under a relative path — only the basename is matched.
    _write_metadata(meta_file, {
        "20": {
            "title": "Has files",
            "status": "landed",
            "files": ["service/isaac_assist_service/multimodal/sub_phase_20_role_retriever.py"],
        }
    })

    scan_dir = tmp_path / "modules"
    scan_dir.mkdir()
    legit_file = scan_dir / "sub_phase_20_role_retriever.py"
    legit_file.write_text("# legit\n", encoding="utf-8")

    reconciler = _make_reconciler(meta_file)
    findings = reconciler.find_file_orphans(scan_dir)

    assert findings == []


# ---------------------------------------------------------------------------
# 8. reconcile composes report with by_category counts + scanned_at timestamp
# ---------------------------------------------------------------------------

def test_reconcile_composes_report(tmp_path: Path):
    """reconcile() returns a ReconciliationReport with correct summary counts."""
    from service.isaac_assist_service.multimodal.sub_phase_96b_spec_reality_reconciliation import (
        ReconciliationReport,
        SpecRealityFinding,
    )

    meta_file = tmp_path / "phase_metadata.yaml"
    # Set up:
    # - one landed phase with missing file  → status_mismatch (warn)
    # - one phase with a broken blocker     → blocker_chain_broken (error)
    _write_metadata(meta_file, {
        "10": {
            "title": "Missing file phase",
            "status": "landed",
            "files": ["service/nonexistent/module.py"],
        },
        "20": {
            "title": "Broken blocker phase",
            "status": "scaffold",
            "blocked_by": ["999"],
        },
    })

    # scan_dir: add one orphan file
    scan_dir = tmp_path / "modules"
    scan_dir.mkdir()
    (scan_dir / "sub_phase_orphan.py").write_text("# orphan", encoding="utf-8")

    from service.isaac_assist_service.multimodal.sub_phase_96b_spec_reality_reconciliation import (
        SpecRealityReconciler,
    )
    reconciler = SpecRealityReconciler(metadata_path=meta_file)
    report = reconciler.reconcile(scan_dir=scan_dir)

    assert isinstance(report, ReconciliationReport)
    assert report.total_phases == 2
    assert report.total_findings == 3  # 1 mismatch + 1 broken + 1 orphan
    assert report.by_category.get("status_mismatch", 0) == 1
    assert report.by_category.get("blocker_chain_broken", 0) == 1
    assert report.by_category.get("file_orphan", 0) == 1
    # scanned_at is an ISO timestamp
    assert "T" in report.scanned_at
    assert len(report.findings) == 3


# ---------------------------------------------------------------------------
# 9. default_reconcile uses repo-default paths and returns a ReconciliationReport
# ---------------------------------------------------------------------------

def test_default_reconcile_returns_report():
    """default_reconcile() runs against the live repo and returns a valid report."""
    from service.isaac_assist_service.multimodal.sub_phase_96b_spec_reality_reconciliation import (
        ReconciliationReport,
        default_reconcile,
    )

    report = default_reconcile()
    assert isinstance(report, ReconciliationReport)
    assert report.total_phases > 0
    assert isinstance(report.findings, list)
    assert isinstance(report.by_category, dict)
    assert "T" in report.scanned_at


# ---------------------------------------------------------------------------
# 10. SpecRealityFinding dataclass fields are correct
# ---------------------------------------------------------------------------

def test_spec_reality_finding_fields():
    from service.isaac_assist_service.multimodal.sub_phase_96b_spec_reality_reconciliation import (
        SpecRealityFinding,
    )
    f = SpecRealityFinding(
        category="spec_orphan",
        phase_id="42",
        detail="Some detail text",
        severity="info",
    )
    assert f.category == "spec_orphan"
    assert f.phase_id == "42"
    assert f.detail == "Some detail text"
    assert f.severity == "info"


# ---------------------------------------------------------------------------
# 11. Multiple broken blockers in one phase produce multiple findings
# ---------------------------------------------------------------------------

def test_multiple_broken_blockers_per_phase(tmp_path: Path):
    """Each missing blocker ID generates a separate finding."""
    meta_file = tmp_path / "phase_metadata.yaml"
    _write_metadata(meta_file, {
        "5": {
            "title": "Multi-blocked phase",
            "status": "scaffold",
            "blocked_by": ["100", "200", "300"],
        }
    })

    reconciler = _make_reconciler(meta_file)
    findings = reconciler.find_broken_blocker_chains()

    assert len(findings) == 3
    missing_ids = {f.detail.split("blocked_by=")[1].split(" ")[0].strip("'") for f in findings}
    assert "100" in missing_ids
    assert "200" in missing_ids
    assert "300" in missing_ids
