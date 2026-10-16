#!/usr/bin/env python3
"""Sweep asset_review_hub.exact_actuator_housings over the library: every
queued asset whose behaviours include a motor gets its throttle's housing body
an SDF collider (the rule apply_articulation now applies), recorded in the
entry's applied_fixes. Files are backed up beside the sweep's report first.

    sweep_actuator_housings.py --dry-run
    sweep_actuator_housings.py [--assets drill_1,...]
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))
QUEUE = REPO / "workspace" / "review_queue"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--assets")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    from pxr import Usd

    from asset_review_hub import exact_actuator_housings, save_queue_entry

    only = set(args.assets.split(",")) if args.assets else None
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = QUEUE / "_runs" / f"actuator_housings_{stamp}"
    report = []
    for qf in sorted(QUEUE.glob("*.json")):
        if qf.name.startswith("_"):
            continue
        e = json.loads(qf.read_text())
        if not e.get("asset_id") or (only and e["asset_id"] not in only) or not e.get("file") or not Path(e["file"]).exists():
            continue
        stage = Usd.Stage.Open(e["file"])
        behaviors, root = None, None
        for p in stage.Traverse():
            raw = p.GetCustomDataByKey("simReady:behaviors")
            if raw:
                behaviors, root = json.loads(raw), p.GetPath().pathString
                break
        if not behaviors or not any(b.get("type") == "motor" for b in behaviors):
            continue
        if args.dry_run:
            report.append({"asset": e["asset_id"], "would": [b.get("throttle") for b in behaviors if b.get("type") == "motor"]})
            continue
        backup.mkdir(parents=True, exist_ok=True)
        shutil.copy2(Path(e["file"]).resolve(), backup / Path(e["file"]).name)
        made = exact_actuator_housings(stage, root, behaviors)
        stage.GetRootLayer().Save()
        e["applied_fixes"] = e.get("applied_fixes", []) + [{"fix": "exact_actuator_housings", "at": stamp, "made": made}]
        save_queue_entry(e) if callable(save_queue_entry) else qf.write_text(json.dumps(e, indent=2))
        report.append({"asset": e["asset_id"], "made": made})
    out = QUEUE / "_runs" / f"actuator_housings_{stamp}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"dry_run": args.dry_run, "backup": str(backup), "assets": report}, indent=1))
    for r in report:
        print(r["asset"], json.dumps(r.get("made") or r.get("would"))[:200])
    print(f"REPORT {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
