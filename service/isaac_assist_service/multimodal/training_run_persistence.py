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
    """SQLite-backed store for RL/IL training run records."""

    def __init__(self, db_path: Path) -> None:
        """Initialise the store, creating the database and schema if needed.

        Args:
            db_path (Path): Path to the SQLite database file; parent directories
                are created automatically.
        """
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as cx:
            cx.executescript(SCHEMA_DDL)

    @contextmanager
    def _conn(self):
        """Open a short-lived SQLite connection, commit on exit, close in finally.

        Yields:
            sqlite3.Connection: Auto-committed connection.
        """
        cx = sqlite3.connect(str(self.db_path))
        try:
            yield cx
            cx.commit()
        finally:
            cx.close()

    def upsert(self, run_id: str, task_name: str, algo: Optional[str],
               state: str, metadata: Optional[Dict] = None) -> None:
        """Insert or update a training run record.

        Preserves the original ``launch_time`` if the run already exists.

        Args:
            run_id (str): Unique run identifier.
            task_name (str): Human-readable task name.
            algo (str, optional): Algorithm name, e.g. ``"PPO"``.
            state (str): Current run state, e.g. ``"running"``, ``"completed"``.
            metadata (Dict, optional): Arbitrary extra data. Defaults to ``{}``.
        """
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
        """Return a run record by *run_id*, or ``None`` if not found.

        Args:
            run_id (str): Run identifier.

        Returns:
            Optional[Dict[str, Any]]: Run dict with ``metadata`` decoded from JSON,
                or ``None``.
        """
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
        """Return all runs whose ``state`` matches *state*.

        Args:
            state (str): State filter, e.g. ``"running"`` or ``"completed"``.

        Returns:
            List[Dict[str, Any]]: Matching run dicts with keys ``run_id``,
                ``task_name``, ``state``, and ``launch_time``.
        """
        with self._conn() as cx:
            rows = cx.execute("SELECT run_id, task_name, state, launch_time "
                              "FROM training_runs WHERE state=?", (state,)).fetchall()
            return [dict(zip(["run_id", "task_name", "state", "launch_time"], r)) for r in rows]


def get_phase_metadata() -> Dict[str, Any]:
    """Return phase identification and status for Phase 65.

    Returns:
        Dict[str, Any]: Keys ``phase``, ``title``, ``status``, and ``spec_ref``.
    """
    return {
        "phase": PHASE_ID, "title": PHASE_TITLE, "status": PHASE_STATUS,
        "spec_ref": "specs/IA_FULL_SPEC_2026-05-10.md Phase 65",
    }
