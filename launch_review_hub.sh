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
# Autonomous visual approval: VISUAL_QA_AUTO=1 sends each newly watched-in
# asset through scripts/visual_qa.py — local judges (Cosmos-Reason2 via vLLM
# on :8021, Gemma via Ollama) + Claude tiebreak; unanimous rigid passes are
# machine-signed and promoted, everything else stays here for the human.
# Tunables: VISUAL_QA_AUDIT_EVERY (default 5 — every Nth machine approval is
# flagged for human spot-check), VISUAL_QA_COSMOS_URL, VISUAL_QA_GEMMA_MODEL.
#
# Sets up an OpenUSD pxr python so ingest checks and USD certification
# stamping work headlessly. Override with USD_INSTALL=/path/to/openusd.
set -euo pipefail
SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)

# .env provides ANTHROPIC_API_KEY for the hub's VLM classification button
if [ -f "$SCRIPT_DIR/.env" ]; then
    set -a; source "$SCRIPT_DIR/.env"; set +a
fi
if [ -f "$SCRIPT_DIR/.env.local" ]; then
    set -a; source "$SCRIPT_DIR/.env.local"; set +a
fi

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

# autonomous approval needs its local judges up
if [[ "${VISUAL_QA_AUTO:-}" == "1" ]]; then
    "$SCRIPT_DIR/launch_judges.sh" || echo "⚠️  judge stack incomplete — QA will fail closed to human review"
fi

echo "🧪 Asset Review Hub on http://127.0.0.1:${REVIEW_HUB_PORT:-8777}"
exec python3 "$SCRIPT_DIR/scripts/asset_review_hub.py"
