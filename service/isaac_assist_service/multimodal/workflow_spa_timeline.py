"""Phase 38 — SPA-side timeline UI data shape.

Server-side data preparation; the actual SPA component lives in
web/floor-plan-ui/. This module exposes the JSON shape the SPA expects.

Per specs/IA_FULL_SPEC_2026-05-10.md Phase 38.
"""
from typing import Any, Dict, List


def build_spa_timeline_payload(workflow_record: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "workflow_id": workflow_record.get("workflow_id"),
        "current_phase": workflow_record.get("current_phase"),
        "phases": workflow_record.get("plan", {}).get("phases", []),
        "events": workflow_record.get("events", []),
        "render_hint": "spa_timeline_v1",
    }
