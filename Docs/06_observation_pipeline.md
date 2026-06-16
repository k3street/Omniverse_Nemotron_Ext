# 06 — Observation Pipeline

A ROS2 node that fuses sensors into a unified observation message at a fixed rate. Both the Continuity Manager and the policy services consume from this single source of truth.

## Output Schema

Published at the highest control rate of any active policy (typically 50 Hz):

```python
@dataclass
class Observation:
    timestamp: float

    # Proprioception
    tcp_pose_left: Pose       # in base_link
    tcp_pose_right: Pose
    tcp_vel_left: Twist
    tcp_vel_right: Twist
    joint_positions_left: np.ndarray   # (n_dof_arm,)
    joint_positions_right: np.ndarray
    joint_velocities_left: np.ndarray
    joint_velocities_right: np.ndarray

    # Gripper
    gripper_width_left: float
    gripper_width_right: float
    gripper_force_left: float
    gripper_force_right: float

    # Force / torque
    ft_left: np.ndarray      # (6,)  fx fy fz tx ty tz
    ft_right: np.ndarray

    # Vision
    rgb_wrist_left: np.ndarray     # (H,W,3) uint8 — typically downsampled to 84x84 for policies
    rgb_wrist_right: np.ndarray
    rgb_scene: np.ndarray          # head/torso camera, full res for perception
    depth_scene: np.ndarray        # (H,W) float32 m

    # Fused perception
    detected_objects: list[DetectedObject]   # see below

    # Platform state (read-only from this stack)
    base_stationary: bool
    telescope_height_m: float
```

```python
@dataclass
class DetectedObject:
    object_id: str           # stable across frames via tracker
    cls: str
    pose: Pose               # in base_link
    confidence: float
    bbox_3d: np.ndarray      # (3,) extents
    seg_mask: np.ndarray     # optional, 2D mask in scene frame
```

## Pipeline Stages

```
camera drivers ─┐
                ├─▶ time-sync (msg_filters / approximate_time, max slop 30 ms)
proprio ─┬──────┤
F/T ─────┘      ├─▶ object detector (e.g., YOLO-World or Grounded-SAM-2)
                │     │
                │     ▼
                │   pose estimator (FoundationPose, MegaPose, or model-based ICP)
                │     │
                │     ▼
                │   tracker (ByteTrack-3D or simple Hungarian on 3D distance)
                │     │
                │     ▼
                ├─▶ assemble Observation
                ▼
         publish on /manipulation/observation
```

## Object ID Stability

Stable IDs are critical — predicates and Task Spec phases reference objects by ID. The tracker assigns IDs on first detection (`obj_001`, `obj_002`, ...) and the Continuity Manager re-keys them at task start using Pi0.5's semantic labels (`red_mug`, `tray`).

If the tracker drops an object (occlusion) and re-acquires it, ID continuity is best-effort via 3D distance + class match. If continuity is uncertain, mark as `confidence_continuity: LOW` so the predicate evaluator can be more cautious about `object_attached`.

## Camera Configuration (open-arm reference)

For the bimanual platform with open-arm-style configuration:
- Two wrist cameras (one per arm): RGB-D, ~640x480, mounted on forearm, looking forward toward gripper.
- One scene camera: RGB-D, mounted on torso/head, wide field of view, calibrated to base_link.

Wrist cameras feed RL policies (close-up, action-relevant). Scene camera feeds Pi0.5 and the object detector.

## Calibration Discipline

- Hand-eye: must be re-calibrated at any hardware change. Store in `config/calibration/{embodiment_id}.yaml`. The Observation Pipeline refuses to start if calibration is stale (>30 days) without an `--allow-stale-cal` flag.
- Workspace bounds: derived from calibration + telescope position. Republished when telescope height changes.

## Latency Targets

| Stage | Budget |
|---|---|
| Camera capture → pipeline | 15 ms |
| Object detection | 30 ms (run at 10 Hz, interpolate poses between detections) |
| Pose estimation | 20 ms |
| Assembly + publish | 5 ms |
| **Total perception lag** | **~70 ms** |

Proprioception updates every control tick (no detection in the loop). Object poses are interpolated between detection updates using gripper-relative motion when an object is `attached_to`.

## What This Pipeline Does NOT Do

- Does not call Pi0.5. Pi0.5 reads the latest `Observation` via the Continuity Manager.
- Does not evaluate predicates.
- Does not store history beyond a small ring buffer (last 1 s).
- Does not handle navigation cameras or any sensor outside manipulation.
