import asyncio

import pytest

pytestmark = pytest.mark.l0


def test_resolve_object_asset_uses_reviewed_palette_class(monkeypatch, tmp_path):
    from service.isaac_assist_service.multimodal import asset_resolution

    monkeypatch.setenv("ISAAC_ASSIST_ASSET_ROOTS", str(tmp_path / "missing"))
    asset_resolution._load_asset_catalog.cache_clear()
    obj = {
        "id": "obj-1",
        "object_class": "franka_panda",
        "metadata": {
            "cosmos_label": "robot arm",
            "cosmos_confidence": 0.92,
        },
    }

    resolved = asset_resolution.resolve_object_asset(obj)

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


def test_resolve_object_asset_prefers_local_robot_override(monkeypatch, tmp_path):
    from service.isaac_assist_service.multimodal import asset_resolution

    franka = (
        tmp_path
        / "Lightwheel_OpenSource/Locomotion/Grass/E/InteractiveAsset/omron_franka.usd"
    )
    franka.parent.mkdir(parents=True, exist_ok=True)
    franka.write_text("#usda 1.0\n")

    monkeypatch.setenv("ISAAC_ASSIST_ASSET_ROOTS", str(tmp_path))
    asset_resolution._load_asset_catalog.cache_clear()

    resolved = asset_resolution.resolve_object_asset(
        {"id": "robot", "object_class": "franka_panda"}
    )

    assert resolved is not None
    assert resolved.source == "local_assets"
    assert resolved.usd_ref == str(franka)


def test_instantiate_dry_run_references_reviewed_palette_assets(monkeypatch, tmp_path):
    from service.isaac_assist_service.multimodal.instantiator import instantiate
    from service.isaac_assist_service.multimodal import asset_resolution

    monkeypatch.setenv("ISAAC_ASSIST_ASSET_ROOTS", str(tmp_path / "missing"))
    asset_resolution._load_asset_catalog.cache_clear()

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


def test_resolve_object_asset_uses_local_asset_overrides(monkeypatch, tmp_path):
    from service.isaac_assist_service.multimodal import asset_resolution

    franka = (
        tmp_path
        / "Lightwheel_OpenSource/Locomotion/Grass/E/InteractiveAsset/omron_franka.usd"
    )
    bowl = (
        tmp_path
        / "SimReady_Furniture_Misc_01_NVD/Assets/simready_content/common_assets/"
        "props/serving_bowl/serving_bowl.usd"
    )
    orange = (
        tmp_path
        / "SimReady_Furniture_Misc_01_NVD/Assets/simready_content/common_assets/"
        "props/orange_02/orange_02.usd"
    )
    plate = (
        tmp_path
        / "SimReady_Furniture_Misc_01_NVD/Assets/simready_content/common_assets/"
        "props/plate_small/plate_small.usd"
    )
    conveyor = (
        tmp_path
        / "Warehouse_NVD/Assets/DigitalTwin/Assets/Warehouse/Equipment/Conveyors/"
        "ConveyorBelt_A/ConveyorBelt_A12_PR_NVD_01.usd"
    )
    box = (
        tmp_path
        / "SimReady_Containers_Shipping_02_NVD/Assets/simready_content/"
        "common_assets/props/box_a01/box_a01.usd"
    )
    cube = (
        tmp_path
        / "Lightwheel_oz5iukPxYq_KitchenRoom/"
        "omniverse-content-production.s3.us-west-2.amazonaws.com/"
        "Assets/Extensions/Samples/Paint/cube.usd"
    )
    for path in (franka, bowl, orange, plate, conveyor, box, cube):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("#usda 1.0\n")

    monkeypatch.setenv("ISAAC_ASSIST_ASSET_ROOTS", str(tmp_path))
    asset_resolution._load_asset_catalog.cache_clear()

    assert asset_resolution.resolve_object_asset(
        {"id": "robot", "object_class": "franka_panda"}
    ).usd_ref == str(franka)
    assert asset_resolution.resolve_object_asset(
        {"id": "bowl", "object_class": "bowl"}
    ).usd_ref == str(bowl)
    assert asset_resolution.resolve_object_asset(
        {"id": "fruit", "object_class": "fruit"}
    ).usd_ref == str(orange)
    assert asset_resolution.resolve_object_asset(
        {"id": "plate", "object_class": "plate"}
    ).usd_ref == str(plate)
    assert asset_resolution.resolve_object_asset(
        {"id": "conv", "object_class": "conveyor_short"}
    ).usd_ref == str(conveyor)
    assert asset_resolution.resolve_object_asset(
        {"id": "bin", "object_class": "bin"}
    ).usd_ref == str(box)
    assert asset_resolution.resolve_object_asset(
        {"id": "cube", "object_class": "cube"}
    ).usd_ref == str(cube)


def test_resolve_object_asset_uses_catalog_fallback(monkeypatch, tmp_path):
    from service.isaac_assist_service.multimodal import asset_resolution

    catalog_asset = tmp_path / "catalog_assets" / "warehouse_bin.usd"
    catalog_asset.parent.mkdir(parents=True)
    catalog_asset.write_text("#usda 1.0\n")
    catalog = tmp_path / "asset_catalog.json"
    catalog.write_text(
        """
        {
          "assets": [
            {
              "name": "warehouse_bin",
              "usd_path": "%s",
              "category": "container",
              "tags": ["box", "bin", "simready"]
            }
          ]
        }
        """
        % str(catalog_asset)
    )

    monkeypatch.setenv("ISAAC_ASSIST_ASSET_ROOTS", str(tmp_path))
    asset_resolution._load_asset_catalog.cache_clear()

    resolved = asset_resolution.resolve_object_asset(
        {"id": "bin", "object_class": "bin"}
    )

    assert resolved is not None
    assert resolved.source == "asset_catalog"
    assert resolved.usd_ref == str(catalog_asset)


def test_instantiate_dry_run_uses_primitive_fallback_for_fixture_classes(monkeypatch, tmp_path):
    from service.isaac_assist_service.multimodal.instantiator import instantiate
    from service.isaac_assist_service.multimodal import asset_resolution

    monkeypatch.setenv("ISAAC_ASSIST_ASSET_ROOTS", str(tmp_path / "missing"))
    asset_resolution._load_asset_catalog.cache_clear()

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
    assert "UsdGeom.Cube.Define(stage, '/World/table_medium')" in result.generated_code
    assert "source_class='table_medium' -> prim_class='Cube'" in result.generated_code
    assert "isaac_assist:object_class', 'table_medium'" in result.generated_code


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


def test_instantiate_dry_run_computes_nested_spatial_relations(monkeypatch, tmp_path):
    from service.isaac_assist_service.multimodal import asset_resolution
    from service.isaac_assist_service.multimodal.instantiator import instantiate

    monkeypatch.setenv("ISAAC_ASSIST_ASSET_ROOTS", str(tmp_path / "missing"))
    asset_resolution._load_asset_catalog.cache_clear()

    class Spec:
        objects = [
            {
                "id": "table_1",
                "object_class": "table_medium",
                "name": "Table",
                "position": [1.0, 2.0, 0.0],
                "size": {"w": 1.2, "h": 0.8},
            },
            {
                "id": "bowl_1",
                "object_class": "bowl",
                "name": "Bowl",
                "position": [0.0, 0.0, 0.0],
                "size": {"w": 0.25, "h": 0.25},
            },
            {
                "id": "fruit_1",
                "object_class": "fruit",
                "name": "Fruit",
                "position": [0.0, 0.0, 0.0],
                "size": {"w": 0.07, "h": 0.07},
            },
        ]
        relations = [
            {"subject_id": "bowl_1", "relation": "on_top_of", "object_id": "table_1"},
            {"subject_id": "bowl_1", "relation": "contains", "object_id": "fruit_1"},
        ]

    result = asyncio.run(instantiate(Spec(), dry_run=True))

    assert result.status == "dry_run"
    assert "# relation: bowl_1 on_top_of table_1" in result.generated_code
    assert "# relation: fruit_1 inside bowl_1" in result.generated_code
    assert "UsdGeom.Cube.Define(stage, '/World/Table')" in result.generated_code
    assert "UsdGeom.Cylinder.Define(stage, '/World/Bowl')" in result.generated_code
    assert "UsdGeom.Sphere.Define(stage, '/World/Fruit')" in result.generated_code
    assert "isaac_assist:layout_id', 'table_1'" in result.generated_code
    assert "isaac_assist:layout_name', 'Table'" in result.generated_code
    assert "Gf.Vec3d(1.0, 2.0, 0.81)" in result.generated_code
    assert "Gf.Vec3d(1.0, 2.0, 0.815)" in result.generated_code
    assert result.relation_summary == [
        {
            "subject_id": "bowl_1",
            "subject_name": "Bowl",
            "relation": "on_top_of",
            "object_id": "table_1",
            "object_name": "Table",
            "source": "reasoned",
            "confidence": 1.0,
        },
        {
            "subject_id": "fruit_1",
            "subject_name": "Fruit",
            "relation": "inside",
            "object_id": "bowl_1",
            "object_name": "Bowl",
            "source": "reasoned",
            "confidence": 1.0,
        },
    ]


def test_instantiate_dry_run_enables_physics_scene_ground_and_workpiece_body(monkeypatch, tmp_path):
    from service.isaac_assist_service.multimodal import asset_resolution
    from service.isaac_assist_service.multimodal.instantiator import instantiate

    monkeypatch.setenv("ISAAC_ASSIST_ASSET_ROOTS", str(tmp_path / "missing"))
    asset_resolution._load_asset_catalog.cache_clear()

    class Spec:
        objects = [
            {
                "id": "table_1",
                "object_class": "table_medium",
                "name": "Table",
                "position": [0.0, 0.0, 0.0],
                "size": {"w": 1.2, "h": 0.8},
            },
            {
                "id": "fruit_1",
                "object_class": "fruit",
                "name": "Fruit",
                "position": [0.0, 0.0, 0.0],
                "size": {"w": 0.07, "h": 0.07},
            },
            {
                "id": "franka_1",
                "object_class": "franka_panda",
                "name": "Franka",
                "position": [0.0, 0.0, 0.0],
                "size": {"w": 0.4, "h": 0.4},
            },
        ]

    result = asyncio.run(instantiate(Spec(), dry_run=True))

    assert "UsdPhysics.Scene.Define(stage, '/World/PhysicsScene')" in result.generated_code
    assert "UsdGeom.Cube.Define(stage, '/World/GroundPlane')" in result.generated_code
    assert "_apply_collision(ground.GetPrim(), '/World/GroundPlane')" in result.generated_code
    assert "_apply_collision(prim.GetPrim(), '/World/Table')" in result.generated_code
    assert "_apply_collision(prim.GetPrim(), '/World/Fruit')" in result.generated_code
    assert "_apply_rigid_body(prim.GetPrim(), 0.05)" in result.generated_code
    franka_section = result.generated_code.split(
        "prim = UsdGeom.Xform.Define(stage, '/World/Franka')", 1
    )[1].split("# Isaac Assist live relation readback", 1)[0]
    assert "_apply_rigid_body(prim.GetPrim()" not in franka_section


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
        assert "franka" in response["asset_resolutions"][0]["usd_ref"].lower()
        assert response["instantiation"]["status"] == "dry_run"
        assert response["instantiation"]["dry_run"] is True
        generated = response["instantiation"]["generated_code"]
        assert "AddReference(" in generated
        assert "franka" in generated.lower()
        assert "UsdPhysics.Scene.Define(stage, '/World/PhysicsScene')" in generated
        assert "UsdGeom.Cube.Define(stage, '/World/GroundPlane')" in generated
    finally:
        routes._store.close()
        routes._store = old_store


def test_build_route_returns_relation_summary(tmp_path):
    from service.isaac_assist_service.multimodal import routes
    from service.isaac_assist_service.multimodal.persistence import MultimodalStore
    from service.isaac_assist_service.multimodal.types import LayoutSpec

    old_store = routes._store
    routes._store = MultimodalStore(tmp_path / "state.db")
    try:
        spec = LayoutSpec.model_validate({
            "version": "1.0",
            "intent": {
                "pattern_hint": "pick_place",
                "counts": {"robots": 0, "conveyors": 0, "bins": 0, "cubes": 0, "sensors": 0, "humans": 0},
                "structural_features": {},
                "structural_tags": [],
            },
            "objects": [
                {
                    "id": "plate_1",
                    "class": "plate",
                    "name": "Plate",
                    "position": {"x": 0.0, "y": 0.0},
                    "size": {"w": 0.25, "h": 0.25},
                },
                {
                    "id": "burger_1",
                    "class": "hamburger",
                    "name": "Hamburger",
                    "position": {"x": 0.0, "y": 0.0},
                    "size": {"w": 0.12, "h": 0.12},
                },
            ],
            "relations": [
                {"subject_id": "burger_1", "relation": "on_top_of", "object_id": "plate_1"}
            ],
            "source": {"modality": "drag_drop", "confidence": 1.0, "metadata": {}},
        })
        asyncio.run(routes.get_store().save_with_cas("relations_build", spec, 0))

        response = asyncio.run(
            routes.build_canvas("relations_build", routes.BuildRequest())
        )

        assert response["instantiation"]["relation_summary"] == [
            {
                "subject_id": "burger_1",
                "subject_name": "Hamburger",
                "relation": "on_top_of",
                "object_id": "plate_1",
                "object_name": "Plate",
                "source": "user_explicit",
                "confidence": 1.0,
            }
        ]
        assert response["instantiation"]["relation_diagnostics"] == []
        assert response["instantiation"]["relation_verification"]["status"] == "pass"
        assert response["instantiation"]["relation_verification"]["check_count"] == 1
        assert "# relation: burger_1 on_top_of plate_1" in response["instantiation"]["generated_code"]
    finally:
        routes._store.close()
        routes._store = old_store


def test_build_route_can_request_live_instantiation(monkeypatch, tmp_path):
    from service.isaac_assist_service.multimodal import routes
    from service.isaac_assist_service.multimodal.cosmos3_adapter import (
        CosmosObjectProposal,
        CosmosSceneObservation,
    )
    from service.isaac_assist_service.multimodal.instantiator import InstantiateResult
    from service.isaac_assist_service.multimodal.persistence import MultimodalStore

    calls = {}

    async def fake_instantiate(spec, template_id=None, dry_run=False):
        calls["dry_run"] = dry_run
        calls["template_id"] = template_id
        return InstantiateResult(
            status="ok",
            build_id="build-123",
            generated_code="should not be returned for live builds",
        )

    old_store = routes._store
    routes._store = MultimodalStore(tmp_path / "state.db")
    monkeypatch.setattr(routes, "instantiate", fake_instantiate)
    try:
        proposal = routes.CosmosProposalRequest(
            observation=CosmosSceneObservation(
                objects=[CosmosObjectProposal(label="franka panda")],
            ),
            parent_revision=0,
        )
        asyncio.run(routes.propose_canvas_from_cosmos("live_build", proposal))

        response = asyncio.run(
            routes.build_canvas(
                "live_build",
                routes.BuildRequest(template_id="pick_place", dry_run=False),
            )
        )

        assert calls == {"dry_run": False, "template_id": "pick_place"}
        assert response["instantiation"]["status"] == "ok"
        assert response["instantiation"]["build_id"] == "build-123"
        assert response["instantiation"]["generated_code"] is None
    finally:
        routes._store.close()
        routes._store = old_store
