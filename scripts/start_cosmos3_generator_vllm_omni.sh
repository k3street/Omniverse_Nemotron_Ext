#!/usr/bin/env bash
set -euo pipefail

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  cat <<'EOF'
Start a Cosmos 3 Generator endpoint with vLLM-Omni.

This serves:
  POST /v1/images/generations
  POST /v1/videos/sync

Required before first use:
  uvx hf@latest auth login

Optional:
  COSMOS_GENERATOR_NAME          Container name. Default: nvidia-cosmos3-generator
  COSMOS_GENERATOR_IMAGE         Docker image. Default: vllm/vllm-omni:cosmos3
  COSMOS_GENERATOR_PORT          Host port. Default: 8082
  COSMOS_GENERATOR_MODEL         HF model id. Default: nvidia/Cosmos3-Nano
  COSMOS_GENERATOR_HF_CACHE      HF cache mount. Default: $HOME/.cache/huggingface
  COSMOS_GENERATOR_WORKSPACE     Local media/action mount. Default: $PWD
  COSMOS_GENERATOR_ALLOWED_PATH  Container-visible media path. Default: /workspace
  COSMOS_GENERATOR_EXTRA_ARGS    Extra arguments appended to `vllm serve`.

Example:
  COSMOS_GENERATOR_PORT=8082 \
  COSMOS_GENERATOR_MODEL=nvidia/Cosmos3-Nano \
  ./scripts/start_cosmos3_generator_vllm_omni.sh

Then configure Isaac Assist:
  COSMOS3_GENERATOR_BASE_URL=http://127.0.0.1:8082/v1
  COSMOS3_GENERATOR_MODEL=nvidia/Cosmos3-Nano
EOF
  exit 0
fi

container_name="${COSMOS_GENERATOR_NAME:-nvidia-cosmos3-generator}"
image="${COSMOS_GENERATOR_IMAGE:-vllm/vllm-omni:cosmos3}"
host_port="${COSMOS_GENERATOR_PORT:-8082}"
model="${COSMOS_GENERATOR_MODEL:-nvidia/Cosmos3-Nano}"
hf_cache="${COSMOS_GENERATOR_HF_CACHE:-$HOME/.cache/huggingface}"
workspace="${COSMOS_GENERATOR_WORKSPACE:-$PWD}"
allowed_path="${COSMOS_GENERATOR_ALLOWED_PATH:-/workspace}"
extra_args="${COSMOS_GENERATOR_EXTRA_ARGS:-}"

mkdir -p "$hf_cache" "$workspace"

docker rm -f "$container_name" >/dev/null 2>&1 || true

# shellcheck disable=SC2086
docker run --detach \
  --name "$container_name" \
  --restart unless-stopped \
  --runtime=nvidia \
  --gpus all \
  --ipc=host \
  --volume "$hf_cache:/root/.cache/huggingface" \
  --volume "$workspace:/workspace" \
  --publish "$host_port:8000" \
  "$image" \
  vllm serve "$model" \
    --omni \
    --model-class-name Cosmos3OmniDiffusersPipeline \
    --allowed-local-media-path "$allowed_path" \
    --port 8000 \
    --init-timeout 1800 \
    $extra_args

cat <<EOF
Cosmos 3 Generator is starting.

Endpoint:
  http://127.0.0.1:$host_port/v1

Configure Isaac Assist:
  COSMOS3_GENERATOR_BASE_URL=http://127.0.0.1:$host_port/v1
  COSMOS3_GENERATOR_MODEL=$model

Readiness:
  curl http://127.0.0.1:$host_port/v1/models

Logs:
  docker logs -f $container_name
EOF
