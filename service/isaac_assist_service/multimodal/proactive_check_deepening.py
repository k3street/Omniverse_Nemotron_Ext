"""Phase 93 — proactive_check deepening.

Real trigger-ladder engine with conditional rules and parameter inference.

Per specs/IA_FULL_SPEC_2026-05-10.md Phase 93.
"""
from __future__ import annotations

import dataclasses
from typing import Any, Callable, Dict, List, Optional


PHASE_ID = 93
PHASE_TITLE = "proactive_check deepening"
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
        "spec_ref": "specs/IA_FULL_SPEC_2026-05-10.md Phase 93",
    }


# ---------------------------------------------------------------------------
# Core dataclass
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class ProactiveTrigger:
    """A single rule in the proactive-check ladder.

    Parameters
    ----------
    name:
        The event name this trigger fires on (e.g. ``"scene_opened"``).
    tool_list:
        Ordered list of tool names to recommend when this trigger fires.
    condition:
        Optional callable that receives the event context dict and returns
        ``True`` if this trigger should fire.  ``None`` means always fire.
    priority:
        Higher value fires first.  Triggers with equal priority are ordered
        by insertion order.
    """

    name: str
    tool_list: List[str]
    condition: Optional[Callable[[Dict[str, Any]], bool]] = None
    priority: int = 0


# ---------------------------------------------------------------------------
# Rule engine
# ---------------------------------------------------------------------------

class ProactiveRuleEngine:
    """Evaluate proactive-check rules against incoming events.

    Usage::

        engine = ProactiveRuleEngine()
        tools = engine.evaluate("scene_opened", {})
    """

    def __init__(self) -> None:
        """Initialise with an empty per-event trigger registry (insertion order preserved)."""
        # Keyed by event name, list preserves insertion order per event.
        self._rules: Dict[str, List[ProactiveTrigger]] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def register(self, trigger: ProactiveTrigger) -> None:
        """Add *trigger* to the rule registry for its event name."""
        if trigger.name not in self._rules:
            self._rules[trigger.name] = []
        self._rules[trigger.name].append(trigger)

    def triggers_for(
        self, event: str, context: Dict[str, Any]
    ) -> List[ProactiveTrigger]:
        """Return matching triggers for *event* sorted by priority descending.

        Only triggers whose ``condition`` (if any) returns ``True`` for
        *context* are included.
        """
        candidates = self._rules.get(event, [])
        matched = [
            t for t in candidates
            if t.condition is None or t.condition(context)
        ]
        # Stable sort: highest priority first; ties keep insertion order.
        matched.sort(key=lambda t: t.priority, reverse=True)
        return matched

    def evaluate(self, event: str, context: Dict[str, Any]) -> List[str]:
        """Flatten all matching triggers to a deduplicated, ordered tool list.

        Tools from higher-priority triggers appear first; within a trigger
        the declaration order is preserved.  Duplicates are dropped on first
        occurrence.
        """
        seen: Dict[str, None] = {}
        for trigger in self.triggers_for(event, context):
            for tool in trigger.tool_list:
                if tool not in seen:
                    seen[tool] = None
        return list(seen.keys())


# ---------------------------------------------------------------------------
# Default registry
# ---------------------------------------------------------------------------

def _make_default_engine() -> ProactiveRuleEngine:
    """Build the canonical engine pre-loaded with all default rules."""
    engine = ProactiveRuleEngine()

    # ----- scene_opened ----
    engine.register(ProactiveTrigger(
        name="scene_opened",
        tool_list=["scene_summary", "get_console_errors"],
        priority=10,
    ))

    # ----- robot_imported ----
    engine.register(ProactiveTrigger(
        name="robot_imported",
        tool_list=["scene_summary", "get_articulation_state"],
        priority=10,
    ))

    # ----- console_error — base rule (all severity levels) ----
    engine.register(ProactiveTrigger(
        name="console_error",
        tool_list=["get_console_errors", "explain_error", "fix_error"],
        condition=lambda ctx: ctx.get("severity") != "warning",
        priority=20,
    ))

    # console_error — lighter variant for warnings ----
    engine.register(ProactiveTrigger(
        name="console_error",
        tool_list=["explain_error"],
        condition=lambda ctx: ctx.get("severity") == "warning",
        priority=10,
    ))

    # ----- training_started ----
    engine.register(ProactiveTrigger(
        name="training_started",
        tool_list=["get_console_errors", "get_training_status"],
        priority=10,
    ))

    # ----- target_placed ----
    engine.register(ProactiveTrigger(
        name="target_placed",
        tool_list=["scene_summary", "measure_distance"],
        priority=10,
    ))

    # ----- physics_warning ----
    engine.register(ProactiveTrigger(
        name="physics_warning",
        tool_list=["get_physics_errors", "check_physics_health", "diagnose_physics_error"],
        priority=15,
    ))

    # ----- viewport_idle ----
    engine.register(ProactiveTrigger(
        name="viewport_idle",
        tool_list=["scene_summary", "get_console_errors"],
        priority=5,
    ))

    # ----- fps_drop ----
    engine.register(ProactiveTrigger(
        name="fps_drop",
        tool_list=["get_debug_info", "scene_summary", "diagnose_performance"],
        priority=15,
    ))

    # ----- sim_play ----
    engine.register(ProactiveTrigger(
        name="sim_play",
        tool_list=["get_console_errors", "scene_summary"],
        priority=10,
    ))

    # ----- training_finished ----
    engine.register(ProactiveTrigger(
        name="training_finished",
        tool_list=["get_console_errors", "get_training_status", "compare_policies"],
        priority=10,
    ))

    return engine


# Module-level default engine — import and call evaluate() directly.
default_engine: ProactiveRuleEngine = _make_default_engine()


def evaluate(event: str, context: Optional[Dict[str, Any]] = None) -> List[str]:
    """Convenience wrapper around ``default_engine.evaluate``."""
    return default_engine.evaluate(event, context or {})
