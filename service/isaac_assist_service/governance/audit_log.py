import json
import logging
import os
from pathlib import Path
from typing import List, Optional

from service.isaac_assist_service.governance.models import AuditEntry

logger = logging.getLogger(__name__)

class AuditLogger:
    """Writes and reads audit logs to a JSONL file."""

    def __init__(self, log_path: str = "workspace/audit.jsonl"):
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
        """Appends a new audit entry to the log."""
        try:
            # We serialize datetime to ISO format
            with open(self.log_path, 'a', encoding='utf-8') as f:
                f.write(entry.model_dump_json() + '\n')
            return True
        except Exception as e:
            logger.error(f"Failed to write audit entry {entry.entry_id}: {e}")
            return False

    def query_logs(self, limit: int = 100, event_type: Optional[str] = None) -> List[AuditEntry]:
        """Queries recent logs, optionally filtering by event type."""
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
