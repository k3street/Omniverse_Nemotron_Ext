# Phase 7C — XR Teleoperation: Critique

**Agent:** Research 7C XR Teleoperation  
**Date:** 2026-04-15  
**Status:** Complete

## Problem 1 — LiveKit Is Wrong for Control Path

LiveKit solves media streaming, not deterministic control loops. For robotics teleop: use direct WebSocket from XR client to FastAPI for commands. LiveKit stays for viewport video only.

## Problem 2 — No Latency Targets

| Scenario | Required RTT |
|---|---|
| Perception-only | < 150 ms |
| Coarse manipulation | < 100 ms |
| Dexterous manipulation | < 50 ms |

## Problem 3 — No Safety Stops

Missing: deadman switch, heartbeat watchdog, joint velocity caps, workspace limits.

## Problem 4 — Vision Pro Has No WebXR Path

Safari on visionOS does not expose `XR_EXT_hand_tracking`. Requires native CloudXR app, not "browser link."

## Problem 5 — USD TimeSamples Is Wrong Format

Every major robot learning framework uses HDF5, not USD TimeSamples. Record to HDF5 (robomimic schema), optional export to USD for replay.

## Problem 6 — NVIDIA Already Has This Stack

**IsaacTeleop** (open source) provides: Quest 3 + Vision Pro support, GPU-accelerated stereo streaming, HDF5 recording, dex-retargeting.

## Required Changes

1. Split viewport stream from control channel
2. Add latency budgets per device
3. Fix Vision Pro architecture (native CloudXR)
4. Add safety watchdog (7C.6)
5. Change recording format to HDF5
6. Integrate IsaacTeleop for retargeting

## Sources
- [NVIDIA/IsaacTeleop](https://github.com/NVIDIA/IsaacTeleop)
- [GMR: General Motion Retargeting (ICRA 2026)](https://github.com/YanjieZe/GMR)
- [Polymath Robotics / LiveKit](https://livekit.com/customers/polymath)
- [robomimic HDF5 format](https://robomimic.github.io/docs/datasets/overview.html)
