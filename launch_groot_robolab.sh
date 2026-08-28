#!/usr/bin/env bash
set -euo pipefail

ROBOLAB_ROOT="${ROBOLAB_ROOT:-/home/kimate/Documents/Github/RoboLab}"
ROBOLAB_PYTHON="${ROBOLAB_PYTHON:-$ROBOLAB_ROOT/.venv-51/bin/python}"
LAUNCHER_ROOT="$(cd "$(dirname "$0")" && pwd)"
MODE="replay"
TASK=""
REMOTE_HOST="127.0.0.1"
REMOTE_PORT="5555"
OPEN_LOOP_HORIZON="10"

usage() {
    cat <<'EOF'
Usage: ./launch_groot_robolab.sh [replay|live] [options]

Modes:
  replay  Visualize RoboLab's bundled recorded demonstration (default).
  live    Connect Isaac Sim to GR00T with additive torque/contact state.

Options:
  --task NAME       RoboLab task name.
  --host HOST       GR00T server host for live mode (default: 127.0.0.1).
  --port PORT       GR00T server port for live mode (default: 5555).
  --open-loop-horizon N
                    Actions to execute before sending a new observation
                    (default: 10; use 1 for fully closed-loop control).
  -h, --help        Show this help.
EOF
}

if [[ ${1:-} == "replay" || ${1:-} == "live" ]]; then
    MODE="$1"
    shift
fi

while [[ $# -gt 0 ]]; do
    case "$1" in
        --task) TASK="${2:?--task requires a value}"; shift 2 ;;
        --host) REMOTE_HOST="${2:?--host requires a value}"; shift 2 ;;
        --port) REMOTE_PORT="${2:?--port requires a value}"; shift 2 ;;
        --open-loop-horizon)
            OPEN_LOOP_HORIZON="${2:?--open-loop-horizon requires a value}"
            shift 2
            ;;
        -h|--help) usage; exit 0 ;;
        *) echo "Unknown argument: $1" >&2; usage >&2; exit 2 ;;
    esac
done

if [[ ! -x "$ROBOLAB_PYTHON" ]]; then
    echo "RoboLab Python not found: $ROBOLAB_PYTHON" >&2
    echo "Set ROBOLAB_ROOT or ROBOLAB_PYTHON to the installed RoboLab environment." >&2
    exit 1
fi

export OMNI_KIT_ACCEPT_EULA="${OMNI_KIT_ACCEPT_EULA:-Y}"
export LD_PRELOAD="/lib/aarch64-linux-gnu/libgomp.so.1${LD_PRELOAD:+:$LD_PRELOAD}"
cd "$ROBOLAB_ROOT"

if [[ "$MODE" == "replay" ]]; then
    TASK="${TASK:-RubiksCubeAndBananaTask}"
    exec "$ROBOLAB_PYTHON" "$LAUNCHER_ROOT/scripts/run_robolab_replay.py" \
        --task "$TASK" \
        --num_envs 1 \
        --disable-subtask
fi

TASK="${TASK:-BananaOnPlateTask}"
exec "$ROBOLAB_PYTHON" "$LAUNCHER_ROOT/scripts/run_robolab_live.py" \
    --task "$TASK" \
    --remote-host "$REMOTE_HOST" \
    --remote-port "$REMOTE_PORT" \
    --open-loop-horizon "$OPEN_LOOP_HORIZON" \
    --num-envs 1 \
    --video-mode none
