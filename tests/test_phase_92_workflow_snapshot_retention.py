"""Phase 92 contract tests — workflow snapshot retention pruner."""
from __future__ import annotations

import gzip
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

pytestmark = pytest.mark.l0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_snapshot(
    snapshot_dir: Path,
    name: str,
    age_days: float,
    manifest: dict | None = None,
) -> Path:
    """Create a snapshot sub-directory with a manifest.json, backdated by *age_days*."""
    snap = snapshot_dir / name
    snap.mkdir(parents=True, exist_ok=True)

    if manifest is None:
        manifest = {"snapshot_id": name, "status": "complete"}
    (snap / "manifest.json").write_text(json.dumps(manifest))

    now_ts = time.time()
    past_ts = now_ts - age_days * 86400
    os.utime(snap, (past_ts, past_ts))
    return snap


def _ref_now() -> datetime:
    """Stable reference 'now' for all tests."""
    return datetime(2026, 5, 13, 12, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Test 1 — metadata contract
# ---------------------------------------------------------------------------

def test_phase_92_metadata():
    from service.isaac_assist_service.multimodal.workflow_snapshot_retention import (
        get_phase_metadata,
    )

    md = get_phase_metadata()
    assert md["phase"] == 92
    assert md["status"] == "landed"
    assert "title" in md
    assert "spec_ref" in md


# ---------------------------------------------------------------------------
# Test 2 — SnapshotRetentionPolicy validation
# ---------------------------------------------------------------------------

def test_snapshot_retention_policy_validation():
    from service.isaac_assist_service.multimodal.workflow_snapshot_retention import (
        SnapshotRetentionPolicy,
    )

    p = SnapshotRetentionPolicy(archive_after_days=7, delete_after_days=30)
    assert p.archive_after_days == 7
    assert p.delete_after_days == 30
    assert p.keep_minimum == 5  # default

    p2 = SnapshotRetentionPolicy(archive_after_days=3, delete_after_days=14, keep_minimum=2)
    assert p2.keep_minimum == 2

    with pytest.raises(ValueError, match="archive_after_days must be positive"):
        SnapshotRetentionPolicy(archive_after_days=0, delete_after_days=10)

    with pytest.raises(ValueError, match="delete_after_days must be greater"):
        SnapshotRetentionPolicy(archive_after_days=30, delete_after_days=30)

    with pytest.raises(ValueError, match="keep_minimum must be"):
        SnapshotRetentionPolicy(archive_after_days=7, delete_after_days=30, keep_minimum=-1)


# ---------------------------------------------------------------------------
# Test 3 — mixed-age 10 snapshots → expected archive / delete / kept counts
# ---------------------------------------------------------------------------

def test_prune_mixed_ages(tmp_path: Path):
    """10 snapshots with varied ages; keep_minimum=0 to test raw age logic."""
    from service.isaac_assist_service.multimodal.workflow_snapshot_retention import (
        SnapshotRetentionPolicy,
        WorkflowSnapshotPruner,
    )

    # policy: archive>=7d, delete>=30d, keep_minimum=0 (pure age-based)
    policy = SnapshotRetentionPolicy(
        archive_after_days=7, delete_after_days=30, keep_minimum=0
    )

    # kept (age < 7d): 2 snapshots
    kept_ages = [1, 3]
    # archived (7d <= age < 30d): 3 snapshots
    archive_ages = [8, 14, 20]
    # deleted (age >= 30d): 5 snapshots
    delete_ages = [30, 35, 45, 60, 90]

    for i, age in enumerate(kept_ages):
        _make_snapshot(tmp_path, f"keep_{i}", age)
    for i, age in enumerate(archive_ages):
        _make_snapshot(tmp_path, f"archive_{i}", age)
    for i, age in enumerate(delete_ages):
        _make_snapshot(tmp_path, f"delete_{i}", age)

    pruner = WorkflowSnapshotPruner()
    result = pruner.prune(tmp_path, policy, now=_ref_now())

    assert result["kept"] == len(kept_ages), f"kept mismatch: {result}"
    assert result["archived"] == len(archive_ages), f"archived mismatch: {result}"
    assert result["deleted"] == len(delete_ages), f"deleted mismatch: {result}"
    assert result["kept_due_to_minimum"] == 0

    # Archived manifest stubs must exist in archives/
    archives_dir = tmp_path / "archives"
    gz_stubs = list(archives_dir.glob("*.json.gz"))
    assert len(gz_stubs) == len(archive_ages)

    # Original snapshot dirs for archived/deleted must be gone
    remaining_dirs = [
        d for d in tmp_path.iterdir()
        if d.is_dir() and d.name != "archives"
    ]
    assert len(remaining_dirs) == len(kept_ages)


# ---------------------------------------------------------------------------
# Test 4 — keep_minimum prevents over-deletion
# ---------------------------------------------------------------------------

def test_keep_minimum_prevents_deletion(tmp_path: Path):
    """keep_minimum=3 retains the 3 newest even when they exceed archive threshold."""
    from service.isaac_assist_service.multimodal.workflow_snapshot_retention import (
        SnapshotRetentionPolicy,
        WorkflowSnapshotPruner,
    )

    policy = SnapshotRetentionPolicy(
        archive_after_days=7, delete_after_days=30, keep_minimum=3
    )

    # All 5 snapshots are old enough to archive; 2 are old enough to delete.
    ages = [10, 15, 20, 35, 60]  # newest first after sort
    for i, age in enumerate(ages):
        _make_snapshot(tmp_path, f"snap_{i:02d}", age)

    pruner = WorkflowSnapshotPruner()
    result = pruner.prune(tmp_path, policy, now=_ref_now())

    # The 3 most-recent (ages 10, 15, 20) are kept_due_to_minimum.
    # The remaining 2 (ages 35, 60) are old enough to delete.
    assert result["kept_due_to_minimum"] == 3
    assert result["deleted"] == 2
    assert result["archived"] == 0
    assert result["kept"] == 0

    # The 3 protected dirs must still exist
    remaining_dirs = [
        d for d in tmp_path.iterdir()
        if d.is_dir() and d.name != "archives"
    ]
    assert len(remaining_dirs) == 3


# ---------------------------------------------------------------------------
# Test 5 — archived manifest is gzip-readable and contains original content
# ---------------------------------------------------------------------------

def test_archived_snapshot_is_gzip_readable(tmp_path: Path):
    from service.isaac_assist_service.multimodal.workflow_snapshot_retention import (
        SnapshotRetentionPolicy,
        WorkflowSnapshotPruner,
    )

    policy = SnapshotRetentionPolicy(
        archive_after_days=7, delete_after_days=30, keep_minimum=0
    )

    payload = {"snapshot_id": "snap_old", "robot": "Franka", "status": "complete"}
    _make_snapshot(tmp_path, "snap_old", age_days=10, manifest=payload)

    pruner = WorkflowSnapshotPruner()
    result = pruner.prune(tmp_path, policy, now=_ref_now())

    assert result["archived"] == 1

    gz_path = tmp_path / "archives" / "snap_old.json.gz"
    assert gz_path.exists(), "Archive stub not created"

    with gzip.open(gz_path, "rb") as fh:
        recovered = json.loads(fh.read())

    assert recovered == payload


# ---------------------------------------------------------------------------
# Test 6 — empty directory is handled gracefully
# ---------------------------------------------------------------------------

def test_empty_snapshot_dir(tmp_path: Path):
    from service.isaac_assist_service.multimodal.workflow_snapshot_retention import (
        SnapshotRetentionPolicy,
        WorkflowSnapshotPruner,
    )

    policy = SnapshotRetentionPolicy(
        archive_after_days=7, delete_after_days=30, keep_minimum=5
    )
    pruner = WorkflowSnapshotPruner()
    result = pruner.prune(tmp_path, policy, now=_ref_now())

    assert result == {"archived": 0, "deleted": 0, "kept": 0, "kept_due_to_minimum": 0}


# ---------------------------------------------------------------------------
# Test 7 — non-existent directory raises NotADirectoryError
# ---------------------------------------------------------------------------

def test_nonexistent_dir_raises(tmp_path: Path):
    from service.isaac_assist_service.multimodal.workflow_snapshot_retention import (
        SnapshotRetentionPolicy,
        WorkflowSnapshotPruner,
    )

    policy = SnapshotRetentionPolicy(archive_after_days=7, delete_after_days=30)
    pruner = WorkflowSnapshotPruner()

    with pytest.raises(NotADirectoryError):
        pruner.prune(tmp_path / "does_not_exist", policy, now=_ref_now())


# ---------------------------------------------------------------------------
# Test 8 — snapshot without manifest.json is archived with synthetic manifest
# ---------------------------------------------------------------------------

def test_snapshot_without_manifest_archived(tmp_path: Path):
    from service.isaac_assist_service.multimodal.workflow_snapshot_retention import (
        SnapshotRetentionPolicy,
        WorkflowSnapshotPruner,
    )

    policy = SnapshotRetentionPolicy(
        archive_after_days=7, delete_after_days=30, keep_minimum=0
    )

    # Create a snapshot dir with no manifest.json
    snap = tmp_path / "bare_snap"
    snap.mkdir()
    (snap / "data.bin").write_bytes(b"\x00" * 16)
    past_ts = time.time() - 10 * 86400
    os.utime(snap, (past_ts, past_ts))

    pruner = WorkflowSnapshotPruner()
    result = pruner.prune(tmp_path, policy, now=_ref_now())

    assert result["archived"] == 1

    gz_path = tmp_path / "archives" / "bare_snap.json.gz"
    assert gz_path.exists()
    with gzip.open(gz_path, "rb") as fh:
        content = json.loads(fh.read())
    assert content["snapshot_id"] == "bare_snap"
