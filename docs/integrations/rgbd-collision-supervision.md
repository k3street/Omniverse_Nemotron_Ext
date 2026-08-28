# RGB-D collision supervision for real robot execution

The local collision monitor turns synchronized detector and RGB-D output into
a metric stop signal:

```text
RGB detector box / instance mask
              + registered depth
              + camera intrinsics and T_base_camera
              v
       base-frame object points
              + current/swept link capsules
              v
       clearance -> CLEAR / CONTACT OK / STOP
```

Use the pure NumPy implementation from
[`scripts/rgbd_collision_safety.py`](../../scripts/rgbd_collision_safety.py):

```python
from scripts.rgbd_collision_safety import (
    draw_collision_overlay,
    fuse_detection_with_depth,
    predict_detection_collisions,
)

detection_3d = fuse_detection_with_depth(
    label=detection.label,
    score=detection.score,
    xyxy=detection.xyxy_pixels,
    instance_mask=detection.mask,       # preferred; None accepts a box
    depth_m=registered_depth_m,
    intrinsics=K,
    camera_to_base=T_base_camera,
)

predictions = predict_detection_collisions(
    [detection_3d],
    current_segment_starts=current_capsule_starts,
    current_segment_ends=current_capsule_ends,
    proposed_segment_starts=next_capsule_starts,
    proposed_segment_ends=next_capsule_ends,
    radii_m=capsule_radii_with_uncertainty,
    minimum_clearance_m=0.03,
    allowed_contact_labels={"banana"} if phase == "grasp" else set(),
)

if any(item.potential_collision for item in predictions):
    controller.stop()
    request_fresh_semantic_decision()

annotated_rgb = draw_collision_overlay(rgb, predictions)
```

The production loop should run locally at the camera/control rate. Gemini gets
snapshots on anomalies and periodic semantic checkpoints; it is not placed in
the emergency-stop latency path.

## ROS 2 adapter

The repository includes a standard ROS 2 node and conservative Franka example
configuration:

```bash
python3 scripts/rgbd_collision_monitor_ros2.py \
  --config config/rgbd_collision_monitor.example.json \
  --check-config

./launch_rgbd_collision_monitor.sh \
  config/rgbd_collision_monitor.example.json
```

The node synchronizes these inputs:

- RGB and registered depth `sensor_msgs/Image` (`16UC1` millimeters or `32FC1`
  meters).
- Pixel detections from `vision_msgs/Detection2DArray`.
- Optional nonzero robot self-mask and optional integer instance-ID image. A
  detection's string `id` selects the same value in the instance image.
- `sensor_msgs/CameraInfo` intrinsics and timestamped TF for the optical camera
  and every configured robot-capsule endpoint.
- Current semantic phase on `std_msgs/String` for allowed target contact.
- Optional proposed capsules on `std_msgs/Float32MultiArray`. Each capsule is
  seven base-frame values: `start_xyz, end_xyz, radius_m`. Without it, the node
  uses short-horizon link-velocity extrapolation.

It publishes:

- `/isaac_assist/safety_stop` (`std_msgs/Bool`), latched on the first predicted
  collision or invalid safety input.
- `/isaac_assist/rgbd_collision_status` (`std_msgs/String`) with JSON 3D bounds,
  clearance, phase, rejected inputs, and stop state.
- `/isaac_assist/rgbd_collision_overlay` (`sensor_msgs/Image`) with
  `CLEAR`/`CONTACT OK`/`STOP` boxes and metric clearance.
- `/isaac_assist/reset_safety_stop` (`std_srvs/Trigger`). Reset is refused until
  the configured number of consecutive clear frames has arrived.

Set `topics.stop_service` to a controller-provided `std_srvs/SetBool` service
to invoke the robot-side stop directly on the rising edge. Leaving it `null`
publishes the contract without pretending that a physical controller is wired.

## Physical bring-up checklist

1. Copy the example JSON and change camera/detector topics, base/camera frames,
   capsule endpoints/radii, and labels to match the physical system.
2. Confirm RGB, aligned depth, detections, masks, and CameraInfo use identical
   image geometry and timestamps. The default synchronization allowance is
   50 ms; reduce it after measuring the actual pipeline.
3. Publish the fixed-camera extrinsic or wrist hand-eye calibration through TF.
   The node requests `T_base_camera` at each RGB timestamp and fails safe when
   it is absent.
4. Verify every configured link frame resolves into the base frame while the
   arm moves. Configured capsules receive an additional uncertainty margin.
5. Connect the stop topic or `topics.stop_service` to a controller that cancels
   motion and holds safely. Test this connection at zero speed first.
6. Move a known object outside and then inside the configured clearance and
   verify both overlay and status before permitting autonomous motion.
7. Exercise camera loss, stale frames, missing TF, malformed depth, and detector
   loss. With `fail_safe_on_invalid_input=true`, each must stop the controller.
8. Tune allowed labels per phase. A grasp target may be allowed during grasp;
   people, table edges, cables, and unrelated objects remain disallowed.

## Hardware inputs

- Registered and time-synchronized RGB and metric depth.
- Camera intrinsic matrix from `CameraInfo` or the device SDK.
- A measured optical-camera-to-robot-base transform. Hand-eye calibration is
  required for a wrist camera.
- Current link transforms from joint encoders and forward kinematics.
- Conservative link capsule radii plus calibration/depth uncertainty.
- A detector that supplies pixel boxes; instance masks are strongly preferred.
- A robot self-mask so arm pixels do not look like external obstacles when a
  full-scene depth cloud is used.

Target contact must be phase-aware. Contact with a detected banana can be
allowed during grasp while contact with a person, table edge, cable, or an
unrelated object remains a stop. Force/torque and tactile feedback should be
fused with this visual clearance monitor whenever they are available.

## What is already wired

The Gemini RoboLab runner enables simulated RGB-D, records numerical depth
summaries and a depth panel at mid-motion checkpoints, and locally checks grasp
drift/drop every IK chunk. The reusable fusion, overlay, stop latch, message
conversion, configuration, and ROS contracts are unit tested. The ROS node has
been smoke-started on Jazzy. Physical activation still requires editing the
configuration to match the actual camera, detector, TF frames, capsule
geometry, and controller stop service; those facts cannot be safely inferred
from simulation.
