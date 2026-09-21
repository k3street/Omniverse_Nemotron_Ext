# NVIDIA Isaac GR00T N1.7 integration

Isaac Assist controls an external
[NVIDIA Isaac-GR00T](https://github.com/NVIDIA/Isaac-GR00T) checkout. GR00T's
CUDA, PyTorch, Transformers, and TensorRT dependencies remain isolated from
Isaac Sim's bundled Python and the FastAPI sidecar.

## Configuration

```bash
export GROOT_ROOT=/home/kimate/Documents/Github/Isaac-GR00T
export GROOT_PYTHON="$GROOT_ROOT/.venv/bin/python"
```

On DGX Spark, provision the checkout with NVIDIA's Spark installer and source
`scripts/activate_spark.sh` when operating GR00T directly. `groot_n17_status`
reports the checkout, environment Python version, demo dataset, CLI horizon
flag, and whether the live-execution gate is enabled.

The base checkpoint loads the gated `nvidia/Cosmos-Reason2-2B` backbone. Accept
its Hugging Face terms and authenticate before first inference.

## Tools

- `groot_n17_status`: inspect installation and Spark readiness.
- `groot_n17_infer`: run open-loop dataset inference.
- `groot_n17_serve`: run the ZeroMQ policy server entrypoint.
- `groot_n17_finetune`: post-train on GR00T LeRobot-v2 data with a modality config.

All tools default to `dry_run=true`. Live execution additionally requires:

```bash
export ISAAC_ASSIST_GROOT_EXECUTE=1
```

The adapter invokes argv directly without shell interpolation, validates local
paths, bounds foreground execution with `GROOT_TIMEOUT_SECONDS` (default one
hour), and tail-caps returned logs. Long-running policy servers should be
managed by a supervisor or terminal session.

## DROID acceptance smoke

In Isaac Assist chat:

> Check GR00T N1.7 status, then dry-run base-model inference on the bundled
> DROID sample using embodiment OXE_DROID_RELATIVE_EEF_RELATIVE_JOINT,
> trajectories 1 and 2, PyTorch mode, and execution horizon 8.

After reviewing the command and enabling live execution, repeat with
`dry_run=false`. The installed EA checkout uses `--action-horizon`; current GA
uses `--execution-horizon`. Isaac Assist detects the supported flag.

## Visualize examples in Isaac Sim

The local GUI path uses NVIDIA RoboLab as the Isaac Sim/Isaac Lab client. GR00T
and Isaac Sim remain separate processes so their Python and CUDA dependencies
do not collide.

Launch the bundled recorded example first. It renders a DROID-style arm moving
a Rubik's cube and banana into a bowl, and does not require a policy server:

```bash
./launch_groot_robolab.sh replay
```

The launcher disables RoboLab's redundant OpenCV `cv2.imshow` camera popup
while leaving the Isaac Sim GUI and MP4 recording enabled. This avoids a known
conflict when `opencv-python-headless` owns the shared `cv2` module.

For live GR00T N1.7 control, start the validated DROID policy server from the
Spark-ready Isaac-GR00T environment:

```bash
cd /home/kimate/Documents/Github/Isaac-GR00T
source scripts/activate_spark.sh
uv run python gr00t/eval/run_gr00t_server.py \
  --model-path nvidia/GR00T-N1.7-DROID \
  --embodiment-tag OXE_DROID_RELATIVE_EEF_RELATIVE_JOINT \
  --device cuda \
  --host 127.0.0.1 \
  --port 5555 \
  --use-sim-policy-wrapper
```

Then launch a single Isaac Sim environment from this repository:

```bash
./launch_groot_robolab.sh live --task BananaOnPlateTask
```

The live wrapper also emits the versioned Franka torque/contact state and its
validity mask. Existing DROID checkpoints ignore these additive keys; a
sensor-aware `NEW_EMBODIMENT` checkpoint consumes them directly.

The base `nvidia/GR00T-N1.7-3B` checkpoint is useful for open-loop DROID
inference. RoboLab's validated closed-loop simulation configuration uses the
post-trained `nvidia/GR00T-N1.7-DROID` checkpoint, two DROID cameras, and an
execution horizon of eight.

For versioned Franka torque/contact capture, legacy-episode validity masks, and
sensor-aware post-training, see
[Franka force/contact data for DROID and GR00T](franka-force-tactile-data.md).

On DGX Spark, RoboLab needs its aarch64 Isaac Sim 5.1 environment and a CUDA 13
NVRTC mapping for the GB10 `sm_121` GPU. The launcher also supplies Isaac Sim's
required aarch64 `libgomp` preload. Override checkout locations with
`ROBOLAB_ROOT` or `ROBOLAB_PYTHON`.
