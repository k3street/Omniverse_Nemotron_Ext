import aiohttp
import json
import logging
from dataclasses import dataclass
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)

@dataclass
class LLMResponse:
    text: str
    actions: List[Dict]

class OllamaProvider:
    """
    LLM Provider implementation that streams and communicates with a local Ollama instance.
    """
    def __init__(self, host: str = "127.0.0.1", port: int = 11434, model: str = "isaac-assist-nemotron"):
        self.base_url = f"http://{host}:{port}/api/chat"
        self.model = model

    async def complete(self, messages: List[Dict], context: Dict) -> LLMResponse:
        """
        Sends context and conversation history to Ollama.
        messages format: [{"role": "user", "content": "..."}]
        """
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": 0.2, # Keep it deterministic for code generation
                "num_ctx": 8192
            }
        }
        
        async with aiohttp.ClientSession() as session:
            try:
                async with session.post(self.base_url, json=payload) as response:
                    if response.status != 200:
                        error_text = await response.text()
                        logger.error(f"Ollama API Error: {error_text}")
                        return LLMResponse(text=f"Error connecting to local LLM: {error_text}", actions=[])
                        
                    data = await response.json()
                    response_text = data.get("message", {}).get("content", "")
                    
                    # Simulated parsing of actions from text. If the model outputs ```json at the end, etc.
                    actions = self._parse_actions(response_text)
                    
                    return LLMResponse(text=response_text, actions=actions)
                    
            except aiohttp.ClientError as e:
                logger.error(f"Failed to connect to Ollama at {self.base_url}: {e}")
                return LLMResponse(text="Failed to connect to the local background AI service. Make sure Ollama is running.", actions=[])

    def _parse_actions(self, text: str) -> List[Dict]:
        """
        Simple extraction of action blocks if the model proposes a code fix.
        """
        actions = []
        if "```python" in text:
            # We could extract the code block and attach it as a proposed action card
            blocks = text.split("```python")
            for block in blocks[1:]:
                code = block.split("```")[0].strip()
                if code:
                    actions.append({
                        "type": "code_snippet",
                        "content": code
                    })
        return actions
