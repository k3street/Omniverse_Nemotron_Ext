"""LLM provider for a locally-running Ollama instance.

Sends conversations to Ollama's ``/api/chat`` endpoint using its native
JSON format.  Supports the OpenAI-compatible tool-call format that Ollama
exposes for models with function-calling support.
"""
import aiohttp
import json
import logging
from dataclasses import dataclass, field
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)


@dataclass
class LLMResponse:
    """Unified response container returned by all LLM provider ``complete`` calls.

    Attributes:
        text: Plain-text reply from the model (may be empty when tool_calls present).
        actions: List of ``{"type": "code_snippet", "content": ...}`` dicts extracted
            from fenced Python blocks in the reply text.
        tool_calls: OpenAI-format tool-call dicts, or None if the model returned text.
    """
    text: str
    actions: List[Dict] = field(default_factory=list)
    tool_calls: Optional[List[Dict]] = None


class OllamaProvider:
    """LLM provider communicating with a local Ollama instance.

    Uses Ollama's ``/api/chat`` endpoint with ``stream: false``.  Supports
    tool/function calling via Ollama's OpenAI-compatible tools API for models
    that expose it (e.g. Qwen, Llama 3.1+).
    """
    def __init__(self, host: str = "127.0.0.1", port: int = 11434, model: str = "isaac-assist-nemotron"):
        self.base_url = f"http://{host}:{port}/api/chat"
        self.model = model

    async def complete(self, messages: List[Dict], context: Dict) -> LLMResponse:
        """Send a conversation to Ollama and return the parsed response.

        Args:
            messages (list[dict]): OpenAI-style message list passed directly to
                Ollama (roles: ``user``, ``assistant``, ``system``, ``tool``).
            context (dict): Extra options — ``tools`` is an OpenAI tool-schema list
                forwarded as-is to Ollama.

        Returns:
            LLMResponse: Text reply and/or tool_calls from the model.
        """
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": 0.2,
                "num_ctx": 8192
            }
        }

        # Add tools if provided (Ollama supports OpenAI-compatible tool format)
        tools = context.get("tools")
        if tools:
            payload["tools"] = tools

        async with aiohttp.ClientSession() as session:
            try:
                async with session.post(self.base_url, json=payload) as response:
                    if response.status != 200:
                        error_text = await response.text()
                        logger.error(f"Ollama API Error: {error_text}")
                        return LLMResponse(text=f"Error connecting to local LLM: {error_text}")

                    data = await response.json()
                    message = data.get("message", {})
                    response_text = message.get("content", "")

                    # Parse tool calls from Ollama response
                    raw_tool_calls = message.get("tool_calls")
                    tool_calls = None
                    if raw_tool_calls:
                        tool_calls = []
                        for i, tc in enumerate(raw_tool_calls):
                            fn = tc.get("function", {})
                            tool_calls.append({
                                "id": f"call_{i}_{fn.get('name', '')}",
                                "type": "function",
                                "function": {
                                    "name": fn.get("name", ""),
                                    "arguments": json.dumps(fn.get("arguments", {}))
                                        if isinstance(fn.get("arguments"), dict)
                                        else fn.get("arguments", "{}"),
                                },
                            })

                    actions = self._parse_actions(response_text)
                    return LLMResponse(text=response_text, actions=actions, tool_calls=tool_calls)

            except aiohttp.ClientError as e:
                logger.error(f"Failed to connect to Ollama at {self.base_url}: {e}")
                return LLMResponse(text="Failed to connect to the local background AI service. Make sure Ollama is running.")

    def _parse_actions(self, text: str) -> List[Dict]:
        """
        Simple extraction of action blocks if the model proposes a code fix.
        """
        actions = []
        if text and "```python" in text:
            blocks = text.split("```python")
            for block in blocks[1:]:
                code = block.split("```")[0].strip()
                if code:
                    actions.append({
                        "type": "code_snippet",
                        "content": code
                    })
        return actions
