"""Phase 27 — LayoutSpec → scene blueprint round-trip.

Converts a ratified LayoutSpec into a `generate_scene_blueprint` /
`build_scene_from_blueprint` compatible payload, so canvas-built scenes
can be persisted in the same `workspace/scene_blueprints/` library that
chat-built scenes use.

Per specs/IA_FULL_SPEC_2026-05-10.md Phase 27.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional


def spec_to_blueprint(spec: Any, name: str = "unnamed_layout") -> Dict[str, Any]:
    """Translate a LayoutSpec into a scene-blueprint dict.

    Phase 27 scaffold: minimal mapping of objects → blueprint entries.
    Full canonical schema (lighting, physics, sensors) is daytime work.
    """
    objects = getattr(spec, "objects", None) or []
    bp_objects: List[Dict[str, Any]] = []
    for i, obj in enumerate(objects):
        obj_class = getattr(obj, "object_class", None) or obj.get("object_class", "unknown")
        position = getattr(obj, "position", None) or obj.get("position", [0, 0, 0])
        bp_objects.append({
            "name": f"{obj_class}_{i + 1}",
            "asset_name": obj_class,
            "position": position,
            "prim_path": f"/World/{obj_class}_{i + 1}",
        })
    return {
        "name": name,
        "description": f"Canvas-built scene with {len(bp_objects)} objects",
        "category": "canvas",
        "room_dims": [4, 4, 3],
        "objects": bp_objects,
        "physics_settings": {"gravity": -9.81, "time_step": 1.0 / 60.0, "solver_iterations": 16},
        "source": "canvas_round_trip",
    }


def blueprint_to_spec_stub(blueprint: Dict[str, Any]) -> Dict[str, Any]:
    """Reverse direction: blueprint → spec-shaped dict.

    Phase 27 scaffold: simple object-by-object mapping.
    """
    objects = []
    for entry in blueprint.get("objects", []):
        objects.append({
            "object_class": entry.get("asset_name", "unknown"),
            "position": entry.get("position", [0, 0, 0]),
            "prim_path": entry.get("prim_path", ""),
        })
    return {
        "intent": {"pattern_hint": "from_blueprint"},
        "objects": objects,
        "source": {"modality": "blueprint", "confidence": 1.0},
    }
