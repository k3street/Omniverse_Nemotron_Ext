#!/bin/bash
# Judge stack for autonomous visual approval (scripts/visual_qa.py):
#   - Cosmos-Reason2-2B via vLLM on :8021  (physical plausibility)
#   - gemma4 via Ollama on :11434          (identity / integrity)
# Idempotent — re-running starts only what is missing. The review hub
# calls this automatically when VISUAL_QA_AUTO=1.
set -uo pipefail

VLLM_VENV=${VISUAL_QA_VLLM_VENV:-/home/kimate/cosmos-vllm/.venv}
PORT=${VISUAL_QA_COSMOS_PORT:-8021}
MODEL=${VISUAL_QA_COSMOS_MODEL:-nvidia/Cosmos-Reason2-2B}
LOG_DIR=${XDG_STATE_HOME:-$HOME/.local/state}/visual_qa
mkdir -p "$LOG_DIR"

if curl -s -m 2 "http://127.0.0.1:$PORT/v1/models" | grep -q "$MODEL"; then
    echo "✅ cosmos judge already serving $MODEL on :$PORT"
else
    # ninja must be on the ENGINE SUBPROCESS PATH — invoking the venv's
    # vllm by absolute path alone leaves .venv/bin off PATH and the engine
    # dies with FileNotFoundError('ninja') AFTER loading the weights.
    PATH="$VLLM_VENV/bin:$PATH" nohup "$VLLM_VENV/bin/vllm" serve "$MODEL" \
        --port "$PORT" --max-model-len 8192 --gpu-memory-utilization 0.30 \
        > "$LOG_DIR/vllm_cosmos.log" 2>&1 &
    echo "🚀 cosmos judge starting on :$PORT (pid $!, ~1 min to ready;" \
         "log $LOG_DIR/vllm_cosmos.log)"
fi

if ! curl -s -m 2 http://127.0.0.1:11434/api/tags > /dev/null; then
    nohup ollama serve > "$LOG_DIR/ollama.log" 2>&1 &
    echo "🚀 ollama starting (pid $!)"
    sleep 2
fi
if ollama list 2>/dev/null | grep -q "^gemma4"; then
    echo "✅ gemma judge ready ($(ollama list | grep '^gemma4' | awk '{print $3, $4}'))"
else
    echo "⬇️  pulling gemma4 (~9.6 GB, one-time)"
    ollama pull gemma4
fi
