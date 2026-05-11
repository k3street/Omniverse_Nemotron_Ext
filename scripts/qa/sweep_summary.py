"""sweep_summary.py — summarize a baseline JSON into stable_ok/fail breakdown.

Usage:
  python scripts/qa/sweep_summary.py workspace/baselines/full-n1-overnight-baseline.json [--by-cause]

With --by-cause: stable_fail CPs are further classified by likely root cause
from per_run signals (cube_final z, in_xy, above_floor, at_rest, speed).
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path


def _classify_fail(r):
    """Categorize a stable_fail result by likely root cause."""
    pr = (r.get("per_run") or [{}])[0]
    cube = pr.get("cube_final")
    if not cube or len(cube) < 3:
        return "Z_NO_DATA"

    x, y, z = float(cube[0]), float(cube[1]), float(cube[2])
    speed = float(pr.get("speed", 0) or 0)
    above_floor = bool(pr.get("above_floor", False))
    at_rest = bool(pr.get("at_rest", False))
    in_xy = bool(pr.get("in_xy", False))

    # Physics explosion: cube position absurd
    if abs(x) > 100 or abs(y) > 100 or abs(z) > 100 or speed > 1000:
        return "A_PHYSX_EXPLOSION"
    # Cube fell off / below floor
    if not above_floor:
        if in_xy:
            return "B_FELL_THROUGH_BIN"  # in xy but below z
        return "C_FELL_OFF_BELT"
    # In xy but not at rest (still moving)
    if in_xy and not at_rest:
        return "D_NOT_AT_REST"
    # Above floor, at rest, but not in xy: cube parked somewhere off-target
    if not in_xy:
        return "E_OFF_TARGET_XY"
    # In all 3 conditions but still fail (shouldn't happen often)
    return "F_TRUE_NEAR_MISS"


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: sweep_summary.py <baseline.json> [--by-cause]")
        return 2
    path = Path(sys.argv[1])
    by_cause = "--by-cause" in sys.argv
    d = json.loads(path.read_text())

    by_status = defaultdict(list)
    fail_by_cause = defaultdict(list)
    for r in d.get("results", []):
        s = r.get("status") or r.get("verdict") or "?"
        label = r.get("label", "?")
        by_status[s].append(label)
        if by_cause and s == "stable_fail":
            cause = _classify_fail(r)
            fail_by_cause[cause].append(label)

    total = sum(len(v) for v in by_status.values())
    print(f"=== {path.name} — {total} canonicals ===\n")
    for status in sorted(by_status, key=lambda x: ('ZZZ' if x == 'stable_ok' else x)):
        items = sorted(by_status[status])
        print(f"## {status}: {len(items)}")
        for i in range(0, len(items), 8):
            print("  " + ", ".join(items[i:i+8]))
        print()

    n_stable_ok = len(by_status.get("stable_ok", []))
    n_build_ok = len(by_status.get("BUILD_OK", []))
    n_fail = len(by_status.get("stable_fail", []))
    n_other = total - n_stable_ok - n_build_ok - n_fail

    print("=" * 50)
    print(f"GREEN (stable_ok + BUILD_OK): {n_stable_ok + n_build_ok}/{total}")
    print(f"  stable_ok:   {n_stable_ok}")
    print(f"  BUILD_OK:    {n_build_ok}")
    print(f"  stable_fail: {n_fail}")
    print(f"  other:       {n_other}")

    if by_cause and fail_by_cause:
        print()
        print("=" * 50)
        print("stable_fail by likely cause:")
        cause_names = {
            "A_PHYSX_EXPLOSION": "PhysX explosion (cube > 100m or > 1000 m/s)",
            "B_FELL_THROUGH_BIN": "Fell through bin (in_xy=True but below floor)",
            "C_FELL_OFF_BELT": "Cube fell off belt or scene (above_floor=False)",
            "D_NOT_AT_REST": "In target xy but not settled (moving)",
            "E_OFF_TARGET_XY": "Above floor + at rest but wrong xy",
            "F_TRUE_NEAR_MISS": "All checks pass except gate (rare)",
            "Z_NO_DATA": "No per_run data",
        }
        for cause in sorted(fail_by_cause):
            items = fail_by_cause[cause]
            desc = cause_names.get(cause, cause)
            print(f"\n## {cause}: {len(items)}  ({desc})")
            for i in range(0, len(items), 8):
                print("  " + ", ".join(items[i:i+8]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
