"""
Gemini Robotics-ER 1.6 vision provider for spatial reasoning tasks.

Uses the Google GenAI API to analyze viewport images for:
- Object detection (points + bounding boxes)
- Object tracking across frames
- Trajectory planning from images
- Scene understanding and spatial reasoning
"""
import aiohttp
import base64
import json
import logging
from typing import Dict, List, Optional

from ..config import config

logger = logging.getLogger(__name__)


class GeminiVisionProvider:
    """
    Calls Gemini Robotics-ER 1.6 for vision-based spatial reasoning.
    Separate from the chat LLM — this handles image analysis only.
    """

    def __init__(
        self,
        api_key: str = "",
        model: str = "",
    ):
        self.api_key = api_key or config.api_key_gemini
        self.model = model or config.vision_model_name
        self.base_url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{self.model}:generateContent?key={self.api_key}"
        )

    async def detect_objects(
        self,
        image_bytes: bytes,
        mime_type: str = "image/png",
        labels: Optional[List[str]] = None,
        max_objects: int = 10,
    ) -> List[Dict]:
        """
        Detect objects in an image, returning normalized 2D points + labels.

        Returns: [{"point": [y, x], "label": "..."}, ...]
            Coordinates normalized to 0-1000.
        """
        if labels:
            prompt = (
                f"Get all points matching the following objects: {', '.join(labels)}. "
                "The label returned should be an identifying name for the object detected. "
                'The answer should follow the json format: [{"point": <point>, "label": <label>}, ...]. '
                "The points are in [y, x] format normalized to 0-1000."
            )
        else:
            prompt = (
                f"Point to no more than {max_objects} items in the image. "
                "The label returned should be an identifying name for the object detected. "
                'The answer should follow the json format: [{"point": <point>, "label": <label>}, ...]. '
                "The points are in [y, x] format normalized to 0-1000."
            )
        return await self._query_image(image_bytes, mime_type, prompt)

    async def detect_bounding_boxes(
        self,
        image_bytes: bytes,
        mime_type: str = "image/png",
        max_objects: int = 25,
    ) -> List[Dict]:
        """
        Detect objects and return 2D bounding boxes.

        Returns: [{"box_2d": [ymin, xmin, ymax, xmax], "label": "..."}, ...]
            Coordinates normalized to 0-1000.
        """
        prompt = (
            f"Return bounding boxes as a JSON array with labels. Limit to {max_objects} objects. "
            "Include as many objects as you can identify in the scene. "
            "If an object is present multiple times, name them with unique characteristics. "
            'The format should be: [{"box_2d": [ymin, xmin, ymax, xmax], "label": <label>}] '
            "normalized to 0-1000. The values in box_2d must only be integers."
        )
        return await self._query_image(image_bytes, mime_type, prompt)

    async def plan_trajectory(
        self,
        image_bytes: bytes,
        instruction: str,
        num_points: int = 15,
        mime_type: str = "image/png",
    ) -> List[Dict]:
        """
        Generate a trajectory of 2D points for a robotic task.

        Returns: [{"point": [y, x], "label": "0"}, {"point": [y, x], "label": "1"}, ...]
        """
        prompt = (
            f"{instruction} Generate {num_points} points for the trajectory. "
            "The points should be labeled by order from '0' (start) to the final point. "
            'The answer should follow the json format: [{"point": <point>, "label": <label>}, ...]. '
            "The points are in [y, x] format normalized to 0-1000."
        )
        return await self._query_image(image_bytes, mime_type, prompt, thinking_budget=1024)

    async def analyze_scene(
        self,
        image_bytes: bytes,
        question: str,
        mime_type: str = "image/png",
    ) -> str:
        """
        Free-form spatial reasoning about a scene image.

        Returns: text response from the model.
        """
        payload = self._build_payload(image_bytes, mime_type, question, thinking_budget=2048)
        data = await self._call_api(payload)
        if data is None:
            return "Vision analysis failed — check Gemini API key."
        return self._extract_text(data)

    # ── internals ──────────────────────────────────────────────────────────

    async def _query_image(
        self,
        image_bytes: bytes,
        mime_type: str,
        prompt: str,
        thinking_budget: int = 0,
    ) -> List[Dict]:
        payload = self._build_payload(image_bytes, mime_type, prompt, thinking_budget)
        data = await self._call_api(payload)
        if data is None:
            return []
        text = self._extract_text(data)
        return self._parse_json_array(text)

    def _build_payload(
        self,
        image_bytes: bytes,
        mime_type: str,
        prompt: str,
        thinking_budget: int = 0,
    ) -> Dict:
        b64 = base64.b64encode(image_bytes).decode("utf-8")
        payload: Dict = {
            "contents": [{
                "parts": [
                    {"inline_data": {"mime_type": mime_type, "data": b64}},
                    {"text": prompt},
                ]
            }],
            "generationConfig": {"temperature": 1.0},
        }
        if thinking_budget >= 0:
            payload["generationConfig"]["thinkingConfig"] = {
                "thinkingBudget": thinking_budget
            }
        return payload

    async def _call_api(self, payload: Dict) -> Optional[Dict]:
        async with aiohttp.ClientSession() as session:
            try:
                async with session.post(self.base_url, json=payload) as resp:
                    if resp.status != 200:
                        err = await resp.text()
                        logger.error("Gemini Vision API %d: %s", resp.status, err[:500])
                        return None
                    return await resp.json()
            except aiohttp.ClientError as e:
                logger.error("Gemini Vision connection error: %s", e)
                return None

    @staticmethod
    def _extract_text(data: Dict) -> str:
        try:
            parts = data["candidates"][0]["content"]["parts"]
            return "\n".join(p["text"] for p in parts if "text" in p)
        except (KeyError, IndexError):
            return ""

    @staticmethod
    def _parse_json_array(text: str) -> List[Dict]:
        text = text.strip()
        # Strip markdown code fencing if present
        if text.startswith("```"):
            text = text.split("\n", 1)[-1].rsplit("```", 1)[0]
        try:
            result = json.loads(text)
            if isinstance(result, list):
                return result
        except json.JSONDecodeError:
            # Try to find JSON array within the text
            start = text.find("[")
            end = text.rfind("]")
            if start != -1 and end != -1:
                try:
                    return json.loads(text[start : end + 1])
                except json.JSONDecodeError:
                    pass
        logger.warning("Could not parse vision response as JSON array: %s", text[:200])
        return []
