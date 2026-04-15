# 6A Addendum — PhysX Pre-Flight Collision Validation

**For:** Other session building 6A  
**Priority:** Add to `validate_scene_blueprint` before merging 6A as complete  
**Effort:** Small — one Kit RPC endpoint + validation loop

---

## What to Add

Before `build_scene_from_blueprint` executes, each object in the blueprint should be collision-tested against the existing scene using PhysX scene queries. No new architecture — just a validation step in the existing flow.

## How It Works

```python
from omni.physx import get_physx_scene_query_interface

def check_placement(half_extents, position, rotation=[0,0,0,1]):
    """Test if a box at this position collides with anything in the scene."""
    collisions = []
    
    def report_hit(hit):
        collisions.append(hit.rigid_body)
        return True  # continue reporting
    
    get_physx_scene_query_interface().overlap_box(
        half_extent=half_extents,
        position=position,
        rotation=rotation,
        report_fn=report_hit
    )
    
    return collisions  # empty = clear
```

- No prim needs to be created — overlap_box takes a freestanding shape + transform
- Runs in microseconds — negligible vs LLM call latency
- Uses the same PhysX engine that runs the simulation — ground truth, not approximation

## Where to Hook It In

In `_handle_validate_scene_blueprint()` (tool_executor.py), after the existing overlap/floating/scale checks:

```python
# Existing checks (keep):
# - AABB overlap between blueprint objects (pure math)
# - Floating object detection
# - Scale mismatch

# NEW: PhysX collision check against existing scene
for obj in blueprint["objects"]:
    half_extents = get_half_extents_from_asset_cache(obj["asset"])
    position = obj["position"]
    
    # Kit RPC call to run overlap_box inside Isaac Sim
    result = await kit_tools.post("/check_placement", {
        "half_extents": half_extents,
        "position": position
    })
    
    if result.get("collisions"):
        issues.append({
            "object": obj["name"],
            "problem": f"collides with {result['collisions']}",
            "severity": "hard",
            "suggestion": "adjust position or check asset dimensions"
        })
```

## Kit RPC Endpoint to Add

In `kit_rpc.py`, add a new handler:

```python
@routes.post("/check_placement")
async def handle_check_placement(request):
    data = await request.json()
    half_extents = data["half_extents"]  # [x, y, z]
    position = data["position"]          # [x, y, z]
    rotation = data.get("rotation", [0, 0, 0, 1])  # quaternion
    
    collisions = []
    
    def report_hit(hit):
        collisions.append(str(hit.rigid_body))
        return True
    
    from omni.physx import get_physx_scene_query_interface
    sq = get_physx_scene_query_interface()
    sq.overlap_box(
        carb.Float3(*half_extents),
        carb.Float3(*position),
        carb.Float4(*rotation),
        report_hit
    )
    
    return web.json_response({"collisions": collisions, "clear": len(collisions) == 0})
```

## Prerequisites

1. Scene must have a `PhysicsScene` prim (standard for any Isaac Sim robotics scene)
2. Existing objects must have `CollisionAPI` applied (otherwise PhysX doesn't see them)
3. Asset bounding boxes must be in the asset cache (6A.1 catalog_search should store these)

## What This Enables

- Blueprint validation catches collisions BEFORE code runs
- LLM gets actionable feedback ("collides with /World/Table") and can auto-adjust
- Autonomous workflows (Phase 10) can self-validate without human review
- Cost: ~1ms per object, negligible in a pipeline where LLM calls take seconds

## What This Does NOT Replace

- Runtime physics simulation (PhysX stepping) — still needed for dynamics
- Isaac Sim's own collision visualization — still useful for debugging
- The existing AABB overlap check in validate_scene_blueprint — keep it as a fast pre-filter before the PhysX call (AABB is pure math, no Kit RPC needed)

## Test Strategy

| Test | Level | What |
|------|-------|------|
| Kit RPC endpoint | L1 | Mock PhysX interface, verify request/response format |
| Validation loop integration | L0 | Mock kit_tools.post, verify issues list populated |
| Actual PhysX overlap | L3 | Requires Kit with PhysicsScene |
