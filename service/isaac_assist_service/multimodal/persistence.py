"""
SQLite persistence layer with revision tracking for LayoutSpec.

Compare-and-swap protocol per spec §13.3: every write carries
`parent_revision`; mismatch → 409 Conflict; client merges. Schema migration
forward-only per §13.2 / §18.

Design choices:
- SQLite (stdlib) over JSON-per-session — provides revision column for CAS,
  field-granular updates without whole-file rewrites, atomic transactions,
  and concurrent reads via WAL mode. JSON-per-session was the original spec
  pattern and was classified bet-the-farm by the Opus reversibility audit.
- WAL mode → concurrent reads + serialized writes. Anton's project memory
  documents the ChromaDB segfault on parallel writes; SQLite avoids this
  failure mode entirely at the OS level.
- Per-session asyncio.Lock around read-modify-write ensures CAS atomicity
  even under FastAPI's request-handler concurrency.
- Database lives at workspace/multimodal/state.db — runtime state per
  project's gitignore convention.
"""
from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from .types import LayoutSpec

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

SCHEMA_VERSION = 1

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS layout_specs (
    session_id TEXT NOT NULL,
    revision   INTEGER NOT NULL,
    spec_json  TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (session_id, revision)
);

CREATE INDEX IF NOT EXISTS idx_layout_specs_session_latest
    ON layout_specs(session_id, revision DESC);

CREATE TABLE IF NOT EXISTS bindings (
    session_id TEXT NOT NULL,
    revision   INTEGER NOT NULL,
    role_name  TEXT NOT NULL,
    object_id  TEXT NOT NULL,
    source     TEXT NOT NULL,
    confidence REAL NOT NULL,
    timestamp  TEXT NOT NULL,
    PRIMARY KEY (session_id, revision, role_name)
);

CREATE TABLE IF NOT EXISTS build_log (
    build_id    TEXT PRIMARY KEY,
    session_id  TEXT NOT NULL,
    revision    INTEGER NOT NULL,
    started_at  TEXT NOT NULL DEFAULT (datetime('now')),
    finished_at TEXT,
    status      TEXT NOT NULL,
    progress    TEXT
);

CREATE INDEX IF NOT EXISTS idx_build_log_session
    ON build_log(session_id, started_at DESC);

CREATE TABLE IF NOT EXISTS events (
    event_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    timestamp  TEXT NOT NULL DEFAULT (datetime('now')),
    event_type TEXT NOT NULL,
    payload    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_events_session_time
    ON events(session_id, timestamp DESC);

CREATE INDEX IF NOT EXISTS idx_events_type
    ON events(event_type, timestamp DESC);
"""


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class RevisionConflictError(Exception):
    """Raised when a CAS write fails because parent_revision != current.

    Carries the current state so the caller can present a three-way merge UI
    without an extra round-trip per spec §14.4.
    """
    def __init__(self, session_id: str, expected: int, actual: int,
                 current_spec: Optional[LayoutSpec] = None):
        self.session_id = session_id
        self.expected = expected
        self.actual = actual
        self.current_spec = current_spec
        super().__init__(
            f"revision conflict for session {session_id!r}: "
            f"expected parent_revision={expected}, current is {actual}"
        )


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------

DEFAULT_DB_PATH = (
    Path(__file__).resolve().parent.parent.parent.parent
    / "workspace"
    / "multimodal"
    / "state.db"
)


class MultimodalStore:
    """SQLite-backed store for LayoutSpec, bindings, build log, events.

    Thread-safe via per-connection access (sqlite3 connections are not safe
    to share across threads). Async-safe via per-session asyncio.Lock.

    Connection management: one connection per OS thread, opened lazily.
    For FastAPI use, this means one connection per worker thread, which
    matches sqlite3's threading model cleanly.
    """

    def __init__(self, db_path: Optional[Path] = None):
        self._db_path = db_path or DEFAULT_DB_PATH
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        # Per-thread connection storage
        self._local = threading.local()
        # Per-session locks for CAS atomicity under async concurrency
        self._session_locks: Dict[str, asyncio.Lock] = {}
        self._locks_mutex = threading.Lock()
        # Bootstrap schema (uses the current thread's connection)
        self._init_schema()

    # ------------------------------------------------------------------ #
    # Connection management
    # ------------------------------------------------------------------ #

    def _connection(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(
                self._db_path,
                isolation_level=None,  # autocommit; we manage transactions
                check_same_thread=True,
            )
            conn.row_factory = sqlite3.Row
            # WAL mode for concurrent reads + serialized writes
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA foreign_keys=ON;")
            conn.execute("PRAGMA synchronous=NORMAL;")
            self._local.conn = conn
        return conn

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        """Context manager for an immediate transaction with rollback-on-error.

        SQLite IMMEDIATE locks the database for write at transaction start,
        which is what we want for CAS read-modify-write sequences.
        """
        conn = self._connection()
        conn.execute("BEGIN IMMEDIATE;")
        try:
            yield conn
            conn.execute("COMMIT;")
        except Exception:
            conn.execute("ROLLBACK;")
            raise

    def close(self) -> None:
        """Close the connection for the current thread. Other threads' connections
        are unaffected; they close on thread exit via Python finalization."""
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None

    # ------------------------------------------------------------------ #
    # Schema bootstrap + migrations
    # ------------------------------------------------------------------ #

    def _init_schema(self) -> None:
        conn = self._connection()
        conn.executescript(_SCHEMA_SQL)
        # Record schema version (idempotent)
        conn.execute(
            "INSERT OR IGNORE INTO schema_meta(key, value) VALUES (?, ?)",
            ("schema_version", str(SCHEMA_VERSION)),
        )

    def schema_version(self) -> int:
        row = self._connection().execute(
            "SELECT value FROM schema_meta WHERE key='schema_version'"
        ).fetchone()
        return int(row["value"]) if row else 0

    # ------------------------------------------------------------------ #
    # Session-level lock (for async CAS)
    # ------------------------------------------------------------------ #

    def _session_lock(self, session_id: str) -> asyncio.Lock:
        with self._locks_mutex:
            lock = self._session_locks.get(session_id)
            if lock is None:
                lock = asyncio.Lock()
                self._session_locks[session_id] = lock
            return lock

    # ------------------------------------------------------------------ #
    # LayoutSpec CRUD
    # ------------------------------------------------------------------ #

    def get_latest(self, session_id: str) -> Optional[LayoutSpec]:
        """Return the most recent LayoutSpec for the session, or None if no
        spec has been persisted."""
        row = self._connection().execute(
            "SELECT spec_json FROM layout_specs "
            "WHERE session_id=? ORDER BY revision DESC LIMIT 1",
            (session_id,),
        ).fetchone()
        if row is None:
            return None
        return LayoutSpec.model_validate(json.loads(row["spec_json"]))

    def get_revision(self, session_id: str) -> int:
        """Return the latest revision number; 0 if no spec exists yet."""
        row = self._connection().execute(
            "SELECT MAX(revision) AS r FROM layout_specs WHERE session_id=?",
            (session_id,),
        ).fetchone()
        return int(row["r"]) if row and row["r"] is not None else 0

    def get_at_revision(self, session_id: str, revision: int) -> Optional[LayoutSpec]:
        """Fetch a specific revision. Useful for diff/replay/undo."""
        row = self._connection().execute(
            "SELECT spec_json FROM layout_specs "
            "WHERE session_id=? AND revision=?",
            (session_id, revision),
        ).fetchone()
        if row is None:
            return None
        return LayoutSpec.model_validate(json.loads(row["spec_json"]))

    async def save_with_cas(
        self,
        session_id: str,
        new_spec: LayoutSpec,
        parent_revision: int,
    ) -> LayoutSpec:
        """Compare-and-swap save.

        Spec §13.3: write succeeds iff `parent_revision` matches the current
        latest revision. Mismatch → RevisionConflictError carrying the current
        spec for client-side three-way merge.

        On success, the saved spec carries a fresh `revision = parent_revision + 1`.
        Returns the persisted spec (with the fresh revision applied).
        """
        async with self._session_lock(session_id):
            # SQLite transactions live on the connection thread; we hop to a
            # thread executor so we don't block the asyncio loop on disk IO.
            return await asyncio.get_running_loop().run_in_executor(
                None,
                self._save_with_cas_sync,
                session_id, new_spec, parent_revision,
            )

    def _save_with_cas_sync(
        self,
        session_id: str,
        new_spec: LayoutSpec,
        parent_revision: int,
    ) -> LayoutSpec:
        with self._transaction() as conn:
            row = conn.execute(
                "SELECT MAX(revision) AS r FROM layout_specs WHERE session_id=?",
                (session_id,),
            ).fetchone()
            current = int(row["r"]) if row and row["r"] is not None else 0

            if current != parent_revision:
                # Read current spec for the conflict error payload
                current_spec = None
                if current > 0:
                    cur_row = conn.execute(
                        "SELECT spec_json FROM layout_specs "
                        "WHERE session_id=? AND revision=?",
                        (session_id, current),
                    ).fetchone()
                    if cur_row:
                        current_spec = LayoutSpec.model_validate(
                            json.loads(cur_row["spec_json"])
                        )
                raise RevisionConflictError(
                    session_id=session_id,
                    expected=parent_revision,
                    actual=current,
                    current_spec=current_spec,
                )

            new_revision = parent_revision + 1
            persisted = new_spec.model_copy(update={"revision": new_revision})
            conn.execute(
                "INSERT INTO layout_specs(session_id, revision, spec_json) "
                "VALUES (?, ?, ?)",
                (
                    session_id,
                    new_revision,
                    persisted.model_dump_json(),
                ),
            )

            # Persist bindings if present (denormalized for queryability)
            if persisted.bindings:
                conn.executemany(
                    "INSERT INTO bindings(session_id, revision, role_name, "
                    "object_id, source, confidence, timestamp) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    [
                        (
                            session_id,
                            new_revision,
                            role,
                            b.object_id,
                            b.source,
                            b.confidence,
                            b.timestamp.isoformat(),
                        )
                        for role, b in persisted.bindings.items()
                    ],
                )
            return persisted

    def delete_session(self, session_id: str) -> int:
        """Delete all data for a session. Returns the number of layout_specs
        rows removed (cascading to bindings, build_log, events via session_id)."""
        with self._transaction() as conn:
            cur = conn.execute(
                "DELETE FROM layout_specs WHERE session_id=?",
                (session_id,),
            )
            removed = cur.rowcount
            conn.execute("DELETE FROM bindings WHERE session_id=?", (session_id,))
            conn.execute("DELETE FROM build_log WHERE session_id=?", (session_id,))
            conn.execute("DELETE FROM events WHERE session_id=?", (session_id,))
            return removed

    # ------------------------------------------------------------------ #
    # Build log
    # ------------------------------------------------------------------ #

    def start_build(self, build_id: str, session_id: str, revision: int) -> None:
        with self._transaction() as conn:
            conn.execute(
                "INSERT INTO build_log(build_id, session_id, revision, status, progress) "
                "VALUES (?, ?, ?, 'running', '[]')",
                (build_id, session_id, revision),
            )

    def append_build_progress(
        self,
        build_id: str,
        tool: str,
        status: str,
        args_summary: str = "",
        error: Optional[str] = None,
    ) -> None:
        """Append one tool-call entry to a build's progress JSON.

        Read-modify-write under transaction; safe under concurrent appends
        because of the IMMEDIATE transaction lock.
        """
        with self._transaction() as conn:
            row = conn.execute(
                "SELECT progress FROM build_log WHERE build_id=?",
                (build_id,),
            ).fetchone()
            if row is None:
                raise ValueError(f"unknown build_id {build_id!r}")
            progress = json.loads(row["progress"] or "[]")
            entry: Dict[str, Any] = {
                "tool": tool,
                "args_summary": args_summary,
                "status": status,
                "ts": datetime.utcnow().isoformat() + "Z",
            }
            if error is not None:
                entry["error"] = error
            progress.append(entry)
            conn.execute(
                "UPDATE build_log SET progress=? WHERE build_id=?",
                (json.dumps(progress), build_id),
            )

    def finish_build(self, build_id: str, status: str) -> None:
        with self._transaction() as conn:
            conn.execute(
                "UPDATE build_log SET status=?, finished_at=datetime('now') "
                "WHERE build_id=?",
                (status, build_id),
            )

    def get_build(self, build_id: str) -> Optional[Dict[str, Any]]:
        row = self._connection().execute(
            "SELECT * FROM build_log WHERE build_id=?",
            (build_id,),
        ).fetchone()
        if row is None:
            return None
        d = dict(row)
        d["progress"] = json.loads(d["progress"] or "[]")
        return d

    def latest_build(self, session_id: str) -> Optional[Dict[str, Any]]:
        row = self._connection().execute(
            "SELECT * FROM build_log WHERE session_id=? "
            "ORDER BY started_at DESC LIMIT 1",
            (session_id,),
        ).fetchone()
        if row is None:
            return None
        d = dict(row)
        d["progress"] = json.loads(d["progress"] or "[]")
        return d

    # ------------------------------------------------------------------ #
    # Events log (telemetry per spec §17)
    # ------------------------------------------------------------------ #

    def append_event(
        self,
        session_id: str,
        event_type: str,
        payload: Dict[str, Any],
    ) -> int:
        """Append one telemetry event. Returns the auto-incremented event_id."""
        with self._transaction() as conn:
            cur = conn.execute(
                "INSERT INTO events(session_id, event_type, payload) "
                "VALUES (?, ?, ?)",
                (session_id, event_type, json.dumps(payload)),
            )
            return int(cur.lastrowid)

    def list_events(
        self,
        session_id: Optional[str] = None,
        event_type: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """List events, optionally filtered by session/type. Most recent first."""
        sql = "SELECT * FROM events WHERE 1=1"
        params: List[Any] = []
        if session_id is not None:
            sql += " AND session_id=?"
            params.append(session_id)
        if event_type is not None:
            sql += " AND event_type=?"
            params.append(event_type)
        sql += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)
        rows = self._connection().execute(sql, params).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["payload"] = json.loads(d["payload"])
            result.append(d)
        return result
