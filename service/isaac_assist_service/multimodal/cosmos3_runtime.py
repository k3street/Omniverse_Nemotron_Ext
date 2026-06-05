"""Cosmos 3 Reasoner runtime client.

The committed adapter maps already-structured observations into LayoutSpec.
This module owns the upstream model invocation: image/prompt in,
``CosmosSceneObservation`` out.
"""
from __future__ import annotations

import base64
import json
import logging
from typing import Any, Dict, Optional

from ..config import config
from .cosmos3_adapter import CosmosSceneObservation

logger = logging.getLogger(__name__)


COSMOS_SCENE_PROMPT = """\
You are a Cosmos 3 Reasoner extracting robotics scene structure for Isaac Sim.
Return ONLY valid JSON matching this schema:
{
  "input_kind": "photo|screenshot|render|video_frame|prompt",
  "prompt": "original user intent",
  "summary": "short scene summary",
  "pattern_hint": "pick_place|sort|reorient|navigate|insert|train|other",
  "workspace_size_xy_m": [4.0, 4.0],
  "confidence": 0.0,
  "objects": [
    {
      "label": "visible object name",
      "role": "robot|pick|target|floor|workspace|destination|workpiece|null",
      "asset_hint": "optional Isaac asset or class hint",
      "confidence": 0.0,
      "position_xy_m": [0.0, 0.0],
      "bbox_xyxy_norm": [0.0, 0.0, 1.0, 1.0],
      "size_xy_m": [0.2, 0.2],
      "rotation_deg": 0.0,
      "color": "#ff0000",
      "notes": "",
      "metadata": {}
    }
  ],
  "relations": [
    {
      "subject": "object label",
      "predicate": "on|near|left_of|right_of|in_front_of|inside|reachable_by",
      "object": "object label",
      "confidence": 0.0
    }
  ],
  "metadata": {}
}
Use normalized image bboxes when exact metre positions are uncertain. Use null
for optional fields you cannot infer. Do not include prose or markdown.
"""


class CosmosRuntimeError(RuntimeError):
    """Raised when a Cosmos runtime call fails or returns invalid data."""


def chat_completions_url(base_url: str) -> str:
    """Normalize a base URL to an OpenAI-compatible chat completions URL."""

    value = base_url.rstrip("/")
    if value.endswith("/chat/completions"):
        return value
    if value.endswith("/v1"):
        return f"{value}/chat/completions"
    return f"{value}/v1/chat/completions"


def extract_json_object(text: str) -> Dict[str, Any]:
    """Extract the first JSON object from a model reply."""

    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise CosmosRuntimeError("Cosmos response did not contain a JSON object")

    try:
        parsed = json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError as exc:
        raise CosmosRuntimeError(f"Cosmos response JSON parse failed: {exc}") from exc
    if not isinstance(parsed, dict):
        raise CosmosRuntimeError("Cosmos response JSON was not an object")
    return parsed


class Cosmos3ReasonerClient:
    """OpenAI-compatible client for Cosmos 3 Reasoner endpoints."""

    def __init__(
        self,
        *,
        base_url: str = "",
        model: str = "",
        api_key: str = "",
        timeout_s: float = 180.0,
    ) -> None:
        self.base_url = base_url or config.cosmos3_reasoner_base_url
        self.model = model or config.cosmos3_reasoner_model
        self.api_key = api_key or config.cosmos3_api_key
        self.timeout_s = timeout_s

    def is_configured(self) -> bool:
        return bool(self.base_url and self.model)

    async def observe_scene(
        self,
        *,
        prompt: str,
        image_bytes: Optional[bytes] = None,
        mime_type: str = "image/png",
        input_kind: str = "photo",
    ) -> CosmosSceneObservation:
        """Call Cosmos Reasoner and parse a scene observation."""

        if not self.is_configured():
            raise CosmosRuntimeError(
                "COSMOS3_REASONER_BASE_URL and COSMOS3_REASONER_MODEL must be configured"
            )

        payload = self._build_payload(
            prompt=prompt,
            image_bytes=image_bytes,
            mime_type=mime_type,
            input_kind=input_kind,
        )
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        try:
            import aiohttp  # noqa: PLC0415
        except ModuleNotFoundError as exc:
            raise CosmosRuntimeError(
                "aiohttp is required for Cosmos runtime calls; install requirements.txt"
            ) from exc

        timeout = aiohttp.ClientTimeout(total=self.timeout_s)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            try:
                async with session.post(
                    chat_completions_url(self.base_url),
                    json=payload,
                    headers=headers,
                ) as resp:
                    body = await resp.text()
                    if resp.status != 200:
                        raise CosmosRuntimeError(
                            f"Cosmos Reasoner API error {resp.status}: {body[:500]}"
                        )
            except aiohttp.ClientError as exc:
                raise CosmosRuntimeError(f"Cosmos Reasoner connection failed: {exc}") from exc

        try:
            data = json.loads(body)
            message = data["choices"][0]["message"]
            content = message.get("content") or message.get("reasoning_content") or ""
        except Exception:
            logger.debug("Parsing Cosmos response as raw JSON object")
            content = body

        observation_json = extract_json_object(content)
        observation_json.setdefault("prompt", prompt)
        observation_json.setdefault("input_kind", input_kind)
        return CosmosSceneObservation.model_validate(observation_json)

    def _build_payload(
        self,
        *,
        prompt: str,
        image_bytes: Optional[bytes],
        mime_type: str,
        input_kind: str,
    ) -> Dict[str, Any]:
        user_text = (
            f"{COSMOS_SCENE_PROMPT}\n\n"
            f"User intent: {prompt or 'Reconstruct this robotics scene.'}\n"
            f"Input kind: {input_kind}"
        )
        if image_bytes:
            image_b64 = base64.b64encode(image_bytes).decode("ascii")
            content: Any = [
                {"type": "text", "text": user_text},
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{mime_type};base64,{image_b64}",
                    },
                },
            ]
        else:
            content = user_text

        return {
            "model": self.model,
            "messages": [
                {"role": "system", "content": "Return strict JSON for Isaac Assist."},
                {"role": "user", "content": content},
            ],
            "temperature": 0.0,
            "max_tokens": 4096,
        }


def build_cosmos3_reasoner() -> Cosmos3ReasonerClient:
    """Factory used by routes/tests."""

    return Cosmos3ReasonerClient()


__all__ = [
    "COSMOS_SCENE_PROMPT",
    "Cosmos3ReasonerClient",
    "CosmosRuntimeError",
    "build_cosmos3_reasoner",
    "chat_completions_url",
    "extract_json_object",
]
