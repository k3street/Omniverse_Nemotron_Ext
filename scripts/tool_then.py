#!/usr/bin/env python3
"""A tool's grip, planned against the scene as it is NOW: a stage after the
first (a drill's forward/reverse switch pressed to forward by the hand on
the standing drill, then the drill taken up by its grip and its trigger
run). The tool's parts are read where the replay's live poses put them
(plan_er_grasp._tool_grip_task), the request built from the scene's own
planner request with the hand's state at the end of the stage before, and
HomeHero's adapter plans it (tool_grip_planner.py). Prints TOOL_PLAN
OK|FAIL. (HomeHero's venv, with OpenUSD on PYTHONPATH - as the replay
starts it.)

    tool_then.py <scene_dir> --hold hold.npz --poses live_poses.json --out then_targets.npz
reads spec "tool_stage": {"tool_grip": {...}, "approach_m", "lift_m", "pull_s", "run_s", "release_s", "hold_s"}
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

    from plan_er_grasp import _tool_grip_task, then_request

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("scene_dir")
    ap.add_argument("--hold", required=True)
    ap.add_argument("--poses", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--spec", help="a spec other than the scene's own (an offline test)")
    args = ap.parse_args()
    d = Path(args.scene_dir)
    spec = json.loads(Path(args.spec or d / "spec.json").read_text())
    ts = spec.get("tool_stage")
    if not ts:
        print("TOOL_PLAN FAIL the spec has no tool_stage")
        return 1
    stage = Usd.Stage.Open(str(d / "scene.usda"))
    tgt = next(o for o in spec["objects"] if o.get("target"))
    tool = stage.GetPrimAtPath(f"/World/Objects/{tgt['name']}")
    gp = {"task": "tool_grip", **ts}
    task = _tool_grip_task(stage, tool, UsdGeom.XformCache(), d, gp, np.zeros(3), poses_path=args.poses)
    base = json.loads((d / "grasp_plan" / "request.json").read_text())
    hold = np.load(args.hold)["targets"][-1]
    req = then_request(base, hold, task, Path(args.out).resolve())
    (Path(args.out).with_suffix(".request.json")).write_text(json.dumps(req))
    r = subprocess.run([sys.executable, str(HERE / "homehero_grasp_adapter.py")], input=json.dumps(req), capture_output=True, text=True, timeout=1800)
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
        print(f"TOOL_PLAN FAIL {(out or {}).get('reason') or (r.stderr[-300:] if r.stderr else 'no adapter output')}")
        return 1
    print(f"TOOL_PLAN OK {out.get('ticks')} ticks: {json.dumps(out.get('stage_ticks'))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
