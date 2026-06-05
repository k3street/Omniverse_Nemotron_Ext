"""Phase 22 — sync_from_stage scaffold."""
import pytest
pytestmark = pytest.mark.l0


def test_classify_franka_prim():
    from service.isaac_assist_service.multimodal.sync_stage import _classify_prim
    prim = {"reference_url": "/Isaac/Robots/FrankaRobotics/FrankaPanda/franka.usd"}
    assert _classify_prim(prim) == "franka_panda"


def test_classify_cube_by_usd_type():
    from service.isaac_assist_service.multimodal.sync_stage import _classify_prim
    assert _classify_prim({"usd_type": "Cube"}) == "cube"


def test_classify_unknown_returns_none():
    from service.isaac_assist_service.multimodal.sync_stage import _classify_prim
    assert _classify_prim({"usd_type": "Mystery"}) is None


@pytest.mark.asyncio
async def test_sync_from_stage_returns_spec_shape():
    from service.isaac_assist_service.multimodal.sync_stage import sync_from_stage
    spec = await sync_from_stage("test-session")
    assert "intent" in spec
    assert "objects" in spec
    assert "source" in spec
