# Stack-precision diagnostic — root cause of remaining ✗

Date: 2026-05-09 (final session)

## What we found

For 11 stack-precision canonicals previously failing (CP-10, 11, 12, 14, 19, 26, 28, 30, 37, 46, 65), per-cube position diagnostics show:

### CP-10 (9-cube 3x3 grid palletizer)
Initial: 9 cubes at x=[-2.7..-0.3] (0.3m spacing), y=0.4, z=0.835
Final after 180s: cubes at x=[-2.04..+0.36], spacing PRESERVED, all at z=0.830 (on belt)
- Cube_9 at +0.357 IS within sensor zone (0.4 ± 0.06)
- 0/9 delivered. Controller claimed cubes but no delivery completed.

### CP-11 (8-cube pinwheel palletizer)
Same pattern: cubes preserved on belt, none delivered.

### CP-26 (single-cube postal sorter)
Cube_1 ended at (+0.548, +0.408, +0.830) — at rest in sensor zone.
Target bbox at x=[1.55, 1.85] — robot needs to transport cube 1.15m to deposit station.
0 delivered.

## Root cause analysis

**Cubes are NOT colliding/blowing up** (despite simulate's bogus 40 m/s readings — that's a velocity-calc bug due to dt mismatch in pre-sample; actual cube velocity is ~0 m/s).

**Belt-pause IS working** — cubes traveled only ~0.66m over 180s = essentially stopped after first claim.

**Controller IS claiming cubes** — Cube_9 in CP-10 stopped right at sensor zone, Cube_1 in CP-26 stopped at sensor zone.

**Controller IS NOT completing cycles** — possibly:
1. cuRobo plan_pose() failing repeatedly (target unreachable, IK fail)
2. Trajectory executes but FixedJoint attach fails
3. Cycle timeout before delivery completes (180s × 9 cubes ≈ 20s/cycle → too tight)

## What to investigate next session

1. **Add `print` instrumentation in cuRobo `_on_step`** — log S["mode"] each cycle. See if controller stays in "wait_sensor" or progresses to "planning"/"executing"/"done".

2. **For CP-10**: try with 480s+ duration to see if any delivery eventually happens.

3. **For CP-26**: target bbox at x=1.55-1.85 is far from robot center (Franka rotated 90°, +X-axis = world +Y). Pose may be at/beyond reach. Test by reducing target distance.

4. **Check cuRobo plan_pose error logs** — if planner fails, it should print errors. Look at Kit RPC stderr.

## Conclusion

These ✗ are legitimate controller-level issues, not measurement bugs (after my multi-cube cube_paths + xy_tolerance + rest_speed fixes). They won't unlock with template-side tweaks alone — need controller instrumentation + per-canonical fix.

**Realistic path to 100%**: ~15-20 hours of focused per-canonical debug work. Out of scope for current iteration.


## Per-canonical diagnostic table (final)

| CP | Cubes | Final state | Root cause |
|----|-------|-------------|------------|
| CP-10 | 9 | All 9 on belt at z=0.830, sensor zone has Cube_9 at +0.357 | Controller stuck — claims cube but never delivers |
| CP-11 | 8 | All 8 on belt at z=0.830, sensor zone has Cube_8 at +0.318 | Controller stuck — same as CP-10 |
| CP-26 | 1 | Cube_1 at sensor (+0.55, +0.41, +0.83), at rest | Controller never advances past planning. Target far at x=1.7 — cuRobo IK might fail. |
| CP-30 | 4 | Cube_1 at (+0.59, +0.59, +0.525) — FELL OFF table | Controller knocked cube off / collision |
| CP-37 | 1 | (n=1 from regex; Cube_1 ended at +0.59 +0.59 z=0.525) — FELL OFF | Same pattern as CP-30 |

## Observation: Two distinct failure modes

**Mode A — controller-stuck (CP-10/11/26):** Cube reaches sensor zone, claims OK, but no delivery. Controller's planning/execution doesn't complete the cycle. Need print-debug in cuRobo `_on_step` to see where stuck.

**Mode B — cube-knocked-off (CP-30/37):** Cube falls to floor (z=0.525) instead of target. Controller picks but drops cube during transit (collision with another cube on belt or scene obstacle).

Both modes need controller-level fix. Template-side adjustments (xy_tol, rest_speed, duration) won't unblock them.

