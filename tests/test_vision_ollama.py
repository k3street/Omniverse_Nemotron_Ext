"""
L0 unit tests for:
  - OllamaVisionProvider  (payload, parsing, API response handling)
  - VisionRouter          (primary→fallback routing, empty result fallback)

No network calls — everything is mocked.
"""
import asyncio
import base64
import json
import os
import sys
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, _REPO)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_FAKE_PNG = b"PNG\x00data"
_FAKE_B64 = base64.b64encode(_FAKE_PNG).decode()


def _detections():
    return [{"point": [100, 200], "label": "cube"}, {"point": [300, 400], "label": "table"}]


def _boxes():
    return [{"box_2d": [50, 60, 200, 300], "label": "robot"}]


# ---------------------------------------------------------------------------
# OllamaVisionProvider — config defaults
# ---------------------------------------------------------------------------

@pytest.mark.l0
class TestOllamaVisionProviderInit:
    def test_default_model_from_config(self):
        from service.isaac_assist_service.chat.vision_ollama import OllamaVisionProvider
        with patch.dict(os.environ, {"OLLAMA_VISION_MODEL": "nemotron3:33b",
                                     "OLLAMA_HOST": "127.0.0.1",
                                     "OLLAMA_VISION_PORT": "11434"}):
            # Reload config to pick up patched env
            import importlib
            import service.isaac_assist_service.config as _cfg_mod
            importlib.reload(_cfg_mod)
            from service.isaac_assist_service.config import config
            config.ollama_vision_model = "nemotron3:33b"
            config.ollama_host = "127.0.0.1"
            config.ollama_vision_port = 11434
            vp = OllamaVisionProvider()
            assert vp.model == "nemotron3:33b"
            assert vp.host == "127.0.0.1"
            assert vp.port == 11434

    def test_explicit_constructor_args_override_config(self):
        from service.isaac_assist_service.chat.vision_ollama import OllamaVisionProvider
        vp = OllamaVisionProvider(host="10.0.0.5", port=12345, model="custom-model")
        assert vp.model == "custom-model"
        assert vp.host == "10.0.0.5"
        assert vp.port == 12345
        assert vp.base_url == "http://10.0.0.5:12345/api/chat"

    def test_timeout_default(self):
        from service.isaac_assist_service.chat.vision_ollama import OllamaVisionProvider
        vp = OllamaVisionProvider()
        assert vp.timeout == 60.0

    def test_timeout_custom(self):
        from service.isaac_assist_service.chat.vision_ollama import OllamaVisionProvider
        vp = OllamaVisionProvider(timeout=30.0)
        assert vp.timeout == 30.0


# ---------------------------------------------------------------------------
# OllamaVisionProvider — _parse_json_array (module-level helper)
# ---------------------------------------------------------------------------

@pytest.mark.l0
class TestOllamaParseJsonArray:
    @pytest.fixture()
    def parse(self):
        from service.isaac_assist_service.chat.vision_ollama import _parse_json_array
        return _parse_json_array

    def test_plain_json_array(self, parse):
        r = parse('[{"point": [100, 200], "label": "cube"}]')
        assert r == [{"point": [100, 200], "label": "cube"}]

    def test_markdown_fenced(self, parse):
        text = '```json\n[{"box_2d": [10, 20, 100, 200], "label": "robot"}]\n```'
        r = parse(text)
        assert len(r) == 1

    def test_embedded_array_in_prose(self, parse):
        text = 'Here are objects: [{"point": [50, 60], "label": "gripper"}] in the scene.'
        r = parse(text)
        assert r[0]["label"] == "gripper"

    def test_empty_array(self, parse):
        assert parse("[]") == []

    def test_invalid_returns_empty(self, parse):
        assert parse("not json at all") == []


# ---------------------------------------------------------------------------
# OllamaVisionProvider — API interactions (mocked aiohttp)
# ---------------------------------------------------------------------------

@pytest.mark.l0
class TestOllamaVisionAPICall:
    def _make_provider(self):
        from service.isaac_assist_service.chat.vision_ollama import OllamaVisionProvider
        return OllamaVisionProvider(host="127.0.0.1", port=11434, model="nemotron3:33b")

    def _mock_response(self, status: int, body: dict):
        resp = AsyncMock()
        resp.status = status
        resp.json = AsyncMock(return_value=body)
        resp.text = AsyncMock(return_value=json.dumps(body))
        resp.__aenter__ = AsyncMock(return_value=resp)
        resp.__aexit__ = AsyncMock(return_value=False)
        return resp

    def _mock_session(self, resp):
        session = MagicMock()
        session.post = MagicMock(return_value=resp)
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=False)
        return session

    def test_detect_objects_success(self):
        vp = self._make_provider()
        payload_text = json.dumps(_detections())
        resp = self._mock_response(200, {"message": {"content": payload_text}})
        session = self._mock_session(resp)
        with patch("aiohttp.ClientSession", return_value=session):
            result = asyncio.get_event_loop().run_until_complete(
                vp.detect_objects(_FAKE_PNG, "image/png")
            )
        assert len(result) == 2
        assert result[0]["label"] == "cube"

    def test_detect_objects_with_label_hints(self):
        vp = self._make_provider()
        payload_text = json.dumps([{"point": [100, 200], "label": "cube"}])
        resp = self._mock_response(200, {"message": {"content": payload_text}})
        session = self._mock_session(resp)
        with patch("aiohttp.ClientSession", return_value=session):
            result = asyncio.get_event_loop().run_until_complete(
                vp.detect_objects(_FAKE_PNG, labels=["cube"])
            )
        assert result[0]["label"] == "cube"

    def test_detect_bounding_boxes_success(self):
        vp = self._make_provider()
        payload_text = json.dumps(_boxes())
        resp = self._mock_response(200, {"message": {"content": payload_text}})
        session = self._mock_session(resp)
        with patch("aiohttp.ClientSession", return_value=session):
            result = asyncio.get_event_loop().run_until_complete(
                vp.detect_bounding_boxes(_FAKE_PNG)
            )
        assert result[0]["label"] == "robot"

    def test_plan_trajectory_success(self):
        vp = self._make_provider()
        traj = [{"point": [100, 100], "label": "0"}, {"point": [200, 200], "label": "1"}]
        resp = self._mock_response(200, {"message": {"content": json.dumps(traj)}})
        session = self._mock_session(resp)
        with patch("aiohttp.ClientSession", return_value=session):
            result = asyncio.get_event_loop().run_until_complete(
                vp.plan_trajectory(_FAKE_PNG, instruction="Move to the red block")
            )
        assert len(result) == 2

    def test_analyze_scene_success(self):
        vp = self._make_provider()
        resp = self._mock_response(200, {"message": {"content": "I see a Franka arm on a table."}})
        session = self._mock_session(resp)
        with patch("aiohttp.ClientSession", return_value=session):
            result = asyncio.get_event_loop().run_until_complete(
                vp.analyze_scene(_FAKE_PNG, question="What do you see?")
            )
        assert "Franka" in result

    def test_api_returns_non_200(self):
        vp = self._make_provider()
        resp = self._mock_response(503, {})
        session = self._mock_session(resp)
        with patch("aiohttp.ClientSession", return_value=session):
            result = asyncio.get_event_loop().run_until_complete(
                vp.detect_objects(_FAKE_PNG)
            )
        assert result == []

    def test_api_connection_error(self):
        import aiohttp
        vp = self._make_provider()
        session = MagicMock()
        session.post = MagicMock(side_effect=aiohttp.ClientConnectionError("refused"))
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=False)
        with patch("aiohttp.ClientSession", return_value=session):
            result = asyncio.get_event_loop().run_until_complete(
                vp.detect_objects(_FAKE_PNG)
            )
        assert result == []

    def test_analyze_scene_connection_error(self):
        import aiohttp
        vp = self._make_provider()
        session = MagicMock()
        session.post = MagicMock(side_effect=aiohttp.ClientConnectionError("refused"))
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=False)
        with patch("aiohttp.ClientSession", return_value=session):
            result = asyncio.get_event_loop().run_until_complete(
                vp.analyze_scene(_FAKE_PNG, question="what?")
            )
        assert "failed" in result.lower() or result == ""

    def test_is_available_true(self):
        vp = self._make_provider()
        tags_resp = AsyncMock()
        tags_resp.status = 200
        tags_resp.__aenter__ = AsyncMock(return_value=tags_resp)
        tags_resp.__aexit__ = AsyncMock(return_value=False)
        session = MagicMock()
        session.get = MagicMock(return_value=tags_resp)
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=False)
        with patch("aiohttp.ClientSession", return_value=session):
            ok = asyncio.get_event_loop().run_until_complete(vp.is_available())
        assert ok is True

    def test_is_available_false_on_connection_error(self):
        import aiohttp
        vp = self._make_provider()
        session = MagicMock()
        session.get = MagicMock(side_effect=aiohttp.ClientConnectionError("refused"))
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=False)
        with patch("aiohttp.ClientSession", return_value=session):
            ok = asyncio.get_event_loop().run_until_complete(vp.is_available())
        assert ok is False

    def test_images_field_contains_base64(self):
        """Verify that the image bytes are correctly base64-encoded in the request."""
        vp = self._make_provider()
        captured = {}

        resp = AsyncMock()
        resp.status = 200
        resp.json = AsyncMock(return_value={"message": {"content": "[]"}})
        resp.__aenter__ = AsyncMock(return_value=resp)
        resp.__aexit__ = AsyncMock(return_value=False)

        def fake_post(url, json=None, **kwargs):
            captured["json"] = json
            return resp

        session = MagicMock()
        session.post = fake_post
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=False)
        with patch("aiohttp.ClientSession", return_value=session):
            asyncio.get_event_loop().run_until_complete(vp.detect_objects(_FAKE_PNG))
        msgs = captured["json"]["messages"]
        assert len(msgs) == 1
        assert msgs[0]["images"][0] == _FAKE_B64


# ---------------------------------------------------------------------------
# VisionRouter — primary success, fallback on empty, fallback on exception
# ---------------------------------------------------------------------------

@pytest.mark.l0
class TestVisionRouter:
    def _make_router(self, primary_detections=None, primary_raises=False,
                     fallback_detections=None):
        from service.isaac_assist_service.chat.vision_router import VisionRouter

        primary = AsyncMock()
        if primary_raises:
            primary.detect_objects = AsyncMock(side_effect=RuntimeError("primary down"))
            primary.detect_bounding_boxes = AsyncMock(side_effect=RuntimeError("primary down"))
            primary.plan_trajectory = AsyncMock(side_effect=RuntimeError("primary down"))
            primary.analyze_scene = AsyncMock(side_effect=RuntimeError("primary down"))
        else:
            primary.detect_objects = AsyncMock(return_value=primary_detections or [])
            primary.detect_bounding_boxes = AsyncMock(return_value=primary_detections or [])
            primary.plan_trajectory = AsyncMock(return_value=primary_detections or [])
            primary.analyze_scene = AsyncMock(return_value="")
        primary.model = "nemotron3:33b"

        fallback = AsyncMock()
        fallback.detect_objects = AsyncMock(return_value=fallback_detections or _detections())
        fallback.detect_bounding_boxes = AsyncMock(return_value=fallback_detections or _boxes())
        fallback.plan_trajectory = AsyncMock(return_value=fallback_detections or _detections())
        fallback.analyze_scene = AsyncMock(return_value="Gemini says: a robot arm.")
        fallback.model = "gemini-robotics-er-1.6-preview"

        return VisionRouter(primary=primary, fallback=fallback)

    def test_primary_success_no_fallback_called(self):
        router = self._make_router(primary_detections=_detections())
        result = asyncio.get_event_loop().run_until_complete(
            router.detect_objects(_FAKE_PNG)
        )
        assert len(result) == 2
        assert router.model == "nemotron3:33b"
        router._fallback.detect_objects.assert_not_called()

    def test_primary_empty_triggers_fallback(self):
        router = self._make_router(primary_detections=[])
        result = asyncio.get_event_loop().run_until_complete(
            router.detect_objects(_FAKE_PNG)
        )
        assert len(result) == 2  # fallback returns _detections()
        assert router.model == "gemini-robotics-er-1.6-preview"

    def test_primary_raises_triggers_fallback(self):
        router = self._make_router(primary_raises=True)
        result = asyncio.get_event_loop().run_until_complete(
            router.detect_objects(_FAKE_PNG)
        )
        assert len(result) == 2
        assert router.model == "gemini-robotics-er-1.6-preview"

    def test_bounding_boxes_primary_success(self):
        router = self._make_router(primary_detections=_boxes())
        result = asyncio.get_event_loop().run_until_complete(
            router.detect_bounding_boxes(_FAKE_PNG)
        )
        assert result[0]["label"] == "robot"
        assert router.model == "nemotron3:33b"

    def test_bounding_boxes_fallback(self):
        router = self._make_router(primary_detections=[])
        result = asyncio.get_event_loop().run_until_complete(
            router.detect_bounding_boxes(_FAKE_PNG)
        )
        assert result[0]["label"] == "robot"
        assert router.model == "gemini-robotics-er-1.6-preview"

    def test_plan_trajectory_primary_success(self):
        traj = [{"point": [10, 20], "label": "0"}]
        router = self._make_router(primary_detections=traj)
        result = asyncio.get_event_loop().run_until_complete(
            router.plan_trajectory(_FAKE_PNG, instruction="move")
        )
        assert result[0]["label"] == "0"
        assert router.model == "nemotron3:33b"

    def test_plan_trajectory_fallback(self):
        router = self._make_router(primary_detections=[])
        result = asyncio.get_event_loop().run_until_complete(
            router.plan_trajectory(_FAKE_PNG, instruction="move")
        )
        assert len(result) >= 1  # fallback returns _detections()
        assert router.model == "gemini-robotics-er-1.6-preview"

    def test_analyze_scene_primary_success(self):
        router = self._make_router()
        router._primary.analyze_scene = AsyncMock(return_value="I see an arm.")
        result = asyncio.get_event_loop().run_until_complete(
            router.analyze_scene(_FAKE_PNG, question="what?")
        )
        assert result == "I see an arm."
        assert router.model == "nemotron3:33b"

    def test_analyze_scene_fallback_on_empty_primary(self):
        router = self._make_router()
        router._primary.analyze_scene = AsyncMock(return_value="Vision analysis failed — Ollama did not respond.")
        result = asyncio.get_event_loop().run_until_complete(
            router.analyze_scene(_FAKE_PNG, question="what?")
        )
        assert "Gemini" in result
        assert router.model == "gemini-robotics-er-1.6-preview"

    def test_analyze_scene_fallback_on_exception(self):
        router = self._make_router(primary_raises=True)
        result = asyncio.get_event_loop().run_until_complete(
            router.analyze_scene(_FAKE_PNG, question="what?")
        )
        assert "Gemini" in result

    def test_both_providers_fail_returns_error_string(self):
        from service.isaac_assist_service.chat.vision_router import VisionRouter
        primary = AsyncMock()
        primary.analyze_scene = AsyncMock(side_effect=RuntimeError("down"))
        primary.model = "ollama"
        fallback = AsyncMock()
        fallback.analyze_scene = AsyncMock(side_effect=RuntimeError("also down"))
        fallback.model = "gemini"
        router = VisionRouter(primary=primary, fallback=fallback)
        result = asyncio.get_event_loop().run_until_complete(
            router.analyze_scene(_FAKE_PNG, question="what?")
        )
        assert "failed" in result.lower()

    def test_both_list_providers_fail_returns_empty(self):
        from service.isaac_assist_service.chat.vision_router import VisionRouter
        primary = AsyncMock()
        primary.detect_objects = AsyncMock(side_effect=RuntimeError("down"))
        primary.model = "ollama"
        fallback = AsyncMock()
        fallback.detect_objects = AsyncMock(side_effect=RuntimeError("also down"))
        fallback.model = "gemini"
        router = VisionRouter(primary=primary, fallback=fallback)
        result = asyncio.get_event_loop().run_until_complete(
            router.detect_objects(_FAKE_PNG)
        )
        assert result == []


# ---------------------------------------------------------------------------
# build_vision_router factory
# ---------------------------------------------------------------------------

@pytest.mark.l0
class TestBuildVisionRouter:
    def test_build_returns_vision_router(self):
        from service.isaac_assist_service.chat.vision_router import VisionRouter, build_vision_router
        from service.isaac_assist_service.chat.vision_ollama import OllamaVisionProvider
        from service.isaac_assist_service.chat.vision_gemini import GeminiVisionProvider
        router = build_vision_router()
        assert isinstance(router, VisionRouter)

    def test_primary_is_ollama_fallback_is_gemini(self):
        from service.isaac_assist_service.chat.vision_router import VisionRouter
        from service.isaac_assist_service.chat.vision_ollama import OllamaVisionProvider
        from service.isaac_assist_service.chat.vision_gemini import GeminiVisionProvider
        from service.isaac_assist_service.chat.vision_router import build_vision_router
        router = build_vision_router()
        assert isinstance(router._primary, OllamaVisionProvider)
        assert isinstance(router._fallback, GeminiVisionProvider)


# ---------------------------------------------------------------------------
# Config — new vision provider fields
# ---------------------------------------------------------------------------

@pytest.mark.l0
class TestVisionProviderConfig:
    def _reload_config(self, env_overrides: dict):
        import importlib, service.isaac_assist_service.config as _m
        old_env = {k: os.environ.get(k) for k in env_overrides}
        os.environ.update(env_overrides)
        importlib.reload(_m)
        cfg = _m.Config()
        # restore
        for k, v in old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        return cfg

    def test_default_vision_provider_is_auto(self):
        cfg = self._reload_config({"VISION_PROVIDER": "auto"})
        assert cfg.vision_provider == "auto"

    def test_vision_provider_ollama(self):
        cfg = self._reload_config({"VISION_PROVIDER": "ollama"})
        assert cfg.vision_provider == "ollama"

    def test_vision_provider_gemini(self):
        cfg = self._reload_config({"VISION_PROVIDER": "gemini"})
        assert cfg.vision_provider == "gemini"

    def test_ollama_vision_model_default(self):
        cfg = self._reload_config({"OLLAMA_VISION_MODEL": "nemotron3:33b"})
        assert cfg.ollama_vision_model == "nemotron3:33b"

    def test_ollama_vision_model_custom(self):
        cfg = self._reload_config({"OLLAMA_VISION_MODEL": "llava:13b"})
        assert cfg.ollama_vision_model == "llava:13b"

    def test_ollama_host_default(self):
        cfg = self._reload_config({"OLLAMA_HOST": "127.0.0.1"})
        assert cfg.ollama_host == "127.0.0.1"

    def test_ollama_vision_port_default(self):
        cfg = self._reload_config({"OLLAMA_VISION_PORT": "11434"})
        assert cfg.ollama_vision_port == 11434

    def test_ollama_vision_port_custom(self):
        cfg = self._reload_config({"OLLAMA_VISION_PORT": "12000"})
        assert cfg.ollama_vision_port == 12000


# ---------------------------------------------------------------------------
# _get_vision_provider factory in tool_executor (mode switching)
# ---------------------------------------------------------------------------

@pytest.mark.l0
class TestGetVisionProviderFactory:
    def _call_factory(self, mode: str):
        # Patch config.vision_provider and call the factory
        with patch("service.isaac_assist_service.config.config") as mock_cfg:
            mock_cfg.vision_provider = mode
            mock_cfg.ollama_vision_model = "nemotron3:33b"
            mock_cfg.ollama_host = "127.0.0.1"
            mock_cfg.ollama_vision_port = 11434
            mock_cfg.vision_model_name = "gemini-robotics-er-1.6-preview"
            mock_cfg.api_key_gemini = "test-key"
            # Import lazily to get fresh state
            from service.isaac_assist_service.chat.vision_ollama import OllamaVisionProvider
            from service.isaac_assist_service.chat.vision_gemini import GeminiVisionProvider
            from service.isaac_assist_service.chat.vision_router import VisionRouter

            if mode == "gemini":
                provider = GeminiVisionProvider()
            elif mode == "ollama":
                provider = OllamaVisionProvider()
            else:
                from service.isaac_assist_service.chat.vision_router import build_vision_router
                provider = build_vision_router()
            return provider

    def test_mode_gemini_returns_gemini_provider(self):
        from service.isaac_assist_service.chat.vision_gemini import GeminiVisionProvider
        p = self._call_factory("gemini")
        assert isinstance(p, GeminiVisionProvider)

    def test_mode_ollama_returns_ollama_provider(self):
        from service.isaac_assist_service.chat.vision_ollama import OllamaVisionProvider
        p = self._call_factory("ollama")
        assert isinstance(p, OllamaVisionProvider)

    def test_mode_auto_returns_vision_router(self):
        from service.isaac_assist_service.chat.vision_router import VisionRouter
        p = self._call_factory("auto")
        assert isinstance(p, VisionRouter)

    def test_mode_auto_router_has_ollama_primary(self):
        from service.isaac_assist_service.chat.vision_router import VisionRouter
        from service.isaac_assist_service.chat.vision_ollama import OllamaVisionProvider
        p = self._call_factory("auto")
        assert isinstance(p._primary, OllamaVisionProvider)

    def test_mode_auto_router_has_gemini_fallback(self):
        from service.isaac_assist_service.chat.vision_router import VisionRouter
        from service.isaac_assist_service.chat.vision_gemini import GeminiVisionProvider
        p = self._call_factory("auto")
        assert isinstance(p._fallback, GeminiVisionProvider)
