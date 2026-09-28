# Gemini Robotics ER 2 with adaptive RoboLab IK

This workflow uses `gemini-robotics-er-2-preview` as a visual semantic coach.
For guarded world-effect execution, one Gemini action-planning call composes the
longest currently supportable queue of advertised runtime tool calls. Physics,
joint control, fresh RGB-D/contact checks, and just-in-time typed admission stay
local. The unexecuted queue suffix is discarded and Gemini is called again only
when sensor/execution evidence invalidates it, an unexpected situation appears,
the queue exhausts before the selected goal, or the goal changes.

Collective instructions use the same path across multiple goals. After one
selected outcome is observed complete, the runtime discards its remaining
queue, expires the old scene-membership lease, requests a fresh complete goal
graph from the current RGB-D scene, preserves every unresolved member outcome,
activates the next goal, and rebinds a provider. The task ends only when a fresh
graph declares the whole instruction complete, exposes no remaining candidates
or blockers, and passes a task-completion membership lease. For example:

```bash
./launch_gemini_robotics_robolab.sh \
  --guarded-world-effect-execution \
  --world-effect-max-operations 64 \
  --task BlocksInBinTask \
  --movable-object-asset rubiks_cube \
  --target-receptacle-asset grey_bin \
  --instruction 'Clean the table by putting all movable items in the grey bin'
```

The role-bound Rubik's cube seeds the first provider context; it does not limit
the collective goal graph to one object. Runtime tools remain generic and each
new object is selected from the fresh scene graph.

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

## Running it, and choosing the model

Always go through `./launch_gemini_robotics_robolab.sh`. Calling
`python scripts/run_gemini_robotics_robolab.py` directly fails on the first
import:

```
ModuleNotFoundError: No module named 'cv2'
```

That is the interpreter, not the code. The planner needs Isaac Sim's Python
together with `OMNI_KIT_ACCEPT_EULA`, the `libgomp` preload and the Isaac Lab
and RoboLab paths on `PYTHONPATH` — all of which the launcher sets. A plain
`python3` on this machine resolves to whichever virtualenv is active, and
`.venv-newton` (used for the headless Newton solver work) carries neither cv2
nor Isaac.

The planner speaks one dialect on the wire — messages with `image_url` data
URIs, `tools` with `tool_choice`, `.tool_calls` back — and each provider
translates. Selecting one is a flag:

```bash
# Gemini (default)
./launch_gemini_robotics_robolab.sh --task BlocksInBinTask ...

# GPT-6 Astra, capped at $25 of model spend
OPENAI_API_KEY=sk-... ./launch_gemini_robotics_robolab.sh \
  --provider openai --model gpt-6-astra --budget-usd 25 \
  --task BlocksInBinTask ...
```

Two things to know before pointing it at OpenAI:

* **Reasoning models take the Responses endpoint, not chat completions.**
  `gpt-6-astra` refuses function tools on `/v1/chat/completions` outright, and
  the provider routes the whole reasoning family to `/v1/responses` for that
  reason. Nothing in the planner changes; the translation is inside the
  provider.
* **Do not reuse `OPENAI_API_BASE` from `.env`.** It points at a local Ollama
  (`http://localhost:11434/v1`), so anything that adopts it silently talks to
  a local model instead of OpenAI. The planner defaults to `api.openai.com`
  and only moves when `--provider-base-url` says so.

`--budget-usd` stops the run before the call that would breach it, and prints
what was spent. It defaults to $25; `0` disables it. Per token, Astra costs 10x
Gemini Robotics-ER 2 ($10/$50 against $1/$5 per million in/out; Google doubles
ER 2 on 2027-01-01), so the cap matters more there. The one full BlocksInBinTask
episode sent 659K input tokens and 14K output, and input was 90% of its $7.28:
each call resends a growing observation history.

Scene roles default to the banana task's `banana` and `plate_large`; any other
task needs them bound, e.g. `--movable-object-asset red_block
--target-receptacle-asset grey_bin` for `BlocksInBinTask`.

### Reachability before motion

Astra will not authorize motion on object positions and a list of IK executors
alone: it asks for collision-free reachability of the grasp, carry and
placement first, and Gemini used to assert it without evidence. Before the
feasibility call, the runtime now solves the seeded approach, grasp, lift,
above-receptacle and place poses with damped least squares IK on the live arm
(`scripts/kinematic_reachability.py`). It writes joint positions and refreshes
kinematics with `sim.forward()`, never stepping physics or touching a joint
target, then restores the arm and aborts the run if it cannot. The result is
published as `motion.non_actuating_reachability_probe`: per-pose convergence,
errors, joint-limit margin along the path and the lowest link origin above the
support. It compares link origins with the support height, not meshes, and it
covers the seeded poses, not whatever target the model later picks.

On `BlocksInBinTask` (2026-09-27) all five poses solved with joint-limit
margins of at least 0.185, and Astra went from withholding motion to
authorizing it. A held-arm experiment showed the probe leaves the end effector
where it found it to within a nanometre.

## Measuring a pass rate

One passing episode is an anecdote. `scripts/evaluate_planner.py` runs a fixed
set of seeded scene variations (object and receptacle offsets, object yaw,
light, background), one Isaac process each, and grades every episode from its
trace with `scripts/planner_eval_gate.py` rather than trusting the runner's own
PASS. The report gives the pass rate with a 95% Wilson interval, which failure
checks fired, model calls and spend.

The thresholds live in an acceptance file, e.g.
`config/planner_eval/blocks_in_bin_astra_v1.json`, with two seed splits:

* `development` is for iterating on the planner.
* `final` is refused until the file is frozen (`evaluate_planner.py freeze`,
  which stamps it with a hash so a later edit is detected) and the working tree
  is clean. Each final run is appended to a ledger next to the file; running
  the final seeds again needs `--allow-repeat-final`, and the report says so.

After freezing, a check may only be tightened, with a dated entry in `changes`.
Each episode gets a fresh artifact directory and the passive critic is off, so
no lessons carry between episodes; the gate fails any episode that applied
some. Runs resume where they stopped and halt at `--max-total-usd`.

```bash
scripts/evaluate_planner.py plan config/planner_eval/blocks_in_bin_astra_v1.json --split development
scripts/evaluate_planner.py run  config/planner_eval/blocks_in_bin_astra_v1.json \
  --split development --output runs/eval-dev --max-total-usd 100
scripts/evaluate_planner.py score runs/eval-dev
```

## Training episodes versus evaluation traces

`sequence_trace.json` is audit evidence, not a demonstration. The live runner
has a separate Sim 6 transition recorder that captures the executed actions,
joint state, EEF pose, role-bound movable-object/receptacle poses, available
force/contact channels, and paired exterior/wrist camera video. In guarded
world-effect mode, it publishes `run_N.hdf5` and `episode_N_policy.mp4` only
when the final observed goal predicates pass, any acquired attachment is
released with contact cleared, every lease is consumed or has an explained
revocation, and the sensor/action/model trace is complete. A failed, aborted,
or interrupted run leaves no training episode.

Successful pairs are written to
`artifacts/gemini_robotics_er2_robolab/training_episodes/` by default. Choose a
different shard and explicit index with:

```bash
./launch_gemini_robotics_robolab.sh \
  --training-episode-dir artifacts/gemini_campaign/episodes \
  --episode-index 0
```

The recorded provenance is
`gemini_robotics_er2_model_governed_runtime_tools`: Gemini selects fresh
world-effect operations while the registered, model-configured feedback tools
execute their bounded trajectories. This is policy-distillation data; it is
not mislabeled as direct Gemini motor output.

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

The campaign uses the same guarded, model-planned execution path as the live
run; it does not replay the legacy task routine. It makes real simulator
changes to role-bound object XY/yaw, receptacle XY, sphere-light intensity, and
HDRI background. It can also rotate through rigid objects and receptacles that
already exist in the selected scene:

```bash
python3 scripts/run_gemini_groot_campaign.py \
  --target-successes 20 \
  --task BlocksInBinTask \
  --movable-object-assets red_block blue_block green_block yellow_block \
  --target-receptacle-assets grey_bin \
  --output artifacts/rigid_block_groot_campaign
```

Scene-role labels are derived from asset names unless explicitly supplied, so
the planning and execution contracts do not encode object names, robot joints,
grasp profiles, or a task routine. Table and receptacle material mutation are
still reported as unimplemented because the live scene currently exposes no
verified material-randomization control.

### Task and embodiment training matrix

Use the validated matrix when expanding beyond one task or robot:

```bash
python3 scripts/run_gemini_training_matrix.py \
  --spec config/gemini_training_matrix.example.json \
  --output artifacts/gemini_training_matrix
```

Planning is the default and does not launch Isaac Sim. The example contains
single-object inside/left/right/front/behind tasks, the working Franka with
parallel gripper, a vacuum variant, a dual-Franka embodiment, and the local
Amber Revan dual-Psyonic asset. It currently reports two runnable cells
(inside and robot-frame left-of) and six blocked cells. Each blocked cell names
the missing world capability, RGB-D completion evaluator, or embodiment runtime
adapter; it can never fall back silently to the Franka launcher.

Run just the proven cell with GUI visible for its first attempt:

```bash
python3 scripts/run_gemini_training_matrix.py \
  --campaign franka_cube_inside_bowl \
  --output artifacts/gemini_training_matrix \
  --execute
```

The task records contain only instruction, semantic roles, desired world
relation, and completion evaluator. Arm count, end-effectors, controller/runtime
adapter, and dataset adapter live in the embodiment records. A new embodiment
therefore reuses the same task contract and must supply a real runtime and data
adapter before collection is enabled. `--execute` refuses a selected matrix
that includes blocked cells; use explicit `--campaign` selection or
`--runnable-only` when that is intentional.

Publish a validated GR00T N1.7 projection without changing the canonical
episodes:

```bash
./path/to/Isaac-GR00T/.venv/bin/python \
  scripts/export_gemini_episode_dataset.py \
  --input-dir artifacts/gemini_groot_campaign/episodes \
  --store-root artifacts/gemini_groot_campaign/dataset_store \
  --groot-root /path/to/Isaac-GR00T \
  --instruction 'Clean the table by putting all movable items in the grey bin'
```

The publisher treats every source HDF5/MP4 pair as immutable. It validates and
hashes the complete collection, writes a content-addressed source manifest,
converts into a staging directory, generates N1.7 40-step relative-action
statistics with NVIDIA's own tool, loads a complete multimodal episode through
the N1.7 OXE-DROID loader, re-hashes the originals, and only then atomically
publishes the model-specific export. An identical source and transform cannot
be overwritten. A changed source collection or transform produces a different
directory under `dataset_store/exports/groot-n17-droid-lerobot-v2.1/`.

Each export contains `export_manifest.json` with source/transform/tool hashes
and `validation_report.json` with structural, video-decode, statistics, and
NVIDIA-loader results. `source_manifests/` preserves the canonical snapshot
contract. The underlying converter rechecks the HDF5 success flag and
quaternion convention and copies collection provenance/randomization values
into `meta/episodes.jsonl`; it is invoked only against a fresh staging
directory by the publisher.

The campaign additionally requires `episode_acceptance.json`, checks the
recorder's append-only manifest, and counts an episode only when both the
world-effect gate and real gripper-contact admission summary pass.
The live contact-sensor pilot admitted three episodes from three attempts and
converted 3,291 frames into `artifacts/contact_sensor_pilot/lerobot`. All three
episodes had 100% contact-sensor coverage; one includes a supervised physical
set-down, visual reacquisition, contact-confirmed regrasp, second lift, and
successful placement. Failed or contact-invalid attempts are not admitted.
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
