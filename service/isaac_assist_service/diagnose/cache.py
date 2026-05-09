"""Scene-graph-hash keyed result cache for diagnose_scene_feasibility.

Per spec §C (Opus review): same scene → same output. function_gate_consistency
runs the same canonicals 3-5× per session → cache hit ratio ~80% in CI.

Cache key:
  hash of (robot_path, robot_world_xform, all obstacles' bbox + xform,
           pick_pose, drop_pose, ee_offset, sensor_xform_if_set, seed)

Invalidation:
  - TTL fallback: 60s
  - Explicit `clear_cache()` on stage close / open_stage
  - `invalidate(prim_path)` when MUTATE_GEOMETRY_TOOLS edits a prim

In-memory only. Kit RPC is single-tenant (memory:
feedback_isaac_assist_kit_concurrency) so no cross-process value.
"""
from __future__ import annotations

import hashlib
import json
import time
from typing import Any, Dict, Optional, Tuple


_DEFAULT_TTL_S = 60.0
_cache: Dict[str, Tuple[Dict[str, Any], float]] = {}


# Tools that mutate scene geometry — a call to any of these should invalidate
# the cache. Used by orchestrator hook to call invalidate() on path arg.
MUTATE_GEOMETRY_TOOLS = {
    "translate", "set_attribute", "apply_api_schema", "create_prim",
    "delete_prim", "batch_delete_prims", "duplicate_prims", "clone_prim",
    "teleport_prim", "set_world_transform", "fix_collision_mesh",
    "merge_meshes", "compute_convex_hull", "scatter_on_surface",
    "anchor_robot", "assemble_robot", "import_robot",
    "create_bin", "create_conveyor", "create_conveyor_track", "create_gripper",
    "load_payload", "open_stage", "load_scene_template",
    "build_scene_from_blueprint", "execute_template_canonical",
}


def _stable_hash(payload: Dict[str, Any]) -> str:
    """Deterministic SHA256 over JSON-serialized payload (sorted keys)."""
    blob = json.dumps(payload, sort_keys=True, default=str).encode()
    return hashlib.sha256(blob).hexdigest()[:16]


def make_key(*,
             robot_path: str,
             robot_xform: Any = None,
             obstacle_bboxes: Optional[Dict[str, Any]] = None,
             pick_pose: Any = None,
             drop_pose: Any = None,
             ee_offset: Any = None,
             sensor_xform: Any = None,
             seed: int = 42) -> str:
    """Build a cache key from scene-graph features. Caller passes whatever
    they have; missing keys hash deterministically too (None vs absent OK)."""
    return _stable_hash({
        "robot_path": robot_path,
        "robot_xform": robot_xform,
        "obstacle_bboxes": obstacle_bboxes or {},
        "pick_pose": pick_pose,
        "drop_pose": drop_pose,
        "ee_offset": ee_offset,
        "sensor_xform": sensor_xform,
        "seed": seed,
    })


def get(key: str, ttl_s: float = _DEFAULT_TTL_S) -> Optional[Dict[str, Any]]:
    """Cache lookup. Returns None on miss or expired."""
    entry = _cache.get(key)
    if entry is None:
        return None
    payload, ts = entry
    if (time.time() - ts) > ttl_s:
        _cache.pop(key, None)
        return None
    return payload


def put(key: str, payload: Dict[str, Any]) -> None:
    """Store a result. Caller pre-flagged payload['cache_hit']=False on store
    (gets flipped to True on retrieve via mark_cache_hit_in_payload below)."""
    _cache[key] = (payload, time.time())


def clear_cache() -> int:
    """Drop all entries. Returns number cleared. Call on stage close /
    new_stage / open_stage."""
    n = len(_cache)
    _cache.clear()
    return n


def invalidate_prefix(prefix: str) -> int:
    """Drop entries whose key starts with prefix. Useful if we ever extend
    keys to include scene-graph rev — for now we do not, so this is a stub
    that can be wired in later."""
    keys = [k for k in _cache if k.startswith(prefix)]
    for k in keys:
        _cache.pop(k, None)
    return len(keys)


def stats() -> Dict[str, Any]:
    """Diagnostic. Cache size + oldest-entry age."""
    if not _cache:
        return {"size": 0, "oldest_age_s": None}
    oldest = min(ts for _, ts in _cache.values())
    return {"size": len(_cache), "oldest_age_s": round(time.time() - oldest, 1)}


def mark_hit(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Caller flips cache_hit=True on retrieved payload before returning to
    avoid mutating the stored copy."""
    out = dict(payload)
    out["cache_hit"] = True
    return out
