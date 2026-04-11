import aiohttp
import logging
import json
from dataclasses import dataclass
from typing import List, Dict

logger = logging.getLogger(__name__)

@dataclass
class LLMResponse:
    text: str
    actions: List[Dict]

SYSTEM_PROMPT = (
    "You are Isaac Assist, an expert AI embedded inside NVIDIA Isaac Sim. "
    "You help robotics engineers diagnose scene issues, generate USD patches, "
    "and answer questions about Omniverse, PhysX, ROS2, and robot simulation. "
    "Be concise and precise. When you suggest code, use Python that works inside "
    "the Omniverse Kit scripting environment."
)

class GeminiProvider:
    """
    LLM Provider connecting to Google's Gemini API (supports all v1beta models
    including gemini-robotics-er-1.5).
    """
    def __init__(self, api_key: str, model: str = "gemini-robotics-er-1.5"):
        self.api_key = api_key
        self.model = model
        self.base_url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{self.model}:generateContent?key={self.api_key}"
        )

    async def complete(self, messages: List[Dict], context: Dict) -> LLMResponse:
        gemini_messages = self._format_messages(messages)

        payload = {
            "system_instruction": {"parts": [{"text": SYSTEM_PROMPT}]},
            "contents": gemini_messages,
            "generationConfig": {
                "temperature": 0.2,
                "maxOutputTokens": 2048,
            },
        }
        
        async with aiohttp.ClientSession() as session:
            try:
                async with session.post(self.base_url, json=payload) as response:
                    if response.status != 200:
                        error_text = await response.text()
                        logger.error(f"Gemini API Error: {error_text}")
                        return LLMResponse(text=f"Error from Gemini Cloud: {error_text}", actions=[])
                        
                    data = await response.json()
                    
                    try:
                        response_text = data["candidates"][0]["content"]["parts"][0]["text"]
                    except (KeyError, IndexError):
                        response_text = "Parsing error: " + json.dumps(data)
                        
                    actions = self._parse_actions(response_text)
                    return LLMResponse(text=response_text, actions=actions)
                    
            except aiohttp.ClientError as e:
                logger.error(f"Failed to connect to Gemini cloud: {e}")
                return LLMResponse(text="Failed to connect to cloud AI service.", actions=[])

    def _format_messages(self, messages: List[Dict]) -> List[Dict]:
        """ Converts OpenAI style ['role': 'user', 'content': 'hi'] to Gemini format """
        gemini_msgs = []
        for msg in messages:
            role = "user" if msg["role"] in ["user", "system"] else "model"
            gemini_msgs.append({
                "role": role,
                "parts": [{"text": msg["content"]}]
            })
        return gemini_msgs
        
    def _parse_actions(self, text: str) -> List[Dict]:
        actions = []
        if "```python" in text:
            blocks = text.split("```python")
            for block in blocks[1:]:
                code = block.split("```")[0].strip()
                if code:
                    actions.append({
                        "type": "code_snippet",
                        "content": code
                    })
        return actions
