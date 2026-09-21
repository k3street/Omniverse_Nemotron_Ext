#!/usr/bin/env bash
# Run the passive sequence critic using the already-cached Cosmos model.
# If no shared vLLM endpoint exists, this starts an ephemeral one and stops it
# after the critique so the GPU is free for the next Isaac Sim episode.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VLLM_VENV="${VISUAL_QA_VLLM_VENV:-/home/kimate/cosmos-vllm/.venv}"
PORT="${VISUAL_QA_COSMOS_PORT:-8021}"
MODEL="${VISUAL_QA_COSMOS_MODEL:-nvidia/Cosmos-Reason2-2B}"
ENDPOINT="http://127.0.0.1:$PORT/v1"
LOG_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/visual_qa"
ARTIFACT_DIR="${1:-$ROOT/artifacts/gemini_robotics_er2_robolab}"
STARTED_PID=""

mkdir -p "$LOG_DIR"

cleanup() {
    if [[ -n "$STARTED_PID" ]] && kill -0 "$STARTED_PID" 2>/dev/null; then
        kill "$STARTED_PID" 2>/dev/null || true
        wait "$STARTED_PID" 2>/dev/null || true
        echo "[passive-critic] stopped ephemeral Cosmos server pid=$STARTED_PID"
    fi
}
trap cleanup EXIT INT TERM

if ! curl -fsS --max-time 2 "$ENDPOINT/models" >/dev/null 2>&1; then
    if [[ ! -x "$VLLM_VENV/bin/vllm" ]]; then
        echo "vLLM environment not found: $VLLM_VENV" >&2
        exit 1
    fi
    HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 PATH="$VLLM_VENV/bin:$PATH" \
        "$VLLM_VENV/bin/vllm" serve "$MODEL" \
        --port "$PORT" --max-model-len 8192 --gpu-memory-utilization 0.30 \
        >"$LOG_DIR/vllm_robot_sequence_critic.log" 2>&1 &
    STARTED_PID=$!
    echo "[passive-critic] starting cached $MODEL on :$PORT (pid=$STARTED_PID)"
    for _ in $(seq 1 120); do
        if curl -fsS --max-time 2 "$ENDPOINT/models" >/dev/null 2>&1; then
            break
        fi
        if ! kill -0 "$STARTED_PID" 2>/dev/null; then
            echo "Cosmos server exited during startup; see $LOG_DIR/vllm_robot_sequence_critic.log" >&2
            exit 1
        fi
        sleep 2
    done
fi

if ! curl -fsS --max-time 2 "$ENDPOINT/models" >/dev/null 2>&1; then
    echo "Cosmos server did not become ready on :$PORT" >&2
    exit 1
fi

python3 "$ROOT/scripts/critique_robot_sequence.py" \
    --artifact-dir "$ARTIFACT_DIR" --endpoint "$ENDPOINT" --model "$MODEL"
