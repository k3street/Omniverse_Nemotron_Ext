#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SIDECAR_ENV="${REPO_ROOT}/.venv-omniverse-tools"

python3 -m venv "${SIDECAR_ENV}"
"${SIDECAR_ENV}/bin/python" -m pip install --upgrade pip
"${SIDECAR_ENV}/bin/python" -m pip install \
  -r "${REPO_ROOT}/requirements-omniverse-tools.txt"
"${SIDECAR_ENV}/bin/nvidia_usd_validate" --version
"${SIDECAR_ENV}/bin/python" \
  "${REPO_ROOT}/scripts/nvidia_usd_validate_bridge.py" --self-test

echo "NVIDIA USD validation sidecar ready: ${SIDECAR_ENV}"
