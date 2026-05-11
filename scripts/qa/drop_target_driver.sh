#!/bin/bash
# drop_target_driver.sh — apply drop_target pattern to remaining failing CPs.
#
# For each CP that has:
#   - destination_path = a Bin-like prim
#   - planning_obstacles includes destination
#   - status = stable_fail
# Apply:
#   - drop_target=[bin_x, bin_y, bin_z + 0.10] (10cm above bin top)
#   - Remove destination_path from planning_obstacles
# Verify N=1. If stable_ok → commit + push. Else revert.
#
# Usage: nohup bash scripts/qa/drop_target_driver.sh > /tmp/drop_target_driver.log 2>&1 &

set -u
REPO="/home/anton/projects/Omniverse_Nemotron_Ext"
LOG="/tmp/drop_target_driver.log"
cd "$REPO"

# CPs to attempt — those that have destination_path in planning_obstacles AND are stable_fail
TARGETS=(
    "CP-05" "CP-06" "CP-35" "CP-40"
    "CP-67" "CP-76"
    "CP-60" "CP-62"
)

ITER=0
N_UNLOCKED=0
for cp in "${TARGETS[@]}"; do
    ITER=$((ITER + 1))
    NOW=$(date '+%H:%M:%S')
    echo "[$NOW] iter=$ITER attempting $cp..." | tee -a "$LOG"

    # Backup template
    cp "workspace/templates/${cp}.json" "/tmp/${cp}.json.dropdriver-bak"

    # Use python to apply the pattern
    LABEL="${cp}"
    python << PYEOF
import json, re, sys, os
label = os.environ.get('LABEL', 'UNKNOWN')
p = f'workspace/templates/{label}.json'
d = json.loads(open(p).read())
code = d.get('code','')

# Find destination_path
m = re.search(r'destination_path="([^"]+)"', code)
if not m:
    print(f"{label}: no destination_path, skip")
    sys.exit(1)
dest = m.group(1)

# Find bin position (if destination is a bin/cube)
m2 = re.search(rf'create_(?:bin|prim)\(prim_path="{re.escape(dest)}", *(?:prim_type="[^"]+", *)?position=\[([^\]]+)\](?:, *(?:scale|size)=\[([^\]]+)\])?', code)
if not m2:
    print(f"{label}: no bin/prim definition for {dest}, skip")
    sys.exit(1)
pos = [float(x.strip()) for x in m2.group(1).split(',')]
size_str = m2.group(2)
if size_str:
    size = [float(x.strip()) for x in size_str.split(',')]
    bin_z_top = pos[2] + size[2] / 2.0
else:
    bin_z_top = pos[2] + 0.10

drop_z = bin_z_top + 0.05
drop_target = [pos[0], pos[1], drop_z]
print(f"{label}: dest={dest} pos={pos} drop_target={drop_target}")

# Check if drop_target already explicit
if re.search(r'drop_target=\[', code):
    print(f"{label}: already has drop_target, skip")
    sys.exit(1)

# Pattern: add drop_target before planning_obstacles
pattern = rf'(destination_path="{re.escape(dest)}",\s*\\n)(\s+mutex_path="[^"]+",\s*\\n)?(\s+)(planning_obstacles=\[[^\]]+\])'
def repl(m):
    suffix = m.group(2) or ""
    indent = m.group(3)
    obstacles = m.group(4)
    # Remove dest from obstacles
    new_obs = obstacles.replace(f'\\"{dest}\\"', '').replace(', ,', ',').replace('[, ', '[').replace(', ]', ']')
    # Insert drop_target
    return f'{m.group(1)}{indent}drop_target={drop_target},\\n{suffix}{indent}{new_obs}'
new_code = re.sub(pattern, repl, code, count=1)

if new_code == code:
    print(f"{label}: pattern didn't match (no planning_obstacles?), skip")
    sys.exit(1)

d['code'] = new_code
open(p, 'w').write(json.dumps(d, indent=2))
print(f"{label}: applied drop_target={drop_target}")
PYEOF

    PYRC=$?
    if [ $PYRC -ne 0 ]; then
        echo "[$NOW] $cp: pattern not applied" | tee -a "$LOG"
        cp "/tmp/${cp}.json.dropdriver-bak" "workspace/templates/${cp}.json"
        continue
    fi

    # Verify
    timeout 240 python scripts/qa/multi_run_regression.py --canonicals "$cp" --n-runs 1 --seed 42 --tag "drop-driver-$cp" 2>&1 | tail -5 >> "$LOG"
    STATUS=$(tail -10 "$LOG" | grep -oE 'stable_ok|stable_fail|flaky' | tail -1)
    if [ "$STATUS" = "stable_ok" ]; then
        N_UNLOCKED=$((N_UNLOCKED + 1))
        echo "[$NOW] $cp UNLOCKED via drop_target pattern" | tee -a "$LOG"
        git add "workspace/templates/${cp}.json"
        git commit -m "$cp — drop_target pattern auto-applied (drop-driver overnight)" 2>&1 | tail -1 >> "$LOG"
        git push anton feat/multimodal-foundation 2>&1 | tail -1 >> "$LOG"
    else
        echo "[$NOW] $cp still failing ($STATUS), reverting" | tee -a "$LOG"
        cp "/tmp/${cp}.json.dropdriver-bak" "workspace/templates/${cp}.json"
    fi

    sleep 5
done

echo "[$(date '+%H:%M:%S')] drop_target_driver done. Unlocked: $N_UNLOCKED of ${#TARGETS[@]}" | tee -a "$LOG"
