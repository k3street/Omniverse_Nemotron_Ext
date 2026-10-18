#!/usr/bin/env python3
"""A held tool's tips take a small object and drop it where it is wanted,
planned against the scene as it is NOW (a then-stage): the tweezers in the
hand from the stage before, a tablet on the bench, the open vial.

The tips are read where the live poses put the tool's arms (each arm's inner
face 4 mm from its end, in the arm's own frame), the tool's points where its
bodies are; the payload (spec "payload": {"object", "into"}) is gripped at its
centre and dropped 15 mm over the mouth of the object it goes into. HomeHero's
adapter plans it with the pinch planner's tool mode (pinch_place_planner
tool_tip_pinch): the hand's hold is 'open' (the tool kept, its tips apart),
that hold squeezed is 'closed' (the replay's force band sets how hard).
Prints TIP_PLAN OK|FAIL. (HomeHero's venv, with OpenUSD on PYTHONPATH.)

    tool_tip_then.py <scene_dir> --hold hold.npz --poses live_poses.json --out then_targets.npz [--stop-after approach]

With --stop-after approach the path ends with the tips over the piece; the replay then plans the rest with --correct
from there (grasp_replay.then ["tool_tip_place:approach", "tool_tip_place:correct"]): the tool as it sits in the hand
after the reach, the hand only moved - a round tablet pinched 2 mm off its centre line squirted out from the tips.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def _R(q_wxyz):
    from scipy.spatial.transform import Rotation
    w, x, y, z = q_wxyz
    return Rotation.from_quat([x, y, z, w]).as_matrix()


def arm_tips(arm_poses, flex_length_m: float, from_end_m: float, thick_m: float) -> list:
    """The tips' pinch points: on each arm's inner face (the upper arm's -z face, the lower's +z) from_end_m short of
    its end, from the arms' live poses ({'p', 'q_wxyz'}, upper then lower; each arm's frame at its hinge, along +x)."""
    import numpy as np
    out = []
    for bp, sgn in zip(arm_poses, (1.0, -1.0)):
        out.append(np.asarray(bp["p"], float) + _R(bp["q_wxyz"]) @ np.array([flex_length_m - from_end_m, 0.0, -sgn * thick_m / 2]))
    return out


def body_points_local(stage, body):
    """Every collider shape's points of a body, in the body's own frame (from the build's USD)."""
    import numpy as np
    from pxr import Usd, UsdGeom, UsdPhysics
    from plan_er_grasp import gprim_points
    xc = UsdGeom.XformCache()
    Mb_inv = xc.GetLocalToWorldTransform(body).GetInverse()
    out = []
    for q in Usd.PrimRange(body):
        if q != body and q.HasAPI(UsdPhysics.RigidBodyAPI):
            continue
        if q.HasAPI(UsdPhysics.CollisionAPI):
            for p in gprim_points(q, xc.GetLocalToWorldTransform(q)):
                v = Mb_inv.Transform(__import__("pxr").Gf.Vec3d(*p))
                out.append([v[0], v[1], v[2]])
    return np.asarray(out, float).reshape(-1, 3)


def main() -> int:
    import numpy as np
    from pxr import Usd, UsdPhysics

    from plan_er_grasp import then_request

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("scene_dir")
    ap.add_argument("--hold", required=True)
    ap.add_argument("--poses", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--spec")
    ap.add_argument("--stop-after", choices=["approach"], help="end the path over the piece (a look-and-correct stage follows)")
    ap.add_argument("--correct", action="store_true",
                    help="a correction from over the piece: the hand kept as it is turned, moved only so the tips' live "
                         "midpoint is over the piece, then straight down (no reach, no staging)")
    args = ap.parse_args()
    d = Path(args.scene_dir)
    spec = json.loads(Path(args.spec or d / "spec.json").read_text())
    pay = spec.get("payload")
    if not pay:
        print("TIP_PLAN FAIL the spec has no payload")
        return 1
    tt = (spec.get("tool_tip_place") or {})
    stage = Usd.Stage.Open(str(d / "scene.usda"))
    poses = json.loads(Path(args.poses).read_text())["bodies"]
    tool_obj = next(o for o in spec["objects"] if o.get("target"))
    troot = next((q for q in Usd.PrimRange(stage.GetPrimAtPath(f"/World/Objects/{tool_obj['name']}")) if q.HasCustomDataKey("er:generated")), None)
    meta = json.loads(troot.GetCustomDataByKey("er:generated"))
    L = float(meta["spring"]["flex_length_m"])
    thick = 0.001
    # the tips pinch 1 mm from their ends, a little above the part's middle: pinched 4 mm back at its middle, the ends of
    # a steep pair reached 2 mm into what a 3.5 mm tablet rests on - the pedestal took 41 of 72 grasps (2026-10-17)
    from_end = float(tt.get("tip_from_end_m", 0.001))
    above = float(tt.get("grip_above_centre_m", 0.0005))
    tips = arm_tips([poses[troot.GetChild(meta["arms"][k]).GetPath().pathString] for k in ("upper", "lower")], L, from_end, thick)
    pts = []
    for body in [q for q in Usd.PrimRange(troot) if q.HasAPI(UsdPhysics.RigidBodyAPI)]:
        bp = poses[body.GetPath().pathString]
        R, p = _R(bp["q_wxyz"]), np.asarray(bp["p"], float)
        pts += (p + body_points_local(stage, body) @ R.T).tolist()
    # the payload and where it goes
    pobj = stage.GetPrimAtPath(f"/World/Objects/{pay['object']}")
    pbody = next(q for q in Usd.PrimRange(pobj) if q.HasAPI(UsdPhysics.RigidBodyAPI))
    pp = np.asarray(poses[pbody.GetPath().pathString]["p"], float)
    pmeta = json.loads(next(q for q in Usd.PrimRange(pobj) if q.HasCustomDataKey("er:generated")).GetCustomDataByKey("er:generated"))
    dobj = stage.GetPrimAtPath(f"/World/Objects/{pay['into']}")
    droot = next(q for q in Usd.PrimRange(dobj) if q.HasCustomDataKey("er:generated"))
    dmeta = json.loads(droot.GetCustomDataByKey("er:generated"))
    dbody = droot.GetChild(dmeta["body"])
    db = poses[dbody.GetPath().pathString]
    mouth = np.asarray(db["p"], float) + _R(db["q_wxyz"]) @ np.array([0.0, 0.0, dmeta["opening_z_m"]])
    place = mouth + np.array([0.0, 0.0, float(tt.get("drop_above_m", 0.015)) + pmeta["size_m"][2] / 2])
    base = json.loads((d / "grasp_plan" / "request.json").read_text())
    # the scene's obstacles now: the tool's own cells are in the hand (dropped); the objects as boxes - the payload
    # spared for the tips, the rest (the vial, the other tablets) for the tool and the hand
    obst = [o for o in base["obstacles"] if not o["id"].startswith("held:")]
    for o in spec["objects"]:
        if o.get("target"):
            continue
        q_ = stage.GetPrimAtPath(f"/World/Objects/{o['name']}")
        for b in [x for x in Usd.PrimRange(q_) if x.HasAPI(UsdPhysics.RigidBodyAPI)]:
            bp = poses.get(b.GetPath().pathString)
            if not bp:
                continue
            P = np.asarray(bp["p"], float) + body_points_local(stage, b) @ _R(bp["q_wxyz"]).T
            if not len(P):
                continue
            tag = "payload" if o["name"] == pay["object"] else "obj"
            obst.append(dict(id=f"{tag}:{o['name']}:{b.GetName()}", min=(P.min(0) - 0.002).tolist(), max=(P.max(0) + 0.002).tolist()))
    hold = np.load(args.hold)["targets"][-1]
    grip = pp + np.array([0.0, 0.0, above])
    task = {"kind": "pinch_place", "grip_point": grip.tolist(), "grip_width_m": float(pmeta["size_m"][0]),
            "object_base_z": float(pp[2] - pmeta["size_m"][2] / 2), "object_height_m": float(pmeta["size_m"][2]),
            "place_point": place.tolist(), "destination": f"{pay['into']}'s mouth",
            "tool_pinch": {"tips_world": [t.tolist() for t in tips], "points_world": pts,
                           # the thumb alone presses (the replay's squeeze band): index and middle hold the other arm
                           # where they are - squeezed together in the planner, with no tool between them, the index
                           # met the thumb in a quarter of the orientations (2026-10-17)
                           "squeeze_rad": float(tt.get("squeeze_rad", 0.08)), "squeeze_digits": [4],
                           "spare_obstacles": ["payload:"]},
            "tripod": True, **({"stop_after": args.stop_after} if args.stop_after else {}),
            **({"keep_orientation": True, "direct": True} if args.correct else {}),
            **{k: tt[k] for k in ("lift_m", "close_s", "release_s", "retreat_m", "hold_s", "pregrasp_height_m", "reach",
                                  "cumotion_seeds", "rrt_timeout_s") if k in tt}}
    req = then_request(dict(base, obstacles=obst), hold, task, Path(args.out).resolve())
    Path(args.out).with_suffix(".request.json").write_text(json.dumps(req))
    r = subprocess.run([sys.executable, str(HERE / "homehero_grasp_adapter.py")], input=json.dumps(req), capture_output=True, text=True, timeout=1800)
    out = None
    for ln in r.stdout.splitlines()[::-1]:
        if ln.startswith("{"):
            try:
                out = json.loads(ln)
                break
            except json.JSONDecodeError:
                continue
    Path(args.out).with_suffix(".plan.json").write_text(json.dumps(out if out else {"stdout": r.stdout[-4000:], "stderr": r.stderr[-4000:]}, indent=1))
    print(f"[tip] tips {np.round(tips[0], 4).tolist()} / {np.round(tips[1], 4).tolist()} ({1000 * np.linalg.norm(tips[1] - tips[0]):.1f} mm apart); "
          f"{pay['object']} at {np.round(pp, 4).tolist()}, dropped over {pay['into']} at {np.round(place, 4).tolist()}", flush=True)
    if not out or not out.get("passed"):
        print(f"TIP_PLAN FAIL {(out or {}).get('reason') or (r.stderr[-300:] if r.stderr else 'no adapter output')}")
        return 1
    print(f"TIP_PLAN OK {out.get('ticks')} ticks: {json.dumps(out.get('stage_ticks'))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
