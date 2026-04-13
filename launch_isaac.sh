#!/bin/bash
# Isaac Sim Launch Script for Isaac Assist Extension
#
# This script launches Isaac Sim with the properly configured ROS2 environment
# and auto-loads the Isaac Assist extension.
#
# Prerequisites:
#   1. Set ISAAC_SIM_PATH in your .env (or export it) to your Isaac Sim install dir
#   2. Start the FastAPI service first (see README.md)
#
# Usage:
#   ./launch_isaac.sh                    # Launch empty scene
#   ./launch_isaac.sh /path/to/file.usd  # Launch with a USD file

set -e
SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)

# ── Load .env if present ──────────────────────────────────────────────────────
if [ -f "$SCRIPT_DIR/.env" ]; then
    set -a
    source "$SCRIPT_DIR/.env"
    set +a
fi

# ── Resolve ISAAC_SIM_PATH ────────────────────────────────────────────────────
if [ -n "$ISAAC_SIM_PATH" ] && [ -d "$ISAAC_SIM_PATH" ]; then
    echo "🔧 Using ISAAC_SIM_PATH: $ISAAC_SIM_PATH"
else
    ARCH=$(uname -m)
    if [ "$ARCH" = "aarch64" ]; then
        ISAAC_SIM_PATH="$HOME/Documents/Github/isaacsim/_build/linux-aarch64/release"
        echo "🔧 Detected ARM64 architecture — guessing path: $ISAAC_SIM_PATH"
    else
        ISAAC_SIM_PATH="$HOME/isaac-sim/isaac-sim-standalone-5.1.0-linux-x86_64"
        echo "🔧 Detected x86_64 architecture — guessing path: $ISAAC_SIM_PATH"
    fi
    echo "   (set ISAAC_SIM_PATH in .env to override)"
fi

if [ ! -f "$ISAAC_SIM_PATH/isaac-sim.sh" ]; then
    echo "❌ Error: isaac-sim.sh not found at $ISAAC_SIM_PATH"
    echo "   Please set ISAAC_SIM_PATH in your .env file."
    echo "   Example: ISAAC_SIM_PATH=/home/you/isaac-sim"
    exit 1
fi

# ── ROS2 environment ─────────────────────────────────────────────────────────
export ROS_DISTRO=jazzy
export RMW_IMPLEMENTATION=rmw_fastrtps_cpp
export LD_LIBRARY_PATH="${ISAAC_SIM_PATH}/exts/isaacsim.ros2.bridge/jazzy/lib"
export LD_LIBRARY_PATH="${LD_LIBRARY_PATH}:${ISAAC_SIM_PATH}/kit/python/lib/python3.11/site-packages/torch/lib"
export PYTHONPATH="${ISAAC_SIM_PATH}/exts/isaacsim.ros2.bridge/jazzy/rclpy"

echo "🚀 Launching Isaac Sim with Isaac Assist Extension"
echo "   ROS_DISTRO:          $ROS_DISTRO"
echo "   RMW_IMPLEMENTATION:  $RMW_IMPLEMENTATION"
echo ""

# ── Determine extension folder ────────────────────────────────────────────────
EXT_FOLDER="${ISAAC_ASSIST_EXT_FOLDER:-$SCRIPT_DIR/exts/isaac_5.1}"

# ── Launch ────────────────────────────────────────────────────────────────────
USD_FILE="$1"

if [ -n "$USD_FILE" ] && [ -f "$USD_FILE" ]; then
    USD_FILE=$(realpath "$USD_FILE")
    echo "📂 Opening USD file: $USD_FILE"
    echo ""

    STARTUP_SCRIPT="/tmp/isaac_assist_launcher/open_stage_startup.py"
    mkdir -p /tmp/isaac_assist_launcher
    cat > "$STARTUP_SCRIPT" << PYEOF
import asyncio
import omni.usd
asyncio.ensure_future(omni.usd.get_context().open_stage_async(r'${USD_FILE}'))
PYEOF

    if [ -n "$SCENE_SETUP_SCRIPT" ] && [ -f "$SCENE_SETUP_SCRIPT" ]; then
        echo "🔧 Post-load setup: $SCENE_SETUP_SCRIPT"
        cat >> "$STARTUP_SCRIPT" << PYEOF2

async def _run_post_load_setup():
    import omni.usd as _usd
    for _ in range(600):
        await asyncio.sleep(0.1)
        ctx = _usd.get_context()
        if ctx and ctx.get_stage() and ctx.get_stage_state() == _usd.StageState.OPENED:
            break
    try:
        import omni.kit.app
        _mgr = omni.kit.app.get_app().get_extension_manager()
        _ext_list = ("omni.isaac.core_nodes", "omni.isaac.ros2_bridge", "omni.graph.action")
        for _ in range(120):
            if all(_mgr.is_extension_enabled(e) for e in _ext_list):
                break
            await asyncio.sleep(0.25)
        for _ext in _ext_list:
            if not _mgr.is_extension_enabled(_ext):
                _mgr.set_extension_enabled_immediate(_ext, True)
    except Exception:
        pass
    await asyncio.sleep(2.0)
    try:
        exec(open(r'${SCENE_SETUP_SCRIPT}').read(), {"__name__": "__main__"})
    except Exception as e:
        print(f"⚠️  Post-load setup error: {e}")
asyncio.ensure_future(_run_post_load_setup())
PYEOF2
    fi

    shift
    exec "$ISAAC_SIM_PATH/isaac-sim.sh" \
        --/app/window/dpiScaleOverride=1.0 \
        --/app/window/scaleToMonitor=false \
        --/app/file/ignoreUnsavedOnExit=true \
        --/app/content/emptyStageOnStart=false \
        --ext-folder "$EXT_FOLDER" \
        --exec "$STARTUP_SCRIPT" \
        "$@"
else
    echo "Starting Isaac Sim (empty scene)..."
    exec "$ISAAC_SIM_PATH/isaac-sim.sh" \
        --/app/window/dpiScaleOverride=1.0 \
        --/app/window/scaleToMonitor=false \
        --/app/file/ignoreUnsavedOnExit=true \
        --ext-folder "$EXT_FOLDER" \
        "$@"
fi
