#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_PATH="${1:-${SCRIPT_DIR}/config/rgbd_collision_monitor.example.json}"
export ROS_LOG_DIR="${ROS_LOG_DIR:-${SCRIPT_DIR}/artifacts/ros_logs}"
mkdir -p "${ROS_LOG_DIR}"

if [[ -f /opt/ros/jazzy/setup.bash ]]; then
  # shellcheck disable=SC1091
  set +u
  source /opt/ros/jazzy/setup.bash
  set -u
fi

exec python3 "${SCRIPT_DIR}/scripts/rgbd_collision_monitor_ros2.py" \
  --config "${CONFIG_PATH}"
