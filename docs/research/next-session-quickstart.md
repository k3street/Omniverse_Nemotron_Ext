# Next-Session Quickstart

Read THIS first when continuing master plan work.

## Where we are (2026-05-10 evening)

Branch: `feat/multimodal-foundation` on anton remote. ~40 commits today.

**Industrial-expansion: 6/6 milestones done** (M1 ROS2, M2 Modbus, M3 OPC-UA,
M4 cuMotion-MoveIt, M5 MQTT-Sparkplug, OpenPLC). 13 bridge handlers, 20 l0
tests. All MCP-discoverable (416 schemas).

**Library: 109/111 templates** (drafts for 22 yrkesroll, all need Kit smoke):
- 87 base CP-01..CP-87
- 5 Phase 8 yrkesroll Top-5
- 10 Phase 9 yrkesroll Top 6-15
- 5 Phase 10 yrkesroll Top 16-20
- 2 plumbing-only (PLC-conveyor, OPC-UA-12conveyors)

**Stable_ok status:**
- 9 verified in 25-CP patched-set (was 2 in Phase 0 baseline)
- ~56/86 estimated total (≈65%)

## Live processes (likely still running)

- `progress_watchdog.sh` PID 4151729 — 5min poll, alerts on stalls
  - Log: `/tmp/progress_watchdog.log`
  - Alerts: `/tmp/progress_alert.log`
- `phase5_driver.sh` PID 3542895 (started 19:18) — autonomous loop through
  13 failing CPs trying cube_paths fix. ETA ~20:25.
  - Log: `/tmp/phase5_driver.log`
- Kit RPC + uvicorn — may still be loaded. Check `ps aux | grep uvicorn`

## Top priorities for continuation

### A. Phase 5 drive — more CP unlocks (Kit-bound, ~5min each)

Failing CPs likely needing targeted (not pattern-based) fixes:
- **CP-51, CP-68 (handoff)**: FrankaB closer fix applied, but cube falls
  off bin (z=0.525 < bin_z=0.75 by 22cm). Need bin-physics tuning OR
  drop_target precision. Backup: tighter drop_target_z_offset.
- **CP-67, CP-76, CP-52 (multi-Franka relay)**: cube_paths already applied.
  Coordination/sequencing issue — robots wait for sensor that never triggers.
- **CP-73 (UR10 cuRobo)**: 100% plan_pose fail despite 3D=0.49m << 1.15m
  reach. Likely cuRobo UR10 base orientation or scene_cfg issue.
- **CP-74, CP-80, CP-84, CP-85 (UR10 builtin)**: raycast workaround patterns,
  some still in seek_cube. Memory `project_isaac_assist_phase_b_state.md`.
- **CP-05, CP-06 (spline reorient flip-wall)**: spline controller, sensor
  may not trigger. Spline/builtin don't write `ctrl:plan_calls` so probe
  shows zero engagement.
- **CP-40 (spline 4-cube)**: similar — spline-instrumentation gap.
- **CP-60, CP-62 (conveyor loops)**: build issue or geometry gap; CP-60
  has cube falling off loop (z=0.525).

### B. Phase 8/9/10 yrkesroll Kit smoke tests

20 templates drafted. Need Kit smoke per template:
- Quick wins (no NVIDIA asset needed):
  - CP-NEW-peg-in-hole-single (use existing primitives)
  - CP-NEW-3station-oee (3 Franka, primitives)
  - CP-NEW-controller-shootout-cp (4-cube belt)
  - CP-NEW-y-merge-singulation (3 conveyors)
  - CP-NEW-plc-fixture (Modbus mock)
  - CP-NEW-brick-stacking (6 cubes)
  - CP-NEW-cad-revision-drift (variant scaffold)
  - CP-NEW-cross-belt-sorter (no robot)
- NVIDIA Nucleus dependencies (skip if no Nucleus):
  - CP-NEW-g1-bimanual-tabletop (G1 SimReady)
  - CP-NEW-amr-pickup-handoff (Carter SimReady)
  - CP-NEW-operator-ergonomics (Avatar SimReady)
- External-software dependencies:
  - CP-NEW-rl-clone-env (rsl_rl/rl-games)
  - CP-NEW-defect-sdg (omni.replicator)
  - CP-NEW-tactile-insertion (TacEx framework)
  - CP-NEW-sim2real-gap (real rosbag)
  - CP-NEW-multi-amr-corridor (Carter SimReady)
  - CP-NEW-dr-curriculum (DR handlers)
  - CP-NEW-multi-cam-triangulation (3-camera fusion offline)

### C. Multimodal session work (parallel)

Block 1B, 2, 3, 4, 5 — not in this session's scope. Coordinate via
`docs/specs/multi-session-coordination.md`.

## Tools to use

- `scripts/qa/multi_run_regression.py` — N-run sweep
- `scripts/qa/baseline_compare.py` — diff vs Phase 0 baseline
- `scripts/qa/probe_ctrl_telemetry.py` — runtime instrumentation + 7 diagnoses
- `scripts/qa/classify_failure_modes.py` — categorize failing CPs
- `scripts/qa/controller_shootout_report.py` — controller benchmark snapshot
- `scripts/qa/phase5_driver.sh` — autonomous unlock loop (currently running)

## Restart procedure

If Kit RPC dies / uvicorn crashes:
```bash
# Kill old uvicorn if alive
pkill -f "uvicorn.*service.isaac_assist"
# Restart uvicorn
nohup uvicorn service.isaac_assist_service.main:app --host 0.0.0.0 --port 8000 \
    --no-access-log > /tmp/isaac_assist_uvicorn.log 2>&1 &
# Kit will need restart via Isaac Sim launcher (manual)
```

After tool_executor.py edits, restart uvicorn (cached at startup).

## Quick checks

```bash
# Stable_ok in patched-set
python scripts/qa/multi_run_regression.py --canonicals \
    CP-22,CP-37,CP-53,CP-57,CP-58,CP-59,CP-65,CP-46,CP-48 \
    --n-runs 1 --seed 42 --tag verify-state
# Expected: 9/9 stable_ok

# Phase 5 drive log
tail /tmp/phase5_driver.log

# Watchdog log
tail /tmp/progress_watchdog.log
```

## Watchdog status

- Iter every 5min. iter 72 was at 19:19, currently iter ~75-80 by midnight.
- Auto-restarts uvicorn if dead (gracefully).
- Alerts to /tmp/progress_alert.log if no commit in 30 min.

## Key memories to read

- `project_isaac_assist_2026_05_10_session.md` (this session's deliverables)
- `project_isaac_assist_2026_05_09_patches.md` (yesterday's setup)
- `project_isaac_assist_industrial_expansion.md` (industrial-expansion-spec)
- `project_isaac_assist_function_gate.md`
- `project_isaac_assist_phase_b_state.md` (UR10 raycast)
- `project_isaac_assist_handler_patterns.md`
