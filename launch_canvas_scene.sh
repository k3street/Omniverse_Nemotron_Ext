#!/bin/bash
# Launch one Isaac Assist canvas scene in Isaac Sim with the extension loaded.
#
# Usage:
#   ./launch_canvas_scene.sh
#   ./launch_canvas_scene.sh /path/to/scene.usd
#
# This is intentionally a thin workflow alias around launch_isaac_assist_desktop.sh.
# The underlying launcher starts the backend if needed, selects Isaac Sim 6.0 by
# default, registers the correct extension folder, and enables omni.isaac.assist.

set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
cd "$SCRIPT_DIR"

export ISAAC_VERSION="${ISAAC_VERSION:-6.0}"

exec "$SCRIPT_DIR/launch_isaac_assist_desktop.sh" "$@"
