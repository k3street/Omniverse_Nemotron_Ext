#!/usr/bin/env bash
# Run scripts/robot_chess.py: the DROID Franka plays chess moves in the planner's
# table scene, on the same Isaac Sim 6 stack and behind the same single Isaac slot.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
ROBOLAB_ROOT="${ROBOLAB_ROOT:-/home/kimate/Documents/Github/RoboLab}"
ISAAC_LAB_ROOT="${ISAAC_LAB_ROOT:-/home/kimate/Documents/Github/open_arm_10Things/IsaacLab}"
ISAAC_SIM_ROOT="${ISAAC_SIM_ROOT:-/home/kimate/Documents/Github/isaacsim/_build/linux-aarch64/release}"

if [[ ! -x "$ISAAC_SIM_ROOT/python.sh" ]]; then
    echo "Isaac Sim 6 Python not found: $ISAAC_SIM_ROOT/python.sh" >&2
    exit 1
fi

export ISAAC_SIM_ROOT
export ISAAC_SIM_PATH="$ISAAC_SIM_ROOT"
export OMNI_KIT_ACCEPT_EULA="${OMNI_KIT_ACCEPT_EULA:-Y}"
export LD_PRELOAD="/lib/aarch64-linux-gnu/libgomp.so.1${LD_PRELOAD:+:$LD_PRELOAD}"
OPENPI_CLIENT_DIR="${OPENPI_CLIENT_DIR:-$HOME/.local/share/openpi_client_isaac}"
export PYTHONPATH="$ISAAC_LAB_ROOT/source/isaaclab:$ISAAC_LAB_ROOT/source/isaaclab_tasks:$ROBOLAB_ROOT:$OPENPI_CLIENT_DIR${PYTHONPATH:+:$PYTHONPATH}"

cd "$ROOT"
source "$ROOT/scripts/isaac_slot.sh"
exec "$ISAAC_SIM_ROOT/python.sh" "$ROOT/scripts/robot_chess.py" "$@"
