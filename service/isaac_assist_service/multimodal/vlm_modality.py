"""
Sketch and photo modality producers per spec §10.

Honest treatment: VLM viability is uncertain (Robotics-ER preview-API,
hand-drawn accuracy issues). This module commits to the foundation
being *VLM-ready*. When a viable VLM lands, the modality producer is
the small adapter below — `produce_layout_spec_from_sketch` and
`produce_layout_spec_from_photo` accept a `vlm_call` callable that
returns LayoutSpec-shaped JSON.

Contract (spec §10.2):
    sketch_modality.produce(image_bytes, prompt_context?) → LayoutSpec
    photo_modality.produce(image_bytes, prompt_context?)  → LayoutSpec

Both:
- Set source.modality = "sketch" or "photo"
- Set source.confidence by VLM's reported certainty (0.4-0.7 sketch,
  0.3-0.6 photo per §7.2 reliability profile)
- Pass VLM-emitted bindings through with source = "modality_emitted"
- Validate produced LayoutSpec against multimodal/validate.py

The functions are testable today with stubbed `vlm_call`; the real VLM
client lands when the upstream API path is verified.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from pydantic import ValidationError

from .types import (
    Intent,
    LayoutSpec,
    Modality,
    Position,
    RoleBinding,
    Size,
    Source,
    TypedObject,
)

logger = logging.getLogger(__name__)


VLM_LAYOUT_SYSTEM_PROMPT = """You are a visual layout extractor for an
Isaac Sim robotics scene. Given an image of a sketch or photograph,
emit a JSON object with this structure:

{
  "pattern_hint": "pick_place" | "sort" | "reorient" | "navigate",
  "structural_features": { ... },
  "structural_tags": ["isaac:..."],
  "objects": [
    {
      "class": "franka_panda" | "ur5e" | "conveyor" | "bin" | "cube" | ...,
      "name": "Franka_1" (alphanumeric prim-path-safe),
      "position": {"x": float, "y": float},
      "size": {"w": float, "h": float},
      "rotation": float (degrees, 0..360),
      "role_hint": "primary_robot" | null,
      "confidence": float (0..1)
    },
    ...
  ],
  "overall_confidence": float (0..1)
}

Coordinates are in meters, world-space. If an object's role is obvious
(e.g. "this Franka is clearly the primary picker"), emit a role_hint.
Otherwise leave it null and let the ratifier decide.

Output ONLY the JSON object. No prose, no markdown fences."""


def _coerce_vlm_object(raw: Dict[str, Any]) -> TypedObject:
    """Map VLM-emitted dict to TypedObject. Raises ValueError on shape mismatch."""
    pos = raw.get("position") or {}
    size = raw.get("size") or {}
    return TypedObject(
        **{
            "class": raw.get("class"),
            "name": raw.get("name"),
            "position": Position(x=float(pos.get("x", 0)), y=float(pos.get("y", 0))),
            "size": Size(w=float(size.get("w", 0.1)), h=float(size.get("h", 0.1))),
            "rotation": float(raw.get("rotation", 0.0)),
            "role_hint": raw.get("role_hint"),
            "metadata": raw.get("metadata") or {},
        }
    )


def _produce_from_vlm(
    image_bytes: bytes,
    vlm_call: Callable[[str, bytes, Optional[str]], str],
    modality: Modality,
    default_confidence: float,
    prompt_context: Optional[str],
) -> LayoutSpec:
    """Shared VLM extraction pipeline for sketch and photo modalities.

    Args:
        image_bytes: image payload (bytes — PNG/JPEG/etc.)
        vlm_call: (system_prompt, image_bytes, prompt_context?) → JSON string
        modality: "sketch" or "photo"
        default_confidence: fallback when VLM omits overall_confidence
        prompt_context: optional user-supplied disambiguating prompt

    Returns:
        LayoutSpec with objects + role-hint bindings (where VLM emitted them)

    Raises:
        ValueError on malformed VLM output
    """
    raw = vlm_call(VLM_LAYOUT_SYSTEM_PROMPT, image_bytes, prompt_context)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"VLM did not return JSON: {e}") from e

    pattern_hint = data.get("pattern_hint") or "pick_place"
    sf = data.get("structural_features") or {}
    tags = list(data.get("structural_tags") or [])
    intent = Intent.model_validate({
        "pattern_hint": pattern_hint,
        "structural_features": sf,
        "structural_tags": tags,
    })

    objects: List[TypedObject] = []
    bindings: Dict[str, RoleBinding] = {}
    for raw_obj in data.get("objects") or []:
        try:
            obj = _coerce_vlm_object(raw_obj)
        except (ValidationError, ValueError) as e:
            logger.warning(f"[{modality}] skipping malformed object {raw_obj}: {e}")
            continue
        objects.append(obj)
        # VLM-emitted role_hint becomes a modality-emitted binding.
        if obj.role_hint:
            bindings[obj.role_hint] = RoleBinding(
                object_id=obj.id,
                source="modality_emitted",
                confidence=float(raw_obj.get("confidence", 0.6)),
                timestamp=datetime.now(timezone.utc),
            )

    overall = float(data.get("overall_confidence", default_confidence))
    overall = max(0.0, min(1.0, overall))

    return LayoutSpec(
        intent=intent,
        source=Source(
            modality=modality,
            confidence=overall,
            timestamp=datetime.now(timezone.utc),
            raw_input=None,  # bytes intentionally omitted from spec for size
        ),
        objects=objects,
        bindings=bindings or None,
        revision=1,
    )


def produce_layout_spec_from_sketch(
    image_bytes: bytes,
    vlm_call: Callable[[str, bytes, Optional[str]], str],
    *,
    prompt_context: Optional[str] = None,
    default_confidence: float = 0.5,
) -> LayoutSpec:
    """Sketch-modality producer per spec §10.2.

    Confidence band 0.4-0.7 (hand-drawn variability). VLM emits objects
    with approximate positions; ratifier auto-binds via disambiguators
    where role_hint is absent.
    """
    return _produce_from_vlm(
        image_bytes, vlm_call, "sketch", default_confidence, prompt_context,
    )


def produce_layout_spec_from_photo(
    image_bytes: bytes,
    vlm_call: Callable[[str, bytes, Optional[str]], str],
    *,
    prompt_context: Optional[str] = None,
    default_confidence: float = 0.4,
) -> LayoutSpec:
    """Photo-modality producer per spec §10.3.

    Confidence band 0.3-0.6 (real-world variability dominates).
    Otherwise identical to sketch.
    """
    return _produce_from_vlm(
        image_bytes, vlm_call, "photo", default_confidence, prompt_context,
    )
