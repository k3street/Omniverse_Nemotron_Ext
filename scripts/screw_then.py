#!/usr/bin/env python3
"""A screw's drive, planned against the scene as it is NOW: the replay hands
over the live poses at the end of the lift (the drill as it sits in the hand)
and the robot's state; the bit's tip and axis are carried from where the build
put them (the attachment's record on the chuck's body) to the chuck's live
pose, the screw's head and axis read from the spec (it has not moved: nothing
turns it before the drive), and HomeHero's adapter plans the drive
(screw_drive_planner.py). Prints SCREW_PLAN OK|FAIL. (HomeHero's venv, with
OpenUSD on PYTHONPATH - as the replay starts it.)

    screw_then.py <scene_dir> --hold hold.npz --poses live_poses.json --out then_targets.npz
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def bit_now(stage, poses: dict, obj_name: str, body_name: str, bit_name: str):
    """The bit's tip and axis now: the build's record of them, moved with the
    chuck's body from its authored pose to its live one."""
    import numpy as np
    from pxr import Usd
    root = stage.GetPrimAtPath(f"/World/Objects/{obj_name}")
    body = next((q for q in Usd.PrimRange(root) if q.GetName() == body_name), None)
    bit = body.GetChild(bit_name) if body else None
    if bit is None or not bit.IsValid():
        raise SystemExit(f"no bit {bit_name} on {body_name} under {root.GetPath()}")
    rec = json.loads(bit.GetCustomDataByKey("er:attachment"))
    sp = poses["bodies"].get(body.GetPath().pathString)
    if not sp:
        return np.asarray(rec["tip_world_at_build"], float), np.asarray(rec["axis_world_at_build"], float), False
    # the tip in the body's own frame, carried by its live pose (as the replay measures it)
    from scipy.spatial.transform import Rotation
    w, x, y, z = sp["q_wxyz"]
    R = Rotation.from_quat([x, y, z, w]).as_matrix()
    return np.asarray(sp["p"], float) + R @ np.asarray(rec["tip_body"], float), R @ np.asarray(rec["axis_body"], float), True


def main() -> int:
    import numpy as np
    from pxr import Usd

    from plan_er_grasp import then_request

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("scene_dir")
    ap.add_argument("--hold", required=True)
    ap.add_argument("--poses", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--spec", help="a spec other than the scene's own (an offline test)")
    ap.add_argument("--part", default="drive", choices=("align", "spin", "drive"),
                    help="align: bring the bit onto the axis and hold (a look follows); spin: align, pull the trigger at the standoff "
                         "and hold; drive: align what is left, approach, drive, release")
    args = ap.parse_args()
    d = Path(args.scene_dir)
    spec = json.loads(Path(args.spec or d / "spec.json").read_text())
    sd = spec.get("screw_drive")
    if not sd:
        print("SCREW_PLAN FAIL the spec has no screw_drive stage")
        return 1
    stage = Usd.Stage.Open(str(d / "scene.usda"))
    poses = json.loads(Path(args.poses).read_text())
    tip, bdir, live = bit_now(stage, poses, sd["bit"]["object"], sd["bit"]["body"], sd["bit"]["attachment"])
    head, out = np.asarray(sd["head"], float), np.asarray(sd["out"], float)
    lateral = (head - tip) - out * ((head - tip) @ out)
    print(f"[screw] bit tip {np.round(tip, 4).tolist()} pointing {np.round(bdir, 3).tolist()} ({'live' if live else 'as built'}); "
          f"head {head.round(4).tolist()}: {np.linalg.norm(lateral) * 1000:.1f} mm off its axis, "
          f"{float((tip - head) @ out) * 1000:.1f} mm out, {np.degrees(np.arccos(np.clip(-bdir @ out, -1, 1))):.1f} deg off it", flush=True)
    task = {"kind": "screw_drive",
            "screw": {"bit_tip": tip.tolist(), "bit_dir": bdir.tolist(), "head": head.tolist(), "out": out.tolist(),
                      "pitch_m": sd["pitch_m"], "start_depth_m": sd["start_depth_m"], "seat_depth_m": sd["seat_depth_m"]},
            "screw_drive": dict({k: v for k, v in sd.items() if k not in ("bit", "head", "out", "sources", "why")}, part=args.part)}
    # the index's release: back to where the grip held it short of the trigger (the tool plan's own)
    _gp = d / "grasp_plan" / "plan.json"
    if _gp.exists() and json.loads(_gp.read_text()).get("index_pre_rad") is not None:
        task["screw_drive"].setdefault("index_release_rad", float(json.loads(_gp.read_text())["index_pre_rad"]))
    base = json.loads((d / "grasp_plan" / "request.json").read_text())
    hold = np.load(args.hold)["targets"][-1]
    req = then_request(base, hold, task, Path(args.out).resolve())
    (Path(args.out).with_suffix(".request.json")).write_text(json.dumps(req))
    r = subprocess.run([sys.executable, str(HERE / "homehero_grasp_adapter.py")], input=json.dumps(req), capture_output=True, text=True, timeout=1500)
    out_ = None
    for ln in r.stdout.splitlines()[::-1]:
        if ln.startswith("{"):
            try:
                out_ = json.loads(ln)
                break
            except json.JSONDecodeError:
                continue
    (Path(args.out).with_suffix(".plan.json")).write_text(json.dumps(out_ if out_ else {"stdout": r.stdout[-4000:], "stderr": r.stderr[-4000:]}, indent=1))
    if not out_ or not out_.get("passed"):
        print(f"SCREW_PLAN FAIL {(out_ or {}).get('reason') or (r.stderr[-300:] if r.stderr else 'no adapter output')}")
        return 1
    dv = out_.get("drive", {})
    print(f"SCREW_PLAN OK {out_.get('ticks')} ticks: {dv.get('turns')} turns over {dv.get('drive_m')} m at {dv.get('rpm')} rpm, "
          f"fed at {dv.get('feed_m_s')} m/s; the bit brought {dv.get('align_shift_m')} m and {dv.get('align_rotation_deg')} deg onto the axis")
    return 0


if __name__ == "__main__":
    sys.exit(main())
