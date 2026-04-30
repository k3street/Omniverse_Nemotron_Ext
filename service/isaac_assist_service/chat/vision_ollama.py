"""
Ollama vision provider for spatial reasoning tasks.

Sends viewport images to a local Ollama instance (e.g. nemotron3:33b or any
multimodal model available in Ollama) and parses structured JSON responses
matching the same interface as GeminiVisionProvider.
"""
import aiohttp
import base64
import json
import logging
from typing import Dict, List, Optional

from ..config import config

logger = logging.getLogger(__name__)

_POINT_FMT = (
    'The answer should follow the json format: [{"point": <point>, "label": <label>}, ...]. '
    "The points are in [y, x] format normalized to 0-1000."
)
_BOX_FMT = (
    'The format should be: [{"box_2d": [ymin, xmin, ymax, xmax], "label": <label>}] '
    "normalized to 0-1000. The values in box_2d must only be integers."
)


class OllamaVisionProvider:
    """
    Calls a local Ollama multimodal model (e.g. nemotron3:33b) for vision tasks.

    Uses the Ollama /api/chat endpoint with the ``images`` field to pass
    base64-encoded frames, then parses structured JSON from the model reply.
    """

    def __init__(
        self,
        host: str = "",
        port: int = 0,
        model: str = "",
        timeout: float = 60.0,
    ):
        self.host = host or config.ollama_host
        self.port = port or config.ollama_vision_port
        self.model = model or config.ollama_vision_model
        self.base_url = f"http://{self.host}:{self.port}/api/chat"
        self.timeout = timeout

    # ── public API (same as GeminiVisionProvider) ─────────────────────────

    async def detect_objects(
        self,
        image_bytes: bytes,
        mime_type: str = "image/png",
        labels: Optional[List[str]] = None,
        max_objects: int = 10,
    ) -> List[Dict]:
        """Detect objects in an image, returning normalized 2D points + labels."""
        if labels:
            prompt = (
                f"Get all points matching the following objects: {', '.join(labels)}. "
                "The label returned should be an identifying name for the object detected. "
                + _POINT_FMT
            )
        else:
            prompt = (
                f"Point to no more than {max_objects} items in the image. "
                "The label returned should be an identifying name for the object detected. "
                + _POINT_FMT
            )
        return await self._query_image(image_bytes, prompt)

    async def detect_bounding_boxes(
        self,
        image_bytes: bytes,
        mime_type: str = "image/png",
        max_objects: int = 25,
    ) -> List[Dict]:
        """Detect objects and return 2D bounding boxes (normalized 0-1000)."""
        prompt = (
            f"Return bounding boxes as a JSON array with labels. Limit to {max_objects} objects. "
            "Include as many objects as you can identify in the scene. "
            "If an object is present multiple times, name them with unique characteristics. "
            + _BOX_FMT
        )
        return await self._query_image(image_bytes, prompt)

    async def plan_trajectory(
        self,
        image_bytes: bytes,
        instruction: str,
        num_points: int = 15,
        mime_type: str = "image/png",
    ) -> List[Dict]:
        """Generate a trajectory of 2D points for a robotic task."""
        prompt = (
            f"{instruction} Generate {num_points} points for the trajectory. "
            "The points should be labeled by order from '0' (start) to the final point. "
            + _POINT_FMT
        )
        return await self._query_image(image_bytes, prompt)

    async def analyze_scene(
        self,
        image_bytes: bytes,
        question: str,
        mime_type: str = "image/png",
    ) -> str:
        """Free-form spatial reasoning about a scene image. Returns text."""
        reply = await self._call_api(image_bytes, question)
        return reply or "Vision analysis failed — Ollama did not respond."

    # ── internals ──────────────────────────────────────────────────────────

    async def _query_image(self, image_bytes: bytes, prompt: str) -> List[Dict]:
        reply = await self._call_api(image_bytes, prompt)
        if not reply:
            return []
        return _parse_json_array(reply)

    async def _call_api(self, image_bytes: bytes, prompt: str) -> Optional[str]:
        b64 = base64.b64encode(image_bytes).decode("utf-8")
        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "user",
                    "content": prompt,
                    "images": [b64],
                }
            ],
            "stream": False,
            "options": {"temperature": 0.1},
        }
        timeout = aiohttp.ClientTimeout(total=self.timeout)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            try:
                async with session.post(self.base_url, json=payload) as resp:
                    if resp.status != 200:
                        err = await resp.text()
                        logger.error("Ollama Vision API %d: %s", resp.status, err[:500])
                        return None
                    data = await resp.json()
                    return data.get("message", {}).get("content", "")
            except aiohttp.ClientError as e:
                logger.error("Ollama Vision connection error: %s", e)
                return None

    async def is_available(self) -> bool:
        """Return True if the Ollama endpoint responds to a lightweight ping."""
        url = f"http://{self.host}:{self.port}/api/tags"
        try:
            timeout = aiohttp.ClientTimeout(total=3.0)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url) as resp:
                    return resp.status == 200
        except Exception:
            return False


def _parse_json_array(text: str) -> List[Dict]:
    """Shared parser reused by OllamaVisionProvider and VisionRouter."""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0]
    try:
        result = json.loads(text)
        if isinstance(result, list):
            return result
    except json.JSONDecodeError:
        start = text.find("[")
        end = text.rfind("]")
        if start != -1 and end != -1:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                pass
    logger.warning("Could not parse Ollama vision response as JSON array: %s", text[:200])
    return []
