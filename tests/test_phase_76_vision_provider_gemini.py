"""Phase 76 — Vision SPEC/PROVIDER layer tests.

Gates:
- provider abstraction (dataclasses, Protocol shape)
- PROMPT_TEMPLATES coverage
- GeminiVisionProvider: instantiable, helpers, raises NotImplementedError
- MockVisionProvider: all four task responses well-shaped
- select_provider factory
- BoundingBox dataclass fields exposed

Per specs/IA_FULL_SPEC_2026-05-10.md Phase 76.
"""
from __future__ import annotations

import math

import pytest

pytestmark = pytest.mark.l0

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_request(task="scene_analyze"):
    from service.isaac_assist_service.multimodal.vision_provider_gemini import VisionRequest
    return VisionRequest(image_bytes=b"\x89PNG", prompt="describe the scene", task=task)


# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------

class TestMetadata:
    def test_phase_id(self):
        from service.isaac_assist_service.multimodal.vision_provider_gemini import get_phase_metadata
        md = get_phase_metadata()
        assert md["phase"] == 76

    def test_status_landed(self):
        from service.isaac_assist_service.multimodal.vision_provider_gemini import get_phase_metadata
        md = get_phase_metadata()
        assert md["status"] == "landed"

    def test_spec_ref_present(self):
        from service.isaac_assist_service.multimodal.vision_provider_gemini import get_phase_metadata
        md = get_phase_metadata()
        assert "spec_ref" in md


# ---------------------------------------------------------------------------
# PROMPT_TEMPLATES
# ---------------------------------------------------------------------------

class TestPromptTemplates:
    def test_four_entries(self):
        from service.isaac_assist_service.multimodal.vision_provider_gemini import PROMPT_TEMPLATES
        assert len(PROMPT_TEMPLATES) == 4

    def test_all_four_task_keys_present(self):
        from service.isaac_assist_service.multimodal.vision_provider_gemini import PROMPT_TEMPLATES
        for key in ("scene_analyze", "bounding_boxes", "detect_objects", "plan_trajectory"):
            assert key in PROMPT_TEMPLATES, f"missing key: {key}"

    def test_templates_contain_user_prompt_placeholder(self):
        from service.isaac_assist_service.multimodal.vision_provider_gemini import PROMPT_TEMPLATES
        for key, tpl in PROMPT_TEMPLATES.items():
            assert "{user_prompt}" in tpl, f"{key} template missing {{user_prompt}}"


# ---------------------------------------------------------------------------
# BoundingBox dataclass
# ---------------------------------------------------------------------------

class TestBoundingBox:
    def test_fields_exposed(self):
        from service.isaac_assist_service.multimodal.vision_provider_gemini import BoundingBox
        bb = BoundingBox(label="cube", confidence=0.9, x_min=0.1, y_min=0.2, x_max=0.3, y_max=0.4)
        assert bb.label == "cube"
        assert bb.confidence == pytest.approx(0.9)
        assert bb.x_min == pytest.approx(0.1)
        assert bb.y_min == pytest.approx(0.2)
        assert bb.x_max == pytest.approx(0.3)
        assert bb.y_max == pytest.approx(0.4)


# ---------------------------------------------------------------------------
# VisionRequest + VisionResponse dataclasses
# ---------------------------------------------------------------------------

class TestDataclasses:
    def test_vision_request_defaults(self):
        from service.isaac_assist_service.multimodal.vision_provider_gemini import VisionRequest
        req = VisionRequest(image_bytes=b"img", prompt="test", task="scene_analyze")
        assert req.max_tokens == 1024
        assert req.temperature == pytest.approx(0.3)
        assert req.image_mime == "image/png"

    def test_vision_response_defaults(self):
        from service.isaac_assist_service.multimodal.vision_provider_gemini import VisionResponse
        resp = VisionResponse(task="scene_analyze")
        assert resp.text is None
        assert resp.bounding_boxes == []
        assert resp.tokens_used == 0
        assert resp.error is None


# ---------------------------------------------------------------------------
# GeminiVisionProvider — instantiation + helpers
# ---------------------------------------------------------------------------

class TestGeminiVisionProviderInit:
    def test_instantiates_without_api_key(self):
        from service.isaac_assist_service.multimodal.vision_provider_gemini import GeminiVisionProvider
        provider = GeminiVisionProvider()
        assert provider.model == "gemini-2.0-flash-exp"
        assert provider.max_retries == 3
        assert provider.api_key is None

    def test_custom_model_stored(self):
        from service.isaac_assist_service.multimodal.vision_provider_gemini import GeminiVisionProvider
        provider = GeminiVisionProvider(model="gemini-1.5-pro", api_key="key123")
        assert provider.model == "gemini-1.5-pro"
        assert provider.api_key == "key123"

    def test_build_url_contains_model(self):
        from service.isaac_assist_service.multimodal.vision_provider_gemini import GeminiVisionProvider
        provider = GeminiVisionProvider(model="gemini-2.0-flash-exp")
        url = provider._build_url("scene_analyze")
        assert "gemini-2.0-flash-exp" in url

    def test_build_url_contains_task_path(self):
        from service.isaac_assist_service.multimodal.vision_provider_gemini import GeminiVisionProvider
        provider = GeminiVisionProvider()
        url = provider._build_url("bounding_boxes")
        assert "generateContent" in url

    def test_canonical_prompt_prepends_system(self):
        from service.isaac_assist_service.multimodal.vision_provider_gemini import GeminiVisionProvider
        provider = GeminiVisionProvider()
        prompt = provider._canonical_prompt_for("scene_analyze", "what do you see?")
        assert "System:" in prompt
        assert "what do you see?" in prompt

    def test_canonical_prompt_for_all_tasks(self):
        from service.isaac_assist_service.multimodal.vision_provider_gemini import GeminiVisionProvider
        provider = GeminiVisionProvider()
        for task in ("scene_analyze", "bounding_boxes", "detect_objects", "plan_trajectory"):
            result = provider._canonical_prompt_for(task, "test")
            assert "System:" in result, f"{task} missing System prefix"

    def test_retry_delays_exponential_growth(self):
        from service.isaac_assist_service.multimodal.vision_provider_gemini import GeminiVisionProvider
        provider = GeminiVisionProvider()
        d0 = provider._retry_delays(0)
        d1 = provider._retry_delays(1)
        d2 = provider._retry_delays(2)
        assert d1 == pytest.approx(d0 * 2, rel=1e-6)
        assert d2 == pytest.approx(d0 * 4, rel=1e-6)

    def test_retry_delays_base_value(self):
        from service.isaac_assist_service.multimodal.vision_provider_gemini import GeminiVisionProvider
        provider = GeminiVisionProvider()
        assert provider._retry_delays(0) == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# GeminiVisionProvider — raises NotImplementedError for live calls
# ---------------------------------------------------------------------------

class TestGeminiVisionProviderRaises:
    def test_analyze_scene_raises(self):
        from service.isaac_assist_service.multimodal.vision_provider_gemini import GeminiVisionProvider
        provider = GeminiVisionProvider()
        with pytest.raises(NotImplementedError, match="GEMINI_API_KEY"):
            provider.analyze_scene(_make_request("scene_analyze"))

    def test_detect_objects_raises(self):
        from service.isaac_assist_service.multimodal.vision_provider_gemini import GeminiVisionProvider
        provider = GeminiVisionProvider()
        with pytest.raises(NotImplementedError):
            provider.detect_objects(_make_request("detect_objects"))

    def test_bounding_boxes_raises(self):
        from service.isaac_assist_service.multimodal.vision_provider_gemini import GeminiVisionProvider
        provider = GeminiVisionProvider()
        with pytest.raises(NotImplementedError):
            provider.bounding_boxes(_make_request("bounding_boxes"))

    def test_plan_trajectory_raises(self):
        from service.isaac_assist_service.multimodal.vision_provider_gemini import GeminiVisionProvider
        provider = GeminiVisionProvider()
        with pytest.raises(NotImplementedError):
            provider.plan_trajectory(_make_request("plan_trajectory"))


# ---------------------------------------------------------------------------
# MockVisionProvider
# ---------------------------------------------------------------------------

class TestMockVisionProvider:
    def test_analyze_scene_returns_text(self):
        from service.isaac_assist_service.multimodal.vision_provider_gemini import MockVisionProvider
        p = MockVisionProvider()
        resp = p.analyze_scene(_make_request("scene_analyze"))
        assert resp.text is not None
        assert len(resp.text) > 0
        assert resp.task == "scene_analyze"

    def test_analyze_scene_mock_metadata(self):
        from service.isaac_assist_service.multimodal.vision_provider_gemini import MockVisionProvider
        p = MockVisionProvider()
        resp = p.analyze_scene(_make_request("scene_analyze"))
        assert resp.tokens_used == 50
        assert resp.latency_ms == pytest.approx(12.5)
        assert resp.model == "mock-vision"

    def test_bounding_boxes_returns_nonempty_list(self):
        from service.isaac_assist_service.multimodal.vision_provider_gemini import MockVisionProvider, BoundingBox
        p = MockVisionProvider()
        resp = p.bounding_boxes(_make_request("bounding_boxes"))
        assert len(resp.bounding_boxes) > 0
        assert all(isinstance(bb, BoundingBox) for bb in resp.bounding_boxes)

    def test_bounding_boxes_confidence_in_range(self):
        from service.isaac_assist_service.multimodal.vision_provider_gemini import MockVisionProvider
        p = MockVisionProvider()
        resp = p.bounding_boxes(_make_request("bounding_boxes"))
        for bb in resp.bounding_boxes:
            assert 0.0 <= bb.confidence <= 1.0

    def test_detect_objects_returns_text_and_boxes(self):
        from service.isaac_assist_service.multimodal.vision_provider_gemini import MockVisionProvider
        p = MockVisionProvider()
        resp = p.detect_objects(_make_request("detect_objects"))
        assert resp.text is not None
        assert len(resp.bounding_boxes) > 0
        assert resp.task == "detect_objects"

    def test_plan_trajectory_returns_waypoints_text(self):
        from service.isaac_assist_service.multimodal.vision_provider_gemini import MockVisionProvider
        p = MockVisionProvider()
        resp = p.plan_trajectory(_make_request("plan_trajectory"))
        assert resp.text is not None
        assert "waypoint" in resp.text.lower()
        assert resp.task == "plan_trajectory"

    def test_plan_trajectory_no_error(self):
        from service.isaac_assist_service.multimodal.vision_provider_gemini import MockVisionProvider
        p = MockVisionProvider()
        resp = p.plan_trajectory(_make_request("plan_trajectory"))
        assert resp.error is None


# ---------------------------------------------------------------------------
# select_provider factory
# ---------------------------------------------------------------------------

class TestSelectProvider:
    def test_use_real_false_returns_mock(self):
        from service.isaac_assist_service.multimodal.vision_provider_gemini import (
            select_provider, MockVisionProvider,
        )
        provider = select_provider(use_real=False)
        assert isinstance(provider, MockVisionProvider)

    def test_use_real_true_returns_gemini(self):
        from service.isaac_assist_service.multimodal.vision_provider_gemini import (
            select_provider, GeminiVisionProvider,
        )
        provider = select_provider(use_real=True)
        assert isinstance(provider, GeminiVisionProvider)

    def test_select_provider_passes_api_key(self):
        from service.isaac_assist_service.multimodal.vision_provider_gemini import (
            select_provider, GeminiVisionProvider,
        )
        provider = select_provider(use_real=True, api_key="test-key")
        assert isinstance(provider, GeminiVisionProvider)
        assert provider.api_key == "test-key"

    def test_default_is_mock(self):
        from service.isaac_assist_service.multimodal.vision_provider_gemini import (
            select_provider, MockVisionProvider,
        )
        provider = select_provider()
        assert isinstance(provider, MockVisionProvider)
