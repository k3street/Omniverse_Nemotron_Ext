"""Themed handler modules for the Isaac Assist tool dispatch.

Phase 2 — skeleton only. Each themed module exposes `register(data,
codegen) -> None` that is currently a no-op. Phases 3-7 move handlers
from the `tool_executor.py` monolith into these modules. Phase 9
swaps the dispatch pattern so the legacy `DATA_HANDLERS["X"] = ...`
inline assignments are replaced by `_dispatch.register_handlers(...)`
delegating to each theme.

The 14 themed modules + `_dispatch` central registry:

| Module               | Target scope (per spec Phase 2)                         |
|----------------------|---------------------------------------------------------|
| `scene_authoring`    | USD prim CRUD, attrs, references, layers, materials.    |
| `physics`            | Physics scene config, articulations, joints, drives.    |
| `robot`              | Robot import/anchor, IK, gripper, motion policy.        |
| `sensors`            | Cameras, lidars, contact sensors, proximity, NIR.       |
| `sdg`                | Replicator pipelines, DR ranges, presets, writers.      |
| `training`           | IsaacLab env, training launch, RL/GR00T, Eureka.        |
| `ros2`               | ros2_connect, topics, services, OmniGraph bridge.       |
| `teleop`             | Start/stop sessions, record/validate demos, mapping.    |
| `scene_blueprints`   | Catalog search, generate/validate/build blueprints.     |
| `diagnostics`        | verify_pickplace, check_*, fix_error, explain_error.    |
| `arena`              | Arena create/run/leaderboard/compare_policies.          |
| `workflow`           | Start/edit/approve/reject/revise/cancel/status.         |
| `resolve`            | The 10 resolve_* NL-disambiguation handlers.            |
| `vision`             | vision_detect_objects, bounding_boxes, plan_trajectory. |
| `_dispatch`          | Central `register_handlers(data, codegen)` orchestrator.|

Per `specs/IA_FULL_SPEC_2026-05-10.md` Phase 2.
"""
from __future__ import annotations

# Re-export module list for callers. Imports are lazy — accessing
# `handlers.scene_authoring` triggers the submodule import. This keeps
# `from .handlers import _dispatch` lightweight for the no-op phase.
__all__ = [
    "arena",
    "diagnostics",
    "physics",
    "resolve",
    "robot",
    "ros2",
    "scene_authoring",
    "scene_blueprints",
    "sdg",
    "sensors",
    "teleop",
    "training",
    "vision",
    "workflow",
    "_dispatch",
]
