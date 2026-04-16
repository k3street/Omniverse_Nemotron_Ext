"""
L1 tests for the ChatOrchestrator — full pipeline with mocked LLM and Kit.
"""
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

pytestmark = [pytest.mark.l1, pytest.mark.asyncio]


class TestOrchestratorPipeline:
    """Test the full chat pipeline with mocked providers."""

    @pytest.fixture()
    def orchestrator(self, mock_kit_rpc, mock_llm_provider, monkeypatch, fake_llm_response):
        """Create an orchestrator with mocked LLM and Kit."""
        from service.isaac_assist_service.chat.orchestrator import ChatOrchestrator

        orch = ChatOrchestrator.__new__(ChatOrchestrator)
        orch.llm_provider = mock_llm_provider
        orch._distiller_provider = None
        orch._history = {}
        orch._knowledge = {}

        # Patch classify_intent to return a fixed intent
        monkeypatch.setattr(
            "service.isaac_assist_service.chat.orchestrator.classify_intent",
            AsyncMock(return_value="general_query"),
        )

        # Patch is_kit_rpc_alive to return False (no Kit)
        monkeypatch.setattr(
            "service.isaac_assist_service.chat.orchestrator.is_kit_rpc_alive",
            AsyncMock(return_value=False),
        )

        # Patch RAG retrieval to avoid import issues
        monkeypatch.setattr(
            "service.isaac_assist_service.chat.orchestrator.retrieve_context",
            MagicMock(return_value=[]),
        )
        monkeypatch.setattr(
            "service.isaac_assist_service.chat.orchestrator.format_retrieved_context",
            MagicMock(return_value=""),
        )
        monkeypatch.setattr(
            "service.isaac_assist_service.chat.orchestrator.find_matching_patterns",
            MagicMock(return_value=[]),
        )
        monkeypatch.setattr(
            "service.isaac_assist_service.chat.orchestrator.format_code_patterns",
            MagicMock(return_value=""),
        )
        monkeypatch.setattr(
            "service.isaac_assist_service.chat.orchestrator.detect_isaac_version",
            MagicMock(return_value="5.1"),
        )

        # Patch distill_context to pass messages through
        from service.isaac_assist_service.chat.context_distiller import DistilledContext
        from service.isaac_assist_service.chat.tools.tool_schemas import ISAAC_SIM_TOOLS

        async def fake_distill(**kwargs):
            messages = [
                {"role": "system", "content": "You are Isaac Assist."},
                {"role": "user", "content": kwargs.get("user_message", "")},
            ]
            return DistilledContext(
                system_prompt="You are Isaac Assist.",
                messages=messages,
                tools=ISAAC_SIM_TOOLS[:5],
                token_estimate=500,
            )

        monkeypatch.setattr(
            "service.isaac_assist_service.chat.orchestrator.distill_context",
            fake_distill,
        )

        # Patch KB methods
        monkeypatch.setattr(
            "service.isaac_assist_service.chat.orchestrator._kb",
            MagicMock(),
        )

        return orch

    async def test_simple_text_response(self, orchestrator, mock_llm_provider, fake_llm_response):
        mock_llm_provider.responses = [
            fake_llm_response(text="Here is my answer.")
        ]
        result = await orchestrator.handle_message("s1", "What is a prim?")
        assert result["reply"] == "Here is my answer."
        assert result["intent"] == "general_query"
        assert result["tool_calls"] == []
        assert result["code_patches"] == []

    async def test_tool_calling_loop(self, orchestrator, mock_llm_provider, fake_llm_response, mock_kit_rpc):
        """LLM returns tool call, executor runs, result fed back, then final text."""
        # Round 1: LLM wants to call a tool
        tool_call = {
            "id": "call_1",
            "type": "function",
            "function": {
                "name": "create_prim",
                "arguments": json.dumps({"prim_path": "/World/Cube", "prim_type": "Cube"}),
            },
        }
        mock_llm_provider.responses = [
            fake_llm_response(text="", tool_calls=[tool_call]),
            fake_llm_response(text="I created a cube for you."),
        ]

        result = await orchestrator.handle_message("s1", "Create a cube")
        assert result["reply"] == "I created a cube for you."
        assert len(result["tool_calls"]) == 1
        assert result["tool_calls"][0]["tool"] == "create_prim"
        assert len(result["code_patches"]) == 1

    async def test_max_tool_rounds_limit(self, orchestrator, mock_llm_provider, fake_llm_response, monkeypatch):
        """Should stop after max_tool_rounds even if LLM keeps calling tools."""
        from service.isaac_assist_service.config import config
        monkeypatch.setattr(config, "max_tool_rounds", 2)

        tool_call = {
            "id": "call_1",
            "type": "function",
            "function": {
                "name": "scene_summary",
                "arguments": "{}",
            },
        }
        # Keep returning tool calls forever
        mock_llm_provider.responses = [
            fake_llm_response(text="", tool_calls=[tool_call]),
            fake_llm_response(text="", tool_calls=[tool_call]),
            # After max_rounds, loop breaks and uses last response's text
            fake_llm_response(text="Final answer after max rounds."),
        ]

        result = await orchestrator.handle_message("s1", "summarize")
        # Should have exactly 2 tool calls (max_tool_rounds = 2)
        assert len(result["tool_calls"]) == 2

    async def test_session_history_persisted(self, orchestrator, mock_llm_provider, fake_llm_response):
        mock_llm_provider.responses = [
            fake_llm_response(text="Answer 1"),
        ]
        await orchestrator.handle_message("s1", "Q1")
        assert "s1" in orchestrator._history
        assert len(orchestrator._history["s1"]) == 2  # user + assistant

    async def test_reset_session_clears_history(self, orchestrator, mock_llm_provider, fake_llm_response):
        mock_llm_provider.responses = [fake_llm_response(text="OK")]
        await orchestrator.handle_message("s1", "Hi")
        orchestrator.reset_session("s1")
        assert "s1" not in orchestrator._history

    async def test_error_from_unknown_tool(self, orchestrator, mock_llm_provider, fake_llm_response, mock_kit_rpc):
        """If LLM calls a nonexistent tool, executor returns error gracefully."""
        tool_call = {
            "id": "call_bad",
            "type": "function",
            "function": {
                "name": "nonexistent_tool",
                "arguments": "{}",
            },
        }
        mock_llm_provider.responses = [
            fake_llm_response(text="", tool_calls=[tool_call]),
            fake_llm_response(text="Sorry, that tool does not exist."),
        ]

        result = await orchestrator.handle_message("s1", "do something impossible")
        assert len(result["tool_calls"]) == 1
        assert result["tool_calls"][0]["result"]["type"] == "error"
