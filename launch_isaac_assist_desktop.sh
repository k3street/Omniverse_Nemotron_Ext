#!/bin/bash
# Desktop entry wrapper: start Isaac Assist backend if needed, then launch Isaac Sim.

set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
cd "$SCRIPT_DIR"

SERVICE_LOG="${ISAAC_ASSIST_SERVICE_LOG:-/tmp/isaac_assist_service.log}"
ISAAC_LOG="${ISAAC_ASSIST_ISAAC_LOG:-/tmp/isaac_assist_isaac6_launch.log}"
SERVICE_MODE="${LLM_MODE:-local}"
ISAAC_DESKTOP_VERSION="${ISAAC_VERSION:-6.0}"

service_is_healthy() {
    command -v curl >/dev/null 2>&1 &&
        curl --fail --silent --max-time 3 http://localhost:8000/health >/dev/null
}

if ! service_is_healthy; then
    setsid -f "$SCRIPT_DIR/launch_service.sh" "$SERVICE_MODE" > "$SERVICE_LOG" 2>&1

    for _ in {1..20}; do
        if service_is_healthy; then
            break
        fi
        sleep 1
    done
fi

exec "$SCRIPT_DIR/launch_isaac.sh" --version "$ISAAC_DESKTOP_VERSION" "$@" > "$ISAAC_LOG" 2>&1
