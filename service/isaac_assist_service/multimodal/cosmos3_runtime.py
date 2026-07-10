"""Cosmos 3 Reasoner runtime client.

The committed adapter maps already-structured observations into LayoutSpec.
This module owns the upstream model invocation: image/prompt in,
``CosmosSceneObservation`` out.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass, field
import json
import logging
from typing import Any, Dict, Optional, Protocol

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


def image_generations_url(base_url: str) -> str:
    """Normalize a base URL to an OpenAI-compatible image generation URL."""

    value = base_url.rstrip("/")
    if value.endswith("/images/generations"):
        return value
    if value.endswith("/v1"):
        return f"{value}/images/generations"
    return f"{value}/v1/images/generations"


def videos_sync_url(base_url: str) -> str:
    """Normalize a base URL to a vLLM-Omni synchronous video URL."""

    value = base_url.rstrip("/")
    if value.endswith("/videos/sync"):
        return value
    if value.endswith("/v1"):
        return f"{value}/videos/sync"
    return f"{value}/v1/videos/sync"


def gemini_generate_content_url(
    model: str,
    *,
    api_key: str = "",
    base_url: str = "",
) -> str:
    """Build a Gemini generateContent endpoint URL."""

    value = (
        base_url
        or config.gemini_robotics_er_base_url
        or "https://generativelanguage.googleapis.com/v1beta/models"
    ).rstrip("/")
    if value.endswith(":generateContent"):
        endpoint = value
    else:
        endpoint = f"{value}/{model}:generateContent"
    return f"{endpoint}?key={api_key}" if api_key else endpoint


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


class SceneReasonerClient(Protocol):
    """Common interface for Cosmos-compatible scene reasoners."""

    def is_configured(self) -> bool:
        ...

    async def observe_scene(
        self,
        *,
        prompt: str,
        image_bytes: Optional[bytes] = None,
        mime_type: str = "image/png",
        input_kind: str = "photo",
    ) -> CosmosSceneObservation:
        ...


@dataclass
class CosmosGenerationResult:
    """Media/action payload returned by a Cosmos 3 Generator endpoint."""

    mode: str
    content_type: str
    media_bytes: bytes = b""
    action: Optional[Any] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    extension: str = ".bin"


class CosmosGeneratorClient(Protocol):
    """Common interface for Cosmos-compatible generator endpoints."""

    def is_configured(self) -> bool:
        ...

    async def generate(
        self,
        *,
        mode: str,
        prompt: str,
        negative_prompt: str = "",
        image_bytes: Optional[bytes] = None,
        image_mime_type: str = "image/png",
        video_bytes: Optional[bytes] = None,
        video_mime_type: str = "video/mp4",
        size: str = "320x192",
        num_frames: int = 24,
        fps: int = 12,
        num_inference_steps: int = 35,
        guidance_scale: float = 6.0,
        flow_shift: float = 10.0,
        seed: int = 0,
        guardrails: bool = True,
        extra_params: Optional[Dict[str, Any]] = None,
        domain_name: Optional[str] = None,
        raw_action_dim: Optional[int] = None,
        action_chunk_size: Optional[int] = None,
        action_path: Optional[str] = None,
        action_values: Optional[Any] = None,
    ) -> CosmosGenerationResult:
        ...


def _decode_base64_media(value: str) -> bytes:
    raw = value.strip()
    if "," in raw and raw.startswith("data:"):
        raw = raw.split(",", 1)[1]
    return base64.b64decode(raw, validate=False)


def _media_extension(content_type: str, *, fallback: str = ".bin") -> str:
    kind = content_type.split(";", 1)[0].strip().lower()
    return {
        "image/jpeg": ".jpg",
        "image/jpg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
        "video/mp4": ".mp4",
        "audio/aac": ".aac",
        "application/json": ".json",
    }.get(kind, fallback)


class Cosmos3GeneratorClient:
    """vLLM-Omni/OpenAI-compatible client for Cosmos 3 Generator endpoints."""

    IMAGE_MODES = {"text_to_image"}
    SOUND_MODES = {"text_to_video_with_sound", "image_to_video_with_sound", "video_to_video_with_sound"}
    ACTION_MODES = {"policy", "inverse_dynamics", "forward_dynamics"}
    REFERENCE_IMAGE_MODES = {"image_to_video", "image_to_video_with_sound", "forward_dynamics"}
    REFERENCE_VIDEO_MODES = {"video_to_video", "video_to_video_with_sound", "inverse_dynamics"}
    SUPPORTED_MODES = (
        IMAGE_MODES
        | {
            "text_to_video",
            "image_to_video",
            "video_to_video",
            "text_to_video_with_sound",
            "image_to_video_with_sound",
            "video_to_video_with_sound",
        }
        | ACTION_MODES
    )

    def __init__(
        self,
        *,
        base_url: str = "",
        model: str = "",
        api_key: str = "",
        timeout_s: float = 900.0,
    ) -> None:
        self.base_url = base_url or config.cosmos3_generator_base_url
        self.model = model or config.cosmos3_generator_model
        self.api_key = api_key or config.cosmos3_api_key
        self.timeout_s = timeout_s

    def is_configured(self) -> bool:
        return bool(self.base_url and self.model)

    async def generate(
        self,
        *,
        mode: str,
        prompt: str,
        negative_prompt: str = "",
        image_bytes: Optional[bytes] = None,
        image_mime_type: str = "image/png",
        video_bytes: Optional[bytes] = None,
        video_mime_type: str = "video/mp4",
        size: str = "320x192",
        num_frames: int = 24,
        fps: int = 12,
        num_inference_steps: int = 35,
        guidance_scale: float = 6.0,
        flow_shift: float = 10.0,
        seed: int = 0,
        guardrails: bool = True,
        extra_params: Optional[Dict[str, Any]] = None,
        domain_name: Optional[str] = None,
        raw_action_dim: Optional[int] = None,
        action_chunk_size: Optional[int] = None,
        action_path: Optional[str] = None,
        action_values: Optional[Any] = None,
    ) -> CosmosGenerationResult:
        """Generate image/video/action artifacts through a configured endpoint."""

        mode = mode.strip().lower().replace("-", "_")
        if not self.is_configured():
            raise CosmosRuntimeError(
                "COSMOS3_GENERATOR_BASE_URL and COSMOS3_GENERATOR_MODEL must be configured"
            )
        if mode not in self.SUPPORTED_MODES:
            supported = ", ".join(sorted(self.SUPPORTED_MODES))
            raise CosmosRuntimeError(
                f"unsupported Cosmos generation mode {mode!r}; choose one of: {supported}"
            )
        if mode in self.IMAGE_MODES:
            return await self._generate_image(
                prompt=prompt,
                negative_prompt=negative_prompt,
                size=size,
                num_inference_steps=num_inference_steps,
                guidance_scale=guidance_scale,
                seed=seed,
                guardrails=guardrails,
                extra_params=extra_params or {},
            )
        return await self._generate_video(
            mode=mode,
            prompt=prompt,
            negative_prompt=negative_prompt,
            image_bytes=image_bytes,
            image_mime_type=image_mime_type,
            video_bytes=video_bytes,
            video_mime_type=video_mime_type,
            size=size,
            num_frames=num_frames,
            fps=fps,
            num_inference_steps=num_inference_steps,
            guidance_scale=guidance_scale,
            flow_shift=flow_shift,
            seed=seed,
            guardrails=guardrails,
            extra_params=extra_params or {},
            domain_name=domain_name,
            raw_action_dim=raw_action_dim,
            action_chunk_size=action_chunk_size,
            action_path=action_path,
            action_values=action_values,
        )

    def _headers(self) -> Dict[str, str]:
        headers: Dict[str, str] = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    async def _generate_image(
        self,
        *,
        prompt: str,
        negative_prompt: str,
        size: str,
        num_inference_steps: int,
        guidance_scale: float,
        seed: int,
        guardrails: bool,
        extra_params: Dict[str, Any],
    ) -> CosmosGenerationResult:
        try:
            import aiohttp  # noqa: PLC0415
        except ModuleNotFoundError as exc:
            raise CosmosRuntimeError(
                "aiohttp is required for Cosmos generator calls; install requirements.txt"
            ) from exc

        extra_args = {
            "guardrails": guardrails,
            "use_resolution_template": False,
            **extra_params,
        }
        payload = {
            "model": self.model,
            "prompt": prompt,
            "negative_prompt": negative_prompt,
            "size": size,
            "n": 1,
            "response_format": "b64_json",
            "num_inference_steps": num_inference_steps,
            "guidance_scale": guidance_scale,
            "seed": seed,
            "extra_args": extra_args,
        }

        timeout = aiohttp.ClientTimeout(total=self.timeout_s)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            try:
                async with session.post(
                    image_generations_url(self.base_url),
                    json=payload,
                    headers={"Content-Type": "application/json", **self._headers()},
                ) as resp:
                    body = await resp.read()
                    if resp.status != 200:
                        raise CosmosRuntimeError(
                            f"Cosmos Generator image API error {resp.status}: "
                            f"{body[:500].decode('utf-8', errors='replace')}"
                        )
                    content_type = resp.headers.get("Content-Type", "application/json")
            except aiohttp.ClientError as exc:
                raise CosmosRuntimeError(f"Cosmos Generator connection failed: {exc}") from exc

        if content_type.startswith("image/"):
            return CosmosGenerationResult(
                mode="text_to_image",
                content_type=content_type,
                media_bytes=body,
                metadata={"provider": "cosmos3_generator", "model": self.model},
                extension=_media_extension(content_type, fallback=".png"),
            )

        try:
            data = json.loads(body.decode("utf-8"))
            first = data.get("data", [{}])[0]
            b64_value = first.get("b64_json") or first.get("image_base64") or first.get("media_base64")
            if not b64_value:
                raise KeyError("missing b64_json/image_base64/media_base64")
            media = _decode_base64_media(str(b64_value))
        except Exception as exc:
            raise CosmosRuntimeError(f"Cosmos image response parse failed: {exc}") from exc

        return CosmosGenerationResult(
            mode="text_to_image",
            content_type="image/png",
            media_bytes=media,
            metadata={
                "provider": "cosmos3_generator",
                "model": self.model,
                "created": data.get("created"),
                "usage": data.get("usage"),
            },
            extension=".png",
        )

    async def _generate_video(
        self,
        *,
        mode: str,
        prompt: str,
        negative_prompt: str,
        image_bytes: Optional[bytes],
        image_mime_type: str,
        video_bytes: Optional[bytes],
        video_mime_type: str,
        size: str,
        num_frames: int,
        fps: int,
        num_inference_steps: int,
        guidance_scale: float,
        flow_shift: float,
        seed: int,
        guardrails: bool,
        extra_params: Dict[str, Any],
        domain_name: Optional[str],
        raw_action_dim: Optional[int],
        action_chunk_size: Optional[int],
        action_path: Optional[str],
        action_values: Optional[Any],
    ) -> CosmosGenerationResult:
        try:
            import aiohttp  # noqa: PLC0415
        except ModuleNotFoundError as exc:
            raise CosmosRuntimeError(
                "aiohttp is required for Cosmos generator calls; install requirements.txt"
            ) from exc

        form = aiohttp.FormData()
        fields: Dict[str, Any] = {
            "model": self.model,
            "prompt": prompt,
            "negative_prompt": negative_prompt,
            "size": size,
            "num_frames": num_frames,
            "fps": fps,
            "num_inference_steps": num_inference_steps,
            "guidance_scale": guidance_scale,
            "flow_shift": flow_shift,
            "seed": seed,
        }
        if mode in self.SOUND_MODES:
            fields["generate_sound"] = "true"

        request_extra = {
            "guardrails": guardrails,
            "use_resolution_template": False,
            "use_duration_template": False,
            **extra_params,
        }
        action_mode = mode if mode in self.ACTION_MODES else None
        if action_mode:
            request_extra["action_mode"] = action_mode
            if domain_name:
                request_extra["domain_name"] = domain_name
            if raw_action_dim is not None:
                request_extra["raw_action_dim"] = raw_action_dim
            if action_chunk_size is not None:
                request_extra["action_chunk_size"] = action_chunk_size
            if action_path:
                request_extra["action_path"] = action_path
            if action_values is not None:
                request_extra["action_values"] = action_values

        fields["extra_params"] = json.dumps(request_extra)
        for key, value in fields.items():
            form.add_field(key, str(value))

        if (mode in self.REFERENCE_IMAGE_MODES or mode == "policy") and image_bytes:
            form.add_field(
                "input_reference",
                image_bytes,
                filename="input_reference.png",
                content_type=image_mime_type,
            )
        elif (mode in self.REFERENCE_VIDEO_MODES or mode == "policy") and video_bytes:
            form.add_field(
                "input_reference",
                video_bytes,
                filename="input_reference.mp4",
                content_type=video_mime_type,
            )

        timeout = aiohttp.ClientTimeout(total=self.timeout_s)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            try:
                async with session.post(
                    videos_sync_url(self.base_url),
                    data=form,
                    headers=self._headers(),
                ) as resp:
                    body = await resp.read()
                    if resp.status != 200:
                        raise CosmosRuntimeError(
                            f"Cosmos Generator video API error {resp.status}: "
                            f"{body[:500].decode('utf-8', errors='replace')}"
                        )
                    content_type = resp.headers.get("Content-Type", "video/mp4")
            except aiohttp.ClientError as exc:
                raise CosmosRuntimeError(f"Cosmos Generator connection failed: {exc}") from exc

        if content_type.startswith("application/json"):
            return self._parse_generation_json(mode=mode, body=body)

        return CosmosGenerationResult(
            mode=mode,
            content_type=content_type,
            media_bytes=body,
            metadata={"provider": "cosmos3_generator", "model": self.model},
            extension=_media_extension(content_type, fallback=".mp4"),
        )

    def _parse_generation_json(self, *, mode: str, body: bytes) -> CosmosGenerationResult:
        try:
            data = json.loads(body.decode("utf-8"))
        except Exception as exc:
            raise CosmosRuntimeError(f"Cosmos generation JSON parse failed: {exc}") from exc

        candidates = []
        if isinstance(data.get("data"), list):
            candidates.extend(item for item in data["data"] if isinstance(item, dict))
        if isinstance(data, dict):
            candidates.append(data)

        media_bytes = b""
        content_type = "application/json"
        for item in candidates:
            for key, media_type in (
                ("video_base64", "video/mp4"),
                ("media_base64", "video/mp4"),
                ("b64_json", "image/png"),
                ("image_base64", "image/png"),
            ):
                value = item.get(key)
                if value:
                    media_bytes = _decode_base64_media(str(value))
                    content_type = media_type
                    break
            if media_bytes:
                break

        action = (
            data.get("action")
            or data.get("actions")
            or data.get("action_values")
            or data.get("predicted_actions")
        )
        if action is None and candidates:
            for item in candidates:
                action = (
                    item.get("action")
                    or item.get("actions")
                    or item.get("action_values")
                    or item.get("predicted_actions")
                )
                if action is not None:
                    break

        extension = _media_extension(content_type)
        if not media_bytes and action is not None:
            extension = ".json"
        return CosmosGenerationResult(
            mode=mode,
            content_type=content_type,
            media_bytes=media_bytes,
            action=action,
            metadata={"provider": "cosmos3_generator", "model": self.model, "response": data},
            extension=extension,
        )


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
        metadata = observation_json.setdefault("metadata", {})
        if isinstance(metadata, dict):
            metadata.setdefault("provider", "cosmos3")
            metadata.setdefault("model", self.model)
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


class GeminiRoboticsERReasonerClient:
    """Gemini Robotics-ER fallback that returns CosmosSceneObservation JSON."""

    def __init__(
        self,
        *,
        api_key: str = "",
        model: str = "",
        base_url: str = "",
        timeout_s: float = 180.0,
    ) -> None:
        self.api_key = api_key or config.api_key_gemini
        self.model = model or config.gemini_robotics_er_model
        self.base_url = base_url or config.gemini_robotics_er_base_url
        self.timeout_s = timeout_s

    def is_configured(self) -> bool:
        return bool(self.api_key and self.model)

    async def observe_scene(
        self,
        *,
        prompt: str,
        image_bytes: Optional[bytes] = None,
        mime_type: str = "image/png",
        input_kind: str = "photo",
    ) -> CosmosSceneObservation:
        """Call Gemini Robotics-ER and parse a scene observation."""

        if not self.is_configured():
            raise CosmosRuntimeError(
                "GEMINI_API_KEY and GEMINI_ROBOTICS_ER_MODEL must be configured"
            )

        payload = self._build_payload(
            prompt=prompt,
            image_bytes=image_bytes,
            mime_type=mime_type,
            input_kind=input_kind,
        )

        try:
            import aiohttp  # noqa: PLC0415
        except ModuleNotFoundError as exc:
            raise CosmosRuntimeError(
                "aiohttp is required for Gemini runtime calls; install requirements.txt"
            ) from exc

        timeout = aiohttp.ClientTimeout(total=self.timeout_s)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            try:
                async with session.post(
                    gemini_generate_content_url(
                        self.model,
                        api_key=self.api_key,
                        base_url=self.base_url,
                    ),
                    json=payload,
                ) as resp:
                    body = await resp.text()
                    if resp.status != 200:
                        raise CosmosRuntimeError(
                            f"Gemini Robotics-ER API error {resp.status}: {body[:500]}"
                        )
            except aiohttp.ClientError as exc:
                raise CosmosRuntimeError(
                    f"Gemini Robotics-ER connection failed: {exc}"
                ) from exc

        content = self._extract_text(body)
        observation_json = extract_json_object(content)
        observation_json.setdefault("prompt", prompt)
        observation_json.setdefault("input_kind", input_kind)
        metadata = observation_json.setdefault("metadata", {})
        if isinstance(metadata, dict):
            metadata.setdefault("provider", "gemini_robotics_er")
            metadata.setdefault("model", self.model)
            metadata.setdefault("fallback_for", "cosmos3_reasoner")
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
            "You are Gemini Robotics-ER acting as a cloud backup for Cosmos 3 "
            "Reasoner. Preserve the exact JSON schema above.\n\n"
            f"User intent: {prompt or 'Reconstruct this robotics scene.'}\n"
            f"Input kind: {input_kind}"
        )
        parts: list[Dict[str, Any]] = [{"text": user_text}]
        if image_bytes:
            image_b64 = base64.b64encode(image_bytes).decode("ascii")
            parts.insert(
                0,
                {
                    "inline_data": {
                        "mime_type": mime_type,
                        "data": image_b64,
                    }
                },
            )

        return {
            "contents": [{"parts": parts}],
            "generationConfig": {
                "temperature": 0.0,
                "maxOutputTokens": 4096,
                "responseMimeType": "application/json",
            },
        }

    @staticmethod
    def _extract_text(body: str) -> str:
        try:
            data = json.loads(body)
            parts = data["candidates"][0]["content"]["parts"]
            text = "\n".join(part.get("text", "") for part in parts if "text" in part)
            return text or body
        except Exception:
            logger.debug("Parsing Gemini response as raw JSON object")
            return body


class FallbackSceneReasonerClient:
    """Try Cosmos first, then Gemini Robotics-ER if configured."""

    def __init__(
        self,
        *,
        primary: SceneReasonerClient,
        fallback: Optional[SceneReasonerClient] = None,
    ) -> None:
        self.primary = primary
        self.fallback = fallback

    def is_configured(self) -> bool:
        return self.primary.is_configured() or bool(
            self.fallback and self.fallback.is_configured()
        )

    async def observe_scene(
        self,
        *,
        prompt: str,
        image_bytes: Optional[bytes] = None,
        mime_type: str = "image/png",
        input_kind: str = "photo",
    ) -> CosmosSceneObservation:
        errors: list[str] = []

        if self.primary.is_configured():
            try:
                return await self.primary.observe_scene(
                    prompt=prompt,
                    image_bytes=image_bytes,
                    mime_type=mime_type,
                    input_kind=input_kind,
                )
            except CosmosRuntimeError as exc:
                errors.append(str(exc))
                logger.warning("Cosmos 3 Reasoner failed; trying fallback: %s", exc)
        else:
            errors.append("Cosmos 3 Reasoner is not configured")

        if self.fallback and self.fallback.is_configured():
            return await self.fallback.observe_scene(
                prompt=prompt,
                image_bytes=image_bytes,
                mime_type=mime_type,
                input_kind=input_kind,
            )

        detail = "; ".join(errors) if errors else "No scene reasoner is configured"
        raise CosmosRuntimeError(detail)


def build_cosmos3_reasoner() -> SceneReasonerClient:
    """Factory used by routes/tests."""

    primary = Cosmos3ReasonerClient()
    fallback = (
        GeminiRoboticsERReasonerClient()
        if config.gemini_robotics_er_fallback
        else None
    )
    return FallbackSceneReasonerClient(primary=primary, fallback=fallback)


def build_cosmos3_generator() -> CosmosGeneratorClient:
    """Factory used by routes/tests."""

    return Cosmos3GeneratorClient()


__all__ = [
    "COSMOS_SCENE_PROMPT",
    "Cosmos3GeneratorClient",
    "Cosmos3ReasonerClient",
    "CosmosGenerationResult",
    "CosmosGeneratorClient",
    "CosmosRuntimeError",
    "FallbackSceneReasonerClient",
    "GeminiRoboticsERReasonerClient",
    "SceneReasonerClient",
    "build_cosmos3_generator",
    "build_cosmos3_reasoner",
    "chat_completions_url",
    "extract_json_object",
    "gemini_generate_content_url",
    "image_generations_url",
    "videos_sync_url",
]
