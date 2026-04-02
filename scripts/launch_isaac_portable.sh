#!/bin/bash
# Portable Isaac Sim + Nemotron Extension Launcher
# This script automatically mounts the local `exts/omni.isaac.assist` extension.

set -e

# 1. Determine Isaac Sim Path
if [ -z "$ISAAC_SIM_PATH" ]; then
    echo "🔎 ISAAC_SIM_PATH not set, scanning common install directories..."
    
    # Common Omniverse Launcher default path
    DEFAULT_DIR="$HOME/.local/share/ov/pkg"
    
    if [ -d "$DEFAULT_DIR" ]; then
        # Find the latest isaac-sim package
        LATEST_ISAAC=$(ls -1td "$DEFAULT_DIR"/isaac-sim-* 2>/dev/null | head -n 1)
        if [ -n "$LATEST_ISAAC" ]; then
            export ISAAC_SIM_PATH="$LATEST_ISAAC"
            echo "✅ Found Isaac Sim at: $ISAAC_SIM_PATH"
        fi
    fi
fi

if [ -z "$ISAAC_SIM_PATH" ] || [ ! -f "$ISAAC_SIM_PATH/isaac-sim.sh" ]; then
    echo "❌ Error: Could not locate a valid Isaac Sim installation."
    echo "Please set the ISAAC_SIM_PATH environment variable."
    echo "Example: export ISAAC_SIM_PATH=/home/user/isaac-sim"
    exit 1
fi

# 2. Extract version and set the correct extension directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Fast heuristic: if "6.0" is in the path, use the 6.0 extension bucket
if [[ "$ISAAC_SIM_PATH" == *"6.0"* ]]; then
    EXT_ROOT="$SCRIPT_DIR/../exts/isaac_6.0"
    echo "🔄 Detected Isaac Sim 6.0! Auto-selecting 6.0 extensions."
else
    EXT_ROOT="$SCRIPT_DIR/../exts/isaac_5.1"
    echo "🔄 Detected Isaac Sim 5.1 (or default)! Auto-selecting 5.1 extensions."
fi

# Resolve absolute path
EXT_PATH=$(realpath "$EXT_ROOT")

# Ensure path resolution works
if [ ! -d "$EXT_PATH/omni.isaac.assist" ]; then
    echo "❌ Error: Extension directory not found at $EXT_PATH/omni.isaac.assist"
    exit 1
fi

echo "🚀 Launching Isaac Sim with portable Nemotron Extension..."
echo "Extension Path: $EXT_PATH"

# 3. Apply optional ROS2 jazzy bridge fixes (portability wrapper pattern)
export ROS_DISTRO=jazzy
export RMW_IMPLEMENTATION=rmw_fastrtps_cpp

# 4. Launch Isaac Sim forcing our extension to be mounted and enabled
exec "$ISAAC_SIM_PATH/isaac-sim.sh" \
    --ext-folder "$EXT_PATH" \
    --enable "omni.isaac.assist" \
    --/app/window/dpiScaleOverride=1.0 \
    --/app/window/scaleToMonitor=false \
    "$@"
