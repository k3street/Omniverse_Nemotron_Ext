#!/bin/bash
# Friendly wrapper for running one materialized Isaac Assist scenario variant.

set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
exec python3 "$SCRIPT_DIR/run_materialized_variant.py" "$@"
