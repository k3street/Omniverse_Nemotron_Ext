"""Phase 43 — workflow templates show up in slash_command_discovery.

`slash_command_discovery` aggregates registered workflow templates so
the chat surface can expose them as slash commands.

Per specs/IA_FULL_SPEC_2026-05-10.md Phase 43.
"""
from typing import Any, Dict, List


def discover_workflow_slash_commands(templates: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Convert workflow template registry to slash-command entries."""
    out: List[Dict[str, Any]] = []
    for name, tpl in templates.items():
        out.append({
            "command": f"/workflow {name}",
            "description": tpl.get("description", f"Start {name} workflow"),
            "always": False,
            "phases_count": len(tpl.get("phases", [])),
        })
    return out
