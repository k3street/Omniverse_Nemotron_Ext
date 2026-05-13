"""Phase 65 — training run persistence (SQLite).

Per specs/IA_FULL_SPEC_2026-05-10.md Phase 65.
"""
from __future__ import annotations
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


PHASE_ID = 65
PHASE_TITLE = "Training run persistence"
PHASE_STATUS = "landed"


SCHEMA_DDL = """
CREATE TABLE IF NOT EXISTS training_runs (
    run_id TEXT PRIMARY KEY,
    task_name TEXT NOT NULL,
    algo TEXT,
    state TEXT NOT NULL,
    launch_time TEXT NOT NULL,
    last_update TEXT NOT NULL,
    metadata_json TEXT
);
"""


class TrainingRunStore:
    def __init__(self, db_path: Path) -> None:
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

    def upsert(self, run_id: str, task_name: str, algo: Optional[str],
               state: str, metadata: Optional[Dict] = None) -> None:
        now = datetime.now(timezone.utc).isoformat()
        meta = json.dumps(metadata or {})
        with self._conn() as cx:
            existing = cx.execute("SELECT launch_time FROM training_runs WHERE run_id=?",
                                  (run_id,)).fetchone()
            launch = existing[0] if existing else now
            cx.execute(
                "INSERT OR REPLACE INTO training_runs VALUES (?, ?, ?, ?, ?, ?, ?)",
                (run_id, task_name, algo, state, launch, now, meta),
            )

    def get(self, run_id: str) -> Optional[Dict[str, Any]]:
        with self._conn() as cx:
            row = cx.execute("SELECT * FROM training_runs WHERE run_id=?",
                             (run_id,)).fetchone()
            if row is None:
                return None
            cols = ["run_id", "task_name", "algo", "state", "launch_time",
                    "last_update", "metadata_json"]
            d = dict(zip(cols, row))
            d["metadata"] = json.loads(d.pop("metadata_json"))
            return d

    def by_state(self, state: str) -> List[Dict[str, Any]]:
        with self._conn() as cx:
            rows = cx.execute("SELECT run_id, task_name, state, launch_time "
                              "FROM training_runs WHERE state=?", (state,)).fetchall()
            return [dict(zip(["run_id", "task_name", "state", "launch_time"], r)) for r in rows]


def get_phase_metadata() -> Dict[str, Any]:
    return {
        "phase": PHASE_ID, "title": PHASE_TITLE, "status": PHASE_STATUS,
        "spec_ref": "specs/IA_FULL_SPEC_2026-05-10.md Phase 65",
    }
