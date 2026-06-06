#!/bin/bash
# Isaac Sim Launch Script for Isaac Assist Extension
#
# This script launches Isaac Sim with the properly configured ROS2 environment
# and auto-loads the Isaac Assist extension.
#
# Usage:
#   ./launch_isaac.sh                         # Launch empty scene
#   ./launch_isaac.sh /path/to/file.usd       # Launch with a USD file
#   ./launch_isaac.sh --version 5.1           # Force Isaac Sim 5.1
#   ./launch_isaac.sh --version 6.0           # Force Isaac Sim 6.0/source build
#   ./launch_isaac.sh --lab                   # Launch Isaac Lab shell
#   ./launch_isaac.sh --lab script.py [args]  # Run script through Isaac Lab
#
# Environment overrides:
#   ISAAC_SIM_PATH or ISAAC_SIM_ROOT — explicit Isaac Sim installation dir
#   ISAAC_LAB_ROOT or ISAACLAB_PATH  — explicit Isaac Lab repo dir
#   ISAAC_VERSION                    — "5.1" or "6.0"
#   ISAAC_ASSIST_EXT_FOLDER          — explicit extension search folder

set -euo pipefail
SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)

# ── Load env files if present; .env.local wins ────────────────────────────────
if [ -f "$SCRIPT_DIR/.env" ]; then
    set -a
    source "$SCRIPT_DIR/.env"
    set +a
fi
if [ -f "$SCRIPT_DIR/.env.local" ]; then
    set -a
    source "$SCRIPT_DIR/.env.local"
    set +a
fi

# ── Parse flags ───────────────────────────────────────────────────────────────
LAUNCH_LAB=false
REQUESTED_VERSION="${ISAAC_VERSION:-}"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --version)
            if [[ $# -lt 2 ]]; then
                echo "❌ --version requires 5.1 or 6.0"
                exit 1
            fi
            REQUESTED_VERSION="$2"
            shift 2
            ;;
        --lab)
            LAUNCH_LAB=true
            shift
            ;;
        --)
            shift
            break
            ;;
        *)
            break
            ;;
    esac
done

OS="$(uname -s)"
ARCH="$(uname -m)"
case "$OS" in
    Linux) ;;
    *) echo "❌ Unsupported OS: $OS"; exit 1 ;;
esac

# ── Known install candidates, newest first ───────────────────────────────────
declare -a SIM_CANDIDATES_X86_64=(
    "6.0:$HOME/IsaacSim/_build/linux-x86_64/release"
    "6.0:$HOME/isaac-sim/isaac-sim-standalone-6.0.0-linux-x86_64"
    "6.0:$HOME/.local/share/ov/pkg/isaac-sim-6.0.0"
    "5.1:$HOME/isaac-sim/isaac-sim-standalone-5.1.0-linux-x86_64"
    "5.1:$HOME/.local/share/ov/pkg/isaac-sim-5.1.0"
)
declare -a SIM_CANDIDATES_AARCH64=(
    "6.0:$HOME/IsaacSim/_build/linux-aarch64/release"
    "5.1:$HOME/Documents/Github/isaacsim/_build/linux-aarch64/release"
    "5.1:$HOME/.local/share/ov/pkg/isaac-sim-5.1.0"
)

resolve_sim_path() {
    local explicit="${ISAAC_SIM_ROOT:-${ISAAC_SIM_PATH:-}}"
    if [[ -n "$explicit" && -d "$explicit" ]]; then
        echo "$explicit"
        return
    fi

    local candidates=()
    if [[ "$ARCH" == "aarch64" ]]; then
        candidates=("${SIM_CANDIDATES_AARCH64[@]}")
    else
        candidates=("${SIM_CANDIDATES_X86_64[@]}")
    fi

    local entry ver path
    for entry in "${candidates[@]}"; do
        ver="${entry%%:*}"
        path="${entry#*:}"
        if [[ -n "$REQUESTED_VERSION" && "$ver" != "$REQUESTED_VERSION" ]]; then
            continue
        fi
        if [[ -d "$path" && -f "$path/isaac-sim.sh" ]]; then
            echo "$path"
            return
        fi
    done
}

resolve_lab_path() {
    local explicit="${ISAAC_LAB_ROOT:-${ISAACLAB_PATH:-}}"
    if [[ -n "$explicit" && -d "$explicit" && -f "$explicit/isaaclab.sh" ]]; then
        echo "$explicit"
        return
    fi

    local candidates=(
        "$HOME/IsaacLab"
        "$HOME/Documents/Github/IsaacLab"
        "$HOME/open_arm_10Things/IsaacLab"
    )
    local path
    for path in "${candidates[@]}"; do
        if [[ -d "$path" && -f "$path/isaaclab.sh" ]]; then
            echo "$path"
            return
        fi
    done
}

if [[ "$LAUNCH_LAB" == true ]]; then
    LAB_PATH="$(resolve_lab_path)"
    if [[ -z "$LAB_PATH" ]]; then
        echo "❌ Isaac Lab not found. Set ISAAC_LAB_ROOT or ISAACLAB_PATH to a repo containing isaaclab.sh."
        exit 1
    fi
    echo "🧪 Isaac Lab: $LAB_PATH"
    if [[ $# -eq 0 ]]; then
        echo "🚀 Launching Isaac Lab interactive shell..."
        exec "$LAB_PATH/isaaclab.sh" -p
    fi
    echo "🚀 Running via Isaac Lab: $*"
    exec "$LAB_PATH/isaaclab.sh" -p "$@"
fi

# ── Resolve Isaac Sim path ────────────────────────────────────────────────────
ISAAC_SIM_PATH="$(resolve_sim_path)"

if [[ -z "$ISAAC_SIM_PATH" || ! -f "$ISAAC_SIM_PATH/isaac-sim.sh" ]]; then
    echo "❌ Isaac Sim not found."
    if [[ -n "$REQUESTED_VERSION" ]]; then
        echo "   Requested version: $REQUESTED_VERSION"
    fi
    echo "   Set ISAAC_SIM_PATH or ISAAC_SIM_ROOT, or install to one of the known 5.1/6.0 paths."
    exit 1
fi

VERSION_LABEL="unknown"
[[ "$ISAAC_SIM_PATH" == *"5.1"* ]] && VERSION_LABEL="5.1"
[[ "$ISAAC_SIM_PATH" == *"6.0"* || "$ISAAC_SIM_PATH" == *"/IsaacSim/"* ]] && VERSION_LABEL="6.0"
if [[ -n "$REQUESTED_VERSION" ]]; then
    VERSION_LABEL="$REQUESTED_VERSION"
fi

# ── ROS2 environment ─────────────────────────────────────────────────────────
export ROS_DISTRO=jazzy
export RMW_IMPLEMENTATION=rmw_fastrtps_cpp

BRIDGE_BASE="${ISAAC_SIM_PATH}/exts/isaacsim.ros2.bridge/jazzy"
if [[ -d "$BRIDGE_BASE/lib" ]]; then
    export LD_LIBRARY_PATH="${BRIDGE_BASE}/lib${LD_LIBRARY_PATH:+:${LD_LIBRARY_PATH}}"
fi
TORCH_LIB="${ISAAC_SIM_PATH}/kit/python/lib/python3.11/site-packages/torch/lib"
if [[ -d "$TORCH_LIB" ]]; then
    export LD_LIBRARY_PATH="${LD_LIBRARY_PATH:+${LD_LIBRARY_PATH}:}${TORCH_LIB}"
fi
if [[ -d "$BRIDGE_BASE/rclpy" ]]; then
    export PYTHONPATH="${BRIDGE_BASE}/rclpy${PYTHONPATH:+:${PYTHONPATH}}"
fi

echo "🔧 Isaac Sim $VERSION_LABEL: $ISAAC_SIM_PATH"
echo "🚀 Launching Isaac Sim with Isaac Assist Extension"
echo "   ROS_DISTRO:          $ROS_DISTRO"
echo "   RMW_IMPLEMENTATION:  $RMW_IMPLEMENTATION"
echo ""

# ── Determine extension folder ────────────────────────────────────────────────
if [[ -n "${ISAAC_ASSIST_EXT_FOLDER:-}" ]]; then
    EXT_FOLDER="$ISAAC_ASSIST_EXT_FOLDER"
elif [[ "$VERSION_LABEL" == "6.0" ]]; then
    EXT_FOLDER="$SCRIPT_DIR/exts/isaac_6.0"
else
    EXT_FOLDER="$SCRIPT_DIR/exts/isaac_5.1"
fi

if [[ ! -d "$EXT_FOLDER" ]]; then
    echo "❌ Isaac Assist extension folder not found: $EXT_FOLDER"
    exit 1
fi
echo "🧩 Isaac Assist extension folder: $EXT_FOLDER"

# ── Launch ────────────────────────────────────────────────────────────────────
USD_FILE="${1:-}"

if [ -n "$USD_FILE" ] && [ -f "$USD_FILE" ]; then
    USD_FILE=$(realpath "$USD_FILE")
    echo "📂 Opening USD file: $USD_FILE"
    echo ""

    STARTUP_SCRIPT="/tmp/isaac_assist_launcher/open_stage_startup.py"
    mkdir -p /tmp/isaac_assist_launcher
    cat > "$STARTUP_SCRIPT" << PYEOF
import asyncio
import omni.usd

async def _wait_for_viewport(max_frames=1800):
    try:
        import omni.kit.app
        app = omni.kit.app.get_app()
        for frame in range(max_frames):
            try:
                from omni.kit.viewport.utility import get_active_viewport
                if get_active_viewport() is not None:
                    print(f"[Isaac Assist] viewport ready after {frame} frames")
                    return True
            except Exception:
                pass
            await app.next_update_async()
    except Exception as exc:
        print(f"[Isaac Assist] viewport wait warning: {exc}")
    return False

async def _open_stage_and_setup():
    import omni.usd as _usd
    print("[Isaac Assist] waiting for viewport before opening stage")
    await _wait_for_viewport()
    print(r"[Isaac Assist] opening stage: ${USD_FILE}")
    await _usd.get_context().open_stage_async(r'${USD_FILE}')
    for _ in range(600):
        await asyncio.sleep(0.1)
        ctx = _usd.get_context()
        if ctx and ctx.get_stage() and ctx.get_stage_state() == _usd.StageState.OPENED:
            break
    print("[Isaac Assist] stage state:", _usd.get_context().get_stage_state())
PYEOF

    if [ -n "${SCENE_SETUP_SCRIPT:-}" ] && [ -f "$SCENE_SETUP_SCRIPT" ]; then
        echo "🔧 Post-load setup: $SCENE_SETUP_SCRIPT"
        cat >> "$STARTUP_SCRIPT" << PYEOF2
    try:
        import omni.kit.app
        _mgr = omni.kit.app.get_app().get_extension_manager()
        _ext_list = ("isaacsim.core.nodes", "isaacsim.ros2.bridge", "omni.graph.action")
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
PYEOF2
    fi
    cat >> "$STARTUP_SCRIPT" << PYEOF3

asyncio.ensure_future(_open_stage_and_setup())
PYEOF3

    shift
    exec "$ISAAC_SIM_PATH/isaac-sim.sh" \
        --/app/window/dpiScaleOverride=1.0 \
        --/app/window/scaleToMonitor=false \
        --/app/file/ignoreUnsavedOnExit=true \
        --/app/content/emptyStageOnStart=false \
        --ext-folder "$EXT_FOLDER" \
        --enable omni.isaac.assist \
        --exec "$STARTUP_SCRIPT" \
        "$@"
else
    echo "Starting Isaac Sim (empty scene)..."
    exec "$ISAAC_SIM_PATH/isaac-sim.sh" \
        --/app/window/dpiScaleOverride=1.0 \
        --/app/window/scaleToMonitor=false \
        --/app/file/ignoreUnsavedOnExit=true \
        --ext-folder "$EXT_FOLDER" \
        --enable omni.isaac.assist \
        "$@"
fi
