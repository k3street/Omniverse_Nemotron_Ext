"""Phase 40 — workflow query API.

Read-side helpers for querying workflow records by id, status, phase,
or recency.

Per specs/IA_FULL_SPEC_2026-05-10.md Phase 40.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional


class WorkflowQueryAPI:
    """Read-side helper for querying an in-memory workflow record store."""

    def __init__(self, store: Dict[str, Dict[str, Any]]) -> None:
        """Initialise the query API against the given *store* dict.

        Args:
            store (Dict[str, Dict]): Mapping from ``workflow_id`` to workflow record dicts.
        """
        self._store = store

    def get(self, workflow_id: str) -> Optional[Dict[str, Any]]:
        """Return the workflow record for *workflow_id*, or ``None`` if absent."""
        return self._store.get(workflow_id)

    def by_status(self, status: str) -> List[Dict[str, Any]]:
        """Return all workflows whose ``status`` field equals *status*."""
        return [w for w in self._store.values() if w.get("status") == status]

    def by_phase(self, phase_name: str) -> List[Dict[str, Any]]:
        """Return all workflows whose ``current_phase`` field equals *phase_name*."""
        return [w for w in self._store.values() if w.get("current_phase") == phase_name]

    def recent(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Return the *limit* most-recently created workflows, newest first."""
        all_workflows = list(self._store.values())
        all_workflows.sort(key=lambda w: w.get("created_at", ""), reverse=True)
        return all_workflows[:limit]
