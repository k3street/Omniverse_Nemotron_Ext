#!/bin/bash
# morning_watchdog.sh — second-level watchdog: when overnight_chain.py
# writes /tmp/overnight_chain.done, this script auto-launches
# morning_continuation.py to extend autonomous work past 08:00.
#
# Usage:
#   nohup bash scripts/qa/morning_watchdog.sh > /tmp/morning_watchdog.log 2>&1 &

set -u

REPO_ROOT="/home/anton/projects/Omniverse_Nemotron_Ext"
DONE_FILE="/tmp/overnight_chain.done"
CONTINUATION_LOG="/tmp/morning_continuation.log"

cd "$REPO_ROOT"

echo "[$(date '+%H:%M:%S')] morning_watchdog: waiting for $DONE_FILE"

# Poll every 60s for the done marker (max 24h)
WAITED=0
while [ ! -f "$DONE_FILE" ] && [ $WAITED -lt 86400 ]; do
    sleep 60
    WAITED=$((WAITED + 60))
done

if [ ! -f "$DONE_FILE" ]; then
    echo "[$(date '+%H:%M:%S')] morning_watchdog: timeout (24h) — overnight_chain never finished"
    exit 1
fi

echo "[$(date '+%H:%M:%S')] morning_watchdog: overnight_chain done detected"
sleep 30  # let any final commits push

echo "[$(date '+%H:%M:%S')] launching morning_continuation.py"
nohup python -u "$REPO_ROOT/scripts/qa/morning_continuation.py" \
    > "$CONTINUATION_LOG" 2>&1 &
CONTINUATION_PID=$!
echo "[$(date '+%H:%M:%S')] morning_continuation started PID $CONTINUATION_PID"
echo "$CONTINUATION_PID" > /tmp/morning_continuation.pid

# Wait for it
wait $CONTINUATION_PID
RC=$?
echo "[$(date '+%H:%M:%S')] morning_continuation finished rc=$RC"
echo "$(date '+%Y-%m-%d %H:%M:%S') rc=$RC" >> /tmp/morning_continuation.done
