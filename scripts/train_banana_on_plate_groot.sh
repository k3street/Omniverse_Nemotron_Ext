#!/usr/bin/env bash
set -euo pipefail

GROOT_ROOT="${GROOT_ROOT:-/home/kimate/Documents/Github/Isaac-GR00T}"
DATASET_PATH="${DATASET_PATH:-/home/kimate/Omniverse_Nemotron_Ext/artifacts/groot_datasets/banana_on_plate}"
OUTPUT_DIR="${OUTPUT_DIR:-/home/kimate/Omniverse_Nemotron_Ext/artifacts/checkpoints/banana_on_plate}"

export PATH="$GROOT_ROOT/.venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
export NUM_GPUS="${NUM_GPUS:-1}"
export MAX_STEPS="${MAX_STEPS:-1000}"
export SAVE_STEPS="${SAVE_STEPS:-250}"
export USE_WANDB="${USE_WANDB:-0}"
export DATALOADER_NUM_WORKERS="${DATALOADER_NUM_WORKERS:-0}"
export GLOBAL_BATCH_SIZE="${GLOBAL_BATCH_SIZE:-1}"
export SHARD_SIZE="${SHARD_SIZE:-1024}"
export NUM_SHARDS_PER_EPOCH="${NUM_SHARDS_PER_EPOCH:-1}"
export EPISODE_SAMPLING_RATE="${EPISODE_SAMPLING_RATE:-1}"

cd "$GROOT_ROOT"
exec bash examples/finetune.sh \
  --base-model-path nvidia/GR00T-N1.7-3B \
  --dataset-path "$DATASET_PATH" \
  --embodiment-tag OXE_DROID_RELATIVE_EEF_RELATIVE_JOINT \
  --output-dir "$OUTPUT_DIR" \
  --save-only-model
