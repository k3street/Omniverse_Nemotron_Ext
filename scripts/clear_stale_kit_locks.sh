#!/usr/bin/env bash
# Remove Carbonite shared-memory files left by killed Isaac processes, which
# otherwise make every later Kit launch hang before it prints anything.
# Refuses while any Kit process is alive: an unlinked semaphore still in use
# stops that process and the next one from sharing it.
set -euo pipefail
if pgrep -f '^(\S*bash )?\S*(kit/python/bin/python3|kit_app|isaac-sim\.sh)( |$)' \
    | grep -v -x "$$" >/dev/null; then
    alive="$(pgrep -af '^(\S*bash )?\S*(kit/python/bin/python3|kit_app|isaac-sim\.sh)( |$)' | head -1 | cut -c1-160)"
    echo "[kit-locks] left alone: a Kit process is alive ($alive)"
    exit 0
fi
cd /dev/shm
removed=()
if [[ -e sem.carbonite-sharedmemory ]]; then rm -f sem.carbonite-sharedmemory; removed+=(sem.carbonite-sharedmemory); fi
for f in carb-RStringInternals-* sem.carb-RStringInternals-*; do
    [[ -e "$f" ]] || continue
    [[ -d "/proc/${f##*-}" ]] && continue
    rm -f "$f"; removed+=("$f")
done
echo "[kit-locks] removed ${#removed[@]} stale file(s)"
