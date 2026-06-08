#!/usr/bin/env bash
set -euo pipefail

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  cat <<'EOF'
Start NVIDIA Cosmos 3 Reasoner NIM as an OpenAI-compatible endpoint.

Required:
  NGC_API_KEY              NVIDIA/NGC API key with access to the NIM image/model.

Optional:
  COSMOS_NIM_NAME          Container name. Default: nvidia-cosmos3-reasoner
  COSMOS_NIM_IMAGE         Container image. Default: nvcr.io/nim/nvidia/cosmos3-reasoner:1.7.0
  COSMOS_NIM_PORT          Host port to expose. Default: 8081
  COSMOS_NIM_CACHE         Host cache directory. Default: $PWD/.nim-cache
  NIM_MODEL_SIZE           Cosmos model size. Default: nano
  NIM_MAX_MODEL_LEN        vLLM context length. Default: 32768

Examples:
  export NGC_API_KEY=nvapi-...
  ./scripts/start_cosmos3_reasoner_nim.sh

  COSMOS_NIM_CACHE=$HOME/nim-cache/cosmos3-reasoner \
  COSMOS_NIM_PORT=8081 \
  NIM_MAX_MODEL_LEN=32768 \
  ./scripts/start_cosmos3_reasoner_nim.sh
EOF
  exit 0
fi

if [[ -z "${NGC_API_KEY:-}" ]]; then
  echo "NGC_API_KEY is required. Export it before starting Cosmos NIM." >&2
  exit 2
fi

container_name="${COSMOS_NIM_NAME:-nvidia-cosmos3-reasoner}"
image="${COSMOS_NIM_IMAGE:-nvcr.io/nim/nvidia/cosmos3-reasoner:1.7.0}"
host_port="${COSMOS_NIM_PORT:-8081}"
cache_dir="${COSMOS_NIM_CACHE:-$PWD/.nim-cache}"
model_size="${NIM_MODEL_SIZE:-nano}"
max_model_len="${NIM_MAX_MODEL_LEN:-32768}"

mkdir -p "$cache_dir"
chmod 777 "$cache_dir" 2>/dev/null || true

docker rm -f "$container_name" >/dev/null 2>&1 || true

docker run --detach \
  --name "$container_name" \
  --restart unless-stopped \
  --runtime=nvidia \
  --gpus all \
  --shm-size=32GB \
  --env NGC_API_KEY="$NGC_API_KEY" \
  --env NIM_MODEL_SIZE="$model_size" \
  --env NIM_MAX_MODEL_LEN="$max_model_len" \
  --volume "$cache_dir:/opt/nim/.cache" \
  --publish "$host_port:8000" \
  "$image"

cat <<EOF
Cosmos 3 Reasoner NIM is starting.

Endpoint:
  http://127.0.0.1:$host_port/v1

Readiness:
  curl http://127.0.0.1:$host_port/v1/health/ready
  curl http://127.0.0.1:$host_port/v1/models

Logs:
  docker logs -f $container_name
EOF
