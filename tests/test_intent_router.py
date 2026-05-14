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

    def test_examples_cover_key_intents(self):
        """After 2026-04-19, examples are 4-tuples (message, intent, multi_step,
        complexity) and cover a representative sample — not every intent needs
        an example since vision_inspect/prim_inspect etc are in the system prompt."""
        covered = {label for _, label, _, _ in INTENT_EXAMPLES}
        # At minimum: the intents the orchestrator branches on.
        required = {"general_query", "patch_request", "scene_diagnose",
                    "navigation", "console_review"}
        missing = required - covered
        assert missing == set(), f"INTENT_EXAMPLES missing: {missing}"

    def test_examples_are_quads_with_complexity(self):
        """As of intent_router 138325d (multi_step) + spec-first work,
        each example is (message, intent, multi_step, complexity_tier)."""
        for example in INTENT_EXAMPLES:
            assert isinstance(example, tuple)
            assert len(example) == 4, (
                f"expected 4-tuple, got {len(example)}: {example}"
            )
            assert isinstance(example[0], str)
            assert isinstance(example[1], str)
            assert isinstance(example[2], bool)
            assert isinstance(example[3], str)


class TestClassifyIntent:
    """Test classify_intent with a mock LLM provider."""

    @pytest.mark.asyncio
    async def test_returns_valid_intent(self, mock_llm_provider, fake_llm_response):
        mock_llm_provider.responses = [
            fake_llm_response(text='{"intent": "patch_request", "multi_step": false, "confidence": 0.95}')
        ]
        result = await classify_intent("fix the joint damping", mock_llm_provider)
        assert result["intent"] == "patch_request"
        assert result["multi_step"] is False

    @pytest.mark.asyncio
    async def test_classifies_multi_step(self, mock_llm_provider, fake_llm_response):
        """2026-04-19: multi_step classification drives the orchestrator's
        read-only tool gate on round 0. A multi-action prompt with linked
        dependencies must be flagged."""
        mock_llm_provider.responses = [
            fake_llm_response(text='{"intent": "patch_request", "multi_step": true, "confidence": 0.9}')
        ]
        result = await classify_intent(
            "create a conveyor, scale cubes, start simulation",
            mock_llm_provider,
        )
        assert result["multi_step"] is True

    @pytest.mark.asyncio
    async def test_fallback_on_parse_error(self, mock_llm_provider, fake_llm_response):
        mock_llm_provider.responses = [
            fake_llm_response(text="not valid json")
        ]
        result = await classify_intent("hello", mock_llm_provider)
        assert result["intent"] == "general_query"
        assert result["multi_step"] is False

    @pytest.mark.asyncio
    async def test_fallback_on_exception(self, mock_llm_provider):
        async def fail_complete(messages, options):
            raise RuntimeError("LLM unreachable")

        mock_llm_provider.complete = fail_complete
        result = await classify_intent("test message", mock_llm_provider)
        assert result["intent"] == "general_query"
        assert result["multi_step"] is False

    @pytest.mark.asyncio
    async def test_strips_markdown_fences(self, mock_llm_provider, fake_llm_response):
        mock_llm_provider.responses = [
            fake_llm_response(text='```json\n{"intent": "scene_diagnose", "multi_step": false, "confidence": 0.8}\n```')
        ]
        result = await classify_intent("why is my robot floating?", mock_llm_provider)
        assert result["intent"] == "scene_diagnose"

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
