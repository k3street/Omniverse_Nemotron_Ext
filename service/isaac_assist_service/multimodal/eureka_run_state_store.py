"""Phase 64 — Eureka run state actually persisted (SQLite-backed store).

Pure-Python SQLite implementation for persisting Eureka reward-search run
state and per-iteration scores.  The real Eureka worker lives in the
opus-runtime, but the persistence layer is local and dependency-free.

Per specs/IA_FULL_SPEC_2026-05-10.md Phase 64.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Generator, List, Literal, Optional, Union


PHASE_ID = 64
PHASE_TITLE = "Eureka run state actually persisted"
PHASE_STATUS = "landed"


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class EurekaRun:
    run_id: str
    task_description: str
    environment_id: str
    started_at: str  # ISO-8601
    status: Literal["running", "completed", "failed", "cancelled"]
    best_score: Optional[float] = None
    best_iteration: Optional[int] = None
    total_iterations: int = 0
    finished_at: Optional[str] = None


@dataclass
class EurekaIteration:
    run_id: str
    iteration_idx: int
    reward_function_text: str
    score: float
    success_rate: float
    n_episodes: int
    errors: List[str] = field(default_factory=list)
    created_at: str = ""  # ISO-8601


# ---------------------------------------------------------------------------
# DDL
# ---------------------------------------------------------------------------

_SCHEMA_DDL = """
CREATE TABLE IF NOT EXISTS eureka_runs (
    run_id           TEXT PRIMARY KEY,
    task_description TEXT NOT NULL,
    environment_id   TEXT NOT NULL,
    started_at       TEXT NOT NULL,
    status           TEXT NOT NULL,
    best_score       REAL,
    best_iteration   INTEGER,
    total_iterations INTEGER NOT NULL DEFAULT 0,
    finished_at      TEXT
);

CREATE TABLE IF NOT EXISTS eureka_iterations (
    run_id                TEXT NOT NULL,
    iteration_idx         INTEGER NOT NULL,
    reward_function_text  TEXT NOT NULL,
    score                 REAL NOT NULL,
    success_rate          REAL NOT NULL,
    n_episodes            INTEGER NOT NULL,
    errors_json           TEXT NOT NULL DEFAULT '[]',
    created_at            TEXT NOT NULL,
    PRIMARY KEY (run_id, iteration_idx)
);
"""


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------

class EurekaRunStateStore:
    """SQLite-backed store for Eureka reward-optimisation run state.

    For file-based databases a new connection is opened per operation.
    For ``:memory:`` a single persistent connection is kept open for the
    lifetime of the store object (each new connection to ``:memory:``
    would otherwise start with an empty database).
    """

    def __init__(self, db_path: Union[Path, str] = ":memory:") -> None:
        self._db_path = str(db_path)
        self._is_memory = self._db_path == ":memory:"
        # For a file-based DB, ensure the parent directory exists.
        if not self._is_memory:
            Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        # Persistent connection for in-memory databases.
        self._mem_cx: Optional[sqlite3.Connection] = None
        if self._is_memory:
            self._mem_cx = sqlite3.connect(":memory:")
            self._mem_cx.row_factory = sqlite3.Row
        self._init_schema()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @contextmanager
    def _conn(self) -> Generator[sqlite3.Connection, None, None]:
        if self._is_memory and self._mem_cx is not None:
            # Yield the persistent connection without closing it.
            try:
                yield self._mem_cx
                self._mem_cx.commit()
            except Exception:
                self._mem_cx.rollback()
                raise
        else:
            cx = sqlite3.connect(self._db_path)
            cx.row_factory = sqlite3.Row
            try:
                yield cx
                cx.commit()
            finally:
                cx.close()

    def _init_schema(self) -> None:
        """Create tables if they do not already exist."""
        with self._conn() as cx:
            cx.executescript(_SCHEMA_DDL)

    # ------------------------------------------------------------------
    # Run lifecycle
    # ------------------------------------------------------------------

    def create_run(self, run: EurekaRun) -> None:
        """Insert a new run row (fails if run_id already exists)."""
        with self._conn() as cx:
            cx.execute(
                """
                INSERT INTO eureka_runs
                    (run_id, task_description, environment_id, started_at,
                     status, best_score, best_iteration, total_iterations, finished_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run.run_id,
                    run.task_description,
                    run.environment_id,
                    run.started_at,
                    run.status,
                    run.best_score,
                    run.best_iteration,
                    run.total_iterations,
                    run.finished_at,
                ),
            )

    def mark_completed(self, run_id: str, finished_at: str) -> None:
        """Set run status to 'completed' and record finish timestamp."""
        with self._conn() as cx:
            cx.execute(
                "UPDATE eureka_runs SET status='completed', finished_at=? WHERE run_id=?",
                (finished_at, run_id),
            )

    def mark_failed(self, run_id: str, finished_at: str) -> None:
        """Set run status to 'failed' and record finish timestamp."""
        with self._conn() as cx:
            cx.execute(
                "UPDATE eureka_runs SET status='failed', finished_at=? WHERE run_id=?",
                (finished_at, run_id),
            )

    # ------------------------------------------------------------------
    # Iteration recording
    # ------------------------------------------------------------------

    def record_iteration(self, it: EurekaIteration) -> None:
        """Insert an iteration row and update run best-score bookkeeping.

        If *it.score* is strictly greater than the current best_score (or
        best_score is NULL), the run's best_score and best_iteration are
        updated as well.  total_iterations is always incremented.
        """
        with self._conn() as cx:
            cx.execute(
                """
                INSERT INTO eureka_iterations
                    (run_id, iteration_idx, reward_function_text, score,
                     success_rate, n_episodes, errors_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    it.run_id,
                    it.iteration_idx,
                    it.reward_function_text,
                    it.score,
                    it.success_rate,
                    it.n_episodes,
                    json.dumps(it.errors),
                    it.created_at,
                ),
            )
            # Conditionally update run bookkeeping in the same transaction.
            row = cx.execute(
                "SELECT best_score FROM eureka_runs WHERE run_id=?",
                (it.run_id,),
            ).fetchone()
            if row is not None:
                current_best = row["best_score"]
                if current_best is None or it.score > current_best:
                    cx.execute(
                        """
                        UPDATE eureka_runs
                        SET best_score=?, best_iteration=?,
                            total_iterations = total_iterations + 1
                        WHERE run_id=?
                        """,
                        (it.score, it.iteration_idx, it.run_id),
                    )
                else:
                    cx.execute(
                        "UPDATE eureka_runs SET total_iterations = total_iterations + 1 WHERE run_id=?",
                        (it.run_id,),
                    )

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def get_run(self, run_id: str) -> Optional[EurekaRun]:
        """Return the run with the given *run_id*, or *None*."""
        with self._conn() as cx:
            row = cx.execute(
                "SELECT * FROM eureka_runs WHERE run_id=?", (run_id,)
            ).fetchone()
        if row is None:
            return None
        return self._row_to_run(row)

    def list_runs(
        self,
        status_filter: Optional[str] = None,
        limit: int = 100,
    ) -> List[EurekaRun]:
        """Return up to *limit* runs, optionally filtered by status."""
        with self._conn() as cx:
            if status_filter is not None:
                rows = cx.execute(
                    "SELECT * FROM eureka_runs WHERE status=? LIMIT ?",
                    (status_filter, limit),
                ).fetchall()
            else:
                rows = cx.execute(
                    "SELECT * FROM eureka_runs LIMIT ?", (limit,)
                ).fetchall()
        return [self._row_to_run(r) for r in rows]

    def get_iterations(self, run_id: str) -> List[EurekaIteration]:
        """Return all iterations for *run_id* ordered by iteration_idx."""
        with self._conn() as cx:
            rows = cx.execute(
                "SELECT * FROM eureka_iterations WHERE run_id=? ORDER BY iteration_idx",
                (run_id,),
            ).fetchall()
        return [self._row_to_iteration(r) for r in rows]

    def best_iteration_for(self, run_id: str) -> Optional[EurekaIteration]:
        """Return the iteration with the highest score for *run_id*."""
        with self._conn() as cx:
            row = cx.execute(
                """
                SELECT * FROM eureka_iterations
                WHERE run_id=?
                ORDER BY score DESC
                LIMIT 1
                """,
                (run_id,),
            ).fetchone()
        if row is None:
            return None
        return self._row_to_iteration(row)

    def count_runs(self) -> int:
        """Return total number of run rows."""
        with self._conn() as cx:
            result = cx.execute("SELECT COUNT(*) FROM eureka_runs").fetchone()
        return result[0]

    def close(self) -> None:
        """Close the store.

        For file-based stores this is a no-op (per-operation connections are
        already closed after each use).  For in-memory stores the persistent
        connection is closed and the data is discarded.
        """
        if self._is_memory and self._mem_cx is not None:
            self._mem_cx.close()
            self._mem_cx = None

    # ------------------------------------------------------------------
    # Private converters
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_run(row: sqlite3.Row) -> EurekaRun:
        return EurekaRun(
            run_id=row["run_id"],
            task_description=row["task_description"],
            environment_id=row["environment_id"],
            started_at=row["started_at"],
            status=row["status"],
            best_score=row["best_score"],
            best_iteration=row["best_iteration"],
            total_iterations=row["total_iterations"],
            finished_at=row["finished_at"],
        )

    @staticmethod
    def _row_to_iteration(row: sqlite3.Row) -> EurekaIteration:
        return EurekaIteration(
            run_id=row["run_id"],
            iteration_idx=row["iteration_idx"],
            reward_function_text=row["reward_function_text"],
            score=row["score"],
            success_rate=row["success_rate"],
            n_episodes=row["n_episodes"],
            errors=json.loads(row["errors_json"]),
            created_at=row["created_at"],
        )


# ---------------------------------------------------------------------------
# Phase metadata
# ---------------------------------------------------------------------------

def get_phase_metadata() -> Dict[str, Any]:
    """Return phase identification and status for this phase.

    Returns:
        Dict[str, Any]: Keys ``phase``, ``title``, ``status``, and ``spec_ref``.
    """
    return {
        "phase": PHASE_ID,
        "title": PHASE_TITLE,
        "status": PHASE_STATUS,
        "spec_ref": "specs/IA_FULL_SPEC_2026-05-10.md Phase 64",
    }
