# Function-gate diagnosis — 2026-05-07

## What was investigated

The verifier_smoke `simulate_traversal_check` function-gate fixture
`known_good_cp01` reported `cube_speed=0`, `at_rest=True`,
`in_target_xy=False` — same behavior as the negative fixtures. This
made the function-gate uninformative.

## Root cause #1 — BBoxCache stale read (FIXED in commit 8025170)

`simulate_traversal_check`'s generated code instantiated one
`UsdGeom.BBoxCache` at the top of the script and reused it for every
`_world_pos(cube)` call inside the play loop. The cache stores
per-prim bbox results and never invalidates against PhysX-driven
xformOp updates.

**Verified bug isolation:**
```
After 3s of sim with conveyor surface_velocity=0.2 m/s:
  cached:  [-1.4, 0.4, 0.835]   # stale (BBoxCache reused — bug)
  fresh:   [0.385, 0.4, 0.83]   # correct (new cache per call)
  xform:   [0.385, 0.4, 0.83]   # correct (Xformable.Compute...)
```

**Fix:** build a fresh BBoxCache inside `_world_pos()` (and
`_world_bbox()` for symmetry, even though static targets don't need
it). Falls back to `Xformable.ComputeLocalToWorldTransform` when
bbox is empty.

**Verified post-fix:**
- no-controller fixture: `cube_speed=7.66` m/s, cube falls off end
  of conveyor at x=1.5 (correct — no controller picks it up).
- hard_instantiate_smoke_tests: 6/6 fixtures still pass — no
  regression on production canonical builds.

## Root cause #2 — cuRobo controller stalls cube delivery (NOT FIXED)

After fix #1, `known_good_cp01` shows:
- cube_initial = [-1.4, 0.4, 0.835]
- cube_final   = [-0.78, 0.4, 0.83]   (moved 0.62m)
- cube_speed   = 0.0 (paused)
- at_rest      = True
- in_target_xy = False

So Cube_1 moves 0.62m via conveyor in early sim, then conveyor
pauses (cuRobo's `_pause_belt()` for cube near pick zone), and
stays at -0.78 for the remaining ~25s. None of the 4 cubes reaches
the bin in 30s.

### Hypothesis space (NOT verified)

A. **Stale subscriptions from prior installs**: my audit script
   exercised `setup_pick_place_controller` with bad paths multiple
   times before the silent-success fix shipped. Those installs
   registered subscriptions in `builtins` that still fire on each
   physics step (Tracebacks observed: `Stage.GetPrimAtPath(Stage,
   str)` Boost.Python.ArgumentError on `<string>` line 52, in
   on_step — but line 52 of the CURRENT cuRobo handler is just a
   comment, so the error is from an OLDER generated code whose sub
   never got cleaned up).

B. **Cleanup logic gap**: cuRobo's install-time cleanup unsubs
   `_native_pp_*`, `_spline_pp_*`, etc. for the SAME `_ROBOT_TAG`
   only. Stale subs against deleted prim paths (e.g.
   `_curobo_pp_sub__World_NonExistentRobot` from audit tests) are
   not caught — they fire on every step and crash inside `_on_step`.

C. **Genuine controller bug**: the cuRobo planner can't complete a
   pick-place cycle in 30s on this scene geometry. CP-01 may need
   longer (60s default).

### Recommended next investigation

Fresh Kit RPC restart, then re-run `known_good_cp01` with:
- `duration_s=60` (match production default)
- monitor controller's per-cube state machine via `S["mode"]`
  attribute writes
- count Tracebacks emitted during sim (if any → hypothesis A is
  active)

If hypothesis B confirmed, broaden cleanup to scan for any
`_*_pp_sub_*` builtins whose underlying prim path is invalid (uses
a non-existent path component).

## What was achieved

- Function-gate now reports MOTION when cube moves (was: always
  reported zero motion). Detects "scene matches form criteria but
  cube doesn't actually move" failure mode.
- BBoxCache fix is independent of controller behavior — applies to
  ALL function-gate scenarios.
- Issue #2 (controller stalling) is now diagnosable instead of
  masked by issue #1.
