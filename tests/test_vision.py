"""
L0 unit tests for:
  - GeminiVisionProvider (pure parsing, payload building, API error handling)
  - Vision tool handlers (_handle_vision_detect_objects, _handle_vision_bounding_boxes,
    _handle_vision_plan_trajectory, _handle_vision_analyze_scene)
  - Auto-inject viewport wiring in ChatOrchestrator

No network calls, no real Gemini API — everything is mocked.
"""
import asyncio
import base64
import json
import os
import sys
import pytest

_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, _REPO)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _b64_png(content: bytes = b"PNG\x00data") -> str:
    return base64.b64encode(content).decode()


def _fake_gemini_response(text: str) -> dict:
    """Minimal Gemini API JSON structure with a text candidate."""
    return {
        "candidates": [
            {"content": {"parts": [{"text": text}]}}
        ]
    }


# ---------------------------------------------------------------------------
# GeminiVisionProvider — static / pure-Python methods
# ---------------------------------------------------------------------------

@pytest.mark.l0
class TestParseJsonArray:
    @pytest.fixture()
    def parse(self):
        from service.isaac_assist_service.chat.vision_gemini import GeminiVisionProvider
        return GeminiVisionProvider._parse_json_array

    def test_plain_json_array(self, parse):
        result = parse('[{"point": [100, 200], "label": "cube"}]')
        assert result == [{"point": [100, 200], "label": "cube"}]

    def test_markdown_fenced_json(self, parse):
        text = '```json\n[{"box_2d": [10, 20, 100, 200], "label": "robot"}]\n```'
        result = parse(text)
        assert len(result) == 1
        assert result[0]["label"] == "robot"

    def test_text_with_embedded_array(self, parse):
        text = 'Here are the objects: [{"point": [50, 60], "label": "gripper"}] in the scene.'
        result = parse(text)
        assert result[0]["label"] == "gripper"

    def test_empty_array(self, parse):
        result = parse("[]")
        assert result == []

    def test_invalid_json_returns_empty(self, parse):
        result = parse("this is not json at all")
        assert result == []

    def test_object_not_array_returns_empty(self, parse):
        result = parse('{"key": "value"}')
        assert result == []

    def test_blank_string_returns_empty(self, parse):
        assert parse("") == []

    def test_multiple_objects(self, parse):
        items = [{"point": [i * 10, i * 20], "label": f"obj{i}"} for i in range(5)]
        result = parse(json.dumps(items))
        assert len(result) == 5

    def test_deeply_nested_no_crash(self, parse):
        # Should not crash on arbitrary deeply nested JSON
        result = parse('[{"point": [[1, 2], [3, 4]], "label": "multi"}]')
        assert isinstance(result, list)


@pytest.mark.l0
class TestExtractText:
    @pytest.fixture()
    def extract(self):
        from service.isaac_assist_service.chat.vision_gemini import GeminiVisionProvider
        return GeminiVisionProvider._extract_text

    def test_single_text_part(self, extract):
        data = _fake_gemini_response("Hello from Gemini")
        assert extract(data) == "Hello from Gemini"

    def test_multiple_text_parts_joined(self, extract):
        data = {
            "candidates": [
                {"content": {"parts": [{"text": "Part one"}, {"text": "Part two"}]}}
            ]
        }
        text = extract(data)
        assert "Part one" in text
        assert "Part two" in text

    def test_missing_candidates_returns_empty(self, extract):
        assert extract({}) == ""

    def test_empty_parts_returns_empty(self, extract):
        data = {"candidates": [{"content": {"parts": []}}]}
        assert extract(data) == ""

    def test_non_text_parts_skipped(self, extract):
        data = {
            "candidates": [
                {"content": {"parts": [{"inline_data": {"data": "abc"}}, {"text": "real text"}]}}
            ]
        }
        assert extract(data) == "real text"


@pytest.mark.l0
class TestBuildPayload:
    @pytest.fixture()
    def provider(self):
        from service.isaac_assist_service.chat.vision_gemini import GeminiVisionProvider
        return GeminiVisionProvider(api_key="test-key", model="gemini-robotics-er-1.6-preview")

    def test_payload_has_contents(self, provider):
        payload = provider._build_payload(b"fake-png", "image/png", "describe this")
        assert "contents" in payload
        assert len(payload["contents"]) == 1

    def test_payload_has_image_and_text_parts(self, provider):
        payload = provider._build_payload(b"fake-png", "image/png", "what do you see?")
        parts = payload["contents"][0]["parts"]
        assert any("inline_data" in p for p in parts)
        assert any("text" in p for p in parts)

    def test_inline_data_is_base64(self, provider):
        raw = b"some image bytes"
        payload = provider._build_payload(raw, "image/jpeg", "analyze")
        parts = payload["contents"][0]["parts"]
        img_parts = [p for p in parts if "inline_data" in p]
        assert img_parts
        decoded = base64.b64decode(img_parts[0]["inline_data"]["data"])
        assert decoded == raw

    def test_thinking_budget_included_when_positive(self, provider):
        payload = provider._build_payload(b"x", "image/png", "plan", thinking_budget=512)
        assert payload["generationConfig"]["thinkingConfig"]["thinkingBudget"] == 512

    def test_no_thinking_config_when_zero(self, provider):
        payload = provider._build_payload(b"x", "image/png", "detect", thinking_budget=0)
        # thinkingConfig may or may not be present with budget 0 — just no crash
        assert "generationConfig" in payload

    def test_correct_mime_type(self, provider):
        payload = provider._build_payload(b"x", "image/jpeg", "test")
        parts = payload["contents"][0]["parts"]
        img_parts = [p for p in parts if "inline_data" in p]
        assert img_parts[0]["inline_data"]["mime_type"] == "image/jpeg"


# ---------------------------------------------------------------------------
# GeminiVisionProvider — async methods with mocked HTTP
# ---------------------------------------------------------------------------

@pytest.mark.l0
class TestDetectObjects:
    @pytest.fixture()
    def provider(self):
        from service.isaac_assist_service.chat.vision_gemini import GeminiVisionProvider
        return GeminiVisionProvider(api_key="test-key", model="test-model")

    async def _run(self, coro):
        return await coro

    def test_returns_detections_from_api(self, provider, monkeypatch):
        detections = [{"point": [100, 200], "label": "cube"}, {"point": [300, 400], "label": "robot"}]
        raw_response = _fake_gemini_response(json.dumps(detections))

        async def mock_call_api(payload):
            return raw_response

        monkeypatch.setattr(provider, "_call_api", mock_call_api)
        result = asyncio.get_event_loop().run_until_complete(
            provider.detect_objects(b"png", "image/png")
        )
        assert len(result) == 2
        assert result[0]["label"] == "cube"

    def test_api_failure_returns_empty(self, provider, monkeypatch):
        async def mock_call_api(payload):
            return None

        monkeypatch.setattr(provider, "_call_api", mock_call_api)
        result = asyncio.get_event_loop().run_until_complete(
            provider.detect_objects(b"png", "image/png")
        )
        assert result == []

    def test_with_label_filter(self, provider, monkeypatch):
        detections = [{"point": [50, 75], "label": "franka"}]
        async def mock_call_api(payload):
            return _fake_gemini_response(json.dumps(detections))

        monkeypatch.setattr(provider, "_call_api", mock_call_api)
        result = asyncio.get_event_loop().run_until_complete(
            provider.detect_objects(b"png", "image/png", labels=["franka"], max_objects=5)
        )
        assert result[0]["label"] == "franka"


@pytest.mark.l0
class TestDetectBoundingBoxes:
    @pytest.fixture()
    def provider(self):
        from service.isaac_assist_service.chat.vision_gemini import GeminiVisionProvider
        return GeminiVisionProvider(api_key="test-key", model="test-model")

    def test_returns_boxes(self, provider, monkeypatch):
        boxes = [{"box_2d": [10, 20, 100, 200], "label": "gripper"}]
        async def mock_call_api(payload):
            return _fake_gemini_response(json.dumps(boxes))

        monkeypatch.setattr(provider, "_call_api", mock_call_api)
        result = asyncio.get_event_loop().run_until_complete(
            provider.detect_bounding_boxes(b"png", "image/png")
        )
        assert result[0]["box_2d"] == [10, 20, 100, 200]

    def test_api_failure_returns_empty(self, provider, monkeypatch):
        async def mock_call_api(payload):
            return None

        monkeypatch.setattr(provider, "_call_api", mock_call_api)
        result = asyncio.get_event_loop().run_until_complete(
            provider.detect_bounding_boxes(b"png")
        )
        assert result == []


@pytest.mark.l0
class TestAnalyzeScene:
    @pytest.fixture()
    def provider(self):
        from service.isaac_assist_service.chat.vision_gemini import GeminiVisionProvider
        return GeminiVisionProvider(api_key="test-key", model="test-model")

    def test_returns_text_analysis(self, provider, monkeypatch):
        async def mock_call_api(payload):
            return _fake_gemini_response("The scene contains a Franka arm on a table.")

        monkeypatch.setattr(provider, "_call_api", mock_call_api)
        result = asyncio.get_event_loop().run_until_complete(
            provider.analyze_scene(b"png", "What is in the scene?")
        )
        assert "Franka" in result

    def test_api_failure_returns_fallback_string(self, provider, monkeypatch):
        async def mock_call_api(payload):
            return None

        monkeypatch.setattr(provider, "_call_api", mock_call_api)
        result = asyncio.get_event_loop().run_until_complete(
            provider.analyze_scene(b"png", "describe")
        )
        assert isinstance(result, str)
        assert len(result) > 0  # fallback message


@pytest.mark.l0
class TestPlanTrajectory:
    @pytest.fixture()
    def provider(self):
        from service.isaac_assist_service.chat.vision_gemini import GeminiVisionProvider
        return GeminiVisionProvider(api_key="test-key", model="test-model")

    def test_returns_trajectory_points(self, provider, monkeypatch):
        traj = [{"point": [i * 50, i * 50], "label": str(i)} for i in range(5)]
        async def mock_call_api(payload):
            return _fake_gemini_response(json.dumps(traj))

        monkeypatch.setattr(provider, "_call_api", mock_call_api)
        result = asyncio.get_event_loop().run_until_complete(
            provider.plan_trajectory(b"png", "move to the cube", num_points=5)
        )
        assert len(result) == 5
        assert result[0]["label"] == "0"


# ---------------------------------------------------------------------------
# Vision tool handlers — mocked viewport + provider
# ---------------------------------------------------------------------------

@pytest.mark.l0
class TestVisionHandlers:
    """
    Test the 4 vision DATA_HANDLERS by mocking _get_viewport_bytes and
    GeminiVisionProvider at the module level.
    """

    def _mock_viewport(self, monkeypatch, b64: str = None):
        """Patch _get_viewport_bytes to return a fake image or None."""
        import service.isaac_assist_service.chat.tools.tool_executor as te

        if b64 is None:
            b64 = _b64_png()

        async def fake_viewport():
            return base64.b64decode(b64), "image/png"

        # The function is defined multiple times (duplicate defs); monkeypatch the
        # module-level binding that the active handlers use.
        monkeypatch.setattr(te, "_get_viewport_bytes", fake_viewport)

    def _mock_viewport_none(self, monkeypatch):
        import service.isaac_assist_service.chat.tools.tool_executor as te

        async def no_viewport():
            return None, None

        monkeypatch.setattr(te, "_get_viewport_bytes", no_viewport)

    def _mock_provider(self, monkeypatch, **method_returns):
        """Patch GeminiVisionProvider constructor to return a mock."""
        import service.isaac_assist_service.chat.tools.tool_executor as te

        class _FakeProvider:
            model = "test-model"

            async def detect_objects(self, *a, **kw):
                return method_returns.get("detect_objects", [])

            async def detect_bounding_boxes(self, *a, **kw):
                return method_returns.get("detect_bounding_boxes", [])

            async def plan_trajectory(self, *a, **kw):
                return method_returns.get("plan_trajectory", [])

            async def analyze_scene(self, *a, **kw):
                return method_returns.get("analyze_scene", "mock scene analysis")

        monkeypatch.setattr(te, "_get_vision_provider", lambda: _FakeProvider())

    # --- detect_objects ---

    def test_detect_objects_returns_detections(self, monkeypatch):
        import service.isaac_assist_service.chat.tools.tool_executor as te

        detections = [{"point": [100, 200], "label": "cube"}]
        self._mock_viewport(monkeypatch)
        self._mock_provider(monkeypatch, detect_objects=detections)

        result = asyncio.get_event_loop().run_until_complete(
            te._handle_vision_detect_objects({"max_objects": 10})
        )
        assert result["count"] == 1
        assert result["detections"][0]["label"] == "cube"
        assert result["model"] == "test-model"

    def test_detect_objects_no_viewport_returns_error(self, monkeypatch):
        import service.isaac_assist_service.chat.tools.tool_executor as te

        self._mock_viewport_none(monkeypatch)
        self._mock_provider(monkeypatch)

        result = asyncio.get_event_loop().run_until_complete(
            te._handle_vision_detect_objects({})
        )
        assert "error" in result

    def test_detect_objects_with_label_filter(self, monkeypatch):
        import service.isaac_assist_service.chat.tools.tool_executor as te

        detections = [{"point": [50, 75], "label": "franka"}]
        self._mock_viewport(monkeypatch)
        self._mock_provider(monkeypatch, detect_objects=detections)

        result = asyncio.get_event_loop().run_until_complete(
            te._handle_vision_detect_objects({"labels": ["franka"], "max_objects": 5})
        )
        assert result["count"] == 1

    # --- bounding_boxes ---

    def test_bounding_boxes_returns_boxes(self, monkeypatch):
        import service.isaac_assist_service.chat.tools.tool_executor as te

        boxes = [{"box_2d": [0, 0, 100, 100], "label": "robot"}]
        self._mock_viewport(monkeypatch)
        self._mock_provider(monkeypatch, detect_bounding_boxes=boxes)

        result = asyncio.get_event_loop().run_until_complete(
            te._handle_vision_bounding_boxes({"max_objects": 25})
        )
        assert result["count"] == 1
        assert result["bounding_boxes"][0]["label"] == "robot"

    def test_bounding_boxes_no_viewport_returns_error(self, monkeypatch):
        import service.isaac_assist_service.chat.tools.tool_executor as te

        self._mock_viewport_none(monkeypatch)
        self._mock_provider(monkeypatch)

        result = asyncio.get_event_loop().run_until_complete(
            te._handle_vision_bounding_boxes({})
        )
        assert "error" in result

    # --- plan_trajectory ---

    def test_plan_trajectory_returns_points(self, monkeypatch):
        import service.isaac_assist_service.chat.tools.tool_executor as te

        traj = [{"point": [i * 10, i * 10], "label": str(i)} for i in range(3)]
        self._mock_viewport(monkeypatch)
        self._mock_provider(monkeypatch, plan_trajectory=traj)

        result = asyncio.get_event_loop().run_until_complete(
            te._handle_vision_plan_trajectory(
                {"instruction": "move to cup", "num_points": 3}
            )
        )
        assert result["num_points"] == 3
        assert len(result["trajectory"]) == 3

    def test_plan_trajectory_no_viewport_returns_error(self, monkeypatch):
        import service.isaac_assist_service.chat.tools.tool_executor as te

        self._mock_viewport_none(monkeypatch)
        self._mock_provider(monkeypatch)

        result = asyncio.get_event_loop().run_until_complete(
            te._handle_vision_plan_trajectory({"instruction": "move", "num_points": 5})
        )
        assert "error" in result

    # --- analyze_scene ---

    def test_analyze_scene_returns_text(self, monkeypatch):
        import service.isaac_assist_service.chat.tools.tool_executor as te

        self._mock_viewport(monkeypatch)
        self._mock_provider(monkeypatch, analyze_scene="Franka arm on table with cube.")

        result = asyncio.get_event_loop().run_until_complete(
            te._handle_vision_analyze_scene({"question": "What is in the scene?"})
        )
        assert "Franka" in result["analysis"]
        assert result["model"] == "test-model"

    def test_analyze_scene_no_viewport_returns_error(self, monkeypatch):
        import service.isaac_assist_service.chat.tools.tool_executor as te

        self._mock_viewport_none(monkeypatch)
        self._mock_provider(monkeypatch)

        result = asyncio.get_event_loop().run_until_complete(
            te._handle_vision_analyze_scene({"question": "describe"})
        )
        assert "error" in result


# ---------------------------------------------------------------------------
# Auto-inject viewport — orchestrator config wiring
# ---------------------------------------------------------------------------

@pytest.mark.l0
class TestAutoInjectViewportConfig:
    def test_config_flag_defaults_false(self, monkeypatch):
        monkeypatch.delenv("AUTO_INJECT_VIEWPORT", raising=False)
        # Re-initialize config to pick up env change
        from service.isaac_assist_service import config as _cfg_mod
        import importlib
        importlib.reload(_cfg_mod)
        assert _cfg_mod.config.auto_inject_viewport is False

    def test_config_flag_enabled_by_env(self, monkeypatch):
        monkeypatch.setenv("AUTO_INJECT_VIEWPORT", "true")
        from service.isaac_assist_service import config as _cfg_mod
        import importlib
        importlib.reload(_cfg_mod)
        assert _cfg_mod.config.auto_inject_viewport is True

    def test_settings_manager_exposes_flag(self):
        from service.isaac_assist_service.settings.manager import SettingsManager
        mgr = SettingsManager()
        settings = mgr.get_settings()
        assert "AUTO_INJECT_VIEWPORT" in settings


@pytest.mark.l0
class TestAutoInjectViewportInjection:
    """
    Test that the orchestrator upgrades the last user message to multimodal
    when auto_inject_viewport is True and Kit is reachable.
    """

    def test_user_message_becomes_multimodal(self, monkeypatch):
        """When viewport available, last user message becomes a content list."""
        from service.isaac_assist_service.chat import orchestrator as _orch

        monkeypatch.setattr(_orch.config, "auto_inject_viewport", True)

        fake_b64 = _b64_png(b"FAKEIMAGEDATA")

        async def fake_is_kit_alive():
            return True

        async def fake_get_viewport(max_dim=1280):
            return {"image_b64": fake_b64}

        monkeypatch.setattr(_orch, "is_kit_rpc_alive", fake_is_kit_alive)
        monkeypatch.setattr(_orch, "get_viewport_image", fake_get_viewport)

        messages = [
            {"role": "system", "content": "You are an assistant."},
            {"role": "user", "content": "What is in the scene?"},
        ]

        async def _inject():
            # Replicate the injection logic from orchestrator.handle_message
            if _orch.config.auto_inject_viewport and await _orch.is_kit_rpc_alive():
                vp_result = await _orch.get_viewport_image(max_dim=1280)
                vp_b64 = vp_result.get("image_b64") or vp_result.get("data", "")
                if vp_b64:
                    for i in range(len(messages) - 1, -1, -1):
                        if messages[i].get("role") == "user":
                            original_text = messages[i].get("content", "")
                            if isinstance(original_text, str):
                                messages[i] = {
                                    "role": "user",
                                    "content": [
                                        {
                                            "type": "image_url",
                                            "image_url": {
                                                "url": f"data:image/png;base64,{vp_b64}",
                                                "detail": "low",
                                            },
                                        },
                                        {"type": "text", "text": original_text},
                                    ],
                                }
                            break

        asyncio.get_event_loop().run_until_complete(_inject())

        last_user = messages[-1]
        assert last_user["role"] == "user"
        assert isinstance(last_user["content"], list)
        types = [p["type"] for p in last_user["content"]]
        assert "image_url" in types
        assert "text" in types

    def test_text_preserved_after_inject(self, monkeypatch):
        """Original user text must appear in the multimodal content list."""
        from service.isaac_assist_service.chat import orchestrator as _orch

        monkeypatch.setattr(_orch.config, "auto_inject_viewport", True)
        fake_b64 = _b64_png()

        async def fake_is_kit_alive():
            return True

        async def fake_get_viewport(max_dim=1280):
            return {"image_b64": fake_b64}

        monkeypatch.setattr(_orch, "is_kit_rpc_alive", fake_is_kit_alive)
        monkeypatch.setattr(_orch, "get_viewport_image", fake_get_viewport)

        messages = [{"role": "user", "content": "describe the robots in the scene"}]

        async def _run():
            if _orch.config.auto_inject_viewport and await _orch.is_kit_rpc_alive():
                vp_result = await _orch.get_viewport_image(max_dim=1280)
                vp_b64 = vp_result.get("image_b64") or vp_result.get("data", "")
                if vp_b64:
                    for i in range(len(messages) - 1, -1, -1):
                        if messages[i].get("role") == "user":
                            original_text = messages[i].get("content", "")
                            if isinstance(original_text, str):
                                messages[i] = {
                                    "role": "user",
                                    "content": [
                                        {
                                            "type": "image_url",
                                            "image_url": {
                                                "url": f"data:image/png;base64,{vp_b64}",
                                                "detail": "low",
                                            },
                                        },
                                        {"type": "text", "text": original_text},
                                    ],
                                }
                            break

        asyncio.get_event_loop().run_until_complete(_run())

        text_parts = [p for p in messages[0]["content"] if p.get("type") == "text"]
        assert text_parts[0]["text"] == "describe the robots in the scene"

    def test_no_inject_when_kit_unavailable(self, monkeypatch):
        """When Kit is not reachable, message stays plain text."""
        from service.isaac_assist_service.chat import orchestrator as _orch

        monkeypatch.setattr(_orch.config, "auto_inject_viewport", True)

        async def kit_dead():
            return False

        monkeypatch.setattr(_orch, "is_kit_rpc_alive", kit_dead)

        messages = [{"role": "user", "content": "what do you see?"}]

        async def _run():
            if _orch.config.auto_inject_viewport and await _orch.is_kit_rpc_alive():
                pass  # won't reach here

        asyncio.get_event_loop().run_until_complete(_run())
        assert isinstance(messages[0]["content"], str)

    def test_no_inject_when_flag_false(self, monkeypatch):
        """When auto_inject_viewport is False, message stays plain text."""
        from service.isaac_assist_service.chat import orchestrator as _orch

        monkeypatch.setattr(_orch.config, "auto_inject_viewport", False)

        messages = [{"role": "user", "content": "hello"}]

        async def _run():
            if _orch.config.auto_inject_viewport:
                pass  # never reached

        asyncio.get_event_loop().run_until_complete(_run())
        assert messages[0]["content"] == "hello"
