# Full N=1 Sweep — 2026-05-11 01:40

Ran `multi_run_regression.py` across all 109 templates (CP-01..CP-87 +
22 CP-NEW-*) with N=1 seed=42 per-CP timeout 240s. Suite started 23:39,
completed 01:40 (~2h).

## Results

```
stable_ok:    31 / 109
BUILD_OK:      7 / 109
stable_fail:  70 / 109
TIMEOUT:       1 / 109 (CP-20)
```

## Stable_ok (31)

CP-01, CP-02, CP-03, CP-04, CP-07, CP-08, CP-09, CP-11,
CP-13, CP-14, CP-19, CP-21, CP-23, CP-25, CP-26, CP-30,
CP-32, CP-33, CP-34, CP-35, CP-36, CP-39, CP-51, CP-52,
CP-55, CP-59, CP-63, CP-64, CP-65, CP-78, CP-NEW-3station-oee

## BUILD_OK (7)

CP-NEW-defect-sdg, CP-NEW-multi-amr-corridor,
CP-NEW-opcua-12conveyors, CP-NEW-plc-conveyor,
CP-NEW-plc-fixture, CP-NEW-rl-clone-env, CP-NEW-sim2real-gap

## Caveat: Kit-state corruption

The sequential 109-CP sweep may have suffered cumulative Kit-state
corruption (cuRobo planner cache, scene drift). Earlier-today's
isolated stable_ok confirms (e.g. CP-22, CP-37, CP-46, CP-48, CP-53,
CP-57, CP-58, CP-68 — all confirmed stable_ok in fresh-Kit isolation)
show as stable_fail in this sweep.

This suggests Kit needs restart every 30-50 CPs to maintain clean state.

## Genuine unlocks today (cross-verified in fresh Kit)

These show stable_ok in N=5 verify OR fresh-Kit isolation after Kit restart:
- CP-22, CP-37, CP-53, CP-57, CP-58, CP-46, CP-48 (Phase 4 / cube_paths)
- CP-51, CP-68, CP-52 (Multi-Franka drop_target)
- CP-59 (was flaky, now stable)
- CP-65 (already stable)

= **12 verified unlocks in patched-set today**.

## Genuine yrkesroll stable_ok (smoke-verified in fresh Kit earlier)

- CP-NEW-controller-shootout-cp
- CP-NEW-3station-oee (also in sweep)
- CP-NEW-y-merge-singulation
- CP-NEW-cad-revision-drift
- CP-NEW-inspect-reject
- CP-NEW-dr-curriculum
- CP-NEW-multi-cam-triangulation

= **7 yrkesroll unlocked**.

## Need follow-up

After Kit restart, re-verify in BATCHES of ~30 CPs max to confirm:
- The 31 sweep-confirmed stable_ok still hold (mostly trustworthy)
- The 70 stable_fail: split into genuine fail vs Kit-corruption false negatives
