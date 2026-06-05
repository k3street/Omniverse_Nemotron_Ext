"""Deterministic asset resolution for reviewed LayoutSpec objects.

The floor-plan UI lets users correct ``object_class`` before build.  This
module turns that reviewed class into the USD reference the instantiator should
materialise, while preserving explicit per-object overrides when present.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, List, Optional

from .object_palette import get_class


@dataclass(frozen=True)
class AssetResolution:
    object_id: str
    object_class: str
    usd_ref: str
    source: str
    label: str = ""
    confidence: Optional[float] = None
    needs_review: bool = False


def _obj_get(obj: Any, attr: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(attr, default)
    return getattr(obj, attr, default)


def _metadata(obj: Any) -> dict:
    value = _obj_get(obj, "metadata", {}) or {}
    return value if isinstance(value, dict) else {}


def resolve_object_asset(obj: Any) -> Optional[AssetResolution]:
    """Resolve one LayoutSpec object to a USD reference, if known."""

    object_class = str(_obj_get(obj, "object_class", "") or _obj_get(obj, "class", "") or "")
    if not object_class:
        return None

    metadata = _metadata(obj)
    explicit = (
        _obj_get(obj, "asset_path")
        or _obj_get(obj, "asset_ref")
        or metadata.get("asset_path")
        or metadata.get("asset_ref")
        or metadata.get("reviewed_asset_ref")
    )
    palette_entry = get_class(object_class)
    usd_ref = str(explicit or (palette_entry.usd_ref if palette_entry else "") or "")
    if not usd_ref:
        return None

    confidence = metadata.get("cosmos_confidence")
    if not isinstance(confidence, (int, float)):
        confidence = None
    label = str(metadata.get("cosmos_label") or "")
    source = "explicit" if explicit else "palette"
    needs_review = bool(
        metadata.get("requires_asset_review")
        or (confidence is not None and confidence < 0.7)
        or object_class == "obstacle_box"
    )
    return AssetResolution(
        object_id=str(_obj_get(obj, "id", "")),
        object_class=object_class,
        usd_ref=usd_ref,
        source=source,
        label=label,
        confidence=float(confidence) if confidence is not None else None,
        needs_review=needs_review,
    )


def resolve_layout_assets(objects: Iterable[Any]) -> List[AssetResolution]:
    """Resolve all known object assets in a LayoutSpec object collection."""

    resolved: List[AssetResolution] = []
    for obj in objects:
        item = resolve_object_asset(obj)
        if item is not None:
            resolved.append(item)
    return resolved
