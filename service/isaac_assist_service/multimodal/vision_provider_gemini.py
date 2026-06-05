"""Phase 76 — Vision: SPEC/PROVIDER layer for Gemini Vision.

This module is the TYPED PROTOCOL LAYER for vision providers — it ships
the request/response dataclasses (VisionRequest, BoundingBox,
VisionResponse), the VisionProvider Protocol, a NotImplementedError-
gated `GeminiVisionProvider` stub for unit testing the abstraction, and
`MockVisionProvider` for deterministic in-process responses.

## Relationship to `service.isaac_assist_service.chat.vision_gemini`

For ACTUAL live Gemini API calls, use the async implementation at
`service.isaac_assist_service.chat.vision_gemini.GeminiVisionProvider`.
That module is the concrete API caller (uses aiohttp, GEMINI_API_KEY,
real network) and is the one wired into `handlers/vision.py` and
`handlers/_shared.py`.

The two modules are LAYERS, not duplicates:

| Layer                              | Module                                                    | Purpose                                        |
|------------------------------------|-----------------------------------------------------------|------------------------------------------------|
| Typed protocol + dataclasses + mock| `multimodal.vision_provider_gemini` (this module)         | Abstraction + tests, no network               |
| Concrete async API caller          | `chat.vision_gemini`                                      | Hits Gemini Vision REST endpoint              |

If you need typed inputs/outputs WITH a real API call, build a thin
adapter that wraps `chat.vision_gemini.GeminiVisionProvider` in a class
implementing the VisionProvider Protocol defined here.

Per specs/IA_FULL_SPEC_2026-05-10.md Phase 76.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional, Protocol


# ---------------------------------------------------------------------------
# Phase metadata
# ---------------------------------------------------------------------------

PHASE_ID = 76
PHASE_TITLE = "Vision tool handlers: real Gemini Vision"
PHASE_STATUS = "landed"


def get_phase_metadata() -> Dict[str, Any]:
    """Return phase metadata for spec-coverage audits."""
    return {
        "phase": PHASE_ID,
        "title": PHASE_TITLE,
        "status": PHASE_STATUS,
        "spec_ref": "specs/IA_FULL_SPEC_2026-05-10.md Phase 76",
    }


# ---------------------------------------------------------------------------
# Prompt templates
# ---------------------------------------------------------------------------

PROMPT_TEMPLATES: Dict[str, str] = {
    "scene_analyze": (
        "System: You are an expert Isaac Sim scene analyst. Analyse the provided "
        "image and describe the scene composition, object placement, robot poses, "
        "conveyor states, and any anomalies. Be concise and structured.\n"
        "User: {user_prompt}"
    ),
    "bounding_boxes": (
        "System: You are a precise object detector for robotic simulation scenes. "
        "Return a JSON array of bounding boxes. Each element must have keys: "
        "label, confidence, x_min, y_min, x_max, y_max. "
        "Coordinates are normalised 0.0–1.0 relative to image width/height.\n"
        "User: {user_prompt}"
    ),
    "detect_objects": (
        "System: You are an object-detection model for robotic simulation scenes. "
        "List all distinct object classes visible in the image, one per line. "
        "Also return bounding boxes as JSON where possible.\n"
        "User: {user_prompt}"
    ),
    "plan_trajectory": (
        "System: You are a motion-planning assistant for robotic simulation. "
        "Analyse the scene image and propose a collision-free waypoint trajectory "
        "for the robot arm to reach the goal. Express waypoints as joint-space or "
        "Cartesian coordinates where visible.\n"
        "User: {user_prompt}"
    ),
}


# ---------------------------------------------------------------------------
# Dataclasses — request / response shapes
# ---------------------------------------------------------------------------

@dataclass
class VisionRequest:
    """Input to any VisionProvider method."""
    image_bytes: bytes
    prompt: str
    task: Literal["scene_analyze", "bounding_boxes", "detect_objects", "plan_trajectory"]
    max_tokens: int = 1024
    temperature: float = 0.3
    image_mime: str = "image/png"


@dataclass
class BoundingBox:
    """A single detected bounding box."""
    label: str
    confidence: float
    x_min: float
    y_min: float
    x_max: float
    y_max: float


@dataclass
class VisionResponse:
    """Output from any VisionProvider method."""
    task: str
    text: Optional[str] = None
    bounding_boxes: List[BoundingBox] = field(default_factory=list)
    tokens_used: int = 0
    latency_ms: float = 0.0
    model: str = ""
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# VisionProvider Protocol
# ---------------------------------------------------------------------------

class VisionProvider(Protocol):
    """Protocol that every vision provider must satisfy."""

    def analyze_scene(self, req: VisionRequest) -> VisionResponse:
        """Analyse the overall scene composition."""
        ...

    def detect_objects(self, req: VisionRequest) -> VisionResponse:
        """Detect and classify objects in the image."""
        ...

    def bounding_boxes(self, req: VisionRequest) -> VisionResponse:
        """Return normalised bounding boxes for all detected objects."""
        ...

    def plan_trajectory(self, req: VisionRequest) -> VisionResponse:
        """Propose a robot trajectory based on the scene image."""
        ...


# ---------------------------------------------------------------------------
# GeminiVisionProvider — real implementation stub
# ---------------------------------------------------------------------------

_GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"

_TASK_PATHS: Dict[str, str] = {
    "scene_analyze": "generateContent",
    "bounding_boxes": "generateContent",
    "detect_objects": "generateContent",
    "plan_trajectory": "generateContent",
}


class GeminiVisionProvider:
    """
    Real Gemini Vision provider.

    Methods raise NotImplementedError because live API calls require
    GEMINI_API_KEY and must be executed in opus-runtime mode (real GPU +
    network). The class is fully instantiable for unit-testing the provider
    abstraction.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = "gemini-2.0-flash-exp",
        timeout_s: float = 30.0,
        max_retries: int = 3,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.timeout_s = timeout_s
        self.max_retries = max_retries

    # ------------------------------------------------------------------
    # Internal helpers — pure functions, unit-testable
    # ------------------------------------------------------------------

    def _build_url(self, task: str) -> str:
        """Return the Gemini REST endpoint URL for the given task."""
        path = _TASK_PATHS.get(task, "generateContent")
        return f"{_GEMINI_BASE_URL}/{self.model}:{path}"

    def _canonical_prompt_for(self, task: str, user_prompt: str) -> str:
        """Expand the prompt template for `task` with `user_prompt`."""
        template = PROMPT_TEMPLATES.get(
            task,
            "System: You are a helpful vision assistant.\nUser: {user_prompt}",
        )
        return template.format(user_prompt=user_prompt)

    def _retry_delays(self, attempt: int) -> float:
        """Return exponential-backoff delay in seconds for `attempt` (0-based)."""
        return 0.5 * math.pow(2, attempt)

    # ------------------------------------------------------------------
    # VisionProvider interface
    # ------------------------------------------------------------------

    def analyze_scene(self, req: VisionRequest) -> VisionResponse:  # noqa: ARG002
        raise NotImplementedError(
            "GeminiVisionProvider.analyze_scene: real API call — "
            "set GEMINI_API_KEY and run in opus-runtime mode"
        )

    def detect_objects(self, req: VisionRequest) -> VisionResponse:  # noqa: ARG002
        raise NotImplementedError(
            "GeminiVisionProvider.detect_objects: real API call — "
            "set GEMINI_API_KEY and run in opus-runtime mode"
        )

    def bounding_boxes(self, req: VisionRequest) -> VisionResponse:  # noqa: ARG002
        raise NotImplementedError(
            "GeminiVisionProvider.bounding_boxes: real API call — "
            "set GEMINI_API_KEY and run in opus-runtime mode"
        )

    def plan_trajectory(self, req: VisionRequest) -> VisionResponse:  # noqa: ARG002
        raise NotImplementedError(
            "GeminiVisionProvider.plan_trajectory: real API call — "
            "set GEMINI_API_KEY and run in opus-runtime mode"
        )


# ---------------------------------------------------------------------------
# MockVisionProvider — deterministic in-process responses
# ---------------------------------------------------------------------------

_MOCK_BOXES = [
    BoundingBox("cube_1", 0.95, 0.1, 0.2, 0.3, 0.4),
    BoundingBox("cube_2", 0.88, 0.5, 0.1, 0.7, 0.35),
    BoundingBox("table", 0.99, 0.0, 0.6, 1.0, 1.0),
]

_MOCK_META = dict(tokens_used=50, latency_ms=12.5, model="mock-vision")


class MockVisionProvider:
    """
    Deterministic mock that satisfies the VisionProvider protocol.

    Returns well-shaped VisionResponse objects for each task type without
    touching any external service.
    """

    def analyze_scene(self, req: VisionRequest) -> VisionResponse:  # noqa: ARG002
        return VisionResponse(
            task="scene_analyze",
            text="Mock scene: 3 cubes on table",
            **_MOCK_META,
        )

    def detect_objects(self, req: VisionRequest) -> VisionResponse:  # noqa: ARG002
        return VisionResponse(
            task="detect_objects",
            text="cube, table",
            bounding_boxes=list(_MOCK_BOXES),
            **_MOCK_META,
        )

    def bounding_boxes(self, req: VisionRequest) -> VisionResponse:  # noqa: ARG002
        return VisionResponse(
            task="bounding_boxes",
            bounding_boxes=list(_MOCK_BOXES),
            **_MOCK_META,
        )

    def plan_trajectory(self, req: VisionRequest) -> VisionResponse:  # noqa: ARG002
        return VisionResponse(
            task="plan_trajectory",
            text="waypoints: [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6], [0.7, 0.8, 0.9]]",
            **_MOCK_META,
        )


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def select_provider(
    use_real: bool = False,
    api_key: Optional[str] = None,
) -> "GeminiVisionProvider | MockVisionProvider":
    """Return either the live Gemini provider or the deterministic mock."""
    if use_real:
        return GeminiVisionProvider(api_key=api_key)
    return MockVisionProvider()
