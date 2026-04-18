# Phase 7C — XR Teleoperation

**Status:** Scaffold only (LiveKit agent stub exists)  
**Depends on:** Phase 3 (set_joint_targets), Phase 4C (viewport streaming)  
**Blocks:** Phase 7G (GR00T fine-tuning needs teleop demo data)  
**Research:** `research_reports/7C_xr_teleoperation.md`

---

## Overview

Stream viewport via WebRTC and map XR hand-tracking / controller inputs to robot joint targets in real-time. Record demonstrations for imitation learning.

---

## Architecture Decision: IsaacTeleop Integration

**NVIDIA already ships `github.com/NVIDIA/IsaacTeleop`** which provides Quest 3 + Vision Pro support, GPU-accelerated stereo streaming, dex-retargeting, and HDF5 recording. Recommend integrating IsaacTeleop as the backend rather than building from scratch.

**Transport split:**
- **Viewport video:** LiveKit WebRTC (existing scaffold) OR `omni.kit.livestream.webrtc` (NVIDIA native, lower engineering)
- **Control commands:** Direct WebSocket to FastAPI (`/ws/teleop`), NOT LiveKit DataPacket. Deterministic, lower latency.

---

## Tools

### 7C.1 `start_teleop_session(robot_path, input_device, stream_quality)`

**Input devices:** `quest_3`, `vision_pro`, `spacemouse`, `keyboard`

**Implementation:**
- Launch IsaacTeleop retargeting backend as subprocess
- Configure WebSocket bridge for control data
- Start viewport stream (LiveKit or native WebRTC)

**Critical: Vision Pro requires native CloudXR app** — Safari/visionOS does NOT expose `XR_EXT_hand_tracking` via WebXR. The "browser link" path (7C.5) works for Quest 3 only.

### 7C.2 `configure_teleop_mapping(device_axes, joint_names, gains)`

**Implementation:** Pass-through to IsaacTeleop's YAML robot config or dex-retargeting config.

### 7C.3 `record_teleop_demo(output_path)`

**Format: HDF5 (robomimic schema)** — NOT USD TimeSamples.

Reasons:
- Every major robot learning framework uses HDF5 (robomimic, DROID, BridgeData, LeRobot)
- IsaacLab Mimic reads HDF5
- Phase 7G (GR00T fine-tuning) requires LeRobot v2 format (HDF5-based)
- USD TimeSamples cannot be stream-appended during recording

**Optional:** `export_demo_to_usd(hdf5_path)` for in-sim replay visualization.

### 7C.4 Hand-Tracking Retargeting

**Backend:** dex-retargeting (used by IsaacTeleop and Unitree xr_teleoperate). Maps 21-point hand skeleton to robot finger joints via optimization.

**IK solver:** `isaacsim.robot_motion.motion_generation.LulaKinematicsSolver` for end-effector IK. Note: Phase 8B provides the full motion planning stack — 7C.4 can use basic IK first and upgrade to RMPflow later.

### 7C.5 Chat Integration

"Start teleop with my Quest" → auto-configure, return browser link for WebXR viewer.
- Quest 3: WebXR in Meta Browser — works
- Vision Pro: native CloudXR app link — different path, document clearly

---

## Required: Safety (7C.6 — new task)

**Not in original spec. Must be added.**

- `teleop_watchdog_timeout_ms` (default 500ms): if no input received, hold-last-command → zero-velocity after 2s
- Joint velocity cap: applied before every PhysX write
- Workspace limits: configurable bounding box, reject commands outside
- E-stop: dedicated button/gesture that zeros all joint targets immediately

---

## Latency Targets (must be documented)

| Device | Max RTT |
|--------|---------|
| Keyboard/SpaceMouse (local) | < 20 ms |
| Quest 3 WebXR (Wi-Fi) | < 80 ms |
| Vision Pro CloudXR | < 60 ms |

---

## Test Strategy

| Test | Level | What |
|------|-------|------|
| WebSocket message parsing | L0 | Joint command deserialization |
| HDF5 recording format | L0 | Verify schema matches robomimic |
| Watchdog timeout | L0 | Timer fires correctly |
| Velocity clamping | L0 | Joint commands capped |
| End-to-end teleop | L3 | Requires Kit + XR device |

## Known Limitations

- Vision Pro requires native app, not browser
- dex-retargeting needs per-robot YAML config
- IK solver has no singularity detection
- No SLAM/localization for mobile robot teleop
