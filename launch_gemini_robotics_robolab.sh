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

# One Kit process at a time on this machine: concurrent Kit processes have
# wedged NVIDIA UVM on the Spark, which only a host reboot clears. Take the
# lock HomeHero's sim_run_guard.sh uses, so the two projects queue instead of
# colliding, and also wait out any Kit that did not take it. fd 9 stays open
# in the simulator and the critic, so the lock is held until this script ends.
ISAAC_LOCK_PATH="${ISAAC_LOCK_PATH:-/tmp/homehero_isaac_sim.lock}"
ISAAC_LOCK_WAIT_SECONDS="${ISAAC_LOCK_WAIT_SECONDS:-0}"  # 0 waits indefinitely
exec 9>"$ISAAC_LOCK_PATH"
lock_waited=0
while :; do
    if flock -n 9; then
        # Anchored on the executable (or the bash running an Isaac script), so
        # a shell that merely mentions these names does not count.
        other_kit="$(pgrep -af '^(\S*bash )?\S*(kit/python/bin/python3|kit_app|isaac-sim\.sh)( |$)' \
            | grep -v 'omni.telemetry.transmitter' || true)"
        [[ -z "$other_kit" ]] && break
        flock -u 9
    else
        other_kit="another run holds $ISAAC_LOCK_PATH"
    fi
    if (( ISAAC_LOCK_WAIT_SECONDS > 0 && lock_waited >= ISAAC_LOCK_WAIT_SECONDS )); then
        echo "[isaac-lock] gave up after ${lock_waited}s; still running: ${other_kit%%$'\n'*}" >&2
        exit 76
    fi
    if (( lock_waited % 300 == 0 )); then
        echo "[isaac-lock] waiting for the GPU; running: ${other_kit%%$'\n'*}" | cut -c1-240
    fi
    sleep 10
    lock_waited=$((lock_waited + 10))
done
echo "[isaac-lock] acquired $ISAAC_LOCK_PATH after ${lock_waited}s"

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
