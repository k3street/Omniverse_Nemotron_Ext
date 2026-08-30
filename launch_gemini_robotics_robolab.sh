#!/usr/bin/env bash
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
export PYTHONPATH="$ISAAC_LAB_ROOT/source/isaaclab:$ISAAC_LAB_ROOT/source/isaaclab_tasks:$ROBOLAB_ROOT${PYTHONPATH:+:$PYTHONPATH}"

cd "$ROOT"

# Isaac Lab 3 / Isaac Sim 6 defaults to headless unless a visualizer is named.
# This launcher is intentionally visual; preserve explicit user overrides.
viewer_args=(--viz kit)
artifact_dir="$ROOT/artifacts/gemini_robotics_er2_robolab"
expect_artifact_dir=0
shadow_plan_only=0
for arg in "$@"; do
    if [[ "$expect_artifact_dir" == 1 ]]; then
        artifact_dir="$arg"
        expect_artifact_dir=0
        continue
    fi
    case "$arg" in
        --shadow-plan-only|--guarded-world-effect-execution)
            shadow_plan_only=1
            ;;
        --viz|--viz=*|--visualizer|--visualizer=*|--headless)
            viewer_args=()
            ;;
        --artifact-dir)
            expect_artifact_dir=1
            ;;
        --artifact-dir=*)
            artifact_dir="${arg#--artifact-dir=}"
            ;;
    esac
done

critic_marker="$(mktemp /tmp/robot-sequence-critic.XXXXXX)"
set +e
"$ISAAC_SIM_ROOT/python.sh" "$ROOT/scripts/run_gemini_robotics_robolab.py" "${viewer_args[@]}" "$@"
sim_status=$?
set -e

# Critique only after Isaac Sim exits.  The ephemeral local Cosmos server is
# then stopped again, so it cannot contend with the next simulator run.
if [[ "$shadow_plan_only" == 0 && "${ROBOT_SEQUENCE_CRITIC:-1}" != 0 \
      && -f "$artifact_dir/sequence_trace.json" \
      && "$artifact_dir/sequence_trace.json" -nt "$critic_marker" ]]; then
    if ! "$ROOT/scripts/run_local_sequence_critic.sh" "$artifact_dir"; then
        echo "[passive-critic] unavailable; simulator result remains status $sim_status" >&2
    fi
elif [[ "$shadow_plan_only" == 0 && "${ROBOT_SEQUENCE_CRITIC:-1}" != 0 ]]; then
    echo "[passive-critic] skipped: this simulator invocation produced no fresh trace" >&2
fi
rm -f "$critic_marker"

exit "$sim_status"
