"""Phase 92 — Workflow snapshot retention.

Enforces a TTL policy on workflow snapshot directories:
- Snapshots older than `archive_after_days` have their manifest gzip-archived.
- Snapshots older than `delete_after_days` are fully deleted.
- The `keep_minimum` most-recent snapshots are always retained, regardless of age.

Per specs/IA_FULL_SPEC_2026-05-10.md Phase 92.
"""
from __future__ import annotations

import gzip
import json
import os
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


PHASE_ID = 92
PHASE_TITLE = "Workflow snapshot retention"
PHASE_STATUS = "landed"


def get_phase_metadata() -> Dict[str, Any]:
    """Return phase identification and status for Phase 92.

    Returns:
        Dict[str, Any]: Keys ``phase``, ``title``, ``status``, and ``spec_ref``.
    """
    return {
        "phase": PHASE_ID,
        "title": PHASE_TITLE,
        "status": PHASE_STATUS,
        "spec_ref": "specs/IA_FULL_SPEC_2026-05-10.md Phase 92",
    }


@dataclass
class SnapshotRetentionPolicy:
    """TTL policy for workflow snapshot pruning.

    Attributes:
        archive_after_days: Snapshot dirs whose mtime is older than this many
            days have their manifest gzip-archived into ``archives/``.  Must be
            positive and strictly less than ``delete_after_days``.
        delete_after_days: Snapshot dirs (and any existing archive stubs) older
            than this many days are fully deleted.  Must be greater than
            ``archive_after_days``.
        keep_minimum: Always retain the N most-recent snapshot dirs regardless
            of age.  Defaults to 5.  Must be >= 0.
    """

    archive_after_days: int
    delete_after_days: int
    keep_minimum: int = 5

    def __post_init__(self) -> None:
        """Validate policy field constraints.

        Raises:
            ValueError: When ``archive_after_days ≤ 0``, ``delete_after_days ≤ archive_after_days``,
                or ``keep_minimum < 0``.
        """
        if self.archive_after_days <= 0:
            raise ValueError("archive_after_days must be positive")
        if self.delete_after_days <= self.archive_after_days:
            raise ValueError(
                "delete_after_days must be greater than archive_after_days"
            )
        if self.keep_minimum < 0:
            raise ValueError("keep_minimum must be >= 0")


class WorkflowSnapshotPruner:
    """Walks a snapshot directory and enforces a :class:`SnapshotRetentionPolicy`.

    Snapshot layout expected under *snapshot_dir*::

        snapshot_dir/
            2026-05-01T10:00:00/    # or any opaque snapshot_id dir
                manifest.json
                ...
            archives/
                2026-05-01T10:00:00.json.gz   # produced by this class

    Usage::

        pruner = WorkflowSnapshotPruner()
        result = pruner.prune(Path("/data/snapshots"), SnapshotRetentionPolicy(7, 30))
    """

    # Name of the manifest file inside each snapshot sub-directory.
    MANIFEST_FILENAME = "manifest.json"

    def prune(
        self,
        snapshot_dir: Path,
        policy: SnapshotRetentionPolicy,
        now: Optional[datetime] = None,
    ) -> Dict[str, int]:
        """Apply *policy* to *snapshot_dir*.

        Sub-directories of *snapshot_dir* are treated as individual snapshots.
        The special ``archives/`` sub-directory is never treated as a snapshot.

        Args:
            snapshot_dir: Root directory containing snapshot sub-dirs.
            policy: Retention rules to apply.
            now: Reference timestamp (defaults to ``datetime.now(UTC)``).

        Returns:
            A dict with keys:

            ``"archived"``
                Number of snapshots whose manifest was gzip-archived and whose
                source directory was then removed.
            ``"deleted"``
                Number of snapshot directories (or archive stubs) that were
                fully deleted.
            ``"kept"``
                Number of snapshot directories that were left untouched because
                they are recent enough.
            ``"kept_due_to_minimum"``
                Number of snapshots that would have been archived or deleted but
                were retained because they are within the N most-recent
                (``keep_minimum``).

        Raises:
            NotADirectoryError: If *snapshot_dir* does not exist or is not a
                directory.
        """
        if not snapshot_dir.is_dir():
            raise NotADirectoryError(f"{snapshot_dir} is not a directory")

        if now is None:
            now = datetime.now(timezone.utc)

        archives_dir = snapshot_dir / "archives"

        # Collect all snapshot sub-dirs (exclude 'archives' and plain files).
        entries: List[Tuple[float, Path]] = []
        for entry in snapshot_dir.iterdir():
            if not entry.is_dir():
                continue
            if entry.name == "archives":
                continue
            entries.append((entry.stat().st_mtime, entry))

        # Sort newest first so we can identify the keep_minimum most recent.
        entries.sort(key=lambda t: t[0], reverse=True)

        archived = 0
        deleted = 0
        kept = 0
        kept_due_to_minimum = 0

        for rank, (mtime_ts, snap_dir) in enumerate(entries):
            mtime = datetime.fromtimestamp(mtime_ts, tz=timezone.utc)
            age_days = (now - mtime).total_seconds() / 86400.0

            # The N most-recent snapshots are always kept.
            if rank < policy.keep_minimum:
                if age_days >= policy.archive_after_days:
                    # Would have been archived/deleted but kept_due_to_minimum.
                    kept_due_to_minimum += 1
                else:
                    kept += 1
                continue

            if age_days >= policy.delete_after_days:
                shutil.rmtree(snap_dir)
                # Also remove any pre-existing archive stub for this snapshot.
                stub = archives_dir / f"{snap_dir.name}.json.gz"
                if stub.exists():
                    stub.unlink()
                deleted += 1
            elif age_days >= policy.archive_after_days:
                _archive_snapshot(snap_dir, archives_dir, mtime_ts)
                archived += 1
            else:
                kept += 1

        # Sweep the archives/ dir — delete stubs older than delete_after_days
        # that no longer have a corresponding live snapshot.
        if archives_dir.is_dir():
            for stub in archives_dir.iterdir():
                if not stub.is_file():
                    continue
                stub_mtime = datetime.fromtimestamp(
                    stub.stat().st_mtime, tz=timezone.utc
                )
                stub_age = (now - stub_mtime).total_seconds() / 86400.0
                if stub_age >= policy.delete_after_days:
                    stub.unlink()
                    deleted += 1

        return {
            "archived": archived,
            "deleted": deleted,
            "kept": kept,
            "kept_due_to_minimum": kept_due_to_minimum,
        }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _archive_snapshot(snap_dir: Path, archives_dir: Path, mtime_ts: float) -> Path:
    """Compress the snapshot manifest into *archives_dir* and remove *snap_dir*.

    The archive filename is ``{snap_dir.name}.json.gz``.  Its mtime is set to
    *mtime_ts* so subsequent pruning runs can age the archive correctly.

    Returns the path to the created ``.json.gz`` file.
    """
    archives_dir.mkdir(parents=True, exist_ok=True)

    manifest_path = snap_dir / "manifest.json"
    if manifest_path.exists():
        payload = manifest_path.read_bytes()
    else:
        # Synthesise a minimal manifest when the file is absent.
        payload = json.dumps({"snapshot_id": snap_dir.name, "archived": True}).encode()

    gz_path = archives_dir / f"{snap_dir.name}.json.gz"
    with gzip.open(gz_path, "wb") as fh:
        fh.write(payload)

    # Preserve original mtime on the archive for age-based pruning continuity.
    os.utime(gz_path, (mtime_ts, mtime_ts))

    shutil.rmtree(snap_dir)
    return gz_path
