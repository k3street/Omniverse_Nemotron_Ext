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
    # (prompt_tokens, completion_tokens) as reported by the API. Needed to
    # meter spend; a run cannot be capped on usage it never recorded.
    usage: Optional[tuple] = None


def is_reasoning_model(model: str) -> bool:
    """Families that take max_completion_tokens and reject a chosen temperature.

    Matched by family prefix so each release does not need its own line: gpt-6
    fell outside an explicit gpt-5/o1/o3 list and would have been sent
    max_tokens, and a 400.
    """
    return (model or "").startswith(("gpt-5", "gpt-6", "o1", "o3", "o4"))


def build_payload(model: str, messages: List[Dict], context: Dict) -> Dict:
    """The request body, separated from the I/O so it can be checked."""
    name = model or ""
    reasoning = is_reasoning_model(name)
    payload: Dict = {
        "model": model,
        "messages": messages,
        ("max_completion_tokens" if reasoning else "max_tokens"): 4096,
    }
    if not reasoning:
        payload["temperature"] = 0.2
    # Kimi K2.6 (Moonshot) rejects any temperature != 1 with 400.
    if "kimi-k2.6" in name or "kimi-k2-thinking" in name:
        payload["temperature"] = 1.0
    tools = context.get("tools")
    if tools:
        payload["tools"] = tools
        # Honour the caller's choice. Callers that then require exactly one tool
        # call pass "required"; forcing "auto" here let the model answer with
        # prose instead, and the caller raised on the missing call.
        payload["tool_choice"] = context.get("tool_choice", "auto")
    return payload


# --- Responses API ---------------------------------------------------------
# Reasoning models refuse function tools on /v1/chat/completions:
#   "Function tools with reasoning_effort are not supported for gpt-6-astra in
#    /v1/chat/completions. To use function tools, use /v1/responses or set
#    reasoning_effort to 'none'."
# Turning reasoning off is the wrong trade for a planner, so those models go
# through /v1/responses, which takes a differently shaped request and answers
# with a differently shaped body. These translate between the two so callers
# keep speaking one dialect.

RESPONSES_PATH = "/v1/responses"
CHAT_COMPLETIONS_PATH = "/v1/chat/completions"


def responses_url(base_url: str) -> str:
    """The /v1/responses endpoint beside a configured chat-completions URL."""
    if base_url.endswith(CHAT_COMPLETIONS_PATH):
        return base_url[: -len(CHAT_COMPLETIONS_PATH)] + RESPONSES_PATH
    return base_url.rstrip("/") + RESPONSES_PATH


def to_responses_input(messages: List[Dict]) -> tuple:
    """Chat messages -> (instructions, input) for the Responses API.

    System messages become `instructions`; text and images become the
    input_text / input_image parts Responses expects.
    """
    instructions: List[str] = []
    items: List[Dict] = []
    for message in messages:
        role = message.get("role", "user")
        content = message.get("content")
        if role == "system":
            if isinstance(content, str):
                instructions.append(content)
            continue
        parts: List[Dict] = []
        if isinstance(content, str):
            parts.append({"type": "input_text", "text": content})
        else:
            for part in content or []:
                kind = part.get("type")
                if kind == "text":
                    parts.append({"type": "input_text", "text": part.get("text", "")})
                elif kind == "image_url":
                    url = part.get("image_url", {})
                    parts.append({
                        "type": "input_image",
                        "image_url": url.get("url") if isinstance(url, dict) else url,
                    })
        items.append({"role": role, "content": parts})
    return "\n\n".join(instructions) or None, items


def to_responses_tools(tools) -> List[Dict]:
    """Chat-style tools (nested under "function") -> the flat Responses form."""
    flat = []
    for tool in tools or []:
        fn = tool.get("function") if "function" in tool else tool
        flat.append({
            "type": "function",
            "name": fn.get("name"),
            "description": fn.get("description", ""),
            "parameters": fn.get("parameters", {}),
        })
    return flat


def build_responses_payload(model: str, messages: List[Dict], context: Dict) -> Dict:
    instructions, items = to_responses_input(messages)
    payload: Dict = {"model": model, "input": items, "max_output_tokens": 4096}
    if instructions:
        payload["instructions"] = instructions
    tools = context.get("tools")
    if tools:
        payload["tools"] = to_responses_tools(tools)
        payload["tool_choice"] = context.get("tool_choice", "auto")
    return payload


def parse_responses_body(data: Dict) -> tuple:
    """Responses body -> (text, tool_calls, usage) in the shape callers expect.

    Tool calls are normalised to the same dict the Gemini and chat paths emit,
    so the planner's gates dispatch any of them unchanged.
    """
    text_chunks: List[str] = []
    tool_calls: List[Dict] = []
    for item in data.get("output", []) or []:
        kind = item.get("type")
        if kind == "function_call":
            tool_calls.append({
                "id": item.get("call_id", ""),
                "type": "function",
                "function": {
                    "name": item.get("name"),
                    "arguments": item.get("arguments", "{}"),
                },
            })
        elif kind == "message":
            for part in item.get("content", []) or []:
                if part.get("type") == "output_text":
                    text_chunks.append(part.get("text", ""))
    usage = data.get("usage") or {}
    return (
        "\n".join(c for c in text_chunks if c),
        tool_calls or None,
        (int(usage.get("input_tokens", 0)), int(usage.get("output_tokens", 0))),
    )


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

    async def _complete_via_responses(
        self, messages: List[Dict], context: Dict, headers: Dict
    ) -> LLMResponse:
        payload = build_responses_payload(self.model, messages, context)
        url = responses_url(self.base_url)
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, headers=headers) as response:
                if response.status != 200:
                    error_text = await response.text()
                    logger.error(
                        f"API Error ({response.status}) from {url}: {error_text}"
                    )
                    return LLMResponse(text=f"API Error ({response.status}): {error_text}")
                data = await response.json()
        text, tool_calls, usage = parse_responses_body(data)
        return LLMResponse(
            text=text,
            actions=self._parse_actions(text),
            tool_calls=tool_calls,
            usage=usage,
        )

    async def complete(self, messages: List[Dict], context: Dict) -> LLMResponse:
        # Prepend system message if not already present
        if not messages or messages[0].get("role") != "system":
            system = getattr(self, "_system_override", None) or SYSTEM_PROMPT
            full_messages = [{"role": "system", "content": system}] + messages
        else:
            full_messages = messages

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        # Reasoning models reject function tools on chat/completions, so they
        # take the Responses endpoint instead. Routing the whole family there
        # keeps one path per model rather than switching on whether a given
        # call happens to carry tools.
        if is_reasoning_model(self.model):
            return await self._complete_via_responses(full_messages, context, headers)

        payload = build_payload(self.model, full_messages, context)

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

                    reported = data.get("usage") or {}
                    return LLMResponse(
                        text=reply,
                        actions=self._parse_actions(reply),
                        tool_calls=tool_calls,
                        usage=(
                            int(reported.get("prompt_tokens", 0)),
                            int(reported.get("completion_tokens", 0)),
                        ),
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
