"""Governance audit logger — append-only JSONL audit trail.

Every governance event (patch approved, rejected, executed, rolled back)
is persisted as a JSONL line so post-hoc analysis and compliance queries
can reconstruct exactly what the agent did and when.
"""
import json
import logging
import os
from pathlib import Path
from typing import List, Optional

from service.isaac_assist_service.governance.models import AuditEntry

logger = logging.getLogger(__name__)


class AuditLogger:
    """Append-only writer and reverse-chronological reader for the governance audit log."""

    def __init__(self, log_path: str = "workspace/audit.jsonl"):
        """Initialise the logger, creating the log file and parent directories if absent."""
        self.log_path = Path(log_path)
        self._ensure_log_file()

    def _ensure_log_file(self):
        """Ensures the directory and file exist."""
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            if not self.log_path.exists():
                self.log_path.touch()
        except Exception as e:
            logger.error(f"Failed to create audit log file at {self.log_path}: {e}")

    def log_entry(self, entry: AuditEntry) -> bool:
        """Serialize and append a single audit entry to the JSONL file.

        Args:
            entry (AuditEntry): The governance event to persist.

        Returns:
            bool: True on success, False if the write failed.
        """
        try:
            # We serialize datetime to ISO format
            with open(self.log_path, 'a', encoding='utf-8') as f:
                f.write(entry.model_dump_json() + '\n')
            return True
        except Exception as e:
            logger.error(f"Failed to write audit entry {entry.entry_id}: {e}")
            return False

    def query_logs(self, limit: int = 100, event_type: Optional[str] = None) -> List[AuditEntry]:
        """Return the most recent audit entries, newest first.

        Args:
            limit (int, optional): Maximum number of entries to return. Defaults to 100.
            event_type (str, optional): If given, only return entries whose
                ``event_type`` matches this string exactly.

        Returns:
            list[AuditEntry]: Matching entries, most-recent first.
        """
        entries = []
        try:
            if not self.log_path.exists():
                return entries

            # Read all lines (could be optimized for very large files by reading from end)
            with open(self.log_path, 'r', encoding='utf-8') as f:
                lines = f.readlines()

            for line in reversed(lines):
                if not line.strip():
                    continue

                try:
                    data = json.loads(line)
                    entry = AuditEntry(**data)
                    
                    if event_type and entry.event_type != event_type:
                        continue
                        
                    entries.append(entry)
                    
                    if len(entries) >= limit:
                        break
                except Exception as e:
                    logger.warning(f"Failed to parse audit log line: {e}")

            return entries
        except Exception as e:
            logger.error(f"Failed to query audit logs: {e}")
            return entries
