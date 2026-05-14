"""Phase 78 — IsaacLab arena leaderboard.

JSON-backed append-only leaderboard registry for IsaacLab arena benchmarks.
Supports multi-scenario isolation, top-K queries, and atomic file writes.

Per specs/IA_FULL_SPEC_2026-05-10.md Phase 78.
"""
from __future__ import annotations

import json
import os
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


PHASE_ID = 78
PHASE_TITLE = "IsaacLab arena leaderboard"
PHASE_STATUS = "landed"

DEFAULT_PATH = "data/leaderboards/arena.json"


def get_phase_metadata() -> Dict[str, Any]:
    """Return phase metadata for spec-coverage audits."""
    return {
        "phase": PHASE_ID,
        "title": PHASE_TITLE,
        "status": PHASE_STATUS,
        "spec_ref": "specs/IA_FULL_SPEC_2026-05-10.md Phase 78",
    }


class Leaderboard:
    """Append-only leaderboard registry backed by a JSON file.

    Each entry has:
      entry_id   – UUID string
      scenario_id – str
      score       – float
      agent_name  – str
      timestamp   – ISO-8601 UTC string
      metadata    – dict (optional, default {})

    File format:
      {"entries": [...]}
    """

    def __init__(self, path: str | os.PathLike = DEFAULT_PATH) -> None:
        """Initialise the leaderboard with a backing JSON file path.

        Args:
            path (str | os.PathLike, optional): Path to the leaderboard JSON file.
                Defaults to ``data/leaderboards/arena.json``.
        """
        self._path = Path(path)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load(self) -> List[Dict[str, Any]]:
        """Load entries from disk. Returns empty list if file absent."""
        if not self._path.exists():
            return []
        with open(self._path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data.get("entries", [])

    def _save(self, entries: List[Dict[str, Any]]) -> None:
        """Atomically write entries to disk (write-tmp then rename)."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp_fd, tmp_name = tempfile.mkstemp(
            dir=self._path.parent,
            prefix=".arena_lb_",
            suffix=".tmp",
        )
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as fh:
                json.dump({"entries": entries}, fh, indent=2)
            os.replace(tmp_name, self._path)
        except Exception:
            # Clean up tmp file on failure
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise

    # ------------------------------------------------------------------
    # Write-side
    # ------------------------------------------------------------------

    def submit(
        self,
        scenario_id: str,
        score: float,
        agent_name: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Append a new leaderboard entry and return its UUID entry_id.

        The underlying JSON file is written atomically (write-tmp then rename).

        Args:
            scenario_id (str): Identifier of the benchmark scenario.
            score (float): Agent score for this run.
            agent_name (str): Human-readable label for the agent.
            metadata (Dict[str, Any], optional): Arbitrary extra data. Defaults to ``{}``.

        Returns:
            str: UUID string that uniquely identifies the new entry.
        """
        entry_id = str(uuid.uuid4())
        entry: Dict[str, Any] = {
            "entry_id": entry_id,
            "scenario_id": scenario_id,
            "score": float(score),
            "agent_name": agent_name,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "metadata": metadata or {},
        }
        entries = self._load()
        entries.append(entry)
        self._save(entries)
        return entry_id

    # ------------------------------------------------------------------
    # Read-side
    # ------------------------------------------------------------------

    def top_k(self, scenario_id: str, k: int = 10) -> List[Dict[str, Any]]:
        """Return up to *k* entries for *scenario_id*, sorted descending by score."""
        entries = self.all_for_scenario(scenario_id)
        entries.sort(key=lambda e: e["score"], reverse=True)
        return entries[:k]

    def all_for_scenario(self, scenario_id: str) -> List[Dict[str, Any]]:
        """Return all entries for *scenario_id* (insertion order)."""
        return [e for e in self._load() if e["scenario_id"] == scenario_id]

    def list_scenarios(self) -> List[str]:
        """Return a deduplicated list of known scenario IDs (insertion order)."""
        seen: dict[str, None] = {}
        for entry in self._load():
            seen.setdefault(entry["scenario_id"], None)
        return list(seen.keys())
