import asyncio
import base64
import json

import pytest
from fastapi import HTTPException

from service.isaac_assist_service.multimodal.cosmos3_adapter import (
    CosmosObjectProposal,
    CosmosSceneObservation,
)
from service.isaac_assist_service.multimodal.cosmos3_runtime import (
    Cosmos3ReasonerClient,
    CosmosRuntimeError,
    FallbackSceneReasonerClient,
    GeminiRoboticsERReasonerClient,
    chat_completions_url,
    extract_json_object,
    gemini_generate_content_url,
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


def test_gemini_generate_content_url_normalizes_base_urls():
    assert (
        gemini_generate_content_url(
            "gemini-robotics-er-1.6-preview",
            base_url="https://generativelanguage.googleapis.com/v1beta/models",
        )
        == "https://generativelanguage.googleapis.com/v1beta/models/"
        "gemini-robotics-er-1.6-preview:generateContent"
    )
    assert (
        gemini_generate_content_url(
            "ignored",
            api_key="test-key",
            base_url="https://example.com/v1beta/models/custom:generateContent",
        )
        == "https://example.com/v1beta/models/custom:generateContent?key=test-key"
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


def test_gemini_reasoner_payload_embeds_image_inline_data():
    client = GeminiRoboticsERReasonerClient(
        api_key="test-key",
        model="gemini-robotics-er-1.6-preview",
    )

    payload = client._build_payload(
        prompt="Build a pick scene",
        image_bytes=b"fake-image",
        mime_type="image/png",
        input_kind="screenshot",
    )

    parts = payload["contents"][0]["parts"]
    assert parts[0]["inline_data"]["mime_type"] == "image/png"
    assert parts[0]["inline_data"]["data"] == base64.b64encode(b"fake-image").decode("ascii")
    assert "Return ONLY valid JSON" in parts[1]["text"]
    assert payload["generationConfig"]["responseMimeType"] == "application/json"


def test_gemini_reasoner_extracts_observation_json_from_response():
    body = {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {
                            "text": (
                                '{"input_kind": "screenshot", "objects": [], '
                                '"metadata": {"source": "unit"}}'
                            )
                        }
                    ]
                }
            }
        ]
    }

    text = GeminiRoboticsERReasonerClient._extract_text(json.dumps(body))
    parsed = extract_json_object(text)

    assert parsed["input_kind"] == "screenshot"
    assert parsed["metadata"]["source"] == "unit"


def test_fallback_reasoner_uses_primary_when_available():
    class FakePrimary:
        def __init__(self):
            self.called = False

        def is_configured(self):
            return True

        async def observe_scene(self, **kwargs):
            self.called = True
            return CosmosSceneObservation(
                input_kind=kwargs["input_kind"],
                prompt=kwargs["prompt"],
                metadata={"provider": "primary"},
            )

    class FakeFallback:
        def __init__(self):
            self.called = False

        def is_configured(self):
            return True

        async def observe_scene(self, **kwargs):
            self.called = True
            return CosmosSceneObservation(metadata={"provider": "fallback"})

    primary = FakePrimary()
    fallback = FakeFallback()
    client = FallbackSceneReasonerClient(primary=primary, fallback=fallback)

    observation = asyncio.run(
        client.observe_scene(prompt="scene", input_kind="prompt")
    )

    assert primary.called is True
    assert fallback.called is False
    assert observation.metadata["provider"] == "primary"


def test_fallback_reasoner_uses_gemini_when_primary_fails():
    class FakePrimary:
        def is_configured(self):
            return True

        async def observe_scene(self, **kwargs):
            raise CosmosRuntimeError("cosmos down")

    class FakeFallback:
        def __init__(self):
            self.called_with = None

        def is_configured(self):
            return True

        async def observe_scene(self, **kwargs):
            self.called_with = kwargs
            return CosmosSceneObservation(
                input_kind=kwargs["input_kind"],
                prompt=kwargs["prompt"],
                metadata={"provider": "gemini_robotics_er"},
            )

    fallback = FakeFallback()
    client = FallbackSceneReasonerClient(primary=FakePrimary(), fallback=fallback)

    observation = asyncio.run(
        client.observe_scene(
            prompt="bowl on table",
            image_bytes=b"png",
            mime_type="image/png",
            input_kind="screenshot",
        )
    )

    assert observation.metadata["provider"] == "gemini_robotics_er"
    assert fallback.called_with["image_bytes"] == b"png"


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
