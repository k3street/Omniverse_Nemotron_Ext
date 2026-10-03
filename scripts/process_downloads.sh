#!/bin/bash
# Downloads -> sim-ready library in one command (see scripts/process_downloads.py).
#   scripts/process_downloads.sh --dry-run               # what would happen
#   scripts/process_downloads.sh --delete-duplicates     # do it
#   scripts/process_downloads.sh --delete-duplicates --animate --soft
# Sets up pxr (an OpenUSD build) and ANTHROPIC_API_KEY (.env) like the hub.
set -euo pipefail
REPO=$(cd "$(dirname "$0")/.." && pwd)
if [ -f "$REPO/.env" ]; then set -a; source "$REPO/.env"; set +a; fi
USD=${USD_INSTALL:-/home/kimate/Documents/Github/openusd_build}
export PYTHONPATH="$USD/lib/python${PYTHONPATH:+:$PYTHONPATH}"
export LD_LIBRARY_PATH="$USD/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
# the system python has pxr's dependencies, numpy and the anthropic SDK
exec /usr/bin/python3 "$REPO/scripts/process_downloads.py" "$@"
