# Gemini Robotics ER 2 with adaptive RoboLab IK

This workflow uses `gemini-robotics-er-2-preview` as a visual semantic coach.
Gemini approves, retries, or aborts each task phase from a fresh camera and
state observation. Physics and joint control remain local.

The default executor seeds the proven downward gripper orientation with the
safe approach segment from a successful demonstration. It then computes
approach, descend, grasp, lift, and above-plate targets from the current banana
and plate poses and reaches them with bounded damped-least-squares Jacobian IK.
The existing residual controller performs final plate centering and release,
centering laterally at hover height before lowering toward the plate.

The coach state distinguishes `grasp_candidate` (finger closure was obstructed
near the banana) from `grasp_confirmed` (the banana measurably followed the
lift). A live geometry-based `banana_plate_contact_proxy` suppresses additional
lowering as soon as the object enters the plate contact envelope and is exposed
to Gemini in the next phase observation. These are fused proprioceptive and
geometric signals; the current Isaac Sim 6 RoboLab configuration does not yet
provide a valid direct fingertip/plate force measurement to this coach.

Transport is no longer treated as one open-loop semantic phase. Grasp drift
and object lift are checked after every local IK chunk. A violation stops local
motion immediately and sends the stopped scene to Gemini. During normal
transport, a fresh RGB-D checkpoint is sent every 10 IK chunks by default:

```bash
./launch_gemini_robotics_robolab.sh \
  --coach-interval-iterations 10 \
  --maximum-grasp-drift 0.025 \
  --minimum-transport-lift 0.030
```

The model is therefore not called every physics step. Fast geometric safety
runs locally, while Gemini handles slower semantic reassessment. Transport
anomalies enter a bounded recovery state: hold the closed gripper and measure
whether slip continues; relatch a stable in-hand shift after a fresh Gemini
approval; otherwise set down vertically when still carried, open, retreat,
reacquire from the new object pose, regrasp, re-lift, verify attachment, and
resume. Recovery attempts default to two and every segment is traced.

The known slip-producing scene was first live-validated with two detected
shifts. Both stabilized, transport resumed, and the task passed all nine final
gates; that admitted episode contains 785 executed samples.

The physical fallback was then forced in the same scene. The first anomaly
produced a measured support stop, open-gripper retreat, fresh RGB-D Gemini
reacquisition and pre-grasp approvals, a support-aligned top-down regrasp, a
physical re-lift check, and transport resumption. A second anomaly set the
banana down on the plate. Gemini returned `complete`; local plate overlap and
only 0.04 mm of motion across a second settled hold corroborated completion,
so the controller skipped an unnecessary regrasp. All nine gates passed and
the admitted episode contains 1,350 executed samples. The complete trace is
`artifacts/gemini_groot_smoke/attempts/attempt_000010/sequence_trace.json`.

Recovery is configured by:

```bash
./launch_gemini_robotics_robolab.sh \
  --max-transport-recoveries 2 \
  --recovery-hold-steps 24 \
  --recovery-stability-drift 0.008
```

Run the normal visible task:

```bash
./launch_gemini_robotics_robolab.sh
```

Test live-pose retargeting by relocating both objects after reset:

```bash
./launch_gemini_robotics_robolab.sh \
  --banana-offset 0.05 0.03 \
  --plate-offset -0.04 0.02
```

Offsets are XY meters in robot-root coordinates and must remain inside the
Franka workspace. Every phase writes its requested live target, IK iterations,
final error, Gemini decision, and measured outcome to
`artifacts/gemini_robotics_er2_robolab/sequence_trace.json`.

Rotate the banana and its object-relative 6-DoF grasp together:

```bash
./launch_gemini_robotics_robolab.sh \
  --banana-offset 0.03 -0.02 \
  --banana-yaw-deg 45 \
  --plate-offset -0.02 0.03
```

The local controller now closes both translation and orientation error. The
fixed demonstration is used only to establish a safe initial arm posture; the
live grasp target is an object-frame transform applied to the current banana
pose.

## Training episodes versus evaluation traces

`sequence_trace.json` is audit evidence, not a demonstration. The live runner
has a separate Sim 6 transition recorder that captures the executed actions,
joint state, EEF pose, banana/plate poses, available force/contact channels,
and paired exterior/wrist camera video. It publishes `run_N.hdf5` and
`episode_N_policy.mp4` only when all Gemini supervision gates and physical
lift/place/detachment checks pass. A failed, aborted, or interrupted run leaves
no training episode.

Successful pairs are written to
`artifacts/gemini_robotics_er2_robolab/training_episodes/` by default. Choose a
different shard and explicit index with:

```bash
./launch_gemini_robotics_robolab.sh \
  --training-episode-dir artifacts/gemini_campaign/episodes \
  --episode-index 0
```

The recorded provenance is
`gemini_robotics_er2_supervised_local_se3_ik`: Gemini is the visual/semantic
teacher, while the low-level trajectory comes from the local feedback
controller. This is policy-distillation data; it is not mislabeled as direct
Gemini motor output.

## Campaign collection for GR00T

Plan a deterministic pose/appearance campaign without launching Isaac Sim:

```bash
python3 scripts/run_gemini_groot_campaign.py \
  --target-successes 100 \
  --output artifacts/gemini_groot_campaign \
  --dry-run
```

Run it, showing only the first attempt and collecting until 100 episodes have
passed the admission gate:

```bash
python3 scripts/run_gemini_groot_campaign.py \
  --target-successes 100 \
  --max-attempt-multiplier 2 \
  --visible-first \
  --output artifacts/gemini_groot_campaign
```

The current campaign makes real simulator changes to banana XY/yaw, plate XY,
sphere-light intensity, and HDRI background. The plan explicitly reports that
object identity, receptacle identity, and table/receptacle material are not yet
implemented; those require per-task grasp profiles and verified USD material
bindings before they can be counted as dataset diversity.

Convert only the published successful pairs:

```bash
python3 scripts/convert_robolab_demo_to_groot.py \
  --input-dir artifacts/gemini_groot_campaign/episodes \
  --output artifacts/lerobot/gemini_groot_campaign
```

The converter rechecks the HDF5 success flag and quaternion convention and
copies collection provenance/randomization values into `meta/episodes.jsonl`.
The live validation shard currently converts three admitted episodes and
2,872 frames into `artifacts/gemini_groot_smoke/lerobot_v3`; failed recovery
attempts were not admitted.
The per-attempt launcher is intentionally simple and resumable. Before scaling
past a few hundred episodes, replace it with persistent Isaac Sim workers so
the simulator and model client are reused across resets; the episode admission
and data contracts stay the same.

## RGB-D bounding boxes and collision clearance

[`scripts/rgbd_collision_safety.py`](../../scripts/rgbd_collision_safety.py)
contains the camera-agnostic path needed for simulation or a real RGB-D
camera. It fuses a detector box (preferably its instance mask) with registered
depth, rejects background depth inside the rectangle, back-projects the result
through calibrated intrinsics/extrinsics, and compares those 3D points against
current or short-horizon swept robot-link capsules. Its overlay marks each
detection `CLEAR`, `CONTACT OK`, or `STOP` with metric clearance.

For real hardware this monitor belongs in the local control process, not in
the Gemini request path. It requires synchronized RGB/depth, a calibrated
optical-camera-to-robot-base transform, up-to-date robot joint/link poses,
robot self-filtering, and phase-specific allowed contacts. A box alone includes
background pixels, so an instance mask is safer. Treat this as an additional
supervisory stop and uncertainty signal, not a replacement for certified robot
safety hardware or torque/contact limits.

The standard ROS 2 adapter is
[`scripts/rgbd_collision_monitor_ros2.py`](../../scripts/rgbd_collision_monitor_ros2.py),
with its configurable Franka example in
[`config/rgbd_collision_monitor.example.json`](../../config/rgbd_collision_monitor.example.json).
The dedicated RGB-D guide covers topic, TF, stop-service, and calibration
bring-up.

For comparison, `--disable-adaptive-ik` restores the previous fixed joint-state
replay. This flag is a baseline only and will not follow relocated objects.
