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

# Base URLs for OpenAI-compatible providers
PROVIDER_URLS = {
    "openai": "https://api.openai.com/v1/chat/completions",
    "grok":   "https://api.x.ai/v1/chat/completions",
    "ollama_openai": "http://localhost:11434/v1/chat/completions",  # Ollama OpenAI-compat endpoint
}


@dataclass
class LLMResponse:
    text: str
    actions: List[Dict]


class OpenAICompatProvider:
    """
    Generic OpenAI-compatible chat completion provider.
    Works with OpenAI, xAI Grok, and any OpenAI-compatible endpoint.
    """

    def __init__(self, api_key: str, model: str, base_url: str):
        self.api_key = api_key
        self.model = model
        self.base_url = base_url

    async def complete(self, messages: List[Dict], context: Dict) -> LLMResponse:
        # Prepend system message
        full_messages = [{"role": "system", "content": SYSTEM_PROMPT}] + messages

        payload = {
            "model": self.model,
            "messages": full_messages,
            "max_tokens": 2048,
            "temperature": 0.2,
        }

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
                        return LLMResponse(text=f"API error: {error_text}", actions=[])

                    data = await response.json()
                    try:
                        reply = data["choices"][0]["message"]["content"]
                    except (KeyError, IndexError):
                        reply = "Parsing error: " + json.dumps(data)

                    return LLMResponse(text=reply, actions=self._parse_actions(reply))

            except aiohttp.ClientError as e:
                logger.error(f"Connection error to {self.base_url}: {e}")
                return LLMResponse(text=f"Connection failed: {e}", actions=[])

    def _parse_actions(self, text: str) -> List[Dict]:
        actions = []
        if "```python" in text:
            for block in text.split("```python")[1:]:
                code = block.split("```")[0].strip()
                if code:
                    actions.append({"type": "code_snippet", "content": code})
        return actions
