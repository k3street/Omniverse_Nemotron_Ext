"""Phase 93 — proactive_check deepening tests.

Gate: pytest — trigger ladder, scene_opened → robot_imported → console_error
each emit expected tool list.  7+ tests covering conditional rules, priority
ordering, deduplication, unknown events, and custom registration.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.l0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fresh_engine():
    """Return a new default engine (avoids shared-state mutation between tests)."""
    from service.isaac_assist_service.multimodal.proactive_check_deepening import (
        _make_default_engine,
    )
    return _make_default_engine()


# ---------------------------------------------------------------------------
# 1. Metadata contract
# ---------------------------------------------------------------------------

def test_phase_93_metadata():
    from service.isaac_assist_service.multimodal.proactive_check_deepening import get_phase_metadata
    md = get_phase_metadata()
    assert md["phase"] == 93
    assert md["status"] == "landed"


# ---------------------------------------------------------------------------
# 2. scene_opened emits expected tools
# ---------------------------------------------------------------------------

def test_scene_opened_emits_expected_tools():
    engine = _fresh_engine()
    tools = engine.evaluate("scene_opened", {})
    assert "scene_summary" in tools
    assert "get_console_errors" in tools


# ---------------------------------------------------------------------------
# 3. robot_imported emits expected tools
# ---------------------------------------------------------------------------

def test_robot_imported_emits_expected_tools():
    engine = _fresh_engine()
    tools = engine.evaluate("robot_imported", {})
    assert "scene_summary" in tools
    assert "get_articulation_state" in tools


# ---------------------------------------------------------------------------
# 4. console_error without severity emits full set (error path)
# ---------------------------------------------------------------------------

def test_console_error_no_severity_emits_full_set():
    engine = _fresh_engine()
    tools = engine.evaluate("console_error", {})
    assert "get_console_errors" in tools
    assert "explain_error" in tools
    assert "fix_error" in tools


# ---------------------------------------------------------------------------
# 5. console_error with severity='warning' emits only explain_error
# ---------------------------------------------------------------------------

def test_console_error_warning_severity_emits_explain_only():
    engine = _fresh_engine()
    tools = engine.evaluate("console_error", {"severity": "warning"})
    # warning path should include explain_error
    assert "explain_error" in tools
    # fix_error should NOT appear for warnings (comes from the error-path rule)
    assert "fix_error" not in tools


# ---------------------------------------------------------------------------
# 6. Priority ordering — higher priority trigger tools appear first
# ---------------------------------------------------------------------------

def test_priority_ordering():
    from service.isaac_assist_service.multimodal.proactive_check_deepening import (
        ProactiveTrigger,
        ProactiveRuleEngine,
    )
    engine = ProactiveRuleEngine()
    engine.register(ProactiveTrigger(name="evt", tool_list=["low_tool"], priority=5))
    engine.register(ProactiveTrigger(name="evt", tool_list=["high_tool"], priority=20))

    tools = engine.evaluate("evt", {})
    # high_tool should come before low_tool
    assert tools.index("high_tool") < tools.index("low_tool")


# ---------------------------------------------------------------------------
# 7. Deduplication — same tool from multiple triggers appears once
# ---------------------------------------------------------------------------

def test_dedup_across_triggers():
    from service.isaac_assist_service.multimodal.proactive_check_deepening import (
        ProactiveTrigger,
        ProactiveRuleEngine,
    )
    engine = ProactiveRuleEngine()
    engine.register(ProactiveTrigger(name="evt", tool_list=["a", "b"], priority=10))
    engine.register(ProactiveTrigger(name="evt", tool_list=["b", "c"], priority=5))

    tools = engine.evaluate("evt", {})
    assert tools.count("b") == 1
    assert set(tools) == {"a", "b", "c"}


# ---------------------------------------------------------------------------
# 8. Unknown event returns empty list
# ---------------------------------------------------------------------------

def test_unknown_event_returns_empty():
    engine = _fresh_engine()
    tools = engine.evaluate("no_such_event", {})
    assert tools == []


# ---------------------------------------------------------------------------
# 9. Custom register works and fires
# ---------------------------------------------------------------------------

def test_custom_register_fires():
    from service.isaac_assist_service.multimodal.proactive_check_deepening import (
        ProactiveTrigger,
        ProactiveRuleEngine,
    )
    engine = ProactiveRuleEngine()
    engine.register(ProactiveTrigger(
        name="my_event",
        tool_list=["custom_tool"],
        condition=lambda ctx: ctx.get("flag") is True,
        priority=1,
    ))

    # condition not met
    assert engine.evaluate("my_event", {"flag": False}) == []
    # condition met
    assert engine.evaluate("my_event", {"flag": True}) == ["custom_tool"]


# ---------------------------------------------------------------------------
# 10. triggers_for returns sorted list (not just evaluate)
# ---------------------------------------------------------------------------

def test_triggers_for_returns_sorted_by_priority():
    from service.isaac_assist_service.multimodal.proactive_check_deepening import (
        ProactiveTrigger,
        ProactiveRuleEngine,
    )
    engine = ProactiveRuleEngine()
    engine.register(ProactiveTrigger(name="e", tool_list=["x"], priority=1))
    engine.register(ProactiveTrigger(name="e", tool_list=["y"], priority=99))
    engine.register(ProactiveTrigger(name="e", tool_list=["z"], priority=50))

    triggers = engine.triggers_for("e", {})
    priorities = [t.priority for t in triggers]
    assert priorities == sorted(priorities, reverse=True)


# ---------------------------------------------------------------------------
# 11. Module-level convenience evaluate() works
# ---------------------------------------------------------------------------

def test_module_level_evaluate_convenience():
    from service.isaac_assist_service.multimodal.proactive_check_deepening import evaluate
    tools = evaluate("sim_play")
    assert "get_console_errors" in tools
    assert "scene_summary" in tools


# ---------------------------------------------------------------------------
# 12. training_finished emits expected tools
# ---------------------------------------------------------------------------

def test_training_finished_emits_expected_tools():
    engine = _fresh_engine()
    tools = engine.evaluate("training_finished", {})
    assert "get_training_status" in tools
    assert "compare_policies" in tools
