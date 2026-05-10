#!/bin/bash
# phase5_driver.sh — autonomous Phase 5 unlock-driver loop.
#
# For each failing CP not yet attempted today:
#   1. Probe it (if Kit free)
#   2. Apply known-fix pattern based on probe signal
#   3. Verify N=1
#   4. If stable_ok, commit; else log and try next
#
# Idle until Kit RPC is free. Designed for 24h+ runs.
#
# Usage: nohup bash scripts/qa/phase5_driver.sh > /tmp/phase5_driver.log 2>&1 &

set -u
REPO="/home/anton/projects/Omniverse_Nemotron_Ext"
LOG="/tmp/phase5_driver.log"
cd "$REPO"

# CPs to attempt (already attempted today excluded)
TARGETS=(
    "CP-67" "CP-76" "CP-52"   # multi-Franka relay
    "CP-73" "CP-74"             # UR10 plan-fail / grip
    "CP-80" "CP-84" "CP-85"     # UR10 raycast
    "CP-05" "CP-06"             # spline / reorient
    "CP-40"                       # spline 4-cube
    "CP-60" "CP-62"             # build-failed conveyor loops
)

ITER=0
for cp in "${TARGETS[@]}"; do
    ITER=$((ITER + 1))
    NOW=$(date '+%H:%M:%S')
    echo "[$NOW] iter=$ITER attempting $cp..."

    # Quick probe (if probe completes, Kit is responsive)
    timeout 200 python scripts/qa/probe_ctrl_telemetry.py "$cp" --duration 45 --json > "/tmp/phase5_${cp}_probe.json" 2>&1
    PROBE_RC=$?
    if [ $PROBE_RC -ne 0 ]; then
        echo "[$NOW] $cp probe failed rc=$PROBE_RC, skipping"
        continue
    fi

    # Try multi-cube fix if probe shows multi-cube scene
    NCUBES=$(grep -oE '"cube_paths":[[:space:]]*\[[^]]*\]' "/tmp/phase5_${cp}_probe.json" 2>/dev/null \
             | grep -oE '"/World/[^"]+"' | wc -l)
    DELIVERED=$(grep -oE '"cubes_delivered_final":[[:space:]]*[0-9]+' "/tmp/phase5_${cp}_probe.json" 2>/dev/null \
                | grep -oE '[0-9]+' | head -1)
    PLANS=$(grep -oE '"plan_calls":[[:space:]]*[0-9]+' "/tmp/phase5_${cp}_probe.json" 2>/dev/null \
            | grep -oE '[0-9]+' | head -1)

    DELIVERED=${DELIVERED:-0}
    PLANS=${PLANS:-0}
    NCUBES=${NCUBES:-0}

    echo "[$NOW] $cp probe: ncubes=$NCUBES delivered=$DELIVERED plan_calls=$PLANS"

    # Heuristic: if delivered ≥1 but gate fails, try cube_paths fix on cube_path
    if [ "$DELIVERED" -ge 1 ] && [ "$NCUBES" -ge 2 ]; then
        echo "[$NOW] $cp: candidate for cube_paths fix"
        # Backup template
        cp "workspace/templates/${cp}.json" "/tmp/${cp}.json.backup-$NOW"
        # Apply cube_paths conversion (Python helper)
        python -c "
import json, sys
import re
p = 'workspace/templates/${cp}.json'
d = json.loads(open(p).read())
sa = d.get('simulate_args') or {}
if 'cube_paths' in sa:
    print('already has cube_paths')
    sys.exit(0)
cp_single = sa.get('cube_path')
if not cp_single:
    print('no cube_path to convert')
    sys.exit(0)
code = d.get('code', '')
all_cubes = sorted(set(re.findall(r'/World/Cube\w*', code)))
all_cubes = [c for c in all_cubes if c not in ('/World/Cube_',)]
if len(all_cubes) < 2:
    print('not multi-cube')
    sys.exit(0)
sa['cube_paths'] = all_cubes
sa.pop('cube_path', None)
d['simulate_args'] = sa
open(p, 'w').write(json.dumps(d, indent=2))
print(f'patched: cube_paths={all_cubes}')
" >> "$LOG" 2>&1

        # Verify
        timeout 240 python scripts/qa/multi_run_regression.py --canonicals "$cp" --n-runs 1 --seed 42 --tag "phase5-driver-$cp" 2>&1 | tail -5 >> "$LOG"
        STATUS=$(grep -oE 'stable_ok|stable_fail|flaky' "/tmp/phase5_driver.log" | tail -1)

        if [ "$STATUS" = "stable_ok" ]; then
            echo "[$NOW] $cp UNLOCKED"
            git add "workspace/templates/${cp}.json"
            git commit -m "$cp — phase5 driver auto-fix (cube_paths from probe data)" 2>&1 | tail -1 >> "$LOG"
            git push anton feat/multimodal-foundation 2>&1 | tail -1 >> "$LOG"
        else
            echo "[$NOW] $cp still failing ($STATUS), reverting"
            cp "/tmp/${cp}.json.backup-$NOW" "workspace/templates/${cp}.json"
        fi
    else
        echo "[$NOW] $cp: no obvious cube_paths fix (delivered=$DELIVERED ncubes=$NCUBES)"
    fi

    # Brief pause between iterations
    sleep 5
done

echo "[$(date '+%H:%M:%S')] phase5_driver finished after $ITER iterations"
