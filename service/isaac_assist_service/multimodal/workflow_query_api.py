"""Phase 40 — workflow query API.

Read-side helpers for querying workflow records by id, status, phase,
or recency.

Per specs/IA_FULL_SPEC_2026-05-10.md Phase 40.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional


class WorkflowQueryAPI:
    def __init__(self, store: Dict[str, Dict[str, Any]]) -> None:
        self._store = store

    def get(self, workflow_id: str) -> Optional[Dict[str, Any]]:
        return self._store.get(workflow_id)

    def by_status(self, status: str) -> List[Dict[str, Any]]:
        return [w for w in self._store.values() if w.get("status") == status]

    def by_phase(self, phase_name: str) -> List[Dict[str, Any]]:
        return [w for w in self._store.values() if w.get("current_phase") == phase_name]

    def recent(self, limit: int = 10) -> List[Dict[str, Any]]:
        all_workflows = list(self._store.values())
        all_workflows.sort(key=lambda w: w.get("created_at", ""), reverse=True)
        return all_workflows[:limit]
