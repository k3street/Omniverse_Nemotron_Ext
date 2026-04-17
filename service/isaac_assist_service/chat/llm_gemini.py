import aiohttp
import logging
import json
from dataclasses import dataclass, field
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)

@dataclass
class LLMResponse:
    text: str
    actions: List[Dict] = field(default_factory=list)
    tool_calls: Optional[List[Dict]] = None

SYSTEM_PROMPT = (
    "You are Isaac Assist, an expert AI embedded inside NVIDIA Isaac Sim — "
    "authored by 10Things, Inc. (www.10things.tech). "
    "You help robotics engineers diagnose scene issues, generate USD patches, "
    "and answer questions about Omniverse, PhysX, ROS2, and robot simulation. "
    "Be concise and precise. When you suggest code, use Python that works inside "
    "the Omniverse Kit scripting environment."
)

class GeminiProvider:
    """
    LLM Provider connecting to Google's Gemini API (supports all v1beta models
    including gemini-robotics-er-1.5). Supports tool/function calling.
    """
    def __init__(self, api_key: str, model: str = "gemini-robotics-er-1.6-preview"):
        self.api_key = api_key
        self.model = model
        self.base_url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{self.model}:generateContent?key={self.api_key}"
        )

    async def complete(self, messages: List[Dict], context: Dict) -> LLMResponse:
        # Per-call override via context avoids racing a shared instance attr.
        system = (
            context.get("system_override")
            or getattr(self, "_system_override", None)
            or SYSTEM_PROMPT
        )
        gemini_messages = self._format_messages(messages)

        payload = {
            "system_instruction": {"parts": [{"text": system}]},
            "contents": gemini_messages,
            "generationConfig": {
                "temperature": 0.2,
                "maxOutputTokens": 4096,
            },
        }

        # Convert OpenAI tool schemas to Gemini function declarations
        tools = context.get("tools")
        if tools:
            function_declarations = []
            for t in tools:
                fn = t.get("function", {})
                params = fn.get("parameters", {"type": "object", "properties": {}})
                # Gemini doesn't support 'default' in parameters — strip them
                cleaned_params = self._clean_params(params)
                function_declarations.append({
                    "name": fn["name"],
                    "description": fn.get("description", ""),
                    "parameters": cleaned_params,
                })
            payload["tools"] = [{"function_declarations": function_declarations}]

        async with aiohttp.ClientSession() as session:
            try:
                async with session.post(self.base_url, json=payload) as response:
                    if response.status != 200:
                        error_text = await response.text()
                        logger.error(f"Gemini API Error: {error_text}")
                        return LLMResponse(text=f"Error from Gemini Cloud: {error_text}")

                    data = await response.json()
                    return self._parse_response(data)

            except aiohttp.ClientError as e:
                logger.error(f"Failed to connect to Gemini cloud: {e}")
                return LLMResponse(text="Failed to connect to cloud AI service.")

    def _format_messages(self, messages: List[Dict]) -> List[Dict]:
        """Converts OpenAI style messages to Gemini format, including tool results."""
        gemini_msgs = []
        for msg in messages:
            role = msg.get("role", "user")

            # Skip system messages (handled via system_instruction)
            if role == "system":
                continue

            # Tool result → user message with functionResponse
            if role == "tool":
                content_str = msg.get("content", "{}")
                try:
                    result_data = json.loads(content_str) if isinstance(content_str, str) else content_str
                except json.JSONDecodeError:
                    result_data = {"result": content_str}
                gemini_msgs.append({
                    "role": "user",
                    "parts": [{
                        "functionResponse": {
                            "name": msg.get("tool_call_id", "unknown"),
                            "response": result_data,
                        }
                    }]
                })
                continue

            # Assistant with tool_calls → model message with functionCall
            if role == "assistant" and msg.get("tool_calls"):
                parts = []
                if msg.get("content"):
                    parts.append({"text": msg["content"]})
                for tc in msg["tool_calls"]:
                    fn = tc.get("function", {})
                    args = fn.get("arguments", "{}")
                    parts.append({
                        "functionCall": {
                            "name": fn["name"],
                            "args": json.loads(args) if isinstance(args, str) else args,
                        }
                    })
                gemini_msgs.append({"role": "model", "parts": parts})
                continue

            # Normal text message
            gemini_role = "user" if role in ("user",) else "model"
            gemini_msgs.append({
                "role": gemini_role,
                "parts": [{"text": msg.get("content", "")}]
            })

        return gemini_msgs

    def _parse_response(self, data: Dict) -> LLMResponse:
        """Parse Gemini response which may contain text and/or functionCall parts."""
        try:
            parts = data["candidates"][0]["content"]["parts"]
        except (KeyError, IndexError):
            return LLMResponse(text="Parsing error: " + json.dumps(data))

        text_parts = []
        tool_calls = []

        for part in parts:
            if "text" in part:
                text_parts.append(part["text"])
            elif "functionCall" in part:
                fc = part["functionCall"]
                tool_calls.append({
                    "id": f"gemini_{fc['name']}",
                    "type": "function",
                    "function": {
                        "name": fc["name"],
                        "arguments": json.dumps(fc.get("args", {})),
                    },
                })

        text = "\n".join(text_parts)
        return LLMResponse(
            text=text,
            actions=self._parse_actions(text),
            tool_calls=tool_calls if tool_calls else None,
        )

    def _clean_params(self, params: Dict) -> Dict:
        """Recursively strip 'default' keys that Gemini doesn't support."""
        cleaned = {}
        for k, v in params.items():
            if k == "default":
                continue
            if isinstance(v, dict):
                cleaned[k] = self._clean_params(v)
            elif isinstance(v, list):
                cleaned[k] = [self._clean_params(i) if isinstance(i, dict) else i for i in v]
            else:
                cleaned[k] = v
        return cleaned

    def _parse_actions(self, text: str) -> List[Dict]:
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
