"""Phase 91 contract tests — audit log TTL retention enforcer."""
from __future__ import annotations

import gzip
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

pytestmark = pytest.mark.l0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ref_now() -> datetime:
    """Stable reference 'now' for all tests."""
    return datetime(2026, 5, 13, 12, 0, 0, tzinfo=timezone.utc)


def _make_file(directory: Path, name: str, age_days: float, content: bytes = b"log data\n") -> Path:
    """Create a file in *directory* and backdate its mtime by *age_days*
    days, anchored to ``_ref_now()`` so the enforcer's age math matches
    the test's expectations regardless of wall-clock time."""
    p = directory / name
    p.write_bytes(content)
    past_ts = _ref_now().timestamp() - age_days * 86400
    os.utime(p, (past_ts, past_ts))
    return p


# ---------------------------------------------------------------------------
# Test 1 — metadata contract
# ---------------------------------------------------------------------------

def test_phase_91_metadata():
    from service.isaac_assist_service.multimodal.audit_log_retention import get_phase_metadata
    md = get_phase_metadata()
    assert md["phase"] == 91
    assert md["status"] == "landed"
    assert "title" in md


# ---------------------------------------------------------------------------
# Test 2 — RetentionPolicy validation
# ---------------------------------------------------------------------------

def test_retention_policy_validation():
    from service.isaac_assist_service.multimodal.audit_log_retention import RetentionPolicy

    p = RetentionPolicy(channel="auth", archive_after_days=7, delete_after_days=30)
    assert p.channel == "auth"
    assert p.archive_after_days == 7
    assert p.delete_after_days == 30

    with pytest.raises(ValueError):
        RetentionPolicy(channel="bad", archive_after_days=0, delete_after_days=10)

    with pytest.raises(ValueError):
        RetentionPolicy(channel="bad", archive_after_days=30, delete_after_days=30)


# ---------------------------------------------------------------------------
# Test 3 — mixed-age enforcement: archive / delete / keep counts
# ---------------------------------------------------------------------------

def test_enforce_mixed_ages(tmp_path: Path):
    """10 files with a mix of ages; policy archive_after=7d delete_after=30d."""
    from service.isaac_assist_service.multimodal.audit_log_retention import (
        LogRetentionEnforcer,
        RetentionPolicy,
    )

    # Ages in days:  2 kept, 3 archived (7-29d), 5 deleted (>=30d)
    # Kept:   1d, 3d
    # Archive: 8d, 14d, 20d
    # Delete: 30d, 35d, 45d, 60d, 90d
    kept_ages   = [1, 3]
    archive_ages = [8, 14, 20]
    delete_ages  = [30, 35, 45, 60, 90]

    for i, age in enumerate(kept_ages):
        _make_file(tmp_path, f"keep_{i}.log", age)
    for i, age in enumerate(archive_ages):
        _make_file(tmp_path, f"archive_{i}.log", age)
    for i, age in enumerate(delete_ages):
        _make_file(tmp_path, f"delete_{i}.log", age)

    enforcer = LogRetentionEnforcer()
    enforcer.register_policy(
        RetentionPolicy(channel="audit", archive_after_days=7, delete_after_days=30)
    )

    result = enforcer.enforce(tmp_path, channel="audit", now=_ref_now())

    assert result["kept"] == len(kept_ages)
    assert result["archived"] == len(archive_ages)
    assert result["deleted"] == len(delete_ages)

    # Archived files must exist as .gz
    remaining = list(tmp_path.iterdir())
    gz_files = [f for f in remaining if f.suffix == ".gz"]
    assert len(gz_files) == len(archive_ages)

    # Original plain files for archive candidates must be gone
    plain_log_files = [f for f in remaining if f.suffix == ".log"]
    assert len(plain_log_files) == len(kept_ages)


# ---------------------------------------------------------------------------
# Test 4 — gzip archive is valid and readable
# ---------------------------------------------------------------------------

def test_archived_file_is_valid_gzip(tmp_path: Path):
    from service.isaac_assist_service.multimodal.audit_log_retention import (
        LogRetentionEnforcer,
        RetentionPolicy,
    )

    payload = b"important audit entry\n" * 50
    _make_file(tmp_path, "session.log", age_days=10, content=payload)

    enforcer = LogRetentionEnforcer()
    enforcer.register_policy(
        RetentionPolicy(channel="audit", archive_after_days=7, delete_after_days=30)
    )
    enforcer.enforce(tmp_path, channel="audit", now=_ref_now())

    gz_path = tmp_path / "session.log.gz"
    assert gz_path.exists()
    with gzip.open(gz_path, "rb") as fh:
        recovered = fh.read()
    assert recovered == payload


# ---------------------------------------------------------------------------
# Test 5 — unknown channel raises KeyError
# ---------------------------------------------------------------------------

def test_unknown_channel_raises(tmp_path: Path):
    from service.isaac_assist_service.multimodal.audit_log_retention import LogRetentionEnforcer

    enforcer = LogRetentionEnforcer()
    with pytest.raises(KeyError, match="no_such_channel"):
        enforcer.enforce(tmp_path, channel="no_such_channel", now=_ref_now())


# ---------------------------------------------------------------------------
# Test 6 — already-archived .gz files past delete threshold are deleted
# ---------------------------------------------------------------------------

def test_already_archived_gz_deleted_at_delete_threshold(tmp_path: Path):
    from service.isaac_assist_service.multimodal.audit_log_retention import (
        LogRetentionEnforcer,
        RetentionPolicy,
    )

    # Create a .gz file that is older than delete_after_days
    gz_path = tmp_path / "old_archive.log.gz"
    with gzip.open(gz_path, "wb") as fh:
        fh.write(b"old data\n")
    past_ts = time.time() - 45 * 86400  # 45 days old
    os.utime(gz_path, (past_ts, past_ts))

    # A fresh file that should be kept
    _make_file(tmp_path, "recent.log", age_days=1)

    enforcer = LogRetentionEnforcer()
    enforcer.register_policy(
        RetentionPolicy(channel="audit", archive_after_days=7, delete_after_days=30)
    )
    result = enforcer.enforce(tmp_path, channel="audit", now=_ref_now())

    assert result["deleted"] == 1
    assert result["kept"] == 1
    assert not gz_path.exists()
