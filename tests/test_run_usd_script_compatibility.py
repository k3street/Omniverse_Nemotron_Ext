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


@pytest.mark.asyncio
async def test_run_usd_script_blocks_fake_pick_place_teleport(monkeypatch):
    async def fail_queue_exec_patch(*_args, **_kwargs):
        raise AssertionError("fake pick-place teleport script reached Kit RPC")

    monkeypatch.setattr(
        "service.isaac_assist_service.chat.tools.kit_tools.queue_exec_patch",
        fail_queue_exec_patch,
    )

    result = await execute_tool_call(
        "run_usd_script",
        {
            "description": "execute pick-and-place loop",
            "code": """
import omni.usd
from pxr import UsdGeom, Gf
stage = omni.usd.get_context().get_stage()
for c in ['/World/Cube_0', '/World/Cube_1']:
    cp = stage.GetPrimAtPath(c)
    xform = UsdGeom.Xformable(cp)
    xform.ClearXformOpOrder()
    xform.AddTranslateOp().Set(Gf.Vec3d(0.0, -0.5, 0.2))
print('/World/Bin delivered_count: 2')
""",
        },
    )

    assert result["type"] == "error"
    assert result["validation_blocked"] is True
    assert result["compatibility_blocked"] is True
    assert "setup_pick_place_controller" in result["error"]


@pytest.mark.asyncio
async def test_run_usd_script_allows_pick_place_verification(monkeypatch):
    async def fake_queue_exec_patch(code, desc):
        return {
            "queued": True,
            "executed": True,
            "success": True,
            "output": "delivered_count: 0",
        }

    monkeypatch.setattr(
        "service.isaac_assist_service.chat.tools.kit_tools.queue_exec_patch",
        fake_queue_exec_patch,
    )

    result = await execute_tool_call(
        "run_usd_script",
        {
            "description": "verify delivered_count with bin bbox",
            "code": """
import omni.usd
from pxr import UsdGeom, Usd
stage = omni.usd.get_context().get_stage()
bbox_cache = UsdGeom.BBoxCache(Usd.TimeCode.Default(), ['default'])
bin_bbox = bbox_cache.ComputeWorldBound(stage.GetPrimAtPath('/World/Bin')).ComputeAlignedBox()
for c in ['/World/Cube_0', '/World/Cube_1']:
    print(c, bbox_cache.ComputeWorldBound(stage.GetPrimAtPath(c)).ComputeAlignedBox().GetMidpoint())
print(bin_bbox.GetMin(), bin_bbox.GetMax())
""",
        },
    )

    assert result["type"] == "code_patch"
    assert result["success"] is True
