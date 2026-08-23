#!/bin/bash
# Isaac Assist — FastAPI Service Launcher
#
# Quick way to start the service with a specific LLM provider.
#
# Usage:
#   ./launch_service.sh                  # Interactive menu
#   ./launch_service.sh local            # Ollama (local)
#   ./launch_service.sh anthropic        # Claude
#   ./launch_service.sh google           # Gemini
#   ./launch_service.sh openai           # OpenAI
#   ./launch_service.sh grok             # xAI Grok

set -e
SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
cd "$SCRIPT_DIR"

# ── Load environment ─────────────────────────────────────────────────────────
[ -f .env ]              && { set -a; source .env; set +a; }
[ -f service/isaac_assist_service/.env ] && { set -a; source service/isaac_assist_service/.env; set +a; }
[ -f .env.local ]        && { set -a; source .env.local; set +a; }

# ── Determine mode ────────────────────────────────────────────────────────────
MODE="$1"

if [ -z "$MODE" ]; then
    echo ""
    echo "╔══════════════════════════════════════╗"
    echo "║    Isaac Assist — Select LLM Mode    ║"
    echo "╠══════════════════════════════════════╣"
    echo "║  1) local      — Ollama (local GPU)  ║"
    echo "║  2) anthropic  — Claude              ║"
    echo "║  3) google     — Gemini              ║"
    echo "║  4) openai     — OpenAI              ║"
    echo "║  5) grok       — xAI Grok            ║"
    echo "╚══════════════════════════════════════╝"
    echo ""
    read -rp "Enter choice [1-5] or mode name (default: ${LLM_MODE:-local}): " CHOICE

    case "$CHOICE" in
        1|local)      MODE="local" ;;
        2|anthropic)  MODE="anthropic" ;;
        3|google)     MODE="google" ;;
        4|openai)     MODE="openai" ;;
        5|grok)       MODE="grok" ;;
        "")           MODE="${LLM_MODE:-local}" ;;
        *)            MODE="$CHOICE" ;;
    esac
fi

# ── Validate ──────────────────────────────────────────────────────────────────
case "$MODE" in
    local|google|anthropic|openai|grok) ;;
    *)
        echo "Error: Invalid mode '$MODE'. Choose: local, google, anthropic, openai, grok"
        exit 1
        ;;
esac

# ── Resolve model name for display ────────────────────────────────────────────
if [ "$MODE" = "local" ]; then
    MODEL="${LOCAL_MODEL_NAME:-qwen3.5:35b}"
elif [ "$MODE" = "google" ]; then
    MODEL="${GEMINI_MODEL_NAME:-gemini-3.1-pro-preview}"
else
    MODEL="${CLOUD_MODEL_NAME:-claude-sonnet-4-6}"
fi

export LLM_MODE="$MODE"
SERVICE_HOST="${ISAAC_ASSIST_HOST:-127.0.0.1}"
SERVICE_PORT="${ISAAC_ASSIST_PORT:-8000}"

RUNNER=()
RUNNER_LABEL=""

if [ -n "${UVICORN_BIN:-}" ]; then
    RUNNER=("$UVICORN_BIN")
    RUNNER_LABEL="$UVICORN_BIN"
elif [ -n "${SERVICE_PYTHON:-}" ]; then
    RUNNER=("$SERVICE_PYTHON" -m uvicorn)
    RUNNER_LABEL="$SERVICE_PYTHON -m uvicorn"
elif [ -x "$SCRIPT_DIR/.venv/bin/python" ]; then
    RUNNER=("$SCRIPT_DIR/.venv/bin/python" -m uvicorn)
    RUNNER_LABEL="${RUNNER[*]}"
elif python3 -c 'import aiohttp, fastapi, uvicorn' >/dev/null 2>&1; then
    RUNNER=(python3 -m uvicorn)
    RUNNER_LABEL="${RUNNER[*]}"
else
    # Isaac Sim ships a Python environment with the HTTP stack needed by both
    # the extension and sidecar. Reuse it when the host Python is incomplete.
    ISAAC_PYTHON_CANDIDATES=()
    for root in "${ISAAC_SIM_ROOT:-}" "${ISAAC_SIM_PATH:-}" "${ISAACSIM_PATH:-}"; do
        [ -n "$root" ] && ISAAC_PYTHON_CANDIDATES+=("$root/python.sh")
    done
    for candidate in "$HOME"/Documents/Github/isaacsim/_build/*/release/python.sh \
                     "$HOME"/isaac-sim/*/python.sh \
                     "$HOME"/.local/share/ov/pkg/isaac-sim-*/python.sh; do
        [ -x "$candidate" ] && ISAAC_PYTHON_CANDIDATES+=("$candidate")
    done

    for candidate in "${ISAAC_PYTHON_CANDIDATES[@]}"; do
        if "$candidate" -c 'import aiohttp, fastapi, uvicorn' >/dev/null 2>&1; then
            RUNNER=("$candidate" -m uvicorn)
            RUNNER_LABEL="${RUNNER[*]}"
            export ISAAC_ASSIST_ISAAC_PYTHON=1
            break
        fi
    done
fi

if [ "${#RUNNER[@]}" -eq 0 ]; then
    echo "Error: no Python environment contains aiohttp, FastAPI, and Uvicorn."
    echo "Create .venv with 'python3 -m pip install -e .' or set SERVICE_PYTHON."
    exit 1
fi

echo ""
echo "Starting Isaac Assist service..."
echo "  Mode:  $MODE"
echo "  Model: $MODEL"
echo "  URL:   http://$SERVICE_HOST:$SERVICE_PORT"
echo "  Runner: $RUNNER_LABEL"
echo ""

RELOAD_ARGS=()
if [ "${ISAAC_ASSIST_RELOAD:-0}" = "1" ]; then
    RELOAD_ARGS=(--reload)
fi

exec "${RUNNER[@]}" service.isaac_assist_service.main:app \
    --host "$SERVICE_HOST" \
    --port "$SERVICE_PORT" \
    "${RELOAD_ARGS[@]}"
