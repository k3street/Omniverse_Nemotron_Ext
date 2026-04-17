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
    "the Omniverse Kit scripting environment.\n\n"
    "Response discipline:\n"
    "- Match the user's intent. Action phrasing ('do X', 'make X', 'drop it in', "
    "'run the import', 'just give me the script') means CALL TOOLS or RETURN CODE "
    "— keep prose to one short line or skip it entirely.\n"
    "- Do not pad action requests with background explanation the user did not ask for. "
    "Do not ask for confirmation when the user has already specified what they want "
    "— just do it.\n"
    "- Only explain at length when the user asked a question ('what is…', 'why…', "
    "'how does…') or when there is a genuine ambiguity that would change the action. "
    "Ambiguous? Ask ONE concise clarifying question, do not write a tutorial.\n"
    "- If the user says 'less prose' or similar, drop all prose for the rest of the "
    "session and emit only code / tool calls / one-line confirmations.\n\n"
    "Grounding discipline (CRITICAL):\n"
    "- NEVER describe scene contents, tool output, API return values, file paths, "
    "asset names, standard-document quotations, or product specifications unless "
    "a tool actually returned that information in this session.\n"
    "- If a tool returned an error, empty output, or didn't run: say 'I don't have "
    "verified output from <tool_name>, so I can't confirm that' and either retry, "
    "call a different tool, or ask the user.\n"
    "- NEVER pattern-match API names based on what they 'should look like'. When "
    "unsure of a module path, class name, or function signature, say 'I'm not "
    "sure of the exact API — let me look it up' and call lookup_knowledge or "
    "ask the user to confirm.\n"
    "- NEVER quote specific numbers from ISO/IEC/ANSI standards, product datasheets, "
    "or manufacturer specs as authoritative. If asked for such numbers, say 'I'd "
    "need to check the standard directly; my cited value could be wrong'.\n"
    "- Confident-wrong is worse than 'I don't know'. Users prefer admitted "
    "uncertainty over fabrication."
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
        # `context` may carry a per-call system override; fall back to instance
        # attribute (legacy) then to the default. Avoid relying on instance
        # state because concurrent requests share the provider.
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

        # Retry on transient errors (503 overload, 429 rate-limit, connection drops).
        # Google recommends exponential backoff for these; keep total wait bounded
        # so we don't blow the caller's timeout.
        import asyncio as _asyncio_rt
        retry_statuses = {429, 500, 502, 503, 504}
        max_attempts = 4
        backoff = 2.0  # seconds; doubles each retry
        last_error = None
        for attempt in range(1, max_attempts + 1):
            async with aiohttp.ClientSession() as session:
                try:
                    async with session.post(self.base_url, json=payload) as response:
                        if response.status == 200:
                            data = await response.json()
                            return self._parse_response(data)
                        error_text = await response.text()
                        if response.status in retry_statuses and attempt < max_attempts:
                            logger.warning(
                                f"Gemini {response.status} (attempt {attempt}/{max_attempts}), "
                                f"retrying in {backoff:.1f}s"
                            )
                            await _asyncio_rt.sleep(backoff)
                            backoff *= 2
                            last_error = error_text
                            continue
                        logger.error(f"Gemini API Error ({response.status}): {error_text[:200]}")
                        # Graceful user-facing message — no raw API JSON in chat.
                        if response.status in retry_statuses:
                            return LLMResponse(text=(
                                "I'm having trouble reaching my reasoning backend right now "
                                "(upstream service overloaded). Please try again in a moment."
                            ))
                        return LLMResponse(text=(
                            f"I couldn't complete that request (backend returned {response.status}). "
                            "Please try rephrasing or try again shortly."
                        ))
                except aiohttp.ClientError as e:
                    last_error = str(e)
                    if attempt < max_attempts:
                        logger.warning(
                            f"Gemini connection error (attempt {attempt}/{max_attempts}): {e}, "
                            f"retrying in {backoff:.1f}s"
                        )
                        await _asyncio_rt.sleep(backoff)
                        backoff *= 2
                        continue
                    logger.error(f"Failed to connect to Gemini cloud: {e}")
                    return LLMResponse(text=(
                        "I couldn't reach my reasoning backend. Check your network or try again."
                    ))
        logger.error(f"Gemini retries exhausted; last error: {last_error}")
        return LLMResponse(text=(
            "Backend is overloaded after several retries. Please try again in a minute."
        ))

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

            # Assistant with tool_calls → model message with functionCall.
            # Gemini 3.x requires thought_signature in functionCall parts when
            # continuing a tool-calling conversation. We preserve the signature
            # from the original response via tc["thought_signature"] if present.
            if role == "assistant" and msg.get("tool_calls"):
                parts = []
                if msg.get("content"):
                    parts.append({"text": msg["content"]})
                for tc in msg["tool_calls"]:
                    fn = tc.get("function", {})
                    args = fn.get("arguments", "{}")
                    part = {
                        "functionCall": {
                            "name": fn["name"],
                            "args": json.loads(args) if isinstance(args, str) else args,
                        }
                    }
                    # Gemini 3.x: thoughtSignature is a sibling of functionCall on the part
                    ts = tc.get("thought_signature")
                    if ts:
                        part["thoughtSignature"] = ts
                    parts.append(part)
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
                entry = {
                    "id": f"gemini_{fc['name']}",
                    "type": "function",
                    "function": {
                        "name": fc["name"],
                        "arguments": json.dumps(fc.get("args", {})),
                    },
                }
                # Gemini 3.x: thoughtSignature is a sibling of functionCall on the part
                ts = part.get("thoughtSignature")
                if ts:
                    entry["thought_signature"] = ts
                tool_calls.append(entry)

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
