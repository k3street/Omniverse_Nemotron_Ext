"""
VisionRouter: tries Ollama (primary) then Gemini (backup).

Instantiate with a primary and fallback provider. Any method that returns an
empty list or raises will automatically retry on the fallback. The `model`
property reflects the last provider that successfully answered.
"""
import logging
from typing import Dict, List, Optional, Union

logger = logging.getLogger(__name__)


class VisionRouter:
    """
    Wraps two vision providers and implements primary→fallback routing.

    Usage::

        router = VisionRouter(
            primary=OllamaVisionProvider(),
            fallback=GeminiVisionProvider(),
        )
        detections = await router.detect_objects(image_bytes, mime_type)
    """

    def __init__(self, primary, fallback):
        self._primary = primary
        self._fallback = fallback
        self.model: str = getattr(primary, "model", "ollama")

    # ── public API (same as GeminiVisionProvider / OllamaVisionProvider) ──

    async def detect_objects(
        self,
        image_bytes: bytes,
        mime_type: str = "image/png",
        labels=None,
        max_objects: int = 10,
    ) -> List[Dict]:
        result = await self._try_primary(
            "detect_objects", image_bytes, mime_type, labels=labels, max_objects=max_objects
        )
        if result is None:
            result = await self._run_fallback(
                "detect_objects", image_bytes, mime_type, labels=labels, max_objects=max_objects
            )
        return result or []

    async def detect_bounding_boxes(
        self,
        image_bytes: bytes,
        mime_type: str = "image/png",
        max_objects: int = 25,
    ) -> List[Dict]:
        result = await self._try_primary(
            "detect_bounding_boxes", image_bytes, mime_type, max_objects=max_objects
        )
        if result is None:
            result = await self._run_fallback(
                "detect_bounding_boxes", image_bytes, mime_type, max_objects=max_objects
            )
        return result or []

    async def plan_trajectory(
        self,
        image_bytes: bytes,
        instruction: str,
        num_points: int = 15,
        mime_type: str = "image/png",
    ) -> List[Dict]:
        result = await self._try_primary(
            "plan_trajectory", image_bytes, instruction, num_points=num_points, mime_type=mime_type
        )
        if result is None:
            result = await self._run_fallback(
                "plan_trajectory", image_bytes, instruction, num_points=num_points, mime_type=mime_type
            )
        return result or []

    async def analyze_scene(
        self,
        image_bytes: bytes,
        question: str,
        mime_type: str = "image/png",
    ) -> str:
        try:
            text = await self._primary.analyze_scene(image_bytes, question, mime_type)
            if text and not text.startswith("Vision analysis failed"):
                self.model = getattr(self._primary, "model", "ollama")
                return text
        except Exception as exc:
            logger.warning("Primary vision provider failed for analyze_scene: %s", exc)
        # fallback
        try:
            text = await self._fallback.analyze_scene(image_bytes, question, mime_type)
            self.model = getattr(self._fallback, "model", "gemini")
            return text
        except Exception as exc:
            logger.error("Fallback vision provider also failed for analyze_scene: %s", exc)
            return "Vision analysis failed — both providers unavailable."

    # ── internals ──────────────────────────────────────────────────────────

    async def _try_primary(self, method: str, *args, **kwargs) -> Optional[List[Dict]]:
        """
        Call `method` on the primary provider.
        Returns the result (possibly empty list) if successful, or None on error.
        We treat an empty list as a trigger to try the fallback.
        """
        try:
            result = await getattr(self._primary, method)(*args, **kwargs)
            if result:  # non-empty list → primary answered
                self.model = getattr(self._primary, "model", "ollama")
                return result
            # empty list — fall through to backup
            logger.debug("Primary vision provider returned empty for %s; trying fallback", method)
            return None
        except Exception as exc:
            logger.warning("Primary vision provider raised for %s: %s", method, exc)
            return None

    async def _run_fallback(self, method: str, *args, **kwargs) -> Optional[List[Dict]]:
        try:
            result = await getattr(self._fallback, method)(*args, **kwargs)
            self.model = getattr(self._fallback, "model", "gemini")
            return result
        except Exception as exc:
            logger.error("Fallback vision provider raised for %s: %s", method, exc)
            return None


def build_vision_router() -> VisionRouter:
    """
    Factory: instantiate Ollama (primary) + Gemini (fallback) from config.
    Imported by tool_executor._get_vision_provider().
    """
    from .vision_ollama import OllamaVisionProvider
    from .vision_gemini import GeminiVisionProvider

    return VisionRouter(
        primary=OllamaVisionProvider(),
        fallback=GeminiVisionProvider(),
    )
