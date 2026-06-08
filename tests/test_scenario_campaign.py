from __future__ import annotations

import asyncio
import json

import pytest

pytestmark = pytest.mark.l0


def _minimal_spec(**scenario_variants):
    from service.isaac_assist_service.multimodal.types import (
        Counts,
        Intent,
        LayoutSpec,
        ScenarioVariants,
        Source,
        StructuralFeatures,
    )

    return LayoutSpec(
        intent=Intent(
            pattern_hint="pick_place",
            counts=Counts(cubes=1),
            structural_features=StructuralFeatures(),
            structural_tags=[],
        ),
        objects=[],
        scenario_variants=ScenarioVariants(**scenario_variants),
        source=Source(modality="drag_drop", confidence=1.0),
        revision=7,
    )


def test_get_canvas_returns_blank_editable_spec_for_new_session(tmp_path, monkeypatch):
    from service.isaac_assist_service.multimodal import routes
    from service.isaac_assist_service.multimodal.persistence import MultimodalStore

    store = MultimodalStore(db_path=tmp_path / "canvas.sqlite")
    monkeypatch.setattr(routes, "_store", store)

    response = asyncio.run(routes.get_canvas("new_browser_session"))

    assert response["revision"] == 0
    assert response["spec"] is not None
    assert response["spec"]["source"]["modality"] == "drag_drop"
    assert response["spec"]["objects"] == []
    assert response["spec"]["relations"] == []


def test_build_campaign_plan_cycles_deterministically():
    from service.isaac_assist_service.multimodal.scenario_campaign import build_campaign_plan

    spec = _minimal_spec(
        enabled=True,
        variant_count=5,
        seed=41,
        lighting=["studio", "backlit"],
        cameras=["overhead", "robot_view"],
        actors=["human_observer"],
        circumstances=["nominal", "occluded_target"],
    )

    plan = build_campaign_plan(spec, session_id="spatial_test")

    assert plan["campaign_id"] == "spatial_test_rev7_seed41"
    assert plan["variant_count"] == 5
    assert [v["seed"] for v in plan["variants"]] == [41, 42, 43, 44, 45]
    assert [v["lighting"] for v in plan["variants"]] == [
        "studio",
        "backlit",
        "studio",
        "backlit",
        "studio",
    ]
    assert plan["variants"][0]["launch_command"].startswith("SCENE_SETUP_SCRIPT=")
    assert "./launch_canvas_scene.sh " in plan["variants"][0]["launch_command"]
    assert plan["variants"][0]["validation"]["require_relations"] is True
    assert plan["relation_verification"]["status"] == "pass"


def test_campaign_plan_route_returns_current_session_plan(tmp_path, monkeypatch):
    from service.isaac_assist_service.multimodal import routes
    from service.isaac_assist_service.multimodal.persistence import MultimodalStore

    store = MultimodalStore(db_path=tmp_path / "canvas.sqlite")
    monkeypatch.setattr(routes, "_store", store)
    spec = _minimal_spec(enabled=True, variant_count=2, seed=10)
    store._save_with_cas_sync("route_session", spec, 0)

    response = asyncio.run(
        routes.plan_canvas_campaign(
            "route_session",
            routes.CampaignPlanRequest(workspace_root=str(tmp_path / "runs")),
        )
    )

    assert response["campaign_id"] == "route_session_rev1_seed10"
    assert response["workspace_dir"].startswith(str(tmp_path / "runs"))
    assert len(response["variants"]) == 2
    assert response["variants"][1]["seed"] == 11


def test_materialize_campaign_writes_manifest_usda_and_setup(tmp_path):
    from service.isaac_assist_service.multimodal.scenario_campaign import materialize_campaign

    spec = _minimal_spec(enabled=True, variant_count=2, seed=5)
    manifest = asyncio.run(
        materialize_campaign(
            spec,
            session_id="mat_session",
            workspace_root=tmp_path / "runs",
        )
    )

    campaign_dir = tmp_path / "runs" / "mat_session_rev7_seed5"
    assert manifest["execution"]["status"] == "materialized"
    assert manifest["execution"]["relation_verification_status"] == "pass"
    assert manifest["relation_verification"]["status"] == "pass"
    assert (campaign_dir / "campaign_plan.json").exists()
    assert (campaign_dir / "layout_spec.json").exists()
    first = manifest["variants"][0]
    assert first["usd_path"].endswith(".usda")
    assert first["setup_script_path"].endswith("_setup.py")
    assert "SCENE_SETUP_SCRIPT=" in first["launch_command"]
    usda_text = (campaign_dir / "mat_session_rev7_seed5_v001.usda").read_text(encoding="utf-8")
    assert usda_text.startswith("#usda 1.0")
    assert 'upAxis = "Z"' in usda_text
    assert "metersPerUnit = 1" in usda_text
    setup_text = (campaign_dir / "mat_session_rev7_seed5_v001_setup.py").read_text(encoding="utf-8")
    assert "Base scene materialization" in setup_text
    assert "mat_session_rev7_seed5_v001" in setup_text
    assert "UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)" in setup_text
    assert "UsdGeom.SetStageMetersPerUnit(stage, 1.0)" in setup_text
    compile(setup_text, "mat_session_rev7_seed5_v001_setup.py", "exec")


def test_campaign_materialize_route_writes_current_session_files(tmp_path, monkeypatch):
    from service.isaac_assist_service.multimodal import routes
    from service.isaac_assist_service.multimodal.persistence import MultimodalStore

    store = MultimodalStore(db_path=tmp_path / "canvas.sqlite")
    monkeypatch.setattr(routes, "_store", store)
    spec = _minimal_spec(enabled=True, variant_count=1, seed=12)
    store._save_with_cas_sync("materialize_route", spec, 0)

    response = asyncio.run(
        routes.materialize_canvas_campaign(
            "materialize_route",
            routes.CampaignPlanRequest(workspace_root=str(tmp_path / "runs")),
        )
    )

    assert response["execution"]["status"] == "materialized"
    assert response["campaign_id"] == "materialize_route_rev1_seed12"
    assert len(response["variants"]) == 1
    assert (tmp_path / "runs" / "materialize_route_rev1_seed12" / "campaign_plan.json").exists()


def test_campaign_launch_route_dry_run_selects_first_variant(tmp_path, monkeypatch):
    from service.isaac_assist_service.multimodal import routes
    from service.isaac_assist_service.multimodal.persistence import MultimodalStore

    store = MultimodalStore(db_path=tmp_path / "canvas.sqlite")
    monkeypatch.setattr(routes, "_store", store)
    spec = _minimal_spec(enabled=True, variant_count=1, seed=22)
    store._save_with_cas_sync("launch_route", spec, 0)

    response = asyncio.run(
        routes.launch_canvas_campaign_variant(
            "launch_route",
            routes.CampaignLaunchRequest(
                workspace_root=str(tmp_path / "runs"),
                variant_index=1,
                dry_run=True,
            ),
        )
    )

    assert response["campaign"]["campaign_id"] == "launch_route_rev1_seed22"
    assert response["launch"]["status"] == "dry_run"
    assert response["launch"]["variant_id"] == "launch_route_rev1_seed22_v001"
    assert (tmp_path / "runs" / "launch_route_rev1_seed22" / "launch_route_rev1_seed22_v001_result.json").exists()


def test_run_materialized_variant_dry_run_writes_result(tmp_path):
    from scripts.run_materialized_variant import (
        launch_variant,
        load_manifest,
        select_variant,
        variant_result_path,
    )

    spec = _minimal_spec(enabled=True, variant_count=2, seed=30)
    manifest = asyncio.run(
        materialize_campaign_for_test(
            spec,
            session_id="runner_session",
            workspace_root=tmp_path / "runs",
        )
    )
    manifest_path = tmp_path / "runs" / "runner_session_rev7_seed30" / "campaign_plan.json"
    loaded = load_manifest(manifest_path)
    variant = select_variant(loaded, index=2, variant_id=None)

    result = launch_variant(variant, dry_run=True, wait=False)
    result_path = variant_result_path(variant)
    saved = json.loads(result_path.read_text(encoding="utf-8"))

    assert result["status"] == "dry_run"
    assert saved["variant_id"] == manifest["variants"][1]["variant_id"]
    assert "SCENE_SETUP_SCRIPT=" in saved["command"]


async def materialize_campaign_for_test(spec, *, session_id, workspace_root):
    from service.isaac_assist_service.multimodal.scenario_campaign import materialize_campaign

    return await materialize_campaign(
        spec,
        session_id=session_id,
        workspace_root=workspace_root,
    )
