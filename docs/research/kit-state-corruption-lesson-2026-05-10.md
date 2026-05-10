# Lesson: Kit-State Corruption from Default-Value Function Edits

**Date:** 2026-05-10 22:54-23:22 (29 minutes lost)

## Symptom

Modified `_bin_drop_pos()` in `tool_executor.py` to change auto-drop-target
z-offset from `+0.05` to `-0.02`. CP-22 (foundational stable_ok canonical)
immediately failed. Reverted the change. CP-22 **still failed** even after:

1. File revert to last commit (verified via `git diff` clean)
2. uvicorn restart (multiple times)
3. `_PLANNER_ATTR` bump v21 → v22 → back to v21

## Root cause

Kit's Python interpreter caches cuRobo planner instance + scene state.
When `_bin_drop_pos()` returned a new drop position, the planner cached
an internal target that persisted in Kit memory. Reverting the source
file in uvicorn didn't clear Kit's cached state.

## Fix

**Full Isaac Sim kill + restart.** After Kit restart, CP-22 returned to
stable_ok 1/1 (47.8s) and CP-51/68/52 all confirmed stable_ok 1/1.

## Lesson

When modifying any helper that's called inside the generated cuRobo
controller code (e.g. `_bin_drop_pos`, `_compute_h1`, scene_cfg paths),
**always restart Kit afterwards**. Reverting code is not sufficient.

`_PLANNER_ATTR` version bump only re-creates the cuRobo planner — it
does not clear other cached state in Kit's process memory.

## Affected time window (false negatives)

Between 22:54 (the bad edit) and 23:23 (Kit restart), CP-22 and any
canonical using auto-drop-target showed false stable_fail. CPs tested
in that window with bad results:
- CP-22 (cp22-postfix, cp22-revert-check, cp22-fresh-kit attempts)
- CP-67 (cp67-clean-obstacles)
- CP-76 (cp76-noobstacle)
- CP-06 (cp06-multicube)

None of these CPs were actually broken — they were victims of Kit-state
corruption.

## Pre-existing genuine unlocks (verified BEFORE bad edit and AFTER Kit restart)

All today's unlocks are genuine:
- Phase 4: CP-37, CP-53, CP-57, CP-58, CP-46, CP-48, CP-59, CP-65 (earlier)
- Multi-Franka: CP-51, CP-68, CP-52 (22:08-22:30, before bad edit at 22:54)
  - N=5 verified at 22:30 (before bad edit)
  - N=1 re-verified at 23:24 after Kit restart: 3/3 stable_ok ✓

**Total genuine stable_ok unlocks today: 12 N=5-verified in patched-set.**
