"""LLM provider for Anthropic's Claude API.

Sends OpenAI-style conversation history to the Anthropic Messages endpoint,
translating tool_calls and tool_result messages to the Anthropic content-block
format.  Returns an ``LLMResponse`` with text, extracted code-snippet actions,
and any tool_calls the model requested.
"""
import aiohttp
import logging
import json
from dataclasses import dataclass, field
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You are Isaac Assist, an expert AI embedded inside NVIDIA Isaac Sim — "
    "authored by 10Things, Inc. (www.10things.tech). "
    "You help robotics engineers diagnose scene issues, generate USD patches, "
    "and answer questions about Omniverse, PhysX, ROS2, and robot simulation. "
    "Be concise and precise. When you suggest code, use Python that works inside "
    "the Omniverse Kit scripting environment."
)


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


class AnthropicProvider:
    """LLM provider connecting to Anthropic's Claude API via REST.

    Translates the OpenAI-style conversation format (used internally by the
    orchestrator) to Anthropic's content-block format, then maps the response
    back to :class:`LLMResponse`.  Supports tool use / function calling.
    """
    API_URL = "https://api.anthropic.com/v1/messages"
    ANTHROPIC_VERSION = "2023-06-01"

    def __init__(self, api_key: str, model: str = "claude-opus-4-6"):
        self.api_key = api_key
        self.model = model

    async def complete(self, messages: List[Dict], context: Dict) -> LLMResponse:
        """Send a conversation to Claude and return the parsed response.

        Args:
            messages (list[dict]): OpenAI-style message list with ``role`` and
                ``content``.  ``tool`` roles are converted to
                ``tool_result`` content blocks.
            context (dict): Extra options — ``system_override`` replaces the
                default system prompt; ``tools`` is an OpenAI tool-schema list
                that is converted to Anthropic format.

        Returns:
            LLMResponse: Text reply and/or tool_calls from the model.
        """
        system = (
            context.get("system_override")
            or getattr(self, "_system_override", None)
            or SYSTEM_PROMPT
        )

        # Filter out system messages (Anthropic uses separate 'system' field)
        filtered_msgs = [m for m in messages if m.get("role") != "system"]

        # Convert tool-result messages to Anthropic format
        anthropic_msgs = []
        for m in filtered_msgs:
            if m.get("role") == "tool":
                anthropic_msgs.append({
                    "role": "user",
                    "content": [{
                        "type": "tool_result",
                        "tool_use_id": m.get("tool_call_id", ""),
                        "content": m.get("content", ""),
                    }]
                })
            elif m.get("role") == "assistant" and m.get("tool_calls"):
                # Convert OpenAI-style tool_calls to Anthropic content blocks
                content_blocks = []
                if m.get("content"):
                    content_blocks.append({"type": "text", "text": m["content"]})
                for tc in m["tool_calls"]:
                    args = tc["function"]["arguments"]
                    content_blocks.append({
                        "type": "tool_use",
                        "id": tc.get("id", ""),
                        "name": tc["function"]["name"],
                        "input": json.loads(args) if isinstance(args, str) else args,
                    })
                anthropic_msgs.append({"role": "assistant", "content": content_blocks})
            else:
                anthropic_msgs.append({"role": m["role"], "content": m.get("content", "")})

        payload = {
            "model": self.model,
            "max_tokens": 4096,
            "system": system,
            "messages": anthropic_msgs,
        }

        # Convert OpenAI tool schema to Anthropic tool format
        tools = context.get("tools")
        if tools:
            anthropic_tools = []
            for t in tools:
                fn = t.get("function", {})
                anthropic_tools.append({
                    "name": fn["name"],
                    "description": fn.get("description", ""),
                    "input_schema": fn.get("parameters", {"type": "object", "properties": {}}),
                })
            payload["tools"] = anthropic_tools

        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": self.ANTHROPIC_VERSION,
            "content-type": "application/json",
        }

        async with aiohttp.ClientSession() as session:
            try:
                async with session.post(self.API_URL, json=payload, headers=headers) as response:
                    if response.status != 200:
                        error_text = await response.text()
                        logger.error(f"Anthropic API Error ({response.status}): {error_text}")
                        return LLMResponse(text=f"Error from Claude: {error_text}")

                    data = await response.json()
                    return self._parse_response(data)

            except aiohttp.ClientError as e:
                logger.error(f"Failed to connect to Anthropic API: {e}")
                return LLMResponse(text="Failed to connect to Claude.")

    def _parse_response(self, data: Dict) -> LLMResponse:
        """Parse Anthropic response which may contain text and/or tool_use blocks."""
        content_blocks = data.get("content", [])
        text_parts = []
        tool_calls = []

        for block in content_blocks:
            if block.get("type") == "text":
                text_parts.append(block["text"])
            elif block.get("type") == "tool_use":
                tool_calls.append({
                    "id": block.get("id", ""),
                    "type": "function",
                    "function": {
                        "name": block["name"],
                        "arguments": json.dumps(block.get("input", {})),
                    },
                })

        text = "\n".join(text_parts)
        return LLMResponse(
            text=text,
            actions=self._parse_actions(text),
            tool_calls=tool_calls if tool_calls else None,
        )

    def _parse_actions(self, text: str) -> List[Dict]:
        actions = []
        if text and "```python" in text:
            for block in text.split("```python")[1:]:
                code = block.split("```")[0].strip()
                if code:
                    actions.append({"type": "code_snippet", "content": code})
        return actions
