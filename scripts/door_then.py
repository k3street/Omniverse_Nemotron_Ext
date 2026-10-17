#!/usr/bin/env python3
"""A door's next stage, planned against the scene as it is NOW: the replay
hands over the live poses (a microwave's door popped ajar by its latch) and
the robot's state at the end of the stage before; the door task is read from
the scene at those poses (plan_er_grasp._door_open_task: its free edge as the
handle when spec door_edge.handle is "@edge"), the request built from the
scene's own planner request, and HomeHero's adapter plans it
(door_open_planner.py). Prints DOOR_PLAN OK|FAIL. (HomeHero's venv, with
OpenUSD on PYTHONPATH - as the replay starts it.)

    door_then.py <scene_dir> --hold hold.npz --poses live_poses.json --out then_targets.npz
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def main() -> int:
    import numpy as np
    from pxr import Usd, UsdGeom

    from plan_er_grasp import _door_open_task, then_request

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("scene_dir")
    ap.add_argument("--hold", required=True)
    ap.add_argument("--poses", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--spec", help="a spec other than the scene's own (an offline test of a door_edge change)")
    args = ap.parse_args()
    d = Path(args.scene_dir)
    spec = json.loads(Path(args.spec or d / "spec.json").read_text())
    de = spec.get("door_edge")
    if not de:
        print("DOOR_PLAN FAIL the spec has no door_edge stage")
        return 1
    stage = Usd.Stage.Open(str(d / "scene.usda"))
    xc = UsdGeom.XformCache()
    obj = next(o for o in stage.GetPrimAtPath("/World/Objects").GetChildren()
               if any(q.GetName() == de["joint"] for q in Usd.PrimRange(o)))
    gp = {"door_open": de, **{k: de[k] for k in ("approach_m", "close_s", "hold_s", "rrt_timeout_s") if k in de}}
    task = _door_open_task(stage, obj, xc, d, gp, np.zeros(3), poses_path=args.poses)
    base = json.loads((d / "grasp_plan" / "request.json").read_text())
    hold = np.load(args.hold)["targets"][-1]
    req = then_request(base, hold, task, Path(args.out).resolve())
    (Path(args.out).with_suffix(".request.json")).write_text(json.dumps(req))
    r = subprocess.run([sys.executable, str(HERE / "homehero_grasp_adapter.py")], input=json.dumps(req), capture_output=True, text=True, timeout=1500)
    out = None
    for ln in r.stdout.splitlines()[::-1]:
        if ln.startswith("{"):
            try:
                out = json.loads(ln)
                break
            except json.JSONDecodeError:
                continue
    (Path(args.out).with_suffix(".plan.json")).write_text(json.dumps(out if out else {"stdout": r.stdout[-4000:], "stderr": r.stderr[-4000:]}, indent=1))
    if not out or not out.get("passed"):
        print(f"DOOR_PLAN FAIL {(out or {}).get('reason') or (r.stderr[-300:] if r.stderr else 'no adapter output')}")
        return 1
    gr = out.get("grip", {})
    print(f"DOOR_PLAN OK {out.get('ticks')} ticks, the door {'pushed' if gr.get('push') else 'pulled'} {out.get('pull_deg')} deg further; "
          f"grip {gr.get('push') or json.dumps(gr.get('wrap'))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
