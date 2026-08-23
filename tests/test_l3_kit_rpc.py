"""L3 contracts for a live Isaac Sim instance with Isaac Assist Kit RPC.

These tests intentionally fail—not skip—when the runtime is unavailable. The
L3 marker is opt-in and is executed only on a provisioned Isaac Sim host, so a
missing bridge must not produce a misleading green workflow.
"""
from __future__ import annotations

import pytest

from service.isaac_assist_service.chat.tools import kit_tools

pytestmark = [pytest.mark.l3, pytest.mark.asyncio]


async def test_kit_rpc_health_identifies_service() -> None:
    health = await kit_tools._get("/health")
    assert health.get("ok") is True, (
        f"Kit RPC is unavailable at {kit_tools.KIT_RPC_BASE}: {health!r}"
    )
    assert health.get("service") == "isaac-assist-kit-rpc"


async def test_kit_rpc_context_has_live_stage() -> None:
    context = await kit_tools.get_stage_context(full=False)
    assert "error" not in context, f"Kit RPC context failed: {context!r}"
    stage = context.get("stage")
    assert isinstance(stage, dict), f"Missing stage payload: {context!r}"
    assert "error" not in stage, f"No live USD stage: {stage!r}"
    assert isinstance(stage.get("stage_url"), str)
    assert isinstance(stage.get("prim_count"), int)


async def test_kit_rpc_main_thread_read_probe() -> None:
    result = await kit_tools.exec_sync(
        "import omni.usd\n"
        "stage = omni.usd.get_context().get_stage()\n"
        "print('isaac-assist-l3', bool(stage), stage.GetRootLayer().identifier if stage else '')",
        timeout=30,
    )
    assert result.get("success") is True, f"Main-thread probe failed: {result!r}"
    assert "isaac-assist-l3 True" in result.get("output", "")
