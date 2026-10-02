# Sourced by every launcher that starts Isaac Sim on this machine.
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
        # Skip our own ancestors: a harness running under Isaac's Python
        # launched this script and must not count as a second simulator.
        ancestors=" $$ "
        pid=$$
        while [[ -n "$pid" && "$pid" != 1 ]]; do
            pid="$(awk '/^PPid:/{print $2}' "/proc/$pid/status" 2>/dev/null || true)"
            ancestors+="$pid "
        done
        other_kit="$(pgrep -af '^(\S*bash )?\S*(kit/python/bin/python3|kit_app|isaac-sim\.sh)( |$)' \
            | grep -v 'omni.telemetry.transmitter' \
            | while read -r kit_pid kit_rest; do
                [[ "$ancestors" == *" $kit_pid "* ]] || echo "$kit_pid $kit_rest"
              done || true)"
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
