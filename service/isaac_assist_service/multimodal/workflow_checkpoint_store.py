"""Phase 39 — SQLite checkpoint store for workflows.

Persistence for workflow lifecycle. Each checkpoint stores the full
WorkflowRecord at approval time so revisions can rollback to known-good
states.

Per specs/IA_FULL_SPEC_2026-05-10.md Phase 39.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional


SCHEMA_DDL = """
CREATE TABLE IF NOT EXISTS workflow_checkpoints (
    workflow_id TEXT NOT NULL,
    checkpoint_id TEXT NOT NULL,
    phase_name TEXT NOT NULL,
    record_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (workflow_id, checkpoint_id)
);
"""


class CheckpointStore:
    """SQLite-backed store for workflow lifecycle checkpoints."""

    def __init__(self, db_path: Path):
        """Initialise the store, creating the DB and schema if they do not exist.

        Args:
            db_path (Path): Path to the SQLite file; parent directories are created
                automatically.
        """
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as cx:
            cx.executescript(SCHEMA_DDL)

    @contextmanager
    def _conn(self):
        """Open a short-lived SQLite connection, commit on exit, close in finally."""
        cx = sqlite3.connect(str(self.db_path))
        try:
            yield cx
            cx.commit()
        finally:
            cx.close()

    def save(self, workflow_id: str, checkpoint_id: str, phase_name: str,
             record: Dict[str, Any], created_at: str) -> None:
        """Persist a workflow checkpoint, replacing any existing row with the same keys.

        Args:
            workflow_id (str): Parent workflow identifier.
            checkpoint_id (str): Unique checkpoint identifier within the workflow.
            phase_name (str): Phase name at the time of the checkpoint.
            record (Dict): Full workflow record dict, serialised as JSON.
            created_at (str): ISO-8601 timestamp string.
        """
        with self._conn() as cx:
            cx.execute(
                "INSERT OR REPLACE INTO workflow_checkpoints VALUES (?, ?, ?, ?, ?)",
                (workflow_id, checkpoint_id, phase_name, json.dumps(record), created_at),
            )

    def load(self, workflow_id: str, checkpoint_id: str) -> Optional[Dict[str, Any]]:
        """Return the deserialized workflow record for the given checkpoint, or ``None``.

        Args:
            workflow_id (str): Parent workflow identifier.
            checkpoint_id (str): Checkpoint identifier.

        Returns:
            Optional[Dict]: Parsed workflow record, or ``None`` if not found.
        """
        with self._conn() as cx:
            row = cx.execute(
                "SELECT record_json FROM workflow_checkpoints WHERE workflow_id=? AND checkpoint_id=?",
                (workflow_id, checkpoint_id),
            ).fetchone()
            if row is None:
                return None
            return json.loads(row[0])

    def list_for_workflow(self, workflow_id: str) -> List[Dict[str, Any]]:
        """Return checkpoint summaries for *workflow_id*, ordered by creation time.

        Args:
            workflow_id (str): Workflow identifier.

        Returns:
            List[Dict]: Each dict has keys ``checkpoint_id``, ``phase_name``, ``created_at``.
        """
        with self._conn() as cx:
            rows = cx.execute(
                "SELECT checkpoint_id, phase_name, created_at FROM workflow_checkpoints "
                "WHERE workflow_id=? ORDER BY created_at",
                (workflow_id,),
            ).fetchall()
            return [{"checkpoint_id": r[0], "phase_name": r[1], "created_at": r[2]} for r in rows]
