"""Phase 84 — Per-session QA logging expansion.

Each QA session maintains an ndjson log file on disk and an in-memory ring
buffer (capped at ``max_events`` entries).  The ring buffer allows fast
in-process queries (``get_recent``) without re-parsing the file.

Row format (one JSON object per line)::

    {"timestamp": "<iso8601>", "session_id": "<str>", "event_type": "<str>", ...payload}

Per specs/IA_FULL_SPEC_2026-05-10.md Phase 84.
"""
from __future__ import annotations

import json
import os
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Deque, Dict, List


PHASE_ID = 84
PHASE_TITLE = "Per-session QA logging expansion"
PHASE_STATUS = "landed"


def get_phase_metadata() -> Dict[str, Any]:
    """Return phase identification and status for this phase.

    Returns:
        Dict[str, Any]: Keys ``phase``, ``title``, ``status``, and ``spec_ref``.
    """
    return {
        "phase": PHASE_ID,
        "title": PHASE_TITLE,
        "status": PHASE_STATUS,
        "spec_ref": "specs/IA_FULL_SPEC_2026-05-10.md Phase 84",
    }


class SessionQALogger:
    """Append-only QA event logger for a single session.

    Parameters
    ----------
    session_id:
        Opaque identifier for this QA session.  Used as the stem of the
        ndjson log file (``{log_dir}/{session_id}.ndjson``).
    log_dir:
        Directory where the log file is written.  Created if it does not
        exist.
    max_events:
        Maximum number of events retained in the in-memory ring buffer.
        When the buffer is full, the oldest event is dropped to make room.
        Defaults to 10 000.
    """

    def __init__(
        self,
        session_id: str,
        log_dir: Path,
        max_events: int = 10_000,
    ) -> None:
        """Initialise the logger, opening the NDJSON file in append mode.

        Args:
            session_id (str): Opaque identifier for this QA session; used as
                the NDJSON filename stem.
            log_dir (Path): Directory where the log file is written; created if
                it does not already exist.
            max_events (int, optional): Ring-buffer capacity. Defaults to 10 000.
        """
        self.session_id = session_id
        self.log_dir = Path(log_dir)
        self.max_events = max_events

        self.log_dir.mkdir(parents=True, exist_ok=True)
        self._log_path: Path = self.log_dir / f"{session_id}.ndjson"
        self._buf: Deque[Dict[str, Any]] = deque(maxlen=max_events)
        self._fh = self._log_path.open("a", encoding="utf-8")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def log_event(self, event_type: str, payload: dict) -> None:  # noqa: ANN001
        """Append one event to the ndjson log and the ring buffer.

        The event row merges ``payload`` with the envelope fields
        (``timestamp``, ``session_id``, ``event_type``).  Envelope fields
        take priority — they cannot be overridden by payload keys.

        Parameters
        ----------
        event_type:
            Short string identifying the kind of event (e.g. ``"tool_call"``,
            ``"assertion_pass"``, ``"assertion_fail"``).
        payload:
            Arbitrary key/value pairs attached to the event.
        """
        row: Dict[str, Any] = {
            **payload,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "session_id": self.session_id,
            "event_type": event_type,
        }
        self._fh.write(json.dumps(row) + "\n")
        self._buf.append(row)

    def get_recent(self, n: int = 100) -> List[Dict[str, Any]]:
        """Return the last *n* events from the in-memory ring buffer.

        Events are returned in chronological order (oldest first, newest
        last).  If *n* exceeds the number of buffered events, all buffered
        events are returned.

        Parameters
        ----------
        n:
            Maximum number of events to return.
        """
        buf_list = list(self._buf)
        return buf_list[-n:] if n < len(buf_list) else buf_list

    def flush(self) -> None:
        """Flush write buffers and fsync to durable storage.

        Useful before handing off a session or before process exit to ensure
        no events are lost in OS write-back caches.
        """
        self._fh.flush()
        os.fsync(self._fh.fileno())

    # ------------------------------------------------------------------
    # Resource management
    # ------------------------------------------------------------------

    def close(self) -> None:
        """Flush and close the NDJSON log file handle."""
        self.flush()
        self._fh.close()

    def __enter__(self) -> "SessionQALogger":
        """Support ``with SessionQALogger(...) as logger:`` usage."""
        return self

    def __exit__(self, *_: object) -> None:
        """Close the logger on context-manager exit."""
        self.close()
