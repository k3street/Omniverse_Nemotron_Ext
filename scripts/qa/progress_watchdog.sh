#!/bin/bash
# progress_watchdog.sh — monitors autonomous-work progress
#
# Polls git log every 5 min for new commits on feat/multimodal-foundation.
# If no commit in last 30 min OR uvicorn dead OR Kit RPC dead, writes
# alert to /tmp/progress_alert.log. Auto-restarts uvicorn if dead.
#
# Usage: nohup bash scripts/qa/progress_watchdog.sh > /tmp/progress_watchdog.log 2>&1 &

set -u
REPO="/home/anton/projects/Omniverse_Nemotron_Ext"
ALERT_LOG="/tmp/progress_alert.log"
WATCH_LOG="/tmp/progress_watchdog.log"
cd "$REPO"

LAST_COMMIT_TS=0
ITER=0

while true; do
    ITER=$((ITER + 1))
    NOW=$(date +%s)
    NOW_HUMAN=$(date '+%H:%M:%S')

    # Latest commit timestamp on current branch
    LATEST_TS=$(git log -1 --format='%ct' 2>/dev/null || echo 0)
    AGE_SEC=$((NOW - LATEST_TS))
    AGE_MIN=$((AGE_SEC / 60))

    # Process health
    KIT_OK=0
    UVICORN_OK=0
    ss -tlnp 2>/dev/null | grep -q ":8001" && KIT_OK=1
    ss -tlnp 2>/dev/null | grep -q ":8000" && UVICORN_OK=1

    STATUS="iter=$ITER commit_age_min=$AGE_MIN kit=$KIT_OK uvicorn=$UVICORN_OK"
    echo "[$NOW_HUMAN] $STATUS"

    # Auto-restart uvicorn if dead
    if [ "$UVICORN_OK" -eq 0 ]; then
        echo "[$NOW_HUMAN] uvicorn dead, restarting..."
        nohup /home/anton/miniconda3/bin/uvicorn service.isaac_assist_service.main:app \
            --host 0.0.0.0 --port 8000 --no-access-log \
            > /tmp/isaac_assist_uvicorn.log 2>&1 &
        sleep 5
    fi

    # Alert if no commit in 30+ min AND watchdog has been running 10+ min
    if [ "$AGE_MIN" -gt 30 ] && [ "$ITER" -gt 2 ]; then
        echo "[$NOW_HUMAN] ALERT: no commit in $AGE_MIN minutes" >> "$ALERT_LOG"
    fi

    # Alert if Kit RPC dead
    if [ "$KIT_OK" -eq 0 ]; then
        echo "[$NOW_HUMAN] ALERT: Kit RPC dead — needs Isaac Sim restart" >> "$ALERT_LOG"
    fi

    sleep 300  # 5 min
done
