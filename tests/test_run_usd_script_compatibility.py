import pytest

from service.isaac_assist_service.chat.tools.tool_executor import execute_tool_call

pytestmark = pytest.mark.l0


@pytest.mark.asyncio
async def test_run_usd_script_blocks_create_fixed_base_attr(monkeypatch):
    async def fail_queue_exec_patch(*_args, **_kwargs):
        raise AssertionError("bad PhysX compatibility script reached Kit RPC")

    monkeypatch.setattr(
        "service.isaac_assist_service.chat.tools.kit_tools.queue_exec_patch",
        fail_queue_exec_patch,
    )

    result = await execute_tool_call(
        "run_usd_script",
        {
            "description": "Anchor the robot",
            "code": "PhysxSchema.PhysxArticulationAPI.Apply(p).CreateFixedBaseAttr(True)",
        },
    )

    assert result["type"] == "error"
    assert result["validation_blocked"] is True
    assert result["compatibility_blocked"] is True
    assert "physxArticulation:fixedBase" in result["error"]
