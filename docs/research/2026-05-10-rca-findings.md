# Root Cause Analysis — 2026-05-10 morning probe

Used probe_ctrl_telemetry.py + ad-hoc cube-trajectory tracking to investigate
4 representative failing CPs.

## CP-05 — TEMPLATE BUG (cylinder-flip mechanism broken)

**Setup:** Cylinder cube spawns at x=-0.9 on belt, supposed to hit FlipWall
at x=0.5 and tip forward to land at sensor at x=0.7.

**Observed:** Cube reaches x=0.469 at t=3s (just before FlipWall) and STAYS
THERE for remaining 27s. Z stays at 0.83 (no tipping). Drifts in y but
never advances past wall.

**Root cause:** FlipWall geometry/physics doesn't tip cylinder. Possible:
rubber material too sticky, wall too tall (0.018m) for cylinder's
horizontal axis (radius 0.025m), or belt friction holds cube against wall.

**Triage class:** 2a TEMPLATE_FIX. Either thinner FlipWall, lower friction,
or a different reorient mechanism.

## CP-37 — PHASE 4 TERRITORY (cuRobo plan_pose fails with scene-floor obstacles)

**Setup:** 4 cubes spawn at x=-1.4 to -0.65, belt drives them past
PickSensor at x=0.7. Pillar obstacle at [0,-0.2,0.95].

**Observed:** All 4 cubes travel +2.4 to +3.07m in x. Z drops to 0.525
(fallen off belt edge). Sensor at x=0.7 should trigger as cubes pass at
t≈4s. Controller log shows `RuntimeError: planning failed for
/World/Cube_3` (and 1, 2). Phase histogram: wait_sensor 92%.

**Root cause:** cuRobo plan_pose fails for all 4 cubes. This matches
session-end memory finding 2026-05-09: "cuRobo plan_pose fails 24/24 in
real-handler context with scene-floor obstacles registered in scene_cfg
via update_world(SceneCfg)". Robot home pose flagged as in-collision
because Table/Belt/Bin are registered as obstacles. Cubes pass while
controller is stuck failing IK.

**Triage class:** Phase 4 scenario-profile (per
`docs/specs/2026-05-09-scenario-profile-controller-config.md`). Needs
per-scenario scene_cfg config: obstacle_rich profile excludes scene-floor.

## CP-67 — Multi-robot relay incomplete

**Setup:** Two Frankas (A + B). Sensor zones for both. Cube_4 was delivered
once (cubes_delivered_final=1, cycles_attempted_final=1) but then both
robots stuck in wait_sensor forever.

**Observed:** Phase histogram shows wait_sensor 100% by run end. One cube
delivery happened early, then nothing.

**Root cause hypothesis:** sensor not re-detecting subsequent cubes after
the first delivery. Or MUTEX_PATH spline-injection released only once.

**Triage class:** Phase 4 scenario-profile (multi_robot_relay).

## CP-74 — UR10 event-state cycling

**Setup:** UR10 with raycast→FixedJoint workaround.

**Observed:** Phase histogram cycles through `seek_cube`, `event=1`, `event=5`,
`event=8 fj_held xyd=0.265`, etc. 0 deliveries, 0 cycles_attempted.

**Root cause hypothesis:** Per session-memory: "CP-74/80 belt-pause-from-callback
bug remains". The event-loop progresses but never completes a cycle.

**Triage class:** 2d CONTROLLER_BUG. Needs targeted fix in UR10 event handler.

## Working CPs (control samples)

**CP-22:** Phase histogram = `executing` 97%. Real run completes pickup +
delivery (within its 90s duration_s window; 30s probe sees mid-execution).
**stable_ok 5/5 in baseline.**

**CP-65:** Phase histogram split between `wait_sensor` and `executing` for
both FrankaA and FrankaB. cubes_delivered=1, cycles=1. Multi-robot relay
**works** (5/5 stable_ok after Phase 0.7 timeout fix).

## Diagnostic tool issues found

1. **`ctrl:plan_calls` and `ctrl:plan_fails` counters DON'T EXIST in
   tool_executor.py.** Session-end memory claimed they were added 2026-05-09
   but `grep` finds nothing. Probably stashed/reverted. probe_ctrl_telemetry
   reads them and returns 0 → misleading. Fix: add counters or remove from probe.

2. **`ctrl:last_error` DOES exist** and works — caught real "planning failed"
   errors for CP-37 cubes.

3. **Cube-trajectory sampling** (added 2026-05-10) provides crucial info
   that ctrl:* alone misses — see CP-05 stuck-at-wall and CP-37
   fallen-off-belt patterns.

## Phase 2 priority impact

Based on these 4 RCA samples:

- **2a TEMPLATE_FIX**: CP-05 (cylinder-flip fix). Estimable.
- **2d CONTROLLER_BUG**: CP-74/80 belt-pause-from-callback. Targeted, risky.
- **Phase 4 territory**: CP-37 + CP-67 + likely many others. Larger refactor.

For autonomous overnight productivity, **only CP-05-class template fixes
are safely actionable**. The rest needs Phase 4 (scenario-profile config)
to land first.
