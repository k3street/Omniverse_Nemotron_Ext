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

# Base URLs for OpenAI-compatible providers
PROVIDER_URLS = {
    "openai": "https://api.openai.com/v1/chat/completions",
    "grok":   "https://api.x.ai/v1/chat/completions",
    "moonshot": "https://api.moonshot.ai/v1/chat/completions",  # Kimi K2 — global endpoint, NOT api.moonshot.cn
    "ollama_openai": "http://localhost:11434/v1/chat/completions",  # Ollama OpenAI-compat endpoint
}


@dataclass
class LLMResponse:
    text: str
    actions: List[Dict] = field(default_factory=list)
    tool_calls: Optional[List[Dict]] = None


class OpenAICompatProvider:
    """
    Generic OpenAI-compatible chat completion provider.
    Works with OpenAI, xAI Grok, and any OpenAI-compatible endpoint.
    Supports tool/function calling when tools are provided in context.
    """

    def __init__(self, api_key: str, model: str, base_url: str):
        self.api_key = api_key
        self.model = model
        self.base_url = base_url

    async def complete(self, messages: List[Dict], context: Dict) -> LLMResponse:
        # Prepend system message if not already present
        if not messages or messages[0].get("role") != "system":
            system = getattr(self, "_system_override", None) or SYSTEM_PROMPT
            full_messages = [{"role": "system", "content": system}] + messages
        else:
            full_messages = messages

        # gpt-5.x and o-series models require max_completion_tokens; older models use max_tokens
        _use_completion_tokens = (
            self.model.startswith("gpt-5") or self.model.startswith("o1") or self.model.startswith("o3")
        )
        payload = {
            "model": self.model,
            "messages": full_messages,
            ("max_completion_tokens" if _use_completion_tokens else "max_tokens"): 4096,
            "temperature": 0.2,
        }
        # Kimi K2.6 (Moonshot) rejects any temperature != 1 with 400.
        # Detect by model name and override.
        if self.model and ("kimi-k2.6" in self.model or "kimi-k2-thinking" in self.model):
            payload["temperature"] = 1.0

        # Add tools if provided
        tools = context.get("tools")
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        async with aiohttp.ClientSession() as session:
            try:
                async with session.post(self.base_url, json=payload, headers=headers) as response:
                    if response.status != 200:
                        error_text = await response.text()
                        logger.error(f"API Error ({response.status}) from {self.base_url}: {error_text}")
                        return LLMResponse(text=f"API error: {error_text}")

                    data = await response.json()
                    try:
                        choice = data["choices"][0]
                        message = choice["message"]
                        # Kimi K2.6 / k2-thinking return chain-of-thought
                        # in `reasoning_content` and the actual JSON/answer
                        # in `content`. But for some queries content can be
                        # empty while the substantive output is in
                        # reasoning_content. Fall back so callers (intent
                        # router, negotiator, distiller) that parse JSON
                        # don't get an empty string.
                        reply = (
                            message.get("content")
                            or message.get("reasoning_content")
                            or ""
                        )
                        raw_tool_calls = message.get("tool_calls")
                    except (KeyError, IndexError):
                        return LLMResponse(text="Parsing error: " + json.dumps(data))

                    # Parse tool calls if present
                    tool_calls = None
                    if raw_tool_calls:
                        tool_calls = [
                            {
                                "id": tc.get("id", ""),
                                "type": "function",
                                "function": {
                                    "name": tc["function"]["name"],
                                    "arguments": tc["function"]["arguments"],
                                },
                            }
                            for tc in raw_tool_calls
                        ]

                    return LLMResponse(
                        text=reply,
                        actions=self._parse_actions(reply),
                        tool_calls=tool_calls,
                    )

            except aiohttp.ClientError as e:
                logger.error(f"Connection error to {self.base_url}: {e}")
                return LLMResponse(text=f"Connection failed: {e}")

    def _parse_actions(self, text: str) -> List[Dict]:
        actions = []
        if text and "```python" in text:
            for block in text.split("```python")[1:]:
                code = block.split("```")[0].strip()
                if code:
                    actions.append({"type": "code_snippet", "content": code})
        return actions
