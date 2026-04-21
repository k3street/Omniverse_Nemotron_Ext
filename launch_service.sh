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

echo ""
echo "Starting Isaac Assist service..."
echo "  Mode:  $MODE"
echo "  Model: $MODEL"
echo "  Port:  8000"
echo ""

exec uvicorn service.isaac_assist_service.main:app \
    --host 0.0.0.0 \
    --port 8000 \
    --reload
