import asyncio

import pytest

pytestmark = pytest.mark.l0


def test_resolve_object_asset_uses_reviewed_palette_class():
    from service.isaac_assist_service.multimodal.asset_resolution import resolve_object_asset

    obj = {
        "id": "obj-1",
        "object_class": "franka_panda",
        "metadata": {
            "cosmos_label": "robot arm",
            "cosmos_confidence": 0.92,
        },
    }

    resolved = resolve_object_asset(obj)

    assert resolved is not None
    assert resolved.object_class == "franka_panda"
    assert resolved.source == "palette"
    assert resolved.usd_ref
    assert resolved.needs_review is False


def test_resolve_object_asset_preserves_explicit_override():
    from service.isaac_assist_service.multimodal.asset_resolution import resolve_object_asset

    obj = {
        "id": "obj-2",
        "object_class": "fixture_custom",
        "metadata": {
            "reviewed_asset_ref": "omniverse://assets/custom_fixture.usd",
        },
    }

    resolved = resolve_object_asset(obj)

    assert resolved is not None
    assert resolved.source == "explicit"
    assert resolved.usd_ref == "omniverse://assets/custom_fixture.usd"


def test_instantiate_dry_run_references_reviewed_palette_assets():
    from service.isaac_assist_service.multimodal.instantiator import instantiate

    class Spec:
        objects = [
            {
                "object_class": "franka_panda",
                "position": [0.0, 0.0, 0.0],
                "size": {"w": 0.4, "h": 0.4},
            }
        ]

    result = asyncio.run(instantiate(Spec(), dry_run=True))

    assert result.status == "dry_run"
    assert "AddReference('Isaac/" in result.generated_code
    assert "franka_panda" in result.generated_code


def test_instantiate_dry_run_uses_primitive_fallback_for_fixture_classes():
    from service.isaac_assist_service.multimodal.instantiator import instantiate

    class Spec:
        objects = [
            {
                "object_class": "table_medium",
                "position": [0.0, 0.0, 0.0],
                "size": {"w": 1.2, "h": 0.8},
            }
        ]

    result = asyncio.run(instantiate(Spec(), dry_run=True))

    assert result.status == "dry_run"
    assert "UsdGeom.Cube.Define(stage, '/World/Cube_1')" in result.generated_code
    assert "source_class='table_medium' -> prim_class='Cube'" in result.generated_code


def test_instantiate_dry_run_handles_pydantic_position_and_size():
    from service.isaac_assist_service.multimodal.instantiator import instantiate
    from service.isaac_assist_service.multimodal.types import Position, Size, TypedObject

    class Spec:
        objects = [
            TypedObject(
                **{
                    "class": "cube_medium",
                    "name": "Cube_1",
                    "position": Position(x=0.25, y=-0.5),
                    "size": Size(w=0.05, h=0.05),
                }
            )
        ]

    result = asyncio.run(instantiate(Spec(), dry_run=True))

    assert result.status == "dry_run"
    assert "Gf.Vec3d(0.25, -0.5, 0.0)" in result.generated_code


def test_build_route_returns_asset_resolution_summary(tmp_path):
    from service.isaac_assist_service.multimodal import routes
    from service.isaac_assist_service.multimodal.cosmos3_adapter import (
        CosmosObjectProposal,
        CosmosSceneObservation,
    )
    from service.isaac_assist_service.multimodal.persistence import MultimodalStore

    old_store = routes._store
    routes._store = MultimodalStore(tmp_path / "state.db")
    try:
        proposal = routes.CosmosProposalRequest(
            observation=CosmosSceneObservation(
                input_kind="screenshot",
                objects=[
                    CosmosObjectProposal(
                        label="franka panda",
                        confidence=0.93,
                    )
                ],
            ),
            parent_revision=0,
        )
        asyncio.run(routes.propose_canvas_from_cosmos("build_assets", proposal))

        response = asyncio.run(
            routes.build_canvas("build_assets", routes.BuildRequest())
        )

        assert response["asset_resolutions"]
        assert response["asset_resolutions"][0]["object_class"] == "franka_panda"
        assert response["asset_resolutions"][0]["usd_ref"].endswith("franka.usd")
    finally:
        routes._store.close()
        routes._store = old_store
