#!/bin/bash
# Where the asset library lives: ~/prometheus-ext is the one switch every library path goes through
# (~/Desktop/assets, workspace/{asset_library,assets_fixed,review_queue,asset_animations}).
#
#   scripts/storage_link.sh status   # where it points, and whether that is there
#   scripts/storage_link.sh local    # the 4 TB drive is away: point it at ~/prometheus-local (same layout)
#   scripts/storage_link.sh drive    # the drive is back: point it at the drive again
#   scripts/storage_link.sh merge    # after `drive`: copy what was made locally onto the drive (never overwriting)
#
# Records made while local carry link-routed paths (ingest uses os.path.abspath), so they stay valid
# after the merge. The local library starts empty: what the drive already holds is not visible to it,
# and the merge leaves a same-named file on the drive alone (listing it) rather than replace it.
set -euo pipefail
LINK=$HOME/prometheus-ext
DRIVE=/media/kimate/prometheus-ext
LOCAL=$HOME/prometheus-local
LAYOUT=(datasets/sim_ready_assets/source_assets/SketchFab_Assets datasets/sim_ready_assets/asset_library
        datasets/sim_ready_assets/assets_fixed datasets/sim_ready_assets/review_queue
        workspace/cache/omniverse_nemotron/asset_animations)

case "${1:-status}" in
  status)
    t=$(readlink "$LINK" || true)
    if [ -d "$t/datasets" ]; then echo "$LINK -> $t (present)"; else echo "$LINK -> $t (MISSING)"; fi
    if mountpoint -q "$DRIVE" 2>/dev/null; then echo "drive mounted at $DRIVE"; else echo "drive not mounted at $DRIVE"; fi
    ;;
  local)
    for d in "${LAYOUT[@]}"; do mkdir -p "$LOCAL/$d"; done
    [ -f "$LOCAL/README" ] || echo "Local stand-in for the prometheus-ext drive (scripts/storage_link.sh); merge onto the drive when it is back." > "$LOCAL/README"
    ln -sfn "$LOCAL" "$LINK"
    echo "$LINK -> $LOCAL"
    ;;
  drive)
    if ! [ -d "$DRIVE/datasets" ]; then echo "the drive is not mounted at $DRIVE" >&2; exit 1; fi
    ln -sfn "$DRIVE" "$LINK"
    echo "$LINK -> $DRIVE"
    ;;
  merge)
    if [ "$(readlink "$LINK")" != "$DRIVE" ]; then echo "point the link at the drive first (storage_link.sh drive)" >&2; exit 1; fi
    for d in "${LAYOUT[@]}"; do
      [ -d "$LOCAL/$d" ] || continue
      rsync -a --ignore-existing "$LOCAL/$d/" "$DRIVE/$d/"
      # what the drive already had under the same name: left as it was, listed for a look
      (cd "$LOCAL/$d" && find . -type f) | while read -r f; do
        if ! cmp -s "$LOCAL/$d/$f" "$DRIVE/$d/$f"; then echo "kept the drive's: $d/$f"; fi
      done
    done
    echo "merged $LOCAL into $DRIVE (nothing on the drive overwritten)"
    ;;
  *) echo "usage: $0 status|local|drive|merge" >&2; exit 2 ;;
esac
