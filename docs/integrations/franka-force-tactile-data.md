# Franka force/contact data for DROID and GR00T

The standard DROID platform has no fingertip tactile array or dedicated wrist
force/torque transducer. The Franka arm does expose measured joint torque and
Polymetis carries libfranka's filtered external-joint-torque estimate. This
integration retains those signals without pretending that missing tactile data
is a zero-force measurement.

## Dataset schema

Sensor schema `2.0` adds two LeRobot columns while preserving the original
17-dimensional DROID state and action columns:

- `observation.sensors` (`float32[38]`): measured, commanded, and external joint
  torque; end-effector wrench; joint contact; gripper contact force; and a
  generic gripper-touch flag.
- `observation.sensor_validity` (`float32[7]`): one validity value for each
  signal group.

Every output episode has both columns. Legacy episodes are zero-filled with a
zero validity mask. Sensor-aware training must consume the mask; a zero value
with validity zero means “not observed,” not “zero force.” Per-signal coverage
and source HDF5 paths are written to `meta/episodes.jsonl`.

## Patch the real DROID collector

The upstream DROID collector already stores measured and computed torque in raw
`trajectory.h5`, but its `FrankaRobot.get_robot_state()` currently omits
Polymetis `motor_torques_external`. On the real robot collection machine:

```bash
python scripts/patch_droid_external_torque.py \
  --droid-root /path/to/droid \
  --check

python scripts/patch_droid_external_torque.py \
  --droid-root /path/to/droid

git -C /path/to/droid diff -- droid/franka/robot.py
```

The installer is narrow and idempotent. It edits only the state dictionary,
refuses an unrecognized collector layout, and verifies the pinned Polymetis
protobuf when that source file is present. New raw episodes then contain
`observation/robot_state/motor_torques_external`. Old episodes cannot recover
this signal and remain mask-invalid.

## Simulation collection and conversion

`generate_banana_on_plate_demos.py` now attaches the canonical
`sensors/franka` group to every generated HDF5 episode. Isaac actuator torque is
correctly labeled as commanded torque—not measured torque. Simulated contact
force and touch are valid only when RoboLab has a live contact sensor.

Convert as before:

```bash
/home/kimate/Documents/Github/Isaac-GR00T/.venv/bin/python \
  scripts/convert_robolab_demo_to_groot.py \
  --input-dir artifacts/banana_on_plate_demos_v3 \
  --output artifacts/groot_datasets/banana_on_plate_sensor_v2
```

The converter recognizes the canonical group, raw-DROID torque names, and
legacy RoboLab torque paths. It computes sensor normalization statistics using
only valid samples.

## Convert real DROID episodes

Convert one newly collected raw DROID episode with timestep-aligned exterior
and wrist MP4 exports (one video frame per trajectory row):

```bash
/home/kimate/Documents/Github/Isaac-GR00T/.venv/bin/python \
  scripts/convert_real_droid_to_groot.py \
  --trajectory /data/episode_000/trajectory.h5 \
  --exterior-video /data/episode_000/exterior.mp4 \
  --wrist-video /data/episode_000/wrist.mp4 \
  --instruction "Pick up the banana and put it on the plate" \
  --output artifacts/groot_datasets/real_banana_sensor_v2
```

For multiple episodes, pass `--manifest episodes.json`. Relative paths are
resolved from the manifest directory:

```json
[
  {
    "trajectory": "episode_000/trajectory.h5",
    "exterior_video": "episode_000/exterior.mp4",
    "wrist_video": "episode_000/wrist.mp4",
    "instruction": "Pick up the banana and put it on the plate",
    "success": true
  }
]
```

Audit the pilot batch before conversion:

```bash
/home/kimate/Documents/Github/Isaac-GR00T/.venv/bin/python \
  scripts/audit_real_droid_episode.py \
  --manifest episodes.json \
  --require-success \
  --output-json artifacts/audits/real_banana_pilot.json
```

The command exits with status 2 when any episode is unsafe to convert. It
checks required state and action shapes, finite values, joint ranges and jumps,
robot timestamp gaps, measured/external torque coverage, camera readability,
frame count and frame-rate alignment, language, and recorded task success. Its
JSON report is suitable for a collection gate or CI artifact. Legacy data can
be inspected with `--no-require-timestamps`, `--no-require-actions`, or
`--no-require-external-torque`; these relaxations should not be used for a new
pilot collection.

The real-data converter retains the DROID state/action layout, converts the
Cartesian XYZ/RPY pose to GR00T's XYZ+rotation-6D representation, copies actual
torque signals with validity masks, and creates both camera streams in LeRobot
v2.1 format. It refuses episodes shorter than the model's 40-step action
horizon, rejects mismatched camera frame rates, and refuses unaligned video
frame counts. Use the DROID camera timestamps to align a full-rate raw camera
export before passing it to this converter.

## Sensor-aware GR00T post-training

The existing OXE DROID embodiment ignores the additive sensor columns, so old
training commands remain compatible. To train a new embodiment that consumes
the sensor values and masks:

```bash
export DATASET_PATH="$PWD/artifacts/groot_datasets/banana_on_plate_sensor_v2"
export OUTPUT_DIR="$PWD/artifacts/checkpoints/banana_on_plate_sensor_v2"
export EMBODIMENT_TAG=NEW_EMBODIMENT
export MODALITY_CONFIG_PATH="$PWD/configs/groot/droid_force_modality.py"
./scripts/train_banana_on_plate_groot.sh
```

The launcher generates or reuses `meta/relative_stats.json` before training,
which GR00T requires for relative end-effector and joint actions. Set
`GENERATE_STATS=0` only when those statistics have already been validated.

The model must be post-trained for these new state dimensions; the base model
does not gain tactile understanding zero-shot. When physical fingertip sensors
are selected later, add their hardware-specific array as another schema
version rather than changing the meaning or width of an existing channel.

## Live sensor-aware inference in RoboLab

The existing live command now registers additive torque/contact observation
terms and includes every schema-v2 state key in each GR00T replan request:

```bash
./launch_groot_robolab.sh live \
  --task BananaOnPlateTask \
  --open-loop-horizon 10
```

Base DROID checkpoints remain compatible because GR00T ignores state keys not
declared by their modality configuration. A checkpoint post-trained with
`droid_force_modality.py` consumes the same request without a client change.
In Isaac Sim, commanded torque and contact force/touch are marked valid;
measured torque, external torque, wrench, and joint-contact channels remain
zero and mask-invalid because the simulator does not physically measure them.

Start a sensor-aware checkpoint with the sim wrapper before launching RoboLab:

```bash
cd /home/kimate/Documents/Github/Isaac-GR00T
uv run python gr00t/eval/run_gr00t_server.py \
  --model-path /path/to/sensor-aware-checkpoint \
  --embodiment-tag NEW_EMBODIMENT \
  --host 0.0.0.0 \
  --port 5555 \
  --use-sim-policy-wrapper
```
