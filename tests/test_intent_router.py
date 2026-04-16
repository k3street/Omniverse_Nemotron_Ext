"""
L0 tests for the intent router.
Tests intent classification logic and routing constants.
"""
import json
import pytest

pytestmark = pytest.mark.l0

from service.isaac_assist_service.chat.intent_router import (
    Intent,
    INTENT_EXAMPLES,
    INTENT_SYSTEM,
    classify_intent,
)


class TestIntentConstants:

    def test_intent_system_prompt_is_nonempty(self):
        assert len(INTENT_SYSTEM) > 100

    def test_intent_system_lists_all_intents(self):
        expected_intents = [
            "general_query",
            "scene_diagnose",
            "vision_inspect",
            "prim_inspect",
            "patch_request",
            "physics_query",
            "console_review",
            "navigation",
        ]
        for intent in expected_intents:
            assert intent in INTENT_SYSTEM, f"Intent '{intent}' not in INTENT_SYSTEM"

    def test_examples_cover_all_intents(self):
        covered = {label for _, label in INTENT_EXAMPLES}
        expected = {
            "general_query",
            "scene_diagnose",
            "vision_inspect",
            "prim_inspect",
            "patch_request",
            "physics_query",
            "console_review",
            "navigation",
        }
        missing = expected - covered
        assert missing == set(), f"INTENT_EXAMPLES missing coverage for: {missing}"

    def test_examples_are_tuples_of_str(self):
        for example in INTENT_EXAMPLES:
            assert isinstance(example, tuple)
            assert len(example) == 2
            assert isinstance(example[0], str)
            assert isinstance(example[1], str)


class TestClassifyIntent:
    """Test classify_intent with a mock LLM provider."""

    @pytest.mark.asyncio
    async def test_returns_valid_intent(self, mock_llm_provider, fake_llm_response):
        mock_llm_provider.responses = [
            fake_llm_response(text='{"intent": "patch_request", "confidence": 0.95}')
        ]
        intent = await classify_intent("fix the joint damping", mock_llm_provider)
        assert intent == "patch_request"

    @pytest.mark.asyncio
    async def test_fallback_on_parse_error(self, mock_llm_provider, fake_llm_response):
        mock_llm_provider.responses = [
            fake_llm_response(text="not valid json")
        ]
        intent = await classify_intent("hello", mock_llm_provider)
        assert intent == "general_query"

    @pytest.mark.asyncio
    async def test_fallback_on_exception(self, mock_llm_provider):
        async def fail_complete(messages, options):
            raise RuntimeError("LLM unreachable")

        mock_llm_provider.complete = fail_complete
        intent = await classify_intent("test message", mock_llm_provider)
        assert intent == "general_query"

    @pytest.mark.asyncio
    async def test_strips_markdown_fences(self, mock_llm_provider, fake_llm_response):
        mock_llm_provider.responses = [
            fake_llm_response(text='```json\n{"intent": "scene_diagnose", "confidence": 0.8}\n```')
        ]
        intent = await classify_intent("why is my robot floating?", mock_llm_provider)
        assert intent == "scene_diagnose"

    @pytest.mark.asyncio
    async def test_restores_system_override(self, mock_llm_provider, fake_llm_response):
        """classify_intent sets _system_override temporarily; it must restore it."""
        mock_llm_provider._system_override = "original_system"
        mock_llm_provider.responses = [
            fake_llm_response(text='{"intent": "general_query"}')
        ]
        await classify_intent("test", mock_llm_provider)
        assert mock_llm_provider._system_override == "original_system"

    @pytest.mark.asyncio
    async def test_cleans_up_system_override_on_error(self, mock_llm_provider):
        async def fail(messages, options):
            raise RuntimeError("boom")

        mock_llm_provider.complete = fail
        await classify_intent("test", mock_llm_provider)
        assert not hasattr(mock_llm_provider, "_system_override") or \
               mock_llm_provider._system_override is None
