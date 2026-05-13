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
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as cx:
            cx.executescript(SCHEMA_DDL)

    @contextmanager
    def _conn(self):
        cx = sqlite3.connect(str(self.db_path))
        try:
            yield cx
            cx.commit()
        finally:
            cx.close()

    def save(self, workflow_id: str, checkpoint_id: str, phase_name: str,
             record: Dict[str, Any], created_at: str) -> None:
        with self._conn() as cx:
            cx.execute(
                "INSERT OR REPLACE INTO workflow_checkpoints VALUES (?, ?, ?, ?, ?)",
                (workflow_id, checkpoint_id, phase_name, json.dumps(record), created_at),
            )

    def load(self, workflow_id: str, checkpoint_id: str) -> Optional[Dict[str, Any]]:
        with self._conn() as cx:
            row = cx.execute(
                "SELECT record_json FROM workflow_checkpoints WHERE workflow_id=? AND checkpoint_id=?",
                (workflow_id, checkpoint_id),
            ).fetchone()
            if row is None:
                return None
            return json.loads(row[0])

    def list_for_workflow(self, workflow_id: str) -> List[Dict[str, Any]]:
        with self._conn() as cx:
            rows = cx.execute(
                "SELECT checkpoint_id, phase_name, created_at FROM workflow_checkpoints "
                "WHERE workflow_id=? ORDER BY created_at",
                (workflow_id,),
            ).fetchall()
            return [{"checkpoint_id": r[0], "phase_name": r[1], "created_at": r[2]} for r in rows]
