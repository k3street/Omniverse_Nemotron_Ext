# Belt-Pause PhysX Fix Research

**Date:** 2026-05-09  
**Status:** Candidate fixes ranked, patch ready  
**Affected:** CP-22, CP-43, CP-74, CP-80 (belt-pause-from-callback bug)

---

## Root Cause Summary

`PhysxSurfaceVelocityAPI` injects its velocity through PhysX's **contact-modify callback** — a C++-level hook that fires during `PxScene::simulate()`. The integrator reads `physxSurfaceVelocity:surfaceVelocity` (and `surfaceVelocityEnabled`) once per step, **before** the contact-modify callback fires. USD attribute writes made from inside `subscribe_physics_step_events` happen *during* `fetchResults`, after that read has already occurred. The integrator then discards the write by re-reading the cached value at the start of the next step.

This is why:
- Writing from inside `_on_step` (registered via `subscribe_physics_step_events`) → restored next tick.
- Writing from `get_post_update_event_stream()` → "partial restoration" — that stream fires after the Kit *render* frame, but PhysX may have already taken its next sub-step.
- The cuRobo handler's identical `_pause_belt()` sometimes works because it fires during a planning pause where no physics sub-steps are interleaved.

The NVIDIA-authored test file (`PhysxSurfaceVelocityAPI.py`) confirms USD writes between `step()` calls propagate correctly — the problem is exclusively the *timing* of our writes relative to PhysX's simulation tick.

---

## Candidate Fixes (Ranked by Feasibility)

### Fix 1 — `subscribe_physics_on_step_events(fn, pre_step=True, order=0)` [HIGHEST PRIORITY]

**How it works:** The PhysX interface exposes a second subscription API:

```python
omni.physx.get_physx_interface().subscribe_physics_on_step_events(fn, pre_step, order)
# signature: (fn: Callable[[float], None], pre_step: bool, order: int) -> carb.Subscription
```

- `pre_step=True` → callback fires **before** `PxScene::simulate()`, before the contact-modify callback reads surface velocity.
- `pre_step=False` → fires **after** `fetchResults()`.

A pre-step write (`pre_step=True, order=0`) sets the USD attribute in the window after `fetchResults` of tick N but before `simulate()` of tick N+1. PhysX reads the attribute at the start of `simulate()`, so the write lands exactly when the integrator expects it.

**Evidence:** NVIDIA's own test `PhysxInterfaceSimulationEvents.py` lines 155–158 show pre-step and post-step subscribers registered in the same scene, confirming the API is live and the ordering is deterministic.

**Feasibility:** High. No schema changes, no new dependencies. Drop-in replacement for the current `subscribe_physics_step_events` call.

**Code patch for the builtin pick-place handler (~line 29534–29549):**

```python
# REPLACE: post-update event stream subscription
# WITH: pre-step physics subscription — fires BEFORE PxScene::simulate(),
# so the write lands before the contact-modify callback reads surfaceVelocity.

_PRESTEP_SUB_ATTR = "_belt_prestep_sub_" + _ROBOT_TAG

def _apply_belt_pause_prestep(_dt):
    """Pre-step callback: fires after fetchResults(N) but before simulate(N+1).
    PhysX reads physxSurfaceVelocity:surfaceVelocity at the START of simulate(),
    so writes here propagate reliably — no integrator cache collision."""
    req = _belt_pause_request[0]
    if req is None:
        return
    if req is True:
        if _belt_en and _belt_en.IsDefined():
            _belt_en.Set(False)
        if _belt_sv:
            _belt_sv.Set((0, 0, 0))
    else:
        if _belt_en and _belt_en.IsDefined():
            _belt_en.Set(True)
        if _belt_sv:
            _belt_sv.Set(_nominal_belt)
    _belt_pause_request[0] = None  # consume

try:
    _old_pre = getattr(builtins, _PRESTEP_SUB_ATTR, None)
    if _old_pre is not None:
        try:
            _old_pre.unsubscribe()
        except Exception:
            pass
    _belt_prestep_sub = omni.physx.get_physx_interface().subscribe_physics_on_step_events(
        _apply_belt_pause_prestep,
        True,   # pre_step=True → fires before simulate(), not after fetchResults
        0,      # order=0 → highest priority, runs before other pre-step callbacks
    )
    setattr(builtins, _PRESTEP_SUB_ATTR, _belt_prestep_sub)
except Exception as _bpe:
    print(f"(builtin pp: pre-step belt-pause subscription failed: {_bpe})")
```

The `_pause_belt()` / `_resume_belt()` functions remain unchanged (they still set `_belt_pause_request`). The change is only in the applier: from a post-update Kit event to a pre-step physics event.

Apply the same pattern to **all six handler variants** that have `_pause_belt`/`_resume_belt` but currently use either a direct callback write or a `post_update_event_stream` subscription (lines ~31375, ~32023, ~33021, ~33678, ~34198).

---

### Fix 2 — Legacy `physics:velocity` on the kinematic RigidBodyAPI [MEDIUM PRIORITY]

**How it works:** The NVIDIA `ConveyorBeltDemo.py` and the OmniGraph `ogn_trigger_conveyor.usda` both use the **legacy** surface-velocity mechanism: they set `physics:velocity` directly on the kinematic body's `UsdPhysics.RigidBodyAPI`. When a kinematic body has a non-zero velocity in USD, PhysX reads that as the body's target velocity and applies it as surface velocity to contacting objects.

```python
rb = UsdPhysics.RigidBodyAPI(belt_prim)
vel_attr = rb.GetVelocityAttr()
# pause:
vel_attr.Set(Gf.Vec3f(0.0, 0.0, 0.0))
# resume:
vel_attr.Set(Gf.Vec3f(*_nominal_belt))
```

This attribute sits under a different path (`physics:velocity`, not `physxSurfaceVelocity:surfaceVelocity`) and may be read at a different point in the tick. The NVIDIA test `test_physics_kinematic_surface_switch_velocity_legacy` explicitly verifies that `physics:velocity` changes propagate between steps.

**Caveat:** The `PhysxSurfaceVelocityAPI` approach (`physxSurfaceVelocity:surfaceVelocity`) was introduced as a replacement precisely because the legacy approach only works on kinematic bodies and applies velocity in world space only. Our belts already have `kinematicEnabled=True`, so the legacy path is available. However, switching from `physxSurfaceVelocity:surfaceVelocity` to `physics:velocity` would mean the belt always runs at fixed world-space velocity — fine for axis-aligned belts (CP-74, CP-80), but would require testing.

**Feasibility:** Medium. Works for axis-aligned kinematic belts; requires `physics:velocity` to be set at scene setup rather than `physxSurfaceVelocity:surfaceVelocity`.

---

### Fix 3 — Unapply/reapply `PhysxSurfaceVelocityAPI` to invalidate integrator cache [LOW PRIORITY]

**How it works:** When an API schema is removed from a prim during simulation, PhysX destroys the associated C++ `PxActor` surface-velocity data and rebuilds it on the next step. Reapplying the API with the new velocity would force a clean re-parse.

```python
# "soft delete" the API schema — PhysX notices schema removal and invalidates cache
PhysxSchema.PhysxSurfaceVelocityAPI(belt_prim).GetSchemaAttributeNames()  # confirm active
belt_prim.RemoveAPI(PhysxSchema.PhysxSurfaceVelocityAPI)
# Immediately reapply with new velocity
sv_api = PhysxSchema.PhysxSurfaceVelocityAPI.Apply(belt_prim)
sv_api.GetSurfaceVelocityAttr().Set(Gf.Vec3f(0.0, 0.0, 0.0))
sv_api.GetSurfaceVelocityEnabledAttr().Set(True)
```

**Caveat:** `RemoveAPI` triggers a full physics object rebuild for that actor which may stall for one frame (collision mesh re-cook is skipped for convex hulls but not for trimeshes). On a 60Hz sim step this is likely acceptable, but it could cause a single-frame contact glitch. Also, the `RemoveAPI` / `Apply` round trip needs to happen from the pre-step window (Fix 1's subscription) for the same timing reason.

**Feasibility:** Low — adds schema mutation overhead on every pause/resume. Only worth trying if Fix 1 turns out to still have a timing issue.

---

### Fix 4 — `IPhysxSimulation.flush_changes()` after USD write [LOW PRIORITY]

**How it works:** The `IPhysxSimulation` interface (distinct from the `PhysX` interface) exposes:

```python
from omni.physx import get_physx_simulation_interface
sim = get_physx_simulation_interface()
sim.flush_changes()
# signature: () -> None
# doc: "Flush changes will force physics to process buffered changes.
#       Changes to physics gets buffered; flushing is required if order is required."
```

The documented purpose is exactly to force USD-buffered attribute changes into PhysX before the next simulation step. However, `flush_changes()` is documented as safe to call between `simulate()` and `fetchResults()`, not from inside a step callback. Calling it from `_on_step` may re-enter the PhysX solver and cause undefined behavior.

**Feasibility:** Very low. Documented use case is object-creation ordering (add body A, set relationship on B pointing to A), not runtime attribute mutations. Likely to crash or be a no-op inside a step callback.

---

### Fix 5 — OmniGraph `WritePrimAttribute` node with `usdWriteBack=1` [CONTINGENCY]

**How it works:** The trigger-conveyor OmniGraph (`ogn_trigger_conveyor.usda`) routes surface-velocity changes through `omni.graph.nodes.WritePrimAttribute` nodes with `inputs:usdWriteBack = 1`. OmniGraph execution runs as part of Kit's **pre-physics** update tick (when the evaluator is `execution` pipeline). This means the OmniGraph write lands before `PxScene::simulate()` for the same reason Fix 1 does.

To use this for runtime pause/resume, you would:
1. Create a small OmniGraph with `OnPlaybackTick → WritePrimAttribute` targeting `physxSurfaceVelocity:surfaceVelocity`.
2. Control the `inputs:value` from Python by writing to the OmniGraph variable node (which bypasses the USD-to-PhysX latency because OmniGraph has its own Fabric-backed attribute path).

**Feasibility:** Low for this use case — adds OmniGraph setup complexity. The pre-step subscription (Fix 1) achieves the same timing with far less overhead.

---

## What NOT to Try

**`get_post_update_event_stream()`** — already tried. Fires after the Kit *render frame*, not before the next *physics sub-step*. When physics runs at 60Hz sub-stepped inside a 30Hz render frame, there are 2 physics ticks per event, so the write misses the first of the two sub-steps.

**`omni.timeline.get_timeline_interface().pause()` + `play()`** — forces a full physics state serialise/deserialise. 50–200ms stall, destroys contact caches, resets any accumulated velocity state. Would break pick-place trajectories mid-move.

**`omni.kit.commands.execute("ChangeProperty", ...)`** — `ChangeProperty` is an undo-tracked USD edit command; it goes through the same USD attribute write path. No faster than direct `.Set()` for physics propagation purposes. It adds command history overhead without fixing the timing problem.

**Direct `IPhysxSimulation.apply_force_at_pos()` or similar** — no surface-velocity analogue exists in the C++ binding layer. `apply_force_at_pos` is for impulse/force injection on dynamic bodies, not for modifying surface-velocity parameters on static/kinematic bodies. There is no `set_surface_velocity()` or `apply_surface_velocity()` method in `IPhysxSimulation` (confirmed by reading the full `_physx.pyi` stub: the class has `apply_force_at_pos`, `apply_torque`, `set_wheel_rotation_speed`, and vehicle methods, but nothing for surface velocity).

---

## API Reference (Confirmed from Stubs)

```python
# subscribe_physics_on_step_events — the critical API
omni.physx.get_physx_interface().subscribe_physics_on_step_events(
    fn: Callable[[float], None],
    pre_step: bool,   # True = before simulate(), False = after fetchResults()
    order: int,       # 0 = highest priority
) -> carb.Subscription

# PhysxSurfaceVelocityAPI schema accessors (confirmed from pxr/PhysxSchema/__init__.pyi)
api = PhysxSchema.PhysxSurfaceVelocityAPI.Get(stage, prim_path)  # or .Apply()
api.GetSurfaceVelocityAttr()          # vector3f physxSurfaceVelocity:surfaceVelocity
api.GetSurfaceVelocityEnabledAttr()   # bool physxSurfaceVelocity:surfaceVelocityEnabled
api.GetSurfaceVelocityLocalSpaceAttr()  # bool physxSurfaceVelocity:surfaceVelocityLocalSpace
api.GetSurfaceAngularVelocityAttr()   # vector3f physxSurfaceVelocity:surfaceAngularVelocity
```

---

## Implementation Plan

1. **Replace** the `post_update_event_stream` subscription in the builtin pick-place handler (lines 29536–29549 in `tool_executor.py`) with `subscribe_physics_on_step_events(..., pre_step=True, order=0)`.

2. **Propagate** the same pattern to the five other handler blocks that use in-callback direct writes: the single-articulation tabletop handler (~31375), the cuRobo Franka handler (~32023), the cuRobo UR10 handler (~33021), the elevated-conveyor handlers (~33678, ~34198).

3. **Test** CP-74 and CP-80 first (UR10 elevated conveyor) — those are the canonicals currently failing specifically due to belt-pause-from-callback.

4. **Do not** change the `_pause_belt()` / `_resume_belt()` function bodies — they still set `_belt_pause_request[0]`. Only the applier subscription needs to change.
