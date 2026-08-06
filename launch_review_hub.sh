#!/bin/bash
# Asset Review Hub — human sim2real review of ingested assets.
#
#   ./launch_review_hub.sh                       # start the hub on :8777
#   python scripts/ingest_asset.py FILE          # queue one asset
#   python scripts/ingest_asset.py --scan DIR    # queue every new asset in DIR
#
# Automatic ingest: set ASSET_WATCH_DIRS to poll folders for new assets, e.g.
#   ASSET_WATCH_DIRS=$HOME/Desktop/assets/SketchFab_Assets ./launch_review_hub.sh
# Tunables: ASSET_WATCH_INTERVAL_S (default 120), ASSET_WATCH_LIMIT (20/pass),
#           ASSET_SCAN_DIR (default folder in the hub's scan box)
#
# Sets up an OpenUSD pxr python so ingest checks and USD certification
# stamping work headlessly. Override with USD_INSTALL=/path/to/openusd.
set -euo pipefail
SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)

USD_CANDIDATES=(
    "${USD_INSTALL:-}"
    "/home/kimate/Documents/Github/openusd_build"
    "$HOME/openusd"
    "/opt/openusd"
)
for u in "${USD_CANDIDATES[@]}"; do
    if [[ -n "$u" && -d "$u/lib/python/pxr" ]]; then
        export PYTHONPATH="$u/lib/python${PYTHONPATH:+:${PYTHONPATH}}"
        export LD_LIBRARY_PATH="$u/lib${LD_LIBRARY_PATH:+:${LD_LIBRARY_PATH}}"
        echo "🔧 OpenUSD: $u"
        break
    fi
done

export PYTHONPATH="$SCRIPT_DIR${PYTHONPATH:+:${PYTHONPATH}}"
echo "🧪 Asset Review Hub on http://127.0.0.1:${REVIEW_HUB_PORT:-8777}"
exec python3 "$SCRIPT_DIR/scripts/asset_review_hub.py"
