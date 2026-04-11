import aiohttp
import logging
import json
from dataclasses import dataclass
from typing import List, Dict

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You are Isaac Assist, an expert AI embedded inside NVIDIA Isaac Sim. "
    "You help robotics engineers diagnose scene issues, generate USD patches, "
    "and answer questions about Omniverse, PhysX, ROS2, and robot simulation. "
    "Be concise and precise. When you suggest code, use Python that works inside "
    "the Omniverse Kit scripting environment."
)


@dataclass
class LLMResponse:
    text: str
    actions: List[Dict]


class AnthropicProvider:
    """
    LLM Provider connecting to Anthropic's Claude API via REST.
    """
    API_URL = "https://api.anthropic.com/v1/messages"
    ANTHROPIC_VERSION = "2023-06-01"

    def __init__(self, api_key: str, model: str = "claude-sonnet-4-6"):
        self.api_key = api_key
        self.model = model

    async def complete(self, messages: List[Dict], context: Dict) -> LLMResponse:
        payload = {
            "model": self.model,
            "max_tokens": 2048,
            "system": SYSTEM_PROMPT,
            "messages": messages,  # already in {"role": "user"/"assistant", "content": "..."} format
        }

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
                        return LLMResponse(text=f"Error from Claude: {error_text}", actions=[])

                    data = await response.json()
                    try:
                        reply = data["content"][0]["text"]
                    except (KeyError, IndexError):
                        reply = "Parsing error: " + json.dumps(data)

                    return LLMResponse(text=reply, actions=self._parse_actions(reply))

            except aiohttp.ClientError as e:
                logger.error(f"Failed to connect to Anthropic API: {e}")
                return LLMResponse(text="Failed to connect to Claude.", actions=[])

    def _parse_actions(self, text: str) -> List[Dict]:
        actions = []
        if "```python" in text:
            for block in text.split("```python")[1:]:
                code = block.split("```")[0].strip()
                if code:
                    actions.append({"type": "code_snippet", "content": code})
        return actions
