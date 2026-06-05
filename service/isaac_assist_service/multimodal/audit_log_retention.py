"""Phase 91 — Audit log retention enforcement.

Enforces a TTL policy on audit log files:
- Files older than `archive_after_days` are gzip-compressed.
- Files older than `delete_after_days` are deleted (including already-archived ones).
- Per-channel policy registration.

Per specs/IA_FULL_SPEC_2026-05-10.md Phase 91.
"""
from __future__ import annotations

import gzip
import os
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional


PHASE_ID = 91
PHASE_TITLE = "Audit log retention enforcement"
PHASE_STATUS = "landed"


def get_phase_metadata() -> Dict[str, Any]:
    return {
        "phase": PHASE_ID,
        "title": PHASE_TITLE,
        "status": PHASE_STATUS,
        "spec_ref": "specs/IA_FULL_SPEC_2026-05-10.md Phase 91",
    }


@dataclass
class RetentionPolicy:
    """TTL policy for a single log channel.

    Attributes:
        channel: Identifier for this log channel (used to match filenames or
            sub-directories inside the log root).
        archive_after_days: Files whose mtime is older than this many days
            are gzip-compressed.  Must be positive and < delete_after_days.
        delete_after_days: Files (raw or already-archived) whose mtime is
            older than this many days are deleted.  Must be > archive_after_days.
    """

    channel: str
    archive_after_days: int
    delete_after_days: int

    def __post_init__(self) -> None:
        if self.archive_after_days <= 0:
            raise ValueError("archive_after_days must be positive")
        if self.delete_after_days <= self.archive_after_days:
            raise ValueError("delete_after_days must be greater than archive_after_days")


class LogRetentionEnforcer:
    """Scans a log directory and enforces per-channel TTL retention policies.

    Usage::

        enforcer = LogRetentionEnforcer()
        enforcer.register_policy(RetentionPolicy("auth", archive_after_days=7, delete_after_days=30))
        result = enforcer.enforce(Path("/var/log/myapp"), channel="auth")
    """

    def __init__(self) -> None:
        self._policies: Dict[str, RetentionPolicy] = {}

    def register_policy(self, policy: RetentionPolicy) -> None:
        """Register (or replace) the retention policy for *policy.channel*."""
        self._policies[policy.channel] = policy

    def enforce(
        self,
        log_dir: Path,
        channel: str,
        now: Optional[datetime] = None,
    ) -> Dict[str, int]:
        """Enforce the registered policy for *channel* against files in *log_dir*.

        Only plain files directly under *log_dir* are considered (non-recursive).
        Already-archived files (``*.gz``) also participate in the delete sweep.

        Args:
            log_dir: Root directory to scan.
            channel: Channel name whose policy should be applied.
            now: Reference timestamp (defaults to ``datetime.now(UTC)``).  Useful
                for deterministic testing via ``os.utime`` back-dating.

        Returns:
            A dict with keys ``"archived"``, ``"deleted"``, ``"kept"`` whose
            values are the counts of files in each outcome bucket.

        Raises:
            KeyError: If no policy has been registered for *channel*.
            NotADirectoryError: If *log_dir* does not exist or is not a directory.
        """
        if channel not in self._policies:
            raise KeyError(f"No retention policy registered for channel {channel!r}")

        if not log_dir.is_dir():
            raise NotADirectoryError(f"{log_dir} is not a directory")

        policy = self._policies[channel]
        if now is None:
            now = datetime.now(timezone.utc)

        archived = 0
        deleted = 0
        kept = 0

        for entry in sorted(log_dir.iterdir()):
            if not entry.is_file():
                continue

            mtime = datetime.fromtimestamp(entry.stat().st_mtime, tz=timezone.utc)
            age_days = (now - mtime).total_seconds() / 86400.0

            if age_days >= policy.delete_after_days:
                entry.unlink()
                deleted += 1
            elif age_days >= policy.archive_after_days:
                if entry.suffix == ".gz":
                    # Already archived — just count as kept until delete threshold
                    kept += 1
                else:
                    _gzip_file(entry)
                    archived += 1
            else:
                kept += 1

        return {"archived": archived, "deleted": deleted, "kept": kept}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _gzip_file(src: Path) -> Path:
    """Compress *src* in-place to *src*.gz and remove the original.

    Returns the path to the new ``.gz`` file.
    """
    gz_path = src.with_suffix(src.suffix + ".gz")
    with src.open("rb") as f_in, gzip.open(gz_path, "wb") as f_out:
        shutil.copyfileobj(f_in, f_out)
    # Preserve mtime on the archive so subsequent runs see the correct age.
    src_stat = src.stat()
    os.utime(gz_path, (src_stat.st_atime, src_stat.st_mtime))
    src.unlink()
    return gz_path
