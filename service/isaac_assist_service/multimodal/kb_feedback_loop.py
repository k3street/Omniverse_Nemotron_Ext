"""Phase 94 — Knowledge base feedback loop.

Real feedback writer + KB index refresher.  Provides:

- ``KBFeedbackEntry``   — dataclass for a single feedback record
- ``KBFeedbackWriter``  — append / read NDJSON feedback files per doc
- ``KBIndexRefresher``  — aggregate all feedback into a single JSON index

Per specs/IA_FULL_SPEC_2026-05-10.md Phase 94.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Literal


PHASE_ID = 94
PHASE_TITLE = "Knowledge base feedback loop"
PHASE_STATUS = "landed"


def get_phase_metadata() -> Dict[str, Any]:
    """Return phase identification and status for Phase 94.

    Returns:
        Dict[str, Any]: Keys ``phase``, ``title``, ``status``, and ``spec_ref``.
    """
    return {
        "phase": PHASE_ID,
        "title": PHASE_TITLE,
        "status": PHASE_STATUS,
        "spec_ref": "specs/IA_FULL_SPEC_2026-05-10.md Phase 94",
    }


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

FeedbackKind = Literal["correction", "addition", "deprecation", "endorsement"]


@dataclass
class KBFeedbackEntry:
    """A single feedback record attached to a knowledge-base document.

    Attributes:
        entry_id:      UUID string; generated automatically when not supplied.
        kb_doc_id:     Identifier of the KB document being annotated.
        feedback_kind: Nature of the feedback.
        note:          Human-readable explanation.
        submitter:     Who submitted; defaults to ``"anonymous"``.
        timestamp:     ISO-8601 string; defaults to the current UTC instant.
    """

    kb_doc_id: str
    feedback_kind: FeedbackKind
    note: str
    submitter: str = "anonymous"
    entry_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    # ------------------------------------------------------------------
    # Serialisation helpers
    # ------------------------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        """Serialise this entry to a plain dict suitable for JSON/NDJSON output.

        Returns:
            Dict[str, Any]: All fields as string values.
        """
        return {
            "entry_id": self.entry_id,
            "kb_doc_id": self.kb_doc_id,
            "feedback_kind": self.feedback_kind,
            "note": self.note,
            "submitter": self.submitter,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "KBFeedbackEntry":
        """Reconstruct a KBFeedbackEntry from a plain dict (e.g. parsed from NDJSON).

        Args:
            data (Dict[str, Any]): Dict with keys matching the dataclass fields.

        Returns:
            KBFeedbackEntry: Populated instance.
        """
        return cls(
            entry_id=data["entry_id"],
            kb_doc_id=data["kb_doc_id"],
            feedback_kind=data["feedback_kind"],
            note=data["note"],
            submitter=data.get("submitter", "anonymous"),
            timestamp=data["timestamp"],
        )


# ---------------------------------------------------------------------------
# Writer — NDJSON persistence per document
# ---------------------------------------------------------------------------


class KBFeedbackWriter:
    """Persist and retrieve feedback entries using one NDJSON file per document.

    Each ``kb_doc_id`` maps to ``{feedback_dir}/{kb_doc_id}.ndjson``.

    Usage::

        writer = KBFeedbackWriter(Path("/var/kb/feedback"))
        entry = KBFeedbackEntry(
            kb_doc_id="doc-42",
            feedback_kind="correction",
            note="The torque limit cited here is outdated.",
        )
        eid = writer.append(entry)
        entries = writer.read_all("doc-42")
    """

    def __init__(self, feedback_dir: Path) -> None:
        """Initialise the writer, creating *feedback_dir* if it does not exist.

        Args:
            feedback_dir (Path): Directory where per-document NDJSON files are stored.
        """
        self._dir = feedback_dir
        self._dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def append(self, entry: KBFeedbackEntry) -> str:
        """Append *entry* to the NDJSON file for its document.

        Args:
            entry: The feedback record to persist.

        Returns:
            The ``entry_id`` of the persisted record.
        """
        ndjson_path = self._path_for(entry.kb_doc_id)
        with ndjson_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry.to_dict(), ensure_ascii=False) + "\n")
        return entry.entry_id

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def read_all(self, kb_doc_id: str) -> List[KBFeedbackEntry]:
        """Return all feedback entries for *kb_doc_id* in append order.

        Returns an empty list if no feedback file exists for the document.
        """
        ndjson_path = self._path_for(kb_doc_id)
        if not ndjson_path.exists():
            return []
        entries: List[KBFeedbackEntry] = []
        with ndjson_path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                entries.append(KBFeedbackEntry.from_dict(json.loads(line)))
        return entries

    def corrections_for(self, kb_doc_id: str) -> List[KBFeedbackEntry]:
        """Return only *correction*-kind entries for *kb_doc_id*.

        Returns an empty list when there are no entries or none are corrections.
        """
        return [e for e in self.read_all(kb_doc_id) if e.feedback_kind == "correction"]

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _path_for(self, kb_doc_id: str) -> Path:
        """Return the NDJSON file path for *kb_doc_id*.

        Args:
            kb_doc_id (str): Knowledge-base document identifier.

        Returns:
            Path: ``{feedback_dir}/{kb_doc_id}.ndjson``
        """
        return self._dir / f"{kb_doc_id}.ndjson"


# ---------------------------------------------------------------------------
# Refresher — aggregate index across all documents
# ---------------------------------------------------------------------------


class KBIndexRefresher:
    """Scan all NDJSON feedback files and write an aggregate index.

    The index file has the shape::

        {
            "<kb_doc_id>": {
                "n_feedback": <int>,
                "last_update": "<iso-8601>",
                "by_kind": {
                    "correction": <int>,
                    "addition": <int>,
                    "deprecation": <int>,
                    "endorsement": <int>
                }
            },
            ...
        }

    Usage::

        refresher = KBIndexRefresher(
            feedback_dir=Path("/var/kb/feedback"),
            kb_index_path=Path("/var/kb/feedback_index.json"),
        )
        summary = refresher.refresh()
    """

    _ALL_KINDS: List[str] = ["correction", "addition", "deprecation", "endorsement"]

    def __init__(self, feedback_dir: Path, kb_index_path: Path) -> None:
        """Initialise the refresher with source and destination paths.

        Args:
            feedback_dir (Path): Directory containing the per-document NDJSON files.
            kb_index_path (Path): Destination path for the aggregate JSON index file.
        """
        self._dir = feedback_dir
        self._index_path = kb_index_path

    def refresh(self) -> Dict[str, Any]:
        """Rebuild the aggregate index from all NDJSON files on disk.

        Returns the index dict (also written atomically to ``kb_index_path``).
        Handles an empty feedback directory gracefully (returns ``{}``).
        """
        index: Dict[str, Any] = {}

        if not self._dir.exists():
            self._write_index(index)
            return index

        for ndjson_file in sorted(self._dir.glob("*.ndjson")):
            kb_doc_id = ndjson_file.stem
            by_kind: Dict[str, int] = {k: 0 for k in self._ALL_KINDS}
            last_ts: str = ""
            n_total = 0

            with ndjson_file.open("r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    record = json.loads(line)
                    n_total += 1
                    kind = record.get("feedback_kind", "")
                    if kind in by_kind:
                        by_kind[kind] += 1
                    ts = record.get("timestamp", "")
                    if ts > last_ts:
                        last_ts = ts

            if n_total == 0:
                continue

            index[kb_doc_id] = {
                "n_feedback": n_total,
                "last_update": last_ts,
                "by_kind": by_kind,
            }

        self._write_index(index)
        return index

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _write_index(self, index: Dict[str, Any]) -> None:
        """Atomically write *index* to ``kb_index_path`` via a temp-file rename.

        Args:
            index (Dict[str, Any]): Aggregate index mapping ``kb_doc_id`` → stats.
        """
        self._index_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._index_path.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(index, fh, indent=2, ensure_ascii=False)
        tmp.replace(self._index_path)
