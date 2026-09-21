# Post-training Cosmos 3 Edge on repo demonstrations

`scripts/convert_robolab_demo_to_lerobot_v3.py` turns recorded episodes into a
LeRobot v3.0 dataset that [cosmos-framework](https://github.com/NVIDIA/cosmos-framework)
can post-train on. This page records what that framework requires beyond the
LeRobot spec, because none of it is discoverable from the spec and each item
costs a failed multi-GPU run to find.

## The shipped action-policy recipes are Nano, not Edge

Every experiment under
`cosmos_framework/configs/base/experiment/action/posttrain_config/action_policy_*`
uses `NANO_MODEL_CONFIG` (a Qwen3-VL-8B backbone). The only Edge configs that
ship are *vision* SFT. Pointing `action_policy_droid_nano` at a Cosmos3-Edge DCP
fails at checkpoint load:

```
ValueError: Size mismatch between saved torch.Size([131072, 2048])
            and current: torch.Size([151936, 4096])
            for net.language_model.lm_head.weight
```

`[131072, 2048]` is Edge's 2B Nemotron reasoner; `[151936, 4096]` is Qwen3-VL-8B.

The derivation to an Edge recipe is mechanical, because
`sft/models/edge_model_config.py` documents `EDGE_MODEL_CONFIG` as *"derived
from NANO_MODEL_CONFIG; every field is identical except the Cosmos3-Edge
deltas"*, and it already carries `action_gen=True`, `max_action_dim=64`,
`resolution="480"` and `action_loss_weight=10.0`:

1. Copy `action_policy_droid_nano.py` to `action_policy_droid_edge.py`.
2. Swap `NANO_MODEL_CONFIG` for `EDGE_MODEL_CONFIG`, import included.
3. Rename the module-level `LazyDict` and the `job.name`. The file self-registers
   by looking its own global up by identity, so the variable name *is* the
   experiment name.
4. Add the import to `cosmos_framework/configs/base/config.py`.
5. Point the TOML's `experiment` and `name` at `action_policy_droid_edge`.

## `action_space="joint_pos"` needs the split columns

The space Edge is post-trained in reads `action.joint_position`,
`observation.state.joint_positions` and `observation.state.gripper_position`
directly, and takes the gripper action from `ACTION_FEATURES[version]`. Unlike
the cartesian branches it never consults `IS_FLAT_ACTION`, so the packed 17-D
vectors alone produce `ValueError: Column 'action.joint_position' doesn't exist.`

The exporter writes both layouts, so one directory serves either reader. Gripper
convention differs between them: DROID stores the gripper raw as **0=closed** and
the reader flips it back to 1=closed for the training target, while this repo's
packed vectors are 1=closed throughout. The split columns therefore store `1-g`,
and the dataset registers as flipped — which leaves the training target equal to
what `scripts/cosmos3_edge_executor.py` already consumes.

## The directory name selects the schema

`DROIDLeRobotDataset` derives its feature mapping from `os.path.basename(root)`
and rejects any name outside its table. Register a new key in the six dicts of
`droid_lerobot_dataset_config.py` (`LEROBOT_ROOTS`, `IMAGE_FEATURES`,
`STATE_FEATURES`, `ACTION_FEATURES`, `IS_FLAT_ACTION`,
`HAS_MULTI_LANGUAGE_ANNOTATIONS`, `IS_GRIPPER_ACTION_FLIPPED`) rather than
naming the export after a shipped NVIDIA release — impersonating one silently
adopts that release's gripper convention and camera names.

It also reads three cameras. A two-camera recording satisfies
`viewpoint="wrist_view"` but not `"concat_view"` or `"third_person_view"`, which
query the right over-shoulder view and fail on the missing column.

## Sizing

- **One 80 GB GPU is not enough.** The model builds and loads, but training OOMs
  at `max_samples_per_batch=1` with full activation checkpointing. Post-training
  needs a multi-GPU node; the shipped recipe assumes `data_parallel_shard_degree=8`.
- **Set the shard degree to the GPUs you actually have.** Verify with
  `nvidia-smi -L | wc -l` — a node billed as 8× can come up with 7, and
  `torchrun --nproc_per_node=8` then dies with `CUDA error: invalid device ordinal`.
- **Disable `compile_tokenizer` without a CUDA toolkit.** The callback AOT-compiles
  the Wan VAE encoder and *raises* rather than falling back when `CUDA_HOME` is
  unset: `RuntimeError: AOT compilation produced no loadable functions`, on every
  rank, at the first training step. Rented GPU nodes routinely ship the driver
  with no toolkit (`nvidia-smi` reports a CUDA version; `/usr/local/cuda` and
  `nvcc` are absent). It is a speed optimisation, so set
  `[trainer.callbacks.compile_tokenizer] enabled = false` unless a toolkit is
  installed.
- **Lower `num_workers` for small datasets.** The recipe's `num_workers=16`
  shards episodes by rank × worker. 46 episodes over 7 ranks × 16 workers leaves
  most workers with no episode, pre-warm never buffers its 16 samples, and the
  ranks hang at the NCCL barrier indefinitely. The ranks that did fill spin at
  100% GPU while hung, so utilization is not a liveness signal. Pass
  `dataloader_train.dataloader.num_workers=2` at this dataset size.

## The action head trains from scratch

`checkpoint.keys_to_skip_loading` drops `action2llm`, `llm2action`,
`action_modality_embed` and `action_pos_embed`, so the action head is *not*
warm-started from the base checkpoint even though the reasoner and generation
heads are. A short run therefore cannot produce a working policy no matter how
good the data is — there is no pretrained action head to nudge.

Size expectations follow from that. The reference DROID run is 10,000
iterations at global batch 8192 (64 nodes x 4 GB200). On a single 8x80 GB node
at `max_samples_per_batch=4`, one iteration is a global batch of 32, so
matching the reference sample count would take far longer than the iteration
count alone suggests. Plan a real post-training run as hours-to-days of
multi-GPU time, and treat short runs strictly as pipeline validation.

Measured on 7xA100-80GB with the tokenizer compile disabled:

| `max_samples_per_batch` | samples/iteration | s/iteration | samples/s | GPU memory |
| --- | --- | --- | --- | --- |
| 4  | 28  | 2.2 | 12.7 | ~15-19 GB |
| 16 | 112 | 6.8 | 16.5 | ~17-22 GB |

Memory barely moved between the two, so it is dominated by model and optimizer
state rather than activations (full activation checkpointing is on) — there is
room for a much larger batch than the shipped default implies, and the larger
batch is ~30% more efficient per sample. A checkpoint is 36 GB (weights plus
optimizer and EMA state), large enough that pulling one off a rented node is
itself a planning item; `cosmos_framework.scripts.export_model` converts a DCP
checkpoint to a far smaller Hugging Face model directory, which is what the
policy server wants anyway.

## Launching

Parallelism must be set in the TOML — `model.parallelism.*` is rejected as a
Hydra override (`Key 'parallelism' is not in struct`). Overrides are bare
`key=value` with no separating `--`, which the trainer consumes as an override
token:

```bash
export WAN_VAE_PATH=<Wan2.2_VAE.pth>
export BASE_CHECKPOINT_PATH=<Cosmos3-Edge DCP dir>   # convert_model_to_dcp
export IMAGINAIRE_OUTPUT_ROOT=<output root>

torchrun --nproc_per_node=<gpus> --standalone \
  -m cosmos_framework.scripts.train \
  --sft-toml <patched.toml> \
  dataloader_train.dataloader.datasets.droid.dataset.root=<dataset root> \
  dataloader_train.dataloader.num_workers=2 \
  dataloader_train.dataloader.datasets.droid.dataset.viewpoint=wrist_view
```

### Seeing that it is actually training

The recipe sets `log_train_loss_to_console: False`, so by default a healthy run
prints nothing per iteration. Two traps when turning it on:

- **Do not put it in the TOML.** `[trainer.callbacks.wandb]` is rejected by the
  config schema: `ValidationError: trainer.callbacks.wandb — Extra inputs are not
  permitted`. Only certain callback tables (e.g. `compile_tokenizer`) are
  allowed there. Pass it as a Hydra CLI override instead —
  `trainer.callbacks.wandb.log_train_loss_to_console=true` — which is accepted.
- **The `iter_speed` callback is rate-limited to the first 50 iterations**
  (`Hit counter: N/50`). After iteration 50 it goes quiet, so a watch grepping
  for later iteration numbers sees nothing and silence looks like a hang. Past
  iteration 50 the only routine progress signal is checkpoint saves.

`wandb_mode="offline"` did not record usable history either, so the console
override is the practical route.
