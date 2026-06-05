import asyncio

import pytest

from service.isaac_assist_service.multimodal.cosmos3_adapter import (
    CosmosObjectProposal,
    CosmosRelationProposal,
    CosmosSceneObservation,
    cosmos_observation_to_layout_spec,
    normalize_object_class,
)
from service.isaac_assist_service.multimodal.persistence import MultimodalStore
from service.isaac_assist_service.multimodal import routes


pytestmark = pytest.mark.l0


def test_cosmos_observation_maps_to_reviewable_layout_spec():
    observation = CosmosSceneObservation(
        input_kind="screenshot",
        prompt="Recreate this pick cell",
        summary="A Franka arm reaches toward a cube on a table with a target bin.",
        objects=[
            CosmosObjectProposal(
                label="Franka robot arm",
                role="robot",
                confidence=0.91,
                bbox_xyxy_norm=(0.10, 0.20, 0.30, 0.70),
            ),
            CosmosObjectProposal(
                label="red cube",
                role="pick",
                confidence=0.82,
                color="#ff0000",
                position_xy_m=(0.4, -0.2),
            ),
            CosmosObjectProposal(
                label="target bin",
                confidence=0.76,
                position_xy_m=(1.0, 0.4),
            ),
        ],
        relations=[
            CosmosRelationProposal(
                subject="red cube",
                predicate="on",
                object="table",
                confidence=0.7,
            )
        ],
    )

    spec = cosmos_observation_to_layout_spec(observation, session_id="smoke")

    assert spec.source.modality == "photo"
    assert spec.source.metadata["provider"] == "cosmos3"
    assert spec.parameters["requires_user_review"] is True
    assert spec.intent.counts.robots == 1
    assert spec.intent.counts.cubes == 1
    assert spec.intent.counts.bins == 1
    assert len(spec.objects or []) == 3
    assert spec.objects[0].object_class == "franka_panda"
    assert spec.bindings["primary_robot"].object_id == spec.objects[0].id
    assert spec.bindings["workpiece"].object_id == spec.objects[1].id
    assert spec.constraints[0]["type"] == "cosmos_relation"


def test_cosmos_class_normalization_prefers_palette_asset_hint():
    proposal = CosmosObjectProposal(
        label="robot",
        asset_hint="ur5e",
    )

    assert normalize_object_class(proposal) == "ur5e"


def test_cosmos_proposal_route_saves_with_cas(tmp_path):
    old_store = routes._store
    routes._store = MultimodalStore(tmp_path / "state.db")
    try:
        request = routes.CosmosProposalRequest(
            parent_revision=0,
            observation=CosmosSceneObservation(
                input_kind="photo",
                objects=[
                    CosmosObjectProposal(
                        label="Franka",
                        role="robot",
                        confidence=0.9,
                    )
                ],
            ),
        )

        response = asyncio.run(
            routes.propose_canvas_from_cosmos("cosmos_route_smoke", request)
        )

        assert response["valid"] is True
        assert response["revision"] == 1
        assert response["spec"]["source"]["metadata"]["provider"] == "cosmos3"
        latest = routes._store.get_latest("cosmos_route_smoke")
        assert latest is not None
        assert latest.objects[0].object_class == "franka_panda"
    finally:
        routes._store.close()
        routes._store = old_store
