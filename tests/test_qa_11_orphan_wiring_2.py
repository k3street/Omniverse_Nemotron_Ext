"""QA-11 orphan-wiring tests for Phase 20/72/77."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.l0


@pytest.mark.asyncio
async def test_phase_72_validate_assembly_constraint_dispatchable():
    """Phase 72: validate_assembly_constraint tool reachable; rejects bad spec."""
    from service.isaac_assist_service.chat.tools import tool_executor
    result = await tool_executor.execute_tool_call(
        "validate_assembly_constraint",
        {
            "name": "peg_a_into_hole_b",
            "type": "distance_between",
            "target_a": {"prim_path": "/World/Peg"},
            "target_b": {"prim_path": "/World/Hole"},
            "params": {"distance": 0.05},
        },
    )
    assert result["valid"] is True
    assert result["issues"] == []

    bad = await tool_executor.execute_tool_call(
        "validate_assembly_constraint",
        {
            "name": "",  # empty
            "type": "distance_between",
            "target_a": {"prim_path": "/World/Peg"},
            "target_b": {"prim_path": "/World/Hole"},
            # missing required 'distance' param
            "params": {},
        },
    )
    assert bad["valid"] is False
    assert any("name" in issue or "distance" in issue for issue in bad["issues"])


@pytest.mark.asyncio
async def test_phase_77_viewport_cache_stats_dispatchable():
    """Phase 77: viewport_cache_stats returns expected shape."""
    from service.isaac_assist_service.chat.tools import tool_executor
    result = await tool_executor.execute_tool_call(
        "viewport_cache_stats", {"clear": True}
    )
    for key in ("hits", "misses", "evictions", "entries", "total_bytes", "hit_rate"):
        assert key in result, f"missing {key} in stats output"
    assert result["hits"] == 0
    assert result["misses"] == 0
    assert result["entries"] == 0


@pytest.mark.asyncio
async def test_phase_20_retrieve_template_by_role_dispatchable():
    """Phase 20: retrieve_template_by_role returns ranked matches."""
    from service.isaac_assist_service.chat.tools import tool_executor
    result = await tool_executor.execute_tool_call(
        "retrieve_template_by_role",
        {"query": "pick boxes from a bin", "role_hints": ["picker"], "max_results": 5},
    )
    assert "matches" in result
    assert result["count"] <= 5
    # When role_hints includes a real role from the Phase 21 registry, we
    # should get role_based source ahead of any legacy entries.
    if result["matches"]:
        sources = [m["source"] for m in result["matches"]]
        if "role_based" in sources and "legacy" in sources:
            first_role = sources.index("role_based")
            first_legacy = sources.index("legacy")
            assert first_role < first_legacy


@pytest.mark.asyncio
async def test_phase_20_missing_query_returns_error():
    """retrieve_template_by_role refuses empty query."""
    from service.isaac_assist_service.chat.tools import tool_executor
    result = await tool_executor.execute_tool_call(
        "retrieve_template_by_role", {"query": ""}
    )
    # Either Pydantic validation blocks it or the handler returns an error key.
    assert (
        result.get("validation_blocked") is True
        or result.get("error")
        or result.get("type") == "error"
    )
