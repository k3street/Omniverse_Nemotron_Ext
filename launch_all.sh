#!/bin/bash
# ═══════════════════════════════════════════════════════════════════════════════
# Isaac Assist — All-in-One Launcher
# ═══════════════════════════════════════════════════════════════════════════════
#
# Starts everything needed for a full Isaac Assist session:
#   1. rosbridge_server  (ROS2 WebSocket bridge on port 9090)
#   2. Isaac Assist API  (FastAPI on port 8000)
#   3. Isaac Sim         (with Isaac Assist extension loaded)
#
# Usage:
#   ./launch_all.sh                       # Interactive LLM mode selection
#   ./launch_all.sh anthropic             # Claude mode
#   ./launch_all.sh anthropic scene.usd   # Claude + open a USD file
#
# Stop everything:  Ctrl+C in this terminal (sends SIGTERM to all children)
# ═══════════════════════════════════════════════════════════════════════════════

set -e
SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
cd "$SCRIPT_DIR"

# ── Cleanup on exit ──────────────────────────────────────────────────────────
PIDS=()
cleanup() {
    echo ""
    echo "🛑 Shutting down Isaac Assist..."
    for pid in "${PIDS[@]}"; do
        kill "$pid" 2>/dev/null && echo "   Stopped PID $pid"
    done
    wait 2>/dev/null
    echo "   Done."
}
trap cleanup EXIT INT TERM

# ── Load environment ─────────────────────────────────────────────────────────
[ -f .env ]              && { set -a; source .env; set +a; }
[ -f service/isaac_assist_service/.env ] && { set -a; source service/isaac_assist_service/.env; set +a; }

# ── Parse arguments ──────────────────────────────────────────────────────────
LLM_ARG="$1"
USD_FILE="$2"

# ── Determine LLM mode ──────────────────────────────────────────────────────
MODE="${LLM_ARG:-}"

# If no argument given, show a GUI picker (zenity) or fall to env default
if [ -z "$MODE" ]; then
    if command -v zenity &>/dev/null && [ -n "$DISPLAY" ]; then
        MODE=$(zenity --list \
            --title="Isaac Assist — Select LLM Mode" \
            --text="Choose the AI provider for this session:" \
            --column="Mode" --column="Provider" --column="Model" \
            --width=500 --height=320 \
            --window-icon="/home/kimate/Omniverse_Nemotron_Ext/assets/isaac_assist_icon.png" \
            "anthropic" "Claude (Anthropic)" "${CLOUD_MODEL_NAME:-claude-opus-4-7}" \
            "cloud"     "Gemini (Google)"    "${CLOUD_MODEL_NAME:-gemini-robotics-er-1.6-preview}" \
            "openai"    "OpenAI"             "${CLOUD_MODEL_NAME:-gpt-4o}" \
            "grok"      "Grok (xAI)"         "${CLOUD_MODEL_NAME:-grok-3}" \
            "local"     "Ollama (Local GPU)"  "${LOCAL_MODEL_NAME:-qwen3.5:35b}" \
            2>/dev/null) || true
        # User cancelled
        if [ -z "$MODE" ]; then
            echo "No mode selected — cancelled."
            exit 0
        fi
    else
        MODE="${LLM_MODE:-anthropic}"
    fi
fi

case "$MODE" in
    1|local)      MODE="local" ;;
    2|anthropic)  MODE="anthropic" ;;
    3|cloud)      MODE="cloud" ;;
    4|openai)     MODE="openai" ;;
    5|grok)       MODE="grok" ;;
esac

export LLM_MODE="$MODE"

echo ""
echo "╔══════════════════════════════════════════════════════════╗"
echo "║         Isaac Assist — All-in-One Launcher              ║"
echo "╠══════════════════════════════════════════════════════════╣"
echo "║  LLM Mode:  $MODE"
echo "║  ROS2:      jazzy + rosbridge on :9090"
echo "║  API:       http://localhost:8000"
echo "╚══════════════════════════════════════════════════════════╝"
echo ""

# ═══════════════════════════════════════════════════════════════════════════════
# 1. ROS2 rosbridge_server
# ═══════════════════════════════════════════════════════════════════════════════
echo "🌉 Starting rosbridge_server..."

# Source ROS2
if [ -f /opt/ros/jazzy/setup.bash ]; then
    source /opt/ros/jazzy/setup.bash
fi

export ROS_DISTRO=jazzy
export RMW_IMPLEMENTATION=rmw_fastrtps_cpp

# Check if rosbridge is installed
if ros2 pkg list 2>/dev/null | grep -q rosbridge_server; then
    ros2 launch rosbridge_server rosbridge_websocket_launch.xml \
        > /tmp/isaac_assist_rosbridge.log 2>&1 &
    PIDS+=($!)
    echo "   PID: ${PIDS[-1]} (log: /tmp/isaac_assist_rosbridge.log)"

    # Wait briefly for rosbridge to start
    for i in {1..10}; do
        if ss -tlnp 2>/dev/null | grep -q ':9090'; then
            echo "   ✅ rosbridge ready on port 9090"
            break
        fi
        sleep 0.5
    done
else
    echo "   ⚠️  rosbridge_server not installed. ROS2 live tools will be unavailable."
    echo "   Install: sudo apt install ros-jazzy-rosbridge-server"
fi

# ═══════════════════════════════════════════════════════════════════════════════
# 2. Isaac Assist FastAPI Service
# ═══════════════════════════════════════════════════════════════════════════════
echo ""
echo "🤖 Starting Isaac Assist API (mode: $MODE)..."

cd "$SCRIPT_DIR"
uvicorn service.isaac_assist_service.main:app \
    --host 0.0.0.0 \
    --port 8000 \
    --reload \
    > /tmp/isaac_assist_service.log 2>&1 &
PIDS+=($!)
echo "   PID: ${PIDS[-1]} (log: /tmp/isaac_assist_service.log)"

# Wait for API to be ready
for i in {1..20}; do
    if curl -s http://localhost:8000/health > /dev/null 2>&1; then
        echo "   ✅ API ready on port 8000"
        break
    fi
    sleep 0.5
done

# ═══════════════════════════════════════════════════════════════════════════════
# 3. Isaac Sim
# ═══════════════════════════════════════════════════════════════════════════════
echo ""
echo "🚀 Starting Isaac Sim..."

# launch_isaac.sh uses exec (replaces the process), so we call it differently
if [ -n "$USD_FILE" ]; then
    bash "$SCRIPT_DIR/launch_isaac.sh" "$USD_FILE" &
else
    bash "$SCRIPT_DIR/launch_isaac.sh" &
fi
PIDS+=($!)
echo "   PID: ${PIDS[-1]}"

echo ""
echo "════════════════════════════════════════════════════════════"
echo " All services started. Press Ctrl+C to stop everything."
echo ""
echo " Logs:"
echo "   rosbridge:  tail -f /tmp/isaac_assist_rosbridge.log"
echo "   API:        tail -f /tmp/isaac_assist_service.log"
echo "════════════════════════════════════════════════════════════"
echo ""

# Wait for any child to exit
wait -n 2>/dev/null || true
# If one exits, shut everything down
echo "⚠️  A process exited. Shutting down..."
