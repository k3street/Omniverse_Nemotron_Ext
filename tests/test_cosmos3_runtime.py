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


def test_cosmos_observe_viewport_captures_and_saves(monkeypatch, tmp_path):
    class FakeReasoner:
        async def observe_scene(self, *, prompt, image_bytes, mime_type, input_kind):
            assert prompt == "Use viewport"
            assert image_bytes == b"viewport-png"
            assert mime_type == "image/png"
            assert input_kind == "screenshot"
            return CosmosSceneObservation(
                input_kind="screenshot",
                prompt=prompt,
                objects=[
                    CosmosObjectProposal(label="target bin", confidence=0.8)
                ],
            )

    async def fake_capture(max_dim=1280):
        assert max_dim == 640
        return {
            "image_b64": base64.b64encode(b"viewport-png").decode("ascii"),
            "width": 640,
            "height": 360,
        }

    from service.isaac_assist_service.chat.tools import kit_tools

    old_store = routes._store
    routes._store = MultimodalStore(tmp_path / "state.db")
    monkeypatch.setattr(routes, "build_cosmos3_reasoner", lambda: FakeReasoner())
    monkeypatch.setattr(kit_tools, "get_viewport_image", fake_capture)
    try:
        req = routes.CosmosViewportObserveRequest(
            prompt="Use viewport",
            max_dim=640,
        )

        response = asyncio.run(routes.observe_canvas_from_viewport("viewport_smoke", req))

        assert response["valid"] is True
        assert response["viewport_capture"] == {
            "width": 640,
            "height": 360,
            "max_dim": 640,
        }
        assert response["spec"]["objects"][0]["object_class"] == "bin"
    finally:
        routes._store.close()
        routes._store = old_store


def test_cosmos_observe_viewport_reports_missing_capture(monkeypatch):
    async def fake_capture(max_dim=1280):
        return {"error": "Kit is not running"}

    from service.isaac_assist_service.chat.tools import kit_tools

    monkeypatch.setattr(kit_tools, "get_viewport_image", fake_capture)

    try:
        asyncio.run(
            routes.observe_canvas_from_viewport(
                "viewport_missing",
                routes.CosmosViewportObserveRequest(),
            )
        )
    except HTTPException as exc:
        assert exc.status_code == 503
        assert "Kit is not running" in str(exc.detail)
    else:
        raise AssertionError("Expected HTTPException for missing viewport capture")
