#!/usr/bin/env python3
"""Animate every joint of an articulated asset in PhysX and record it.

Headless Isaac Sim (run with the build's python.sh, under the machine's
single Isaac slot). For each moving joint the asset declares, drive it
through its range and back while a camera records, and log the joint's
MEASURED position every frame. The video is for people; the log is the
evidence: tracking error per joint, and for gated joints proof that the
gate holds.

Gates (customData `simReady:gate` on a joint, written by add_mechanism.py)
order the motion: before a gated joint moves, it is first pushed with its
actuator at rest (it must not open), then the actuator is worked and the
joint swings, the actuator is released, and the joint returns (a latch cams
shut on its own). Joints that follow another (PhysX mimic joints) are never
driven directly.

Usage:
    python.sh scripts/animate_asset.py <queue_asset_id | file.usd> [--out DIR]
        [--seconds-per-joint 6] [--fps 30] [--width 1280 --height 720]

Writes <out>/<asset>.mp4, frames/, joints.csv, contact_sheet.png and
summary.json (per-joint reach and tracking error; gate check result).
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
QUEUE_DIR = REPO / "workspace" / "review_queue"
OUT_ROOT = REPO / "workspace" / "asset_animations"

ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
ap.add_argument("asset")
ap.add_argument("--out", type=Path)
ap.add_argument("--seconds-per-joint", type=float, default=6.0)
ap.add_argument("--fps", type=int, default=30)
ap.add_argument("--width", type=int, default=1280)
ap.add_argument("--height", type=int, default=720)
ap.add_argument("--push-torque", type=float, default=80.0,
                help="N m (revolute) used to test a gate: about a firm one-hand shove on a door")
ap.add_argument("--push-force", type=float, default=100.0, help="N (prismatic) used to test a gate")
ap.add_argument("--hold", action="store_true",
                help="hold a loose asset still (as a hand or a stand would); automatic for tall ones that would topple")
args = ap.parse_args()

if args.asset.endswith((".usd", ".usda", ".usdc", ".usdz")):
    usd_path, asset_id = str(Path(args.asset).resolve()), Path(args.asset).stem
else:
    asset_id = args.asset
    usd_path = json.loads((QUEUE_DIR / f"{asset_id}.json").read_text())["file"]
out_dir = (args.out or OUT_ROOT / asset_id).resolve()
frames_dir = out_dir / "frames"
shutil.rmtree(frames_dir, ignore_errors=True)
frames_dir.mkdir(parents=True)

from isaacsim import SimulationApp  # noqa: E402

app = SimulationApp({"headless": True, "width": args.width, "height": args.height})

import omni.physx  # noqa: E402
import omni.replicator.core as rep  # noqa: E402
import omni.usd  # noqa: E402
from pxr import Gf, Usd, UsdGeom, UsdLux, UsdPhysics  # noqa: E402

omni.usd.get_context().open_stage(usd_path)
for _ in range(20):
    app.update()
stage = omni.usd.get_context().get_stage()
px = omni.physx.get_physx_interface()
pxsim = omni.physx.get_physx_simulation_interface()
import carb  # noqa: E402
PUSH: dict = {}         # a push on a pushed base: force on the root link each step
PHYSICS_HZ = 60
DT = 1.0 / PHYSICS_HZ


# --- what moves ---------------------------------------------------------------

def unit_rot(m: Gf.Matrix4d) -> Gf.Rotation:
    m = Gf.Matrix4d(m)
    m.Orthonormalize()
    return m.ExtractRotation()


def _authored_drive(prim, revolute):
    d = UsdPhysics.DriveAPI.Get(prim, "angular" if revolute else "linear")
    if not d:
        return None
    return (d.GetStiffnessAttr().Get() or 0.0, d.GetDampingAttr().Get() or 0.0)


xf = UsdGeom.XformCache(Usd.TimeCode.Default())
joints = []
for prim in stage.Traverse():
    if not (prim.IsA(UsdPhysics.RevoluteJoint) or prim.IsA(UsdPhysics.PrismaticJoint)):
        continue
    j = UsdPhysics.Joint(prim)
    b0, b1 = j.GetBody0Rel().GetTargets(), j.GetBody1Rel().GetTargets()
    if not b0 or not b1:
        continue
    revolute = prim.IsA(UsdPhysics.RevoluteJoint)
    lower = prim.GetAttribute("physics:lowerLimit").Get()
    upper = prim.GetAttribute("physics:upperLimit").Get()
    unlimited = lower is None or upper is None or not math.isfinite(lower) or not math.isfinite(upper)
    if unlimited:                       # a spindle, a wheel, a caster: swept a turn each way
        lower, upper = (-180.0, 180.0) if revolute else (-0.1, 0.1)
    gate = prim.GetCustomDataByKey("simReady:gate")
    w0, w1 = xf.GetLocalToWorldTransform(stage.GetPrimAtPath(b0[0])), xf.GetLocalToWorldTransform(stage.GetPrimAtPath(b1[0]))
    joints.append({
        "name": prim.GetName(), "path": str(prim.GetPath()), "revolute": revolute,
        "axis": {"X": 0, "Y": 1, "Z": 2}[prim.GetAttribute("physics:axis").Get() or "X"],
        "lower": float(lower), "upper": float(upper),
        "body0": str(b0[0]), "body1": str(b1[0]),
        # bodies carry the wrapper's scale; PhysX poses do not, so keep it
        "scale0": [Gf.Vec3d(*w0.GetRow3(k)).GetLength() for k in range(3)],
        "scale1": [Gf.Vec3d(*w1.GetRow3(k)).GetLength() for k in range(3)],
        "lp0": Gf.Vec3d(j.GetLocalPos0Attr().Get()), "lp1": Gf.Vec3d(j.GetLocalPos1Attr().Get()),
        "lr0": Gf.Rotation(Gf.Quatd(j.GetLocalRot0Attr().Get())), "lr1": Gf.Rotation(Gf.Quatd(j.GetLocalRot1Attr().Get())),
        "follower": any(s.startswith("PhysxMimicJointAPI") for s in prim.GetAppliedSchemas()),
        "gate": json.loads(gate) if isinstance(gate, str) else gate,
        "authored": _authored_drive(prim, revolute),
        "unlimited": unlimited,
    })
# a composed scene can hold two assets with a joint of the same name
_names = [j["name"] for j in joints]
for j in joints:
    if _names.count(j["name"]) > 1:
        parts = j["path"].split("/")
        j["name"] = f"{parts[2] if len(parts) > 3 else parts[1]}/{j['name']}"
by_name = {j["name"]: j for j in joints}
by_name.update({j["path"]: j for j in joints})  # a rule names its actuator by path
# Releases (customData `simReady:releasedBy` on a fixed joint, written by
# add_mechanism.add_press_fit): a press-fitted part held until another joint
# has travelled far enough - a pipette tip until the ejector passes its
# collar. A light part on a joint is too compliant for a pusher to build
# the break force, so tools release it by rule.
releases = []
for prim in stage.Traverse():
    rel = prim.GetCustomDataByKey("simReady:releasedBy") if prim.IsA(UsdPhysics.Joint) else None
    if rel:
        rel = json.loads(rel) if isinstance(rel, str) else dict(rel)
        releases.append({"path": str(prim.GetPath()), "name": prim.GetName(), "by": rel["joint"],
                         "travel": float(rel["travel_m"]), "released_at": None})
# Behaviors (customData `simReady:behaviors` on the asset root, written by a
# drafting tier through the hub): control laws in scripts/behaviors.py that
# work the asset - a drill's trigger runs its chuck the way its switch says
behaviors = []
for prim in stage.Traverse():
    bh = prim.GetCustomDataByKey("simReady:behaviors")
    if bh:
        behaviors += json.loads(bh) if isinstance(bh, str) else list(bh)
actuators = {by_name[j["gate"]["actuator_joint"]]["path"] for j in joints if j["gate"]}
primaries = [j for j in joints if not j["follower"] and j["path"] not in actuators]
if not joints:
    raise SystemExit(f"{asset_id}: no revolute or prismatic joints to animate")


def body_pose(path: str):
    r = px.get_rigidbody_transformation(path)
    if not r.get("ret_val", True):
        return None
    q = r["rotation"]
    return Gf.Vec3d(*r["position"]), Gf.Rotation(Gf.Quatd(q[3], q[0], q[1], q[2]))


def _rot3(path):
    """World rotation of a body as a 3x3 (column vectors) from PhysX."""
    import numpy as np

    x, y, z, w = px.get_rigidbody_transformation(path)["rotation"]
    return np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                     [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                     [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])


def measure(j) -> float:
    """Joint position from the two bodies' live poses and the joint frames."""
    p0, r0 = body_pose(j["body0"])
    p1, r1 = body_pose(j["body1"])
    f0 = j["lr0"] * r0
    if j["revolute"]:
        # The child's turn relative to the parent since the start, about the
        # joint axis in the parent's frame: independent of how the meshes
        # are oriented in the model (a watch hand at 1:30 read backwards
        # through the joint frames).
        import numpy as np

        R0, R1 = _rot3(j["body0"]), _rot3(j["body1"])
        C = R0.T @ R1
        if "_C0" not in j:
            j["_C0"] = C
            q = j["lr0"].GetQuat()  # joint frame -> body0 frame (Gf rotates row vectors)
            m = Gf.Matrix4d().SetRotate(q)
            e = [0.0, 0.0, 0.0]
            e[j["axis"]] = 1.0
            j["_a0"] = np.array(list(m.TransformDir(Gf.Vec3d(*e))))
        D = C @ j["_C0"].T
        v = np.array([D[2, 1] - D[1, 2], D[0, 2] - D[2, 0], D[1, 0] - D[0, 1]])
        a = math.degrees(math.atan2(0.5 * float(v @ j["_a0"]), 0.5 * (np.trace(D) - 1.0)))
        # poses only give the angle mod 360; a screw turns many times, so
        # unwrap against the previous frame's reading
        prev = j.get("_last")
        if prev is not None:
            a += 360.0 * round((prev - a) / 360.0)
        j["_last"] = a
        return a
    a0 = p0 + r0.TransformDir(Gf.CompMult(j["lp0"], Gf.Vec3d(*j["scale0"])))
    a1 = p1 + r1.TransformDir(Gf.CompMult(j["lp1"], Gf.Vec3d(*j["scale1"])))
    axis_w = f0.TransformDir(Gf.Vec3d(*[1.0 if k == j["axis"] else 0.0 for k in range(3)]))
    return Gf.Dot(a1 - a0, axis_w)


def separation(j) -> float:
    """How far a joint's two halves have come apart (m): its anchor seen from
    each body. A joint holds to millimetres; centimetres mean the asset is
    flying apart (colliders fighting the joints), and its frames show the
    floor while the parts tumble out of view."""
    b0, b1 = body_pose(j["body0"]), body_pose(j["body1"])
    if not b0 or not b1:
        return 0.0
    a0 = b0[0] + b0[1].TransformDir(Gf.CompMult(j["lp0"], Gf.Vec3d(*j["scale0"])))
    a1 = b1[0] + b1[1].TransformDir(Gf.CompMult(j["lp1"], Gf.Vec3d(*j["scale1"])))
    d = a1 - a0
    if not j["revolute"]:
        axis_w = (j["lr0"] * b0[1]).TransformDir(Gf.Vec3d(*[1.0 if k == j["axis"] else 0.0 for k in range(3)]))
        d = d - axis_w * Gf.Dot(d, axis_w)
    return d.GetLength()


# --- drives (session layer only: the asset file is never changed) --------------

def set_drive(j, target, stiffness=None, damping=None, max_force=None):
    with Usd.EditContext(stage, stage.GetSessionLayer()):
        prim = stage.GetPrimAtPath(j["path"])
        d = UsdPhysics.DriveAPI.Apply(prim, "angular" if j["revolute"] else "linear")
        d.CreateTargetPositionAttr().Set(float(target))
        if stiffness is not None:
            d.CreateStiffnessAttr().Set(float(stiffness))
        if damping is not None:
            d.CreateDampingAttr().Set(float(damping))
        if max_force is not None:
            d.CreateMaxForceAttr().Set(float(max_force))


def base_root():
    """The wheeled base's root link: a parent in the joint tree, never a child."""
    parents = {j["body0"] for j in joints}
    children = {j["body1"] for j in joints}
    return sorted(parents - children)[0]


def base_pose(path):
    """(position, yaw deg about Z) of a body from PhysX."""
    r = px.get_rigidbody_transformation(path)
    x, y, z, w = r["rotation"]
    yaw = math.degrees(math.atan2(2 * (w * z + x * y), 1 - 2 * (y * y + z * z)))
    return (list(r["position"]), yaw)


def set_velocity(j, velocity, damping=5.0, max_force=50.0):
    """A velocity drive (a motor): no spring, damping toward the speed."""
    with Usd.EditContext(stage, stage.GetSessionLayer()):
        prim = stage.GetPrimAtPath(j["path"])
        d = UsdPhysics.DriveAPI.Apply(prim, "angular" if j["revolute"] else "linear")
        d.CreateStiffnessAttr().Set(0.0)
        d.CreateDampingAttr().Set(float(damping))
        d.CreateMaxForceAttr().Set(float(max_force))
        d.CreateTargetVelocityAttr().Set(float(velocity))


def gains(j):
    # stiff enough to track a smooth sweep; per degree for revolute drives
    return (30.0, 3.0) if j["revolute"] else (2.0e4, 400.0)


def rest_of(j):
    return min(max(0.0, j["lower"]), j["upper"])


def ease(a, b, u):
    u = min(1.0, max(0.0, u))
    return a + (b - a) * u * u * (3 - 2 * u)


# --- the plan: a list of (seconds, {joint: (target_fn(u), force_cap)}) --------

segments = []  # each: dict(seconds, label, moves={name: (start, end, cap)})
T = args.seconds_per_joint
for j in primaries:
    rest = rest_of(j)
    far = j["upper"] if abs(j["upper"] - rest) >= abs(j["lower"] - rest) else j["lower"]
    if j["gate"] and j["gate"].get("mechanism") == "rule":
        # no physical lock: the tool is the lock. The gated joint is held at
        # rest (a refused command is logged) until the actuator engages; then
        # the controller works it, as an elevator opens on its call button.
        a = by_name[j["gate"]["actuator_joint"]]
        engage = float(j["gate"]["engage"])
        segments += [
            {"seconds": 1.0, "label": f"{j['name']}: commanded with {a['name']} at rest (rule refuses)",
             "moves": {}, "rule_refused": j["name"]},
            {"seconds": 0.3, "label": f"{a['name']} pressed", "moves": {a["name"]: (rest_of(a), engage, None)}},
            {"seconds": 0.3, "label": f"{a['name']} released", "moves": {a["name"]: (engage, rest_of(a), None)},
             "authored_gains": [a["name"]]},
            {"seconds": 0.4 * T, "label": f"{j['name']} opens (rule satisfied)", "moves": {j["name"]: (rest, far, None)}},
            {"seconds": 1.0, "label": "hold open", "moves": {}},
            {"seconds": 0.4 * T, "label": f"{j['name']} closes", "moves": {j["name"]: (far, rest, None)}},
            {"seconds": 0.8, "label": "closed", "moves": {}},
        ]
    elif j["gate"]:
        a = by_name[j["gate"]["actuator_joint"]]
        engage = float(j["gate"]["engage"])
        try_cap = args.push_torque if j["revolute"] else args.push_force
        mid = rest + 0.6 * (far - rest)
        segments += [
            {"seconds": 1.0, "label": f"{j['name']}: at rest", "moves": {}},
            {"seconds": 1.5, "label": f"{j['name']}: pushed with {a['name']} at rest (gate check)",
             "moves": {j["name"]: (rest, mid, try_cap)}, "gate_check": j["name"]},
            {"seconds": 0.6, "label": f"{a['name']} engaged", "moves": {a["name"]: (rest_of(a), engage, None)}},
            {"seconds": 0.4 * T, "label": f"{j['name']} opens", "moves": {j["name"]: (rest, far, None)}},
            # released onto its own spring, so a closing latch can cam it back
            {"seconds": 0.5, "label": f"{a['name']} released", "moves": {a["name"]: (engage, rest_of(a), None)},
             "authored_gains": [a["name"]]},
            {"seconds": 0.8, "label": "hold open", "moves": {}},
            # aim past closed, as a door closer's preload does: the limit
            # stops the leaf and the leftover torque finishes camming the latch
            {"seconds": 0.4 * T, "label": f"{j['name']} closes (re-latches)",
             "moves": {j["name"]: (far, rest - 0.1 * (far - rest), None)}},
            {"seconds": 1.0, "label": "closed", "moves": {}},
        ]
    elif not j["revolute"] and abs(j["upper"] - j["lower"]) <= 0.01:
        # a button or a short slider: a quick press, not a slow sweep (a
        # remote has dozens)
        segments += [
            {"seconds": 0.3, "label": f"{j['name']} pressed", "moves": {j["name"]: (rest, far, None)}},
            {"seconds": 0.2, "label": "hold", "moves": {}},
            {"seconds": 0.3, "label": f"{j['name']} released", "moves": {j["name"]: (far, rest, None)}},
        ]
    else:
        segments += [
            {"seconds": 0.5, "label": f"{j['name']}: at rest", "moves": {}},
            {"seconds": 0.4 * T, "label": f"{j['name']} to {far:g}", "moves": {j["name"]: (rest, far, None)}},
            {"seconds": 0.5, "label": "hold", "moves": {}},
            {"seconds": 0.4 * T, "label": f"{j['name']} back", "moves": {j["name"]: (far, rest, None)}},
        ]
        # the other way too, where the joint has real travel that way (a pair
        # of pliers opens AND closes; closing is where the jaws meet)
        near = j["lower"] if far == j["upper"] else j["upper"]
        if abs(near - rest) >= max(0.15 * abs(far - rest), 1.0 if j["revolute"] else 0.002):
            segments += [
                {"seconds": 0.4 * T, "label": f"{j['name']} to {near:g}", "moves": {j["name"]: (rest, near, None)}},
                {"seconds": 0.5, "label": "hold", "moves": {}},
                {"seconds": 0.4 * T, "label": f"{j['name']} back", "moves": {j["name"]: (near, rest, None)}},
            ]

# the behaviors, worked as a user would: each scenario sets the inputs, the
# law drives the output from the inputs PhysX measures, and the output's
# speed is checked against the law (sign and size)
for bi, b in enumerate(behaviors):
    if b.get("type") != "motor" or b.get("output") not in by_name or b.get("throttle") not in by_name:
        continue
    t_full = 0.25 * float(b["throttle_full"])          # a quarter squeeze: speed follows the trigger
    d_name, d_fwd = b.get("direction"), float(b.get("direction_forward") or 0.0)
    if d_name not in by_name:
        d_name = None

    def scenario(name, switch_to, battery_off=False):
        segs = []
        if d_name:
            segs.append({"seconds": 0.4, "label": f"motor: switch to {name}",
                         "moves": {d_name: (0.0, switch_to, None)}, "behavior": {"i": bi, "scenario": name}})
        segs += [
            {"seconds": 0.3, "label": f"motor ({name}): battery off" if battery_off else f"motor ({name})",
             "moves": {}, "behavior": {"i": bi, "scenario": name, "battery_off": battery_off}},
            {"seconds": 0.5, "label": f"motor ({name}): trigger squeezed",
             "moves": {b["throttle"]: (0.0, t_full, None)}, "behavior": {"i": bi, "scenario": name}},
            {"seconds": 1.0, "label": f"motor ({name}): running", "moves": {},
             "behavior": {"i": bi, "scenario": name, "measure": True}},
            {"seconds": 0.4, "label": f"motor ({name}): trigger released",
             "moves": {b["throttle"]: (t_full, 0.0, None)}, "behavior": {"i": bi, "scenario": name}},
        ]
        if d_name:
            segs.append({"seconds": 0.3, "label": f"motor ({name}): switch centred",
                         "moves": {d_name: (switch_to, 0.0, None)}, "behavior": {"i": bi, "scenario": name}})
        return segs

    segments += scenario("forward", d_fwd)
    if d_name:
        segments += scenario("reverse", -d_fwd)
        segments += scenario("locked", 0.0)
    if b.get("power"):
        segments += scenario("unpowered", d_fwd, battery_off=True)


# a wheeled base: driven forward, then turned on the spot; the base's own
# motion (not the wheels') is what is checked
for bi, b in enumerate(behaviors):
    if b.get("type") != "wheeled_base":
        continue
    if b.get("pushed"):
        segments += [
            {"seconds": 2.0, "label": "pushed base: pushed forward", "moves": {},
             "behavior": {"i": bi, "scenario": "pushed", "drive": [0.0, 0.0], "measure": True, "push": 0.5}},
            {"seconds": 1.0, "label": "pushed base: let go", "moves": {},
             "behavior": {"i": bi, "scenario": "pushed_stop", "drive": [0.0, 0.0]}},
        ]
        continue
    for name, v, w in (("forward", 0.3, 0.0), ("turn_left", 0.0, 0.5)):
        segments += [
            {"seconds": 2.0, "label": f"wheeled base: {name}", "moves": {},
             "behavior": {"i": bi, "scenario": name, "drive": [v, w], "measure": True}},
            {"seconds": 0.8, "label": f"wheeled base: stop", "moves": {},
             "behavior": {"i": bi, "scenario": name + "_stop", "drive": [0.0, 0.0]}},
        ]


# --- scene dressing and camera (session layer) --------------------------------

bbox = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.default_, UsdGeom.Tokens.render])
rng = bbox.ComputeWorldBound(stage.GetDefaultPrim() or stage.GetPseudoRoot()).ComputeAlignedRange()
lo, hi = rng.GetMin(), rng.GetMax()
center = (lo + hi) * 0.5
size = hi - lo
radius = 0.5 * size.GetLength()
# Room for the moving parts: a door swings out by its own width. Only a leaf
# that swings does: the whole footprint as reach framed a multimeter whose
# dial turns in place at twice the distance, its dial a few pixels wide.
reach = 0.0
for _j in primaries:
    if _j["revolute"] and not _j["unlimited"] and max(abs(_j["lower"]), abs(_j["upper"])) >= 30.0:
        _r = bbox.ComputeWorldBound(stage.GetPrimAtPath(_j["body1"])).ComputeAlignedRange().GetSize()
        reach = max(reach, max(_r[0], _r[1], _r[2]))
reach = min(reach, max(size[0], size[1], size[2]))
with Usd.EditContext(stage, stage.GetSessionLayer()):
    UsdLux.DomeLight.Define(stage, "/AnimView/Dome").CreateIntensityAttr(1200)
    key = UsdLux.DistantLight.Define(stage, "/AnimView/Key")
    key.CreateIntensityAttr(2500)
    UsdGeom.XformCommonAPI(key.GetPrim()).SetRotate(Gf.Vec3f(-45, 0, -35))
    floor = UsdGeom.Mesh.Define(stage, "/AnimView/Floor")  # visual only: no collider
    f = 4.0 * (radius + max(size[0], size[1]))  # room to drive off on
    z0 = lo[2] - 0.001
    floor.CreatePointsAttr([(center[0] - f, center[1] - f, z0), (center[0] + f, center[1] - f, z0),
                            (center[0] + f, center[1] + f, z0), (center[0] - f, center[1] + f, z0)])
    floor.CreateFaceVertexCountsAttr([4])
    floor.CreateFaceVertexIndicesAttr([0, 1, 2, 3])
    floor.CreateDisplayColorAttr([(0.35, 0.35, 0.37)])
    # An asset that is not anchored to the world needs something to stand on;
    # an anchored one (a door frame) does not, and a collider flush with a
    # leaf's bottom edge would drag on it.
    anchored = any(
        p.IsA(UsdPhysics.Joint) and not UsdPhysics.Joint(p).GetBody0Rel().GetTargets()
        and UsdPhysics.Joint(p).GetBody1Rel().GetTargets() for p in stage.Traverse())
    # A loose asset much taller than its footprint (a pipette on its end)
    # topples before anything moves, and one whose main body is carried up
    # by other parts (a watch case above its hanging strap) lands face down:
    # hold its root link still instead, as a hand or a stand would.
    art = [UsdPhysics.Joint(p) for p in stage.Traverse() if p.IsA(UsdPhysics.Joint)
           and not (p.GetAttribute("physics:excludeFromArticulation").Get() or False)]
    parents = {str(t) for j in art for t in j.GetBody0Rel().GetTargets()}
    children = {str(t) for j in art for t in j.GetBody1Rel().GetTargets()}
    root_links = sorted(parents - children)
    root_floats = bool(root_links) and \
        bbox.ComputeWorldBound(stage.GetPrimAtPath(root_links[0])).ComputeAlignedRange().GetMin()[2] \
        > lo[2] + 0.3 * size[2]
    rolls = any(b.get("type") == "wheeled_base" for b in behaviors)   # it must be free to drive
    # a power tool is run in a hand: loose on the floor, a chuck spun to 2250
    # deg/s flings the body by reaction (electric_drill_1 left the scene)
    in_hand = any(b.get("type") == "motor" for b in behaviors)
    if not anchored and not rolls and (args.hold or in_hand or size[2] > 1.5 * max(size[0], size[1]) or root_floats):
        if root_links:
            UsdPhysics.FixedJoint.Define(stage, "/AnimView/Hold").CreateBody1Rel().SetTargets([root_links[0]])
            anchored = True
    if not anchored:
        ground = UsdGeom.Cube.Define(stage, "/AnimView/Ground")
        ground.CreateSizeAttr(1.0)
        UsdGeom.XformCommonAPI(ground.GetPrim()).SetTranslate(Gf.Vec3d(center[0], center[1], z0 - 0.05))
        UsdGeom.XformCommonAPI(ground.GetPrim()).SetScale(Gf.Vec3f(2 * f, 2 * f, 0.1))
        ground.CreateVisibilityAttr("invisible")
        UsdPhysics.CollisionAPI.Apply(ground.GetPrim())
    cam = UsdGeom.Camera.Define(stage, "/AnimView/Camera")
    cam.CreateFocalLengthAttr(18.0)
    cam.CreateHorizontalApertureAttr(20.955)
    cam.CreateClippingRangeAttr(Gf.Vec2f(0.01, 1000.0))
cam_xf = UsdGeom.Xformable(cam.GetPrim())


def view_azimuth() -> float:
    """Look from the side a revolute leaf swings AWAY from, so it opens into
    view instead of edge-on; otherwise a standard 3/4 view."""
    for j in primaries:
        if j["revolute"] and j["axis"] == 2:
            far = j["upper"] if abs(j["upper"]) >= abs(j["lower"]) else j["lower"]
            p1 = body_pose(j["body1"])
            if p1:
                # swing direction of the child's centre about the hinge
                c1 = bbox.ComputeWorldBound(stage.GetPrimAtPath(j["body1"])).ComputeAlignedRange().GetMidpoint()
                hinge = xf.GetLocalToWorldTransform(stage.GetPrimAtPath(j["body0"])).Transform(j["lp0"])
                r = (c1[0] - hinge[0], c1[1] - hinge[1])
                swing = (-r[1], r[0]) if far > 0 else (r[1], -r[0])
                return math.degrees(math.atan2(-swing[1], -swing[0])) + 30.0
    return -55.0


AZ0 = view_azimuth()
hfov = 2 * math.atan(20.955 / (2 * 18.0))
# fit the sphere around the asset and its swing to the frame's shorter side
vfov = 2 * math.atan(math.tan(hfov / 2) * args.height / args.width)
DIST = 1.2 * (radius + 0.5 * reach) / math.tan(min(hfov, vfov) / 2)
ELEV = math.radians(50)  # high enough that swings read from above, not edge-on


def place_camera(u: float):
    az = math.radians(AZ0 - 10 + 20 * u)  # a gentle orbit: before/after frames stay comparable
    target = Gf.Vec3d(center[0], center[1], lo[2] + 0.5 * size[2])
    eye = target + Gf.Vec3d(DIST * math.cos(ELEV) * math.cos(az), DIST * math.cos(ELEV) * math.sin(az), DIST * math.sin(ELEV))
    CAM[:] = [list(eye), list(target)]
    with Usd.EditContext(stage, stage.GetSessionLayer()):
        cam_xf.ClearXformOpOrder()
        cam_xf.AddTransformOp().Set(Gf.Matrix4d().SetLookAt(eye, target, Gf.Vec3d(0, 0, 1)).GetInverse())


# whether the asset holds together and stays where the camera looks
INTEGRITY = {"sep": {}, "root": (sorted({j["body0"] for j in joints} - {j["body1"] for j in joints}) or [None])[0]}
CAM: list = []          # the camera of the frame being rendered: [eye, target]
CAMS: list = []         # one a saved frame, for critics that mark points on the frames
place_camera(0.0)
# each joint's pivot at rest, in world space (motion_critic marks it on the frames)
PIVOTS = {j["name"]: list(xf.GetLocalToWorldTransform(stage.GetPrimAtPath(j["body0"])).Transform(j["lp0"]))
          for j in joints}
render_product = rep.create.render_product("/AnimView/Camera", (args.width, args.height))
rgb = rep.AnnotatorRegistry.get_annotator("rgb")
rgb.attach([render_product])

# --- run ------------------------------------------------------------------------

for j in joints:
    if j["follower"]:
        continue
    k, c = j["authored"] if j["name"] in actuators and j["authored"] else gains(j)
    set_drive(j, rest_of(j), k, c, 1.0e6)
px.start_simulation()
clock = 0.0
for _ in range(PHYSICS_HZ):  # settle at rest before the first frame
    px.update_simulation(DT, clock)
    clock += DT

from PIL import Image  # noqa: E402

total = sum(s["seconds"] for s in segments)
steps_per_frame = max(1, round(PHYSICS_HZ / args.fps))
log_rows, frame_i, elapsed = [], 0, 0.0
commanded = {j["name"]: rest_of(j) for j in joints}
gate_results = {}
behavior_results, powered_state, beh_track = {}, {}, None
sys.path.insert(0, str(Path(__file__).resolve().parent))
# rule gates: the gated joint's commands are refused until its actuator has
# travelled `engage` from rest (tracked from what PhysX measures, not from
# what was commanded)
rules = {j["path"]: {"actuator": by_name[j["gate"]["actuator_joint"]], "engage": float(j["gate"]["engage"]),
                     "engaged_at": None, "refused": 0, "moved_while_locked": 0.0}
         for j in joints if j["gate"] and j["gate"].get("mechanism") == "rule"}
# the controller holds a rule-gated joint shut from the start (a door with
# no spring of its own would otherwise swing)
for path in rules:
    g = by_name[path]
    set_drive(g, rest_of(g), *gains(g), 1.0e6)
for seg in segments:
    n = max(1, round(seg["seconds"] * args.fps))
    for name in list(seg["moves"]):
        r = rules.get(by_name[name]["path"])
        if r and r["engaged_at"] is None:
            r["refused"] += 1
            seg = dict(seg, moves={k: v for k, v in seg["moves"].items() if k != name},
                       label=seg["label"] + " - REFUSED, rule not satisfied")
    if seg.get("rule_refused"):
        # the command a careless caller would send: it is refused and logged
        rules[by_name[seg["rule_refused"]]["path"]]["refused"] += 1
    for name, (_, _, cap) in seg["moves"].items():
        jn = by_name[name]
        k, c = jn["authored"] if name in seg.get("authored_gains", ()) and jn["authored"] else gains(jn)
        set_drive(jn, commanded[name], k, c, cap if cap is not None else 1.0e6)
    beh = seg.get("behavior")
    if beh:
        bdef = behaviors[beh["i"]]
        res = behavior_results.setdefault(f"{bdef['type']}:{bdef.get('output', 'base')}", {})
        if beh.get("battery_off") and bdef.get("power"):
            # the battery pulled off: its press-fit lets go, the tool is unpowered
            pf = next((p for p in stage.Traverse() if p.GetName() == bdef["power"]), None)
            if pf is not None:
                with Usd.EditContext(stage, stage.GetSessionLayer()):
                    pf.GetAttribute("physics:jointEnabled").Set(False)
            powered_state[beh["i"]] = False
        if bdef["type"] == "wheeled_base":
            from behaviors import wheeled_base as _wb
            v, w = beh["drive"]
            for jn, vel in _wb(bdef, v, w).items():
                if jn in by_name:
                    set_velocity(by_name[jn], vel, damping=50.0, max_force=1.0e4)
            for jn in bdef.get("casters", []):
                # casters roll and swivel free: the joint sweep left position
                # drives on them, which held them straight (they scrubbed)
                for k in (jn, f"caster_swivel_{jn}"):
                    if k in by_name:
                        set_velocity(by_name[k], 0.0, damping=0.0, max_force=0.0)
            PUSH.clear()
            if beh.get("push"):
                # a hand on the frame: a force for push m/s^2 on the whole
                # asset's mass, at the root link's middle, along forward
                root = base_root()
                total = sum(float(UsdPhysics.MassAPI(q).GetMassAttr().Get() or 0.0) for q in stage.Traverse()
                            if q.HasAPI(UsdPhysics.MassAPI)) or 50.0
                f = [beh["push"] * total * float(x) for x in bdef.get("forward", [1, 0, 0])]
                from pxr import PhysicsSchemaTools
                PUSH.update(path=PhysicsSchemaTools.sdfPathToInt(root), force=carb.Float3(*f),
                            stage=omni.usd.get_context().get_stage_id())
            if beh.get("measure"):
                root = base_root()
                beh_track = {"scenario": beh["scenario"], "root": root, "p0": base_pose(root), "t0": clock,
                             "up0": _rot3(root)[:, 2].copy()}
        elif beh.get("measure"):
            out_j = by_name[bdef["output"]]
            beh_track = {"scenario": beh["scenario"], "start": measure(out_j), "t0": clock,
                         "trigger_cmd": commanded[bdef["throttle"]]}
    peak = 0.0
    for i in range(n):
        u = (i + 1) / n
        for name, (a, b, _) in seg["moves"].items():
            commanded[name] = ease(a, b, u)
            set_drive(by_name[name], commanded[name])
        if beh and behaviors[beh["i"]]["type"] == "motor":
            from behaviors import motor as _motor
            bdef = behaviors[beh["i"]]
            q = {k: measure(by_name[k]) for k in (bdef["throttle"], bdef.get("direction")) if k in by_name}
            cmd = _motor(bdef, q, powered_state.get(beh["i"], True))
            if bdef.get("direction") in by_name and cmd["_state"]["direction"] == 0.0:
                # centred: the switch locks the trigger - the squeeze is refused
                commanded[bdef["throttle"]] = 0.0
                set_drive(by_name[bdef["throttle"]], 0.0)
            set_velocity(by_name[bdef["output"]], cmd[bdef["output"]])
        for _ in range(steps_per_frame):
            if PUSH:
                c = base_pose(base_root())[0]
                pxsim.apply_force_at_pos(PUSH["stage"], PUSH["path"], PUSH["force"], carb.Float3(*c), "Force")
            px.update_simulation(DT, clock)
            clock += DT
        # stepping PhysX directly does not publish poses to the renderer
        px.update_transformations(False, True, False, False)
        place_camera(min(1.0, (elapsed + i / args.fps) / total))
        rep.orchestrator.step(rt_subframes=2, delta_time=0.0, pause_timeline=False)
        Image.fromarray(rgb.get_data()[:, :, :3]).save(frames_dir / f"f{frame_i:05d}.png")
        CAMS.append([list(CAM[0]), list(CAM[1])])
        measured = {j["name"]: measure(j) for j in joints}
        for j in joints:
            INTEGRITY["sep"][j["name"]] = max(INTEGRITY["sep"].get(j["name"], 0.0), separation(j))
        if INTEGRITY["root"]:
            pr = body_pose(INTEGRITY["root"])
            if pr:
                INTEGRITY.setdefault("p0", pr[0])
                INTEGRITY["moved"] = max(INTEGRITY.get("moved", 0.0), (pr[0] - INTEGRITY["p0"]).GetLength())
        for path, r in rules.items():
            a = r["actuator"]
            if r["engaged_at"] is None:
                if abs(measured[a["name"]] - rest_of(a)) >= 0.9 * abs(r["engage"]):
                    r["engaged_at"] = round(frame_i / args.fps, 3)
                else:
                    g = by_name[path]
                    r["moved_while_locked"] = max(r["moved_while_locked"], abs(measured[g["name"]] - rest_of(g)))
        for r in releases:
            if r["released_at"] is None and r["by"] in measured and abs(measured[r["by"]]) >= r["travel"]:
                with Usd.EditContext(stage, stage.GetSessionLayer()):
                    stage.GetPrimAtPath(r["path"]).GetAttribute("physics:jointEnabled").Set(False)
                r["released_at"] = round(frame_i / args.fps, 3)
        if seg.get("gate_check"):
            g = by_name[seg["gate_check"]]
            peak = max(peak, abs(measured[g["name"]] - rest_of(g)))
        log_rows.append({"t": round(frame_i / args.fps, 3), "segment": seg["label"],
                         **{f"{n}_cmd": round(commanded[n], 4) for n in commanded},
                         **{f"{n}_meas": round(v, 4) for n, v in measured.items()}})
        frame_i += 1
    if beh and beh.get("measure") and behaviors[beh["i"]]["type"] == "wheeled_base":
        bdef = behaviors[beh["i"]]
        res = behavior_results.setdefault("wheeled_base", {})
        p0, p1 = beh_track["p0"], base_pose(beh_track["root"])
        dt_run = clock - beh_track["t0"]
        fwd = [float(x) for x in bdef.get("forward", [1, 0, 0])]
        moved = sum((p1[0][k] - p0[0][k]) * fwd[k] for k in range(3))
        side = math.hypot(p1[0][0] - p0[0][0], p1[0][1] - p0[0][1])
        yaw = (p1[1] - p0[1] + 180.0) % 360.0 - 180.0
        v, w = beh["drive"]
        if beh["scenario"] == "pushed":
            # rolls when pushed, without tipping or spinning away
            import numpy as np
            tilt = math.degrees(math.acos(max(-1.0, min(1.0, float(np.dot(beh_track["up0"],
                                                                          _rot3(beh_track["root"])[:, 2]))))))
            ok = moved >= 0.15 and abs(yaw) < 25.0 and tilt < 15.0
            res["pushed"] = {"moved_m": round(moved, 3), "yaw_deg": round(yaw, 1), "tilt_deg": round(tilt, 1),
                             "ok": bool(ok)}
        elif beh["scenario"] == "forward":
            want = v * dt_run
            ok = 0.6 * want <= moved <= 1.4 * want and abs(yaw) < 15.0
            res["forward"] = {"moved_m": round(moved, 3), "want_m": round(want, 3), "yaw_deg": round(yaw, 1),
                              "ok": bool(ok)}
        else:
            want = math.degrees(w * dt_run)
            ok = 0.5 * want <= yaw <= 1.5 * want and side < 0.25
            res[beh["scenario"]] = {"yaw_deg": round(yaw, 1), "want_deg": round(want, 1),
                                    "drift_m": round(side, 3), "ok": bool(ok)}
    elif beh and beh.get("measure"):
        from behaviors import motor as _motor
        bdef = behaviors[beh["i"]]
        out_j = by_name[bdef["output"]]
        dt_run = clock - beh_track["t0"]
        got = (measure(out_j) - beh_track["start"]) / max(dt_run, 1e-6)
        trig = measure(by_name[bdef["throttle"]])
        q = {bdef["throttle"]: trig}
        if bdef.get("direction") in by_name:
            q[bdef["direction"]] = measure(by_name[bdef["direction"]])
        want = _motor(bdef, q, powered_state.get(beh["i"], True))[bdef["output"]]
        squeeze = 0.25 * float(bdef["throttle_full"])
        scen = beh["scenario"]
        if scen in ("forward", "reverse"):
            sign = (1.0 if scen == "forward" else -1.0) * float(bdef.get("forward_sign", 1.0))
            ok = got * sign > 0 and 0.5 * abs(want) <= abs(got) <= 1.5 * abs(want) and abs(want) > 0
        else:
            ok = abs(got) < 0.05 * float(bdef.get("max_rpm", 1500.0)) * 6.0 * 0.25
        res[scen] = {"chuck_deg_s": round(got, 1), "law_deg_s": round(want, 1),
                     "trigger_travel": round(trig, 5), "trigger_asked": round(squeeze, 5), "ok": bool(ok)}
        if scen == "locked":
            res[scen]["trigger_blocked"] = abs(trig) < 0.2 * abs(squeeze)
            res[scen]["ok"] = bool(ok and res[scen]["trigger_blocked"])
    if seg.get("gate_check"):
        g = by_name[seg["gate_check"]]
        tried = abs(seg["moves"][g["name"]][1] - rest_of(g))
        gate_results[g["name"]] = {"tried": round(tried, 3), "moved": round(peak, 3),
                                   "held": peak < 0.1 * tried,
                                   "push": args.push_torque if g["revolute"] else args.push_force}
        # free the joint again for the real motion
        set_drive(g, rest_of(g), *gains(g), 1.0e6)
        commanded[g["name"]] = seg["moves"][g["name"]][0]
    elapsed += seg["seconds"]

# --- evidence -------------------------------------------------------------------

with open(out_dir / "joints.csv", "w", newline="") as fh:
    w = csv.DictWriter(fh, fieldnames=list(log_rows[0]))
    w.writeheader()
    w.writerows(log_rows)
summary = {"asset": asset_id, "usd": usd_path, "frames": frame_i, "fps": args.fps,
           "joints": {}, "gates": gate_results,
           "releases": {r["name"]: {"by": r["by"], "at_travel": r["travel"], "released_at_s": r["released_at"]}
                        for r in releases}}
for path, r in rules.items():
    summary["gates"][by_name[path]["name"]] = {
        "mechanism": "rule", "actuator": r["actuator"]["path"], "engage": r["engage"],
        "refused_commands_before": r["refused"], "moved_while_locked": round(r["moved_while_locked"], 4),
        "engaged_at_s": r["engaged_at"]}
for j in joints:
    meas = [r[f"{j['name']}_meas"] for r in log_rows]
    entry = {"type": "revolute" if j["revolute"] else "prismatic", "limits": [j["lower"], j["upper"]],
             "unlimited": j["unlimited"],
             "measured_range": [round(min(meas), 4), round(max(meas), 4)], "follower": j["follower"]}
    if not j["follower"]:
        # tracking error outside gate checks, where the joint is meant to be stopped
        # against the target the limits allow (a closer preload aims past them)
        clamp = lambda v: min(max(v, j["lower"]), j["upper"])  # noqa: E731
        errs = [abs(r[f"{j['name']}_meas"] - clamp(r[f"{j['name']}_cmd"])) for r in log_rows
                if "gate check" not in r["segment"]]
        entry["max_tracking_error"] = round(max(errs), 4)
        entry["final_error"] = round(errs[-1], 4)
    summary["joints"][j["name"]] = entry
summary["behaviors"] = behavior_results
_worst = max(INTEGRITY["sep"].items(), key=lambda kv: kv[1], default=(None, 0.0))
_moved = INTEGRITY.get("moved", 0.0)
# a wheeled base drives off on purpose; anything else that travels more than
# its own size (or 10 cm) has been thrown, and its frames miss it
_away = 0.0 if any(b.get("type") == "wheeled_base" for b in behaviors) else _moved
summary["integrity"] = {
    "max_joint_separation_m": round(_worst[1], 4), "worst_joint": _worst[0],
    "root_moved_m": round(_moved, 4),
    "ok": bool(_worst[1] < max(0.01, 0.05 * radius) and _away < max(0.1, 2 * radius))}
summary["camera"] = {"hfov_deg": math.degrees(hfov), "width": args.width, "height": args.height, "up": [0, 0, 1],
                     "frames": CAMS}
for name, p in PIVOTS.items():
    if name in summary["joints"]:
        summary["joints"][name]["pivot_world"] = p
(out_dir / "summary.json").write_text(json.dumps(summary, indent=1))

mp4 = out_dir / f"{asset_id}.mp4"
ff = shutil.which("ffmpeg") or "ffmpeg"
subprocess.run([ff, "-v", "error", "-y", "-framerate", str(args.fps), "-i", str(frames_dir / "f%05d.png"),
                "-c:v", "libx264", "-preset", "slow", "-crf", "20", "-pix_fmt", "yuv420p",
                "-movflags", "+faststart", str(mp4)], check=True)
picks = [round(frame_i * f) for f in (0.05, 0.25, 0.45, 0.6, 0.8, 0.97)]
tiles = [Image.open(frames_dir / f"f{min(p, frame_i - 1):05d}.png").resize((args.width // 2, args.height // 2)) for p in picks]
sheet = Image.new("RGB", (3 * tiles[0].width, 2 * tiles[0].height))
for i, t in enumerate(tiles):
    sheet.paste(t, ((i % 3) * t.width, (i // 3) * t.height))
sheet.save(out_dir / "contact_sheet.png")
print("ANIMATION", json.dumps({"mp4": str(mp4), **summary}, default=str), flush=True)
app.close()
