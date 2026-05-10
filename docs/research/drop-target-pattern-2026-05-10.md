# drop_target Pattern — Canonical Unlock Pattern

**Date:** 2026-05-10
**Status:** Validated on 3 unlocks (CP-51, CP-68, CP-52)

## The pattern

When a canonical's gate-target is a `create_bin` (or similar small static
collider) and the default auto-computed drop position causes cubes to land
2-5mm off bin xy edge, **add explicit `drop_target` to
`setup_pick_place_controller`**:

```python
setup_pick_place_controller(
    robot_path="/World/FrankaX",
    target_source="curobo",
    sensor_path="/World/SensorX",
    belt_path="/World/ConveyorBelt",
    source_paths=["/World/Cube_1"],
    destination_path="/World/Bin",
    drop_target=[<bin_x_center>, <bin_y_center>, <bin_z_max - 0.02>],  # ← KEY
    planning_obstacles=["/World/Table", "/World/ConveyorBelt"],         # ← exclude destination
)
```

## Why it works

Default `_bin_drop_pos()` returns:
```
(bbox_center_x, bbox_center_y, bbox_max_z + 0.05)
```

For a Bin at `[0.7, -0.5, 0.75]` size `[0.3, 0.3, 0.15]`:
- bbox_max_z = 0.75 + 0.15 = 0.90
- auto drop_z = 0.95

That's **19cm above bin floor**. cube falls 19cm with lateral drift. Result:
cube ends up at bin xy edge (~2-5mm off).

Explicit `drop_z = bbox_max_z - 0.02 = 0.88` reduces fall to ~12cm → less drift.

Better still: `drop_z = bbox_max_z - 0.05 = 0.85` → 9cm fall, tight on target.

## Empirical impact

| CP | Auto drop_z | Explicit drop_z | Auto result | Explicit result |
|---|---|---|---|---|
| CP-51 | 0.95 | 0.85 | stable_fail (cube_final z=0.525, fell off) | stable_ok 5/5 |
| CP-68 | 0.95 | 0.85 | stable_fail (same fall pattern) | stable_ok 5/5 |
| CP-52 | 0.95 | 0.85 | stable_fail | stable_ok 5/5 |

## When to apply

Apply when:
1. Bin is small (≤30cm xy half-extent)
2. Cube delivery distance is significant (>0.5m xy)
3. Initial probe shows controller engaging (plan_calls > 0) but
   cube_final z < bin_z_floor (cube fell off)
4. Robot is on table (h1_offset gives further trajectory drift)

Don't apply when:
1. Target is a large pallet or wide surface
2. Bin is gripper-attached (moving target)
3. cube_paths fix already addresses the issue

## Don't forget: remove destination from `planning_obstacles`

When the destination is ALSO listed in `planning_obstacles`, cuRobo tries
to plan around it → may refuse to plan into the bin opening. Pattern:

```python
# WRONG: destination listed as obstacle
destination_path="/World/Bin",
planning_obstacles=["/World/Table", "/World/Bin"]

# RIGHT: destination not in obstacles
destination_path="/World/Bin",
planning_obstacles=["/World/Table"]
```

## Failed: do NOT modify `_bin_drop_pos()` default

Attempted to change default from `+0.05` to `-0.02` globally. Result: CP-22
broke (stable_ok → stable_fail). Kit's cuRobo planner cached the new
drop logic and broke. Reverting source file alone was NOT enough; required
full Kit kill+restart.

**Lesson:** Per-template explicit `drop_target` is safer than changing
global defaults. Global changes invalidate cached planner state in ways
that aren't easily reversible.

## Coordination with other patterns

The drop_target fix STACKS with other fixes from earlier today:

- **cube_paths multi-cube** (CP-37/53/57/58/46/48/65): allows ANY cube
  to count for success. Apply when scene has >1 cube and gate's cube_path
  is too specific.
- **3D-aware reach check** (CP-37 base unlock): cuRobo internal filter.
  Already applied to all CPs.
- **FrankaB closer to handoff** (CP-51/68): place 2nd robot within
  3D-reach of handoff station.

## What doesn't work

Tested but failed to unlock:
- **CP-67 rotary table relay**: Robot A places on rotary table, Robot B
  picks from far side. RotaryTable doesn't carry cube with rotation (just
  static visual disc). Cube placed at +y radius stays at +y → never reaches
  Robot B's sensor at -y.
- **CP-76 multi-robot mating**: HoldPedestal target is 4x4cm. Wider
  pedestal didn't unlock — Inserter (Robot B) doesn't engage at all.
  Different root cause (sensor placement or stacking sequencing).
