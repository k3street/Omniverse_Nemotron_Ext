#!/bin/bash
# baseline_watchdog.sh — system-level watchdog: when Phase 0 baseline (PID
# from /tmp/phase0_baseline/runner.pid) exits, auto-launches overnight_chain.
#
# Designed to survive Claude Code session end. Run via nohup so it persists.
#
# Usage:
#   nohup bash scripts/qa/baseline_watchdog.sh > /tmp/baseline_watchdog.log 2>&1 &

set -u

REPO_ROOT="/home/anton/projects/Omniverse_Nemotron_Ext"
PID_FILE="/tmp/phase0_baseline/runner.pid"
CHAIN_LOG="/tmp/overnight_chain.log"
WATCHDOG_LOG="/tmp/baseline_watchdog.log"

cd "$REPO_ROOT"

if [ ! -f "$PID_FILE" ]; then
    echo "[$(date '+%H:%M:%S')] PID file missing: $PID_FILE" >&2
    exit 1
fi

BASELINE_PID=$(cat "$PID_FILE")
echo "[$(date '+%H:%M:%S')] watching baseline PID $BASELINE_PID"

# Poll every 60s until process exits
while kill -0 "$BASELINE_PID" 2>/dev/null; do
    sleep 60
done

echo "[$(date '+%H:%M:%S')] baseline PID $BASELINE_PID exited"
sleep 5  # let any final writes flush

# Confirm baseline JSON exists
BASELINE_JSON="$REPO_ROOT/workspace/baselines/2026-05-09-baseline.json"
if [ ! -f "$BASELINE_JSON" ]; then
    echo "[$(date '+%H:%M:%S')] WARN: baseline JSON missing at $BASELINE_JSON"
    echo "[$(date '+%H:%M:%S')] running overnight_chain anyway (it has fail-safe handling)"
fi

echo "[$(date '+%H:%M:%S')] launching overnight_chain.py"
nohup python -u "$REPO_ROOT/scripts/qa/overnight_chain.py" \
    > "$CHAIN_LOG" 2>&1 &
CHAIN_PID=$!
echo "[$(date '+%H:%M:%S')] overnight_chain started PID $CHAIN_PID, logging to $CHAIN_LOG"
echo "$CHAIN_PID" > /tmp/overnight_chain.pid

# Wait for chain to finish (overnight, may be 6-8h)
wait $CHAIN_PID
RC=$?
echo "[$(date '+%H:%M:%S')] overnight_chain finished rc=$RC"

# Final marker file so user can see it's done
echo "$(date '+%Y-%m-%d %H:%M:%S') rc=$RC" > /tmp/overnight_chain.done
