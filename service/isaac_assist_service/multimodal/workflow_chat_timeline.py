"""Phase 37 — chat-side workflow timeline.

Renders workflow events as inline chat messages. Each phase transition
produces a structured message with action buttons.

Per specs/IA_FULL_SPEC_2026-05-10.md Phase 37.
"""
from __future__ import annotations

from typing import Any, Dict, List


def render_timeline_for_chat(workflow_record: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Convert workflow events to chat-renderable messages."""
    messages: List[Dict[str, Any]] = []
    for event in workflow_record.get("events", []):
        messages.append({
            "type": "workflow_event",
            "event_type": event.get("event_type", "unknown"),
            "timestamp": event.get("timestamp", ""),
            "phase": event.get("payload", {}).get("phase", ""),
            "actions": _actions_for_event(event),
        })
    return messages


def _actions_for_event(event: Dict[str, Any]) -> List[str]:
    et = event.get("event_type", "")
    if "awaiting_approval" in et:
        return ["approve", "reject", "revise"]
    return []
