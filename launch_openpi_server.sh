#!/usr/bin/env bash
# Serve pi0.5 (DROID joint-position checkpoint) for run_policy_baseline.py --policy pi05,
# as RoboLab's policies/pi0_family README does, from its OpenPI fork.
#
# JAX preallocation is off: on this GB10 (unified memory) a server that
# reserves a large GPU fraction up front has crashed Isaac's RTX startup.
set -euo pipefail
OPENPI_ROOT="${OPENPI_ROOT:-$HOME/Documents/Github/openpi}"
POLICY_CONFIG="${POLICY_CONFIG:-pi05_droid_jointpos}"
POLICY_DIR="${POLICY_DIR:-gs://openpi-assets-simeval/pi05_droid_jointpos}"
cd "$OPENPI_ROOT"
export XLA_PYTHON_CLIENT_PREALLOCATE=false
export XLA_PYTHON_CLIENT_MEM_FRACTION="${XLA_PYTHON_CLIENT_MEM_FRACTION:-0.35}"
exec uv run scripts/serve_policy.py policy:checkpoint \
    --policy.config="$POLICY_CONFIG" --policy.dir="$POLICY_DIR" "$@"
