import asyncio
import base64

import pytest
from fastapi import HTTPException

from service.isaac_assist_service.multimodal.cosmos3_adapter import (
    CosmosObjectProposal,
    CosmosSceneObservation,
)
from service.isaac_assist_service.multimodal.cosmos3_runtime import (
    Cosmos3ReasonerClient,
    chat_completions_url,
    extract_json_object,
)
from service.isaac_assist_service.multimodal.persistence import MultimodalStore
from service.isaac_assist_service.multimodal import routes


pytestmark = pytest.mark.l0


def test_chat_completions_url_normalizes_base_urls():
    assert chat_completions_url("http://host:8000") == "http://host:8000/v1/chat/completions"
    assert chat_completions_url("http://host:8000/v1") == "http://host:8000/v1/chat/completions"
    assert (
        chat_completions_url("http://host:8000/v1/chat/completions")
        == "http://host:8000/v1/chat/completions"
    )


def test_extract_json_object_handles_markdown_wrapped_reply():
    parsed = extract_json_object(
        '```json\n{"input_kind": "photo", "objects": []}\n```'
    )

    assert parsed["input_kind"] == "photo"


def test_reasoner_payload_embeds_image_data_url():
    client = Cosmos3ReasonerClient(base_url="http://cosmos", model="Cosmos3-Nano")

    payload = client._build_payload(
        prompt="Build a pick scene",
        image_bytes=b"fake-image",
        mime_type="image/png",
        input_kind="screenshot",
    )

    content = payload["messages"][1]["content"]
    assert payload["model"] == "Cosmos3-Nano"
    assert content[0]["type"] == "text"
    assert content[1]["type"] == "image_url"
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")


def test_cosmos_observe_route_calls_reasoner_and_saves(monkeypatch, tmp_path):
    class FakeReasoner:
        async def observe_scene(self, *, prompt, image_bytes, mime_type, input_kind):
            assert prompt == "Recreate this"
            assert image_bytes == b"png"
            assert mime_type == "image/png"
            assert input_kind == "screenshot"
            return CosmosSceneObservation(
                input_kind="screenshot",
                prompt=prompt,
                objects=[
                    CosmosObjectProposal(label="Franka", role="robot", confidence=0.9)
                ],
            )

    old_store = routes._store
    routes._store = MultimodalStore(tmp_path / "state.db")
    monkeypatch.setattr(routes, "build_cosmos3_reasoner", lambda: FakeReasoner())
    try:
        req = routes.CosmosObserveRequest(
            prompt="Recreate this",
            image_base64=base64.b64encode(b"png").decode("ascii"),
            input_kind="screenshot",
        )

        response = asyncio.run(routes.observe_canvas_from_cosmos("observe_smoke", req))

        assert response["valid"] is True
        assert response["revision"] == 1
        assert response["observation"]["input_kind"] == "screenshot"
        assert response["spec"]["objects"][0]["object_class"] == "franka_panda"
    finally:
        routes._store.close()
        routes._store = old_store


def test_cosmos_observe_route_rejects_invalid_base64():
    req = routes.CosmosObserveRequest(image_base64="not base64")

    try:
        asyncio.run(routes.observe_canvas_from_cosmos("bad_base64", req))
    except HTTPException as exc:
        assert exc.status_code == 422
    else:
        raise AssertionError("Expected HTTPException for invalid base64")
