#!/usr/bin/env python3
"""Replay a recorded robot grasp in a built scene and film it (Kit).

The spec's grasp_replay names a recording: per-tick targets for the arm and
the hand's actuated channels (HomeHero's R5 reference pickup: 8 left-arm
joints, then index, middle, ring, pinky, thumb flexion, thumb rotation), at
control_hz. Each tick the targets go through the spec's controller (HomeHero's
gravity-compensated R5 action layer, or plain position targets) to PhysX's
articulation view - finger
distal joints follow their proximal ones by the linkage coefficient, the
shoulder lift is raised by lift_offset_m (this table is higher than the
recording's) - and physics runs 1/control_hz of simulated time. Then the
final targets are held for hold_s.

Filmed every tick from three cameras: a close-up on the grasp with the
robot's head camera inset (grasp.mp4) and the room overview
(grasp_overview.mp4). Measured: how high the target rose and whether it
stayed with the palm through the hold.

    source scripts/isaac_slot.sh && <isaac python.sh> scripts/replay_grasp.py workspace/scenes/<name>
writes <scene>/grasp/{grasp.mp4, grasp_overview.mp4, contact_sheet.png, grasp.json, log.json}
and prints a GRASP PASS|FAIL line.
"""
from __future__ import annotations

import argparse
import json
import math
import shutil
import subprocess
import sys
from pathlib import Path

ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
ap.add_argument("scene_dir")
ap.add_argument("--subframes", type=int, default=4)
ap.add_argument("--pathtrace", action="store_true")
ap.add_argument("--spp", type=int, default=16)
ap.add_argument("--settle", type=float, default=1.5)
ap.add_argument("--no-video", action="store_true", help="physics and the outcome only (minutes, not half an hour)")
ap.add_argument("--contacts", action="store_true", help="log which robot links carry contact force each tick (what blocks the arm)")
ap.add_argument("--allow-stale-spec", action="store_true", help="replay even when the scene's spec.json no longer matches its config")
ap.add_argument("--max-ticks", type=int, default=0, help="stop after this many ticks (a diagnosis, not a result)")
ap.add_argument("--from-stage", default=None,
                help="a rig, not a result: pose the robot at the first row of this plan stage (arm and hand set, the hand's firmware "
                     "reset there) and replay from it - the close, lift and the tool's use in minutes, without the reach")
ap.add_argument("--frame-every", type=int, default=1, help="capture a frame every N ticks only (frames kept, no video)")
ap.add_argument("--no-live-plan", action="store_true",
                help="a cloth: replay the plan on disk instead of planning against the cloth as it rests in THIS run")
args = ap.parse_args()

scene_dir = Path(args.scene_dir).resolve()
spec = json.loads((scene_dir / "spec.json").read_text())
# the scene must be built from its config as it is now: a live plan rebuilds from
# spec.json, and a config edited since replayed the old task (2026-10-15)
_cfg_path = Path(__file__).resolve().parents[1] / "config" / "scenes" / f"{spec.get('name', '')}.json"
if _cfg_path.exists() and not args.allow_stale_spec:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from build_er_scene import config_drift, load_spec as _load_spec
    _drift = config_drift(_load_spec(_cfg_path), spec)
    if _drift:
        raise SystemExit(f"{scene_dir.name}: spec.json is stale against {_cfg_path.name} ({', '.join(_drift[:6])}): "
                         f"rebuild with plan_er_grasp.py {_cfg_path.relative_to(_cfg_path.parents[2])}, or pass --allow-stale-spec")
gr = spec["grasp_replay"]
out = scene_dir / "grasp"
frames = {k: out / f"frames_{k}" for k in ("close", "head", "overview")}
for d in frames.values():
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True)

from isaacsim import SimulationApp  # noqa: E402

app = SimulationApp({"headless": True})

import carb  # noqa: E402
import numpy as np  # noqa: E402
import omni.physics.tensors as tensors  # noqa: E402
import omni.physx  # noqa: E402
import omni.replicator.core as rep  # noqa: E402
import omni.timeline  # noqa: E402
import omni.usd  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402
from pxr import Gf, Sdf, Usd, UsdGeom, UsdPhysics  # noqa: E402

omni.usd.get_context().open_stage(str(scene_dir / "scene.usda"))
for _ in range(30):
    app.update()
stage = omni.usd.get_context().get_stage()
px = omni.physx.get_physx_interface()
if args.pathtrace:
    s = carb.settings.get_settings()
    s.set("/rtx/rendermode", "PathTracing")
    s.set("/rtx/pathtracing/spp", args.spp)
    s.set("/rtx/pathtracing/totalSpp", args.spp)
    s.set("/rtx/pathtracing/optixDenoiser/enabled", True)

LINKS = "/World/Robot/Links"
target = next(p for p in Usd.PrimRange(stage.GetPrimAtPath("/World/Objects")) if p.GetCustomDataByKey("er:target"))
target_body = next((q for q in Usd.PrimRange(target) if q.GetAttribute("physics:mass")), target)


def body_pose(path):
    r = px.get_rigidbody_transformation(path)
    if not r.get("ret_val", True):
        return None
    q = r["rotation"]
    return np.array(r["position"], float), np.array([q[3], q[0], q[1], q[2]], float)


def quat_to_R(wxyz):
    w, x, y, z = wxyz
    return np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                     [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                     [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])


def Rx(a):
    c, s = math.cos(a), math.sin(a)
    return np.array([[1, 0, 0], [0, c, -s], [0, s, c]])


def Ry(a):
    c, s = math.cos(a), math.sin(a)
    return np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]])


def Rz(a):
    c, s = math.cos(a), math.sin(a)
    return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]])


CLOTH = any("DeformableBody" in a for q in Usd.PrimRange(target) for a in q.GetAppliedSchemas())
CLOTH_MESH = next((q for q in Usd.PrimRange(target) if q.IsA(UsdGeom.Mesh) and q.GetName() != "simMesh"), None) if CLOTH else None
TRACK = {"index": None}                                   # the cloth vertex the pinch took, once it closes
# the rest shape the plan was made against (settle_cloth.py), and how long it took to reach
CLOTH_SETTLED = None
CLOTH_SETTLE_S = 0.0
if CLOTH:
    _cs = scene_dir / "cloth_settled.npz"
    if _cs.exists() and target.GetName() in np.load(_cs).files:
        CLOTH_SETTLED = np.load(_cs)[target.GetName()]
    if (scene_dir / "cloth_settled.json").exists():
        CLOTH_SETTLE_S = float(json.loads((scene_dir / "cloth_settled.json").read_text()).get("settled_s", 0.0))


def cloth_points():
    """The cloth's skinned render mesh, in world (PhysX writes it each step)."""
    xc_ = UsdGeom.XformCache()
    M = xc_.GetLocalToWorldTransform(CLOTH_MESH)
    return np.array([list(M.Transform(Gf.Vec3d(p))) for p in UsdGeom.Mesh(CLOTH_MESH).GetPointsAttr().Get()], float)


def target_centre():
    if CLOTH:
        P = cloth_points()
        if TRACK["index"] is None:
            # before the pinch: the lowest hanging point of the cloth (the corner the plan aims at)
            return P[np.argmin(P[:, 2])]
        return P[TRACK["index"]]
    bc = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.default_, UsdGeom.Tokens.render])
    return np.array(bc.ComputeWorldBound(target).ComputeAlignedRange().GetMidpoint(), float)


TILT_REF = {}


def tilt_deg():
    """How far the target has leaned since the replay began (its own axes
    are the asset's: a Y-up can reads 90 degrees from world up at rest)."""
    if CLOTH:
        return None
    b = body_pose(target_body.GetPath().pathString)
    if b is None:
        return None
    R = quat_to_R(b[1])
    R0 = TILT_REF.setdefault("R", R)
    up0 = R0.T @ np.array([0.0, 0.0, 1.0])                 # world up, in the target's frame at the start
    return round(math.degrees(math.acos(max(-1.0, min(1.0, float((R @ up0)[2]))))), 1)


HM = gr.get("hand_model") or {}
# a two-arm task (scissors: the left holds, the right works): the plan carries
# the right arm and hand in columns 14:28, and the right Ability Hand runs its
# own firmware. Known from the spec before the hand is set up (the live plan
# writes the columns later)
BIMANUAL = bool(gr.get("bimanual"))
if HM.get("kind") not in (None, "ability_hand"):
    raise SystemExit(f"unknown grasp_replay.hand_model.kind {HM.get('kind')!r}")
TARGET_RB = next((q for q in Usd.PrimRange(target) if q.HasAPI(UsdPhysics.RigidBodyAPI)), target_body)
_tobj = target
while _tobj.GetParent().GetPath().pathString != "/World/Objects":
    _tobj = _tobj.GetParent()
BODY_PATHS = {q.GetName(): q.GetPath().pathString for q in Usd.PrimRange(_tobj) if q.HasAPI(UsdPhysics.RigidBodyAPI)}
MEMBER_GRIP = None                                        # the grip point in the pinched member's body frame, set at the close
PUSH_LOOP_LOCAL, TIP_VERTEX_R = None, None
if BIMANUAL and spec.get("finger_push"):
    # the pushed loop's centre in its body's frame: the asset-frame point through the object's authored transform, then into the body
    _fp = spec["finger_push"]
    _xc = UsdGeom.XformCache()
    _pu = stage.GetPrimAtPath(BODY_PATHS[_fp["pushed"]])
    _lw = np.array(list(_xc.GetLocalToWorldTransform(_tobj).Transform(Gf.Vec3d(*_fp["loop_point"]))), float)
    _M = np.array(_xc.GetLocalToWorldTransform(_pu)).T
    _R = _M[:3, :3] / np.linalg.norm(_M[:3, :3], axis=0)
    PUSH_LOOP_LOCAL = _R.T @ (_lw - _M[:3, 3])
if HM and not CLOTH:
    # the grip force each digit puts on the target, read like HomeHero's
    # object contact sensor (a sensor on the object, filtered by hand link)
    TARGET_RB.AddAppliedSchema("PhysxContactReportAPI")
    TARGET_RB.CreateAttribute("physxContactReport:threshold", Sdf.ValueTypeNames.Float).Set(0.0)
    if gr.get("tool") or (spec.get("grasp_plan") or {}).get("task") == "loop_grip":
        # a tool's other bodies too (its trigger): a digit on the trigger, not the
        # body around it, is what a pull needs, and a contact view needs the
        # report on its body before the simulation starts
        for _q in Usd.PrimRange(target):
            if _q.HasAPI(UsdPhysics.RigidBodyAPI) and _q != TARGET_RB:
                _q.AddAppliedSchema("PhysxContactReportAPI")
                _q.CreateAttribute("physxContactReport:threshold", Sdf.ValueTypeNames.Float).Set(0.0)

# the plan and the scene must agree on the asset's collision exceptions: a plan
# made with them dropped drives the shoulder into the housing band of a scene
# that still models it (10 minutes of stalled arm, found 2026-10-15 on the towel)
_plan_json = Path(gr["reference"]).expanduser().with_name("plan.json")
if _plan_json.exists() and json.loads(_plan_json.read_text()).get("collision_exceptions_dropped"):
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from r5_collision_exceptions import missing_in_scene
    _missing = missing_in_scene(stage, "/World/Robot")
    if _missing:
        print(f"GRASP_FAIL {spec['name']}: the plan dropped the asset's collision exceptions but the scene does not filter "
              f"{len(_missing)} of them {_missing}: rebuild it (build_er_scene.py applies them by default)", flush=True)
        app.close()
        raise SystemExit(2)

if args.contacts:
    # PhysX's contact report on every left-arm and hand link: which actor
    # pairs touch, each tick (what blocks the arm when it stalls short).
    # Applied before the stage attaches: PhysX ignores the flag afterwards
    # (measured: empty reports with a stalled arm)
    n_rep = 0
    for q in Usd.PrimRange(stage.GetPrimAtPath(LINKS)):
        if q.HasAPI(UsdPhysics.RigidBodyAPI) and (q.GetName().startswith(("amber_left_", "psyonic_left_"))
                                                 or (BIMANUAL and q.GetName().startswith(("amber_right_", "psyonic_right_")))):
            q.AddAppliedSchema("PhysxContactReportAPI")
            q.CreateAttribute("physxContactReport:threshold", Sdf.ValueTypeNames.Float).Set(0.0)
            n_rep += 1
    print(f"[contacts] reporting on {n_rep} links", flush=True)

# --- start: posture, then settle --------------------------------------------------------
timeline = omni.timeline.get_timeline_interface()
timeline.play()
for _ in range(10):
    app.update()
if CLOTH:
    # PhysX cooks the cloth's simulation mesh asynchronously: no stepping
    # until it exists, or the drape starts late and the settle is short
    _sim_mesh = next((q for q in Usd.PrimRange(target) if q.GetName() == "simMesh"), None)
    for _ in range(600):
        pts_ = UsdGeom.Mesh(_sim_mesh).GetPointsAttr().Get() if _sim_mesh else None
        if pts_ and len(pts_) > 3:
            break
        app.update()
    print(f"[cloth] simulation mesh cooked: {len(pts_) if pts_ else 0} points", flush=True)
sim_view = tensors.create_simulation_view("numpy", stage_id=omni.usd.get_context().get_stage_id())
sim_view.set_subspace_roots("/")
art = sim_view.create_articulation_view(f"{LINKS}/amber_base_link")
dof = {n: i for i, n in enumerate(art.shared_metatype.dof_names)}

# --- the tool's own control law (grasp_replay.tool): a drill's motor run on its
# joints each control tick, as animate_asset runs it for the asset's check -
# the trigger's travel and the switch give the chuck's target speed
# (behaviors.motor); the battery seated is the power
TOOL = None
if gr.get("tool"):
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from behaviors import motor as _motor_law
    _troot = next((q for q in Usd.PrimRange(target) if q.HasAPI(UsdPhysics.ArticulationRootAPI)), None)
    _beh = None
    for _q in Usd.PrimRange(target):
        _raw = _q.GetCustomDataByKey("simReady:behaviors")
        if _raw:
            _beh = next((b for b in json.loads(_raw) if b.get("type") == gr["tool"].get("behavior", "motor")), None)
            if _beh:
                break
    if _troot is None or _beh is None:
        raise SystemExit(f"grasp_replay.tool: {target.GetPath()} has no articulation root or no {gr['tool'].get('behavior', 'motor')} behaviour")
    _tv = sim_view.create_articulation_view(_troot.GetPath().pathString)
    _tdof = {n: i for i, n in enumerate(_tv.shared_metatype.dof_names)}
    _tjoints = {q.GetName(): q for q in Usd.PrimRange(target) if q.IsA(UsdPhysics.Joint)}
    _out = _tdof[_beh["output"]]
    _k = np.asarray(_tv.get_dof_stiffnesses(), np.float32).reshape(1, -1).copy(); _k[0, _out] = 0.0
    _c = np.asarray(_tv.get_dof_dampings(), np.float32).reshape(1, -1).copy()
    _c[0, _out] = float(gr["tool"].get("drive_damping_per_deg", 0.05)) * 180.0 / np.pi       # USD's per-degree gain, per radian here
    _f = np.asarray(_tv.get_dof_max_forces(), np.float32).reshape(1, -1).copy(); _f[0, _out] = float(gr["tool"].get("drive_max_force", 5.0))
    _ti = np.arange(1, dtype=np.int32)
    _tv.set_dof_stiffnesses(_k, _ti); _tv.set_dof_dampings(_c, _ti); _tv.set_dof_max_forces(_f, _ti)
    _bat_path = None
    if _beh.get("power") in _tjoints:
        _bat_path = UsdPhysics.Joint(_tjoints[_beh["power"]]).GetBody1Rel().GetTargets()[0].pathString
    TOOL = {"view": _tv, "dof": _tdof, "beh": _beh, "idx": _ti, "battery": _bat_path, "body": TARGET_RB.GetPath().pathString, "bat_ref": None}
    # the throttle's own body (the trigger): which digits press IT, not the body
    # around it (the hand's contact view is filtered to the body: an index
    # stalled on the handle read 9.7 N while the trigger never moved)
    _trig_path = UsdPhysics.Joint(_tjoints[_beh["throttle"]]).GetBody1Rel().GetTargets()[0].pathString
    TOOL["trigger_path"] = _trig_path
    TOOL["trigger_contacts"] = None                       # made beside the hand's own contact view (HAND_LINKS)
    print(f"[tool] {_troot.GetPath()}: dofs {list(_tdof)}; motor {_beh['throttle']} -> {_beh['output']} "
          f"(switch {_beh.get('direction')}, power {_beh.get('power')}); output drive damping {_c[0, _out]:.3f} N m s/rad, max {_f[0, _out]} N m", flush=True)


def tool_step():
    """The motor law on the tool's measured joints: its output's target speed."""
    v = TOOL["view"]
    qd = np.asarray(v.get_dof_positions(), float)[0]
    qv = np.asarray(v.get_dof_velocities(), float)[0]
    b = TOOL["beh"]
    qs = {n: float(qd[TOOL["dof"][n]]) for n in (b["throttle"], b.get("direction")) if n in TOOL["dof"]}
    powered = True
    if TOOL["battery"]:
        bp, tp = body_pose(TOOL["battery"]), body_pose(TOOL["body"])
        if bp is not None and tp is not None:
            rel = quat_to_R(tp[1]).T @ (np.asarray(bp[0]) - np.asarray(tp[0]))
            if TOOL["bat_ref"] is None:
                TOOL["bat_ref"] = rel
            powered = bool(np.linalg.norm(rel - TOOL["bat_ref"]) < 0.003)             # the press-fit seated: within 3 mm of where it started
    law = _motor_law(b, qs, powered)
    w = law[b["output"]] * np.pi / 180.0
    vt = np.asarray(v.get_dof_velocity_targets(), np.float32).reshape(1, -1).copy()
    vt[0, TOOL["dof"][b["output"]]] = w
    v.set_dof_velocity_targets(vt, TOOL["idx"])
    out = {"trigger_m": round(qs.get(b["throttle"], 0.0), 5), "throttle": law["_state"]["throttle"], "direction": law["_state"]["direction"],
           "powered": powered, "law_rpm": round(abs(law[b["output"]]) / 6.0, 1),
           "chuck_rpm": round(abs(float(qv[TOOL["dof"][b["output"]]])) * 60.0 / (2 * np.pi), 1)}
    tb = body_pose(TOOL["body"])
    if tb is not None:
        out["body"] = {"p": np.round(np.asarray(tb[0], float), 5).tolist(), "q_wxyz": np.round(np.asarray(tb[1], float), 6).tolist()}
    if TOOL.get("trigger_contacts") is not None:
        try:
            m = np.linalg.norm(np.asarray(TOOL["trigger_contacts"].get_contact_force_matrix(DT), float).reshape(-1, 3), axis=1)
            out["on_trigger_n"] = {"index": round(float(m[0] + m[1]), 3), "middle": round(float(m[2] + m[3]), 3)}
        except AttributeError:                                 # the view has no backend: unmeasured, not a crash
            TOOL["trigger_contacts"] = None
    return out
posture = spec["robot"]["posture_rad_or_m"]
idx = np.arange(1, dtype=np.int32)
q = art.get_dof_positions().copy()
for n, v in posture.items():
    q[0, dof[n]] = float(v)
art.set_dof_positions(q, idx)
art.set_dof_velocities(np.zeros_like(q), idx)
CMD = q.copy()                                          # the commanded joint targets, before the controller

# --- the controller ------------------------------------------------------------------------
# grasp_replay.controller "homehero_r5_action" is HomeHero's R5 action layer
# (homehero_rl/aida_r5_learning/actions.py, R5Action.apply_actions): every
# second physics step each arm joint's target is raised by its gravity torque
# over its stiffness, and every other joint but the wheels and casters gets
# its gravity torque as a feed-forward effort. HomeHero's recordings and its
# planner's paths are commands to that layer. Without it the shoulder slide
# sits 7.5 mm under its target (7.6 kg carried on a 10 kN/m drive; measured)
# and HomeHero's recorded mug lift came out 5.8 cm instead of 7.0.
# "position" sends the targets as they are.
CONTROLLER = gr.get("controller", "position")
if CONTROLLER not in ("position", "homehero_r5_action"):
    raise SystemExit(f"unknown grasp_replay.controller {CONTROLLER!r}")
dof_names = list(art.shared_metatype.dof_names)
NDOF = len(dof_names)
ARM_IDS = np.array([i for i, n in enumerate(dof_names) if n.startswith("amber_")
                    and any(s in n for s in ("shoulder", "upper_arm", "elbow", "forearm", "wrist"))], int)
BASE_IDS = np.array([i for i, n in enumerate(dof_names) if "wheel" in n or "caster" in n], int)
STIFF = np.asarray(art.get_dof_stiffnesses(), float).reshape(1, NDOF)
LIMITS = np.asarray(art.get_dof_limits(), float).reshape(1, NDOF, 2)
if CONTROLLER == "homehero_r5_action" and np.any(STIFF[0, ARM_IDS] <= 0):
    raise SystemExit("an arm joint has no drive stiffness; gravity compensation needs one")
SENT = {"offset": np.zeros(NDOF)}
PHYS_HZ = int(spec.get("physics_hz", 120))
DT = 1.0 / PHYS_HZ
DECIM = max(1, round(PHYS_HZ / 60))                    # HomeHero's action layer runs at 60 Hz

# --- the hand ------------------------------------------------------------------------------
# grasp_replay.hand_model "ability_hand": the PSYONIC Ability Hand's motors,
# worms and API firmware (scripts/ability_hand_model.py). Each motor digit
# (four fingers' q1, the thumb flexor) is a velocity drive - a DC motor at
# voltage V, damping K Ke, target V / Ke - on the rotor's inertia; its limits
# are the worm's ratchet; the firmware sets both every physics step. The
# fingers' q2 follow through the four-bar (mimic joints, built in the scene).
FINGERS = ("index", "middle", "ring", "pinky")
HAND = None
HAND_R = None
CONTACTS = None
CONTACTS_MORE = []
if HM:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from ability_hand_model import AbilityHandFirmware, AbilityHandParams
    HP = AbilityHandParams.from_spec(HM)
    MOTOR = np.array([dof[f"psyonic_left_{d}_q1"] for d in FINGERS] + [dof["psyonic_left_thumb_q2"]], int)
    DISTAL = np.array([dof[f"psyonic_left_{d}_q2"] for d in FINGERS], int)
    stiff = np.asarray(art.get_dof_stiffnesses(), np.float32).reshape(1, NDOF).copy()
    damp = np.asarray(art.get_dof_dampings(), np.float32).reshape(1, NDOF).copy()
    arm_ = np.asarray(art.get_dof_armatures(), np.float32).reshape(1, NDOF).copy()
    stiff[0, MOTOR], damp[0, MOTOR], arm_[0, MOTOR] = 0.0, HP.motor_damping, HP.rotor_inertia_kg_m2
    stiff[0, DISTAL], damp[0, DISTAL], arm_[0, DISTAL] = 0.0, 0.0, 1e-5      # the linkage moves them
    art.set_dof_stiffnesses(stiff, idx)
    art.set_dof_dampings(damp, idx)
    art.set_dof_armatures(arm_, idx)
    STIFF[0, MOTOR] = 0.0
    HAND = AbilityHandFirmware(HP, np.asarray(art.get_dof_positions(), float)[0, MOTOR],
                               LIMITS[0, MOTOR, 0], LIMITS[0, MOTOR, 1])
    HAND_R, MOTOR_R, DISTAL_R = None, None, None
    if BIMANUAL:
        # the right Ability Hand, the same model (its own motors, worms and firmware)
        MOTOR_R = np.array([dof[f"psyonic_right_{d}_q1"] for d in FINGERS] + [dof["psyonic_right_thumb_q2"]], int)
        DISTAL_R = np.array([dof[f"psyonic_right_{d}_q2"] for d in FINGERS], int)
        stiff[0, MOTOR_R], damp[0, MOTOR_R], arm_[0, MOTOR_R] = 0.0, HP.motor_damping, HP.rotor_inertia_kg_m2
        stiff[0, DISTAL_R], damp[0, DISTAL_R], arm_[0, DISTAL_R] = 0.0, 0.0, 1e-5
        art.set_dof_stiffnesses(stiff, idx)
        art.set_dof_dampings(damp, idx)
        art.set_dof_armatures(arm_, idx)
        STIFF[0, MOTOR_R] = 0.0
        HAND_R = AbilityHandFirmware(HP, np.asarray(art.get_dof_positions(), float)[0, MOTOR_R],
                                     LIMITS[0, MOTOR_R, 0], LIMITS[0, MOTOR_R, 1])
    HAND_LIM = LIMITS.astype(np.float32).copy()
    # set_dof_limits writes every joint; the wheels and casters have none,
    # and the tensor plugin warns for each of them on every call (480 Hz)
    carb.settings.get_settings().set("/log/channels/omni.physx.tensors.plugin", "error")
    HAND_VT = np.zeros((1, NDOF), np.float32)
    HAND_LINKS = [f"{LINKS}/psyonic_left_{d}_{part}" for d in FINGERS + ("thumb",) for part in ("L1", "L2")] \
        + [f"{LINKS}/psyonic_left_base"]
    try:
        if CLOTH:
            raise RuntimeError("a deformable target has no rigid contact view")
        CONTACTS = sim_view.create_rigid_contact_view(TARGET_RB.GetPath().pathString, filter_patterns=HAND_LINKS)
        if (spec.get("grasp_plan") or {}).get("task") == "loop_grip":
            # every body of the scissors: the index is in the OTHER half's loop
            # (the target body is the thumb's half), and its force went unmeasured
            CONTACTS_MORE = [sim_view.create_rigid_contact_view(bp_, filter_patterns=HAND_LINKS)
                             for bp_ in BODY_PATHS.values() if bp_ != TARGET_RB.GetPath().pathString]
        if TOOL is not None:
            TOOL["trigger_contacts"] = sim_view.create_rigid_contact_view(TOOL["trigger_path"], filter_patterns=HAND_LINKS)
    except Exception as e:                              # the grip still runs; its forces go unmeasured
        print(f"[hand] no contact view: {e}", flush=True)


CONTACT_ALL = None
if args.contacts:
    from omni.physx import get_physx_simulation_interface
    CONTACT_ALL = get_physx_simulation_interface()


def link_contacts(top=6):
    if CONTACT_ALL is None:
        return None
    from pxr import PhysicsSchemaTools
    headers, _ = CONTACT_ALL.get_contact_report()
    pairs = {}
    for h in headers:
        if int(h.type) == 2:                                  # lost
            continue
        a = str(PhysicsSchemaTools.intToSdfPath(h.actor0))
        b = str(PhysicsSchemaTools.intToSdfPath(h.actor1))
        key = (a.split("/")[-1], b.split("/")[-1] if "/World/Robot" in b else b.split("/World/")[-1][:40])
        pairs[key] = pairs.get(key, 0) + 1
    return sorted(pairs.items(), key=lambda kv: -kv[1])[:top]


def hand_step():
    qa = np.asarray(art.get_dof_positions(), float)[0]
    qda = np.asarray(art.get_dof_velocities(), float)[0]
    v_t, lo, hi = HAND.step(DT, CMD[0, MOTOR], qa[MOTOR], qda[MOTOR])
    HAND_VT[0, MOTOR] = v_t
    HAND_LIM[0, MOTOR, 0], HAND_LIM[0, MOTOR, 1] = lo, hi
    if HAND_R is not None:
        v_t, lo, hi = HAND_R.step(DT, CMD[0, MOTOR_R], qa[MOTOR_R], qda[MOTOR_R])
        HAND_VT[0, MOTOR_R] = v_t
        HAND_LIM[0, MOTOR_R, 0], HAND_LIM[0, MOTOR_R, 1] = lo, hi
    art.set_dof_velocity_targets(HAND_VT, idx)
    art.set_dof_limits(HAND_LIM, idx)


def digit_forces_by_body():
    """Each digit's contact force on each body of the target (a loop grip's two
    halves): which loop a digit presses, not only how hard the hand presses the
    object - the critic credited 'a digit in each loop' to a thumb and index
    pinching the SAME half (Gemini 4, blind, 2026-10-16)."""
    if CONTACTS is None or not CONTACTS_MORE:
        return None
    out = {}
    names = {bp_: bp_.split("/")[-1] for bp_ in BODY_PATHS.values()}
    views = [(TARGET_RB.GetPath().pathString, CONTACTS)] + list(zip([bp_ for bp_ in BODY_PATHS.values() if bp_ != TARGET_RB.GetPath().pathString], CONTACTS_MORE))
    for bp_, cv_ in views:
        n = np.linalg.norm(np.asarray(cv_.get_contact_force_matrix(DT), float).reshape(-1, 3), axis=1)
        per = [round(float(n[2 * k] + n[2 * k + 1]), 3) for k in range(5)]
        out[names[bp_]] = {"index": per[0], "middle": per[1], "ring": per[2], "pinky": per[3], "thumb": per[4], "palm": round(float(n[10]), 3)}
    return out


def digit_forces():
    """Each digit's contact force on the target (N; L1 + L2), and the palm's."""
    if CONTACTS is None:
        return None
    m = np.asarray(CONTACTS.get_contact_force_matrix(DT), float).reshape(-1, 3)
    n = np.linalg.norm(m, axis=1)
    for cv_ in CONTACTS_MORE:                                   # the object's other bodies (a loop grip's other half)
        n = n + np.linalg.norm(np.asarray(cv_.get_contact_force_matrix(DT), float).reshape(-1, 3), axis=1)
    per = [round(float(n[2 * k] + n[2 * k + 1]), 3) for k in range(5)]
    return {"index": per[0], "middle": per[1], "ring": per[2], "pinky": per[3], "thumb": per[4],
            "palm": round(float(n[10]), 3)}


def apply_controller():
    if CONTROLLER == "position":
        art.set_dof_position_targets(CMD.astype(np.float32), idx)
        return
    g = np.asarray(art.get_gravity_compensation_forces(), float).reshape(1, -1)[:, -NDOF:].copy()
    g[:, BASE_IDS] = 0.0
    t = CMD.astype(float).copy()
    t[:, ARM_IDS] += g[:, ARM_IDS] / STIFF[:, ARM_IDS]
    t = np.clip(t, LIMITS[..., 0], LIMITS[..., 1])
    SENT["offset"] = (t - CMD)[0]
    g[:, ARM_IDS] = 0.0
    if HAND is not None:
        g[:, MOTOR] = 0.0                               # the worms hold the digits; their motors get no free torque
        g[:, DISTAL] = 0.0
    art.set_dof_position_targets(t.astype(np.float32), idx)
    art.set_dof_actuation_forces(g.astype(np.float32), idx)

# physics is stepped here, not by the timeline: a headless timeline over a
# stage with no time range does not advance (measured: 0 s per update), so
# app updates alone simulate nothing. The timeline only attaches PhysX.
CLOCK = [0.0]


def simulate(seconds):
    for k in range(max(1, round(seconds * PHYS_HZ))):
        if k % DECIM == 0:                              # HomeHero's action layer at 60 Hz
            apply_controller()
        if HAND is not None:
            hand_step()                                 # the hand's firmware, every physics step
        px.update_simulation(DT, CLOCK[0])
        CLOCK[0] += DT
    px.update_transformations(False, True, False, False)


# a cloth settles as long as it took to come to rest before the plan was made:
# the plan is against that shape, and a flap still swinging in is a pinch on air
simulate(max(args.settle, CLOTH_SETTLE_S))

LIVE_PLAN = (CLOTH or bool(gr.get("live_plan"))) and not args.no_live_plan
if LIVE_PLAN:
    # perceive, plan, act in one episode: the GPU drape is not repeatable run
    # to run (two identical towels on the rig came to rest 1 cm apart; the
    # replay's towel sat 2-4 cm from the settle run's shape and the tips
    # closed on air 2.3 cm from the cloth, 2026-10-15). The cloth as it rests
    # NOW goes to the planner, and its plan is what this run executes. A rigid
    # articulated target (a scissors hung on a peg, its free half swung on
    # the pivot) goes the same way, as every body's settled pose.
    import os
    px.update_transformations(False, True, False, False)
    if CLOTH:
        _live = cloth_points()
        np.savez(scene_dir / "cloth_settled.npz", **{target.GetName(): _live})
        _meta = json.loads((scene_dir / "cloth_settled.json").read_text()) if (scene_dir / "cloth_settled.json").exists() else {}
        _meta.update({"live_from_replay": True, "live_settled_s": max(args.settle, CLOTH_SETTLE_S),
                      "live_lowest": _live[np.argmin(_live[:, 2])].round(4).tolist()})
        (scene_dir / "cloth_settled.json").write_text(json.dumps(_meta, indent=1))
        CLOTH_SETTLED = _live
        _live_note = f"lowest {_meta['live_lowest']}"
    else:
        _xc = UsdGeom.XformCache()
        _bodies = {}
        for _o in stage.GetPrimAtPath("/World/Objects").GetChildren():
            for _q in Usd.PrimRange(_o):
                if _q.HasAPI(UsdPhysics.RigidBodyAPI):
                    _bp = body_pose(_q.GetPath().pathString)                 # PhysX's own state (the tensors view), as settle_scene.py
                    if _bp is not None:
                        _p, _qw = np.asarray(_bp[0], float), np.asarray(_bp[1], float)
                        _bodies[_q.GetPath().pathString] = {"object": _o.GetName(), "p": [round(float(v), 5) for v in _p],
                                                            "q_wxyz": [round(float(v), 6) for v in _qw]}
                    else:
                        _M = _xc.GetLocalToWorldTransform(_q); _p = _M.ExtractTranslation(); _r = _M.ExtractRotationQuat()
                        _bodies[_q.GetPath().pathString] = {"object": _o.GetName(), "p": [round(v, 5) for v in _p],
                                                            "q_wxyz": [round(_r.GetReal(), 6)] + [round(v, 6) for v in _r.GetImaginary()]}
        (scene_dir / "settled_poses.json").write_text(json.dumps({"seconds": max(args.settle, CLOTH_SETTLE_S), "live_from_replay": True, "bodies": _bodies}, indent=1))
        _tb = _bodies.get(TARGET_RB.GetPath().pathString, {})
        _live_note = f"target body at {_tb.get('p')}"
    _env = {k: v for k, v in os.environ.items()
            if k not in ("PYTHONPATH", "PYTHONHOME", "LD_LIBRARY_PATH", "LD_PRELOAD") and not k.startswith(("CARB", "OMNI", "ISAAC", "KIT", "EXP_PATH"))}
    _env.update({"PYTHONPATH": os.path.expanduser("~/Documents/Github/openusd_build/lib/python"),
                 "LD_LIBRARY_PATH": os.path.expanduser("~/Documents/Github/openusd_build/lib"),
                 "CUMOTION_INFLATE": os.environ.get("CUMOTION_INFLATE", "0.008")})
    print(f"[live] planning against the scene as it rests now ({_live_note})", flush=True)
    _r = subprocess.run(["/usr/bin/python3", str(Path(__file__).resolve().parent / "plan_er_grasp.py"), str(scene_dir / "spec.json")],
                        env=_env, capture_output=True, text=True, timeout=1800)
    _verdict = next((ln for ln in _r.stdout.splitlines() if ln.startswith("GRASP_PLAN")), None)
    (scene_dir / "grasp_plan" / "live_plan.log").write_text(_r.stdout[-20000:] + "\n--- stderr ---\n" + _r.stderr[-20000:])
    print(f"[live] {_verdict or 'the planner printed no verdict'}", flush=True)
    if not (_verdict or "").startswith("GRASP_PLAN OK"):
        print(f"GRASP_FAIL {spec['name']}: no plan against the live scene ({_verdict or _r.stderr[-400:]})", flush=True)
        app.close()
        raise SystemExit(2)

# --- the recording ---------------------------------------------------------------------
_rec = np.load(Path(gr["reference"]).expanduser())
ref = _rec["targets"]
STAGES = [str(x) for x in _rec["stages"]] if "stages" in _rec.files else None   # a planned path names its stages
robot = json.loads(Path(gr["robot"]).expanduser().read_text())
arm_names = robot["arm_names"]
arm_names_right = [n.replace("left", "right") for n in arm_names]
if BIMANUAL and ref.shape[1] < 28:
    raise SystemExit(f"grasp_replay.bimanual is set but the plan has {ref.shape[1]} columns (a two-arm plan has 28)")
if BIMANUAL:
    print(f"[replay] a two-arm plan: {ref.shape[1]} columns", flush=True)
F = float(gr.get("finger_q2_coefficient", 1.05851325))
lift = float(gr.get("lift_offset_m", 0.0))
fingers = ("index", "middle", "ring", "pinky")


pre = gr.get("preload_rad") or {}
start_hand, final_hand = ref[0, 8:13].astype(float), ref[-1, 8:13].astype(float)


def closure(row):
    """How far the hand has closed, 0 (open) to 1 (the recording's grip)."""
    span = np.where(np.abs(final_hand - start_hand) > 1e-6, final_hand - start_hand, 1.0)
    return float(np.clip(np.max((np.asarray(row[8:13], float) - start_hand) / span), 0.0, 1.0))


def to_targets(row):
    row = np.asarray(row, float).copy()
    c = closure(row)
    row[8:12] += float(pre.get("fingers", 0.0)) * c        # past contact: a squeeze, ramped in as the hand closes
    row[12] += float(pre.get("thumb_flex", 0.0)) * c
    t = {n: float(v) for n, v in zip(arm_names, row[:8])}
    t["amber_left_shoulder_lift_joint"] += lift
    for k, f in enumerate(fingers):
        t[f"psyonic_left_{f}_q1"] = float(row[8 + k])
        t[f"psyonic_left_{f}_q2"] = float(row[8 + k]) * F
    t["psyonic_left_thumb_q2"] = float(row[12])          # flexion
    t["psyonic_left_thumb_q1"] = float(row[13])          # rotation
    if BIMANUAL:
        # a two-arm plan: columns 14:22 the right arm (the same joint order,
        # mirrored names), 22:28 the right hand (the same digit order)
        for n, v in zip(arm_names_right, row[14:22]):
            t[n] = float(v)
        for k, f in enumerate(fingers):
            t[f"psyonic_right_{f}_q1"] = float(row[22 + k])
            t[f"psyonic_right_{f}_q2"] = float(row[22 + k]) * F
        t["psyonic_right_thumb_q2"] = float(row[26])
        t["psyonic_right_thumb_q1"] = float(row[27])
    return t


# --- cameras -----------------------------------------------------------------------------
hc = spec["head_camera"]
link = body_pose(f"{LINKS}/{hc['mount_link']}")
R_link = quat_to_R(link[1])
r_, p_, y_ = hc["mount_rpy"]
R_opt = R_link @ (Rz(y_) @ Ry(p_) @ Rx(r_)) @ Rz(float(hc.get("optical_roll_rad", 0.0)))
t_opt = link[0] + R_link @ np.array(hc["mount_xyz"], float)
T = np.eye(4)
T[:3, :3], T[:3, 3] = R_opt @ Rx(math.pi), t_opt
xf = UsdGeom.Xformable(stage.GetPrimAtPath("/World/Cameras/HeadRGB"))
xf.ClearXformOpOrder()
xf.AddTransformOp().Set(Gf.Matrix4d(T.T.tolist()))       # the base is fixed and the head still: one pose
products = {} if args.no_video else {
    "close": rep.create.render_product("/World/Cameras/GraspCloseup", (1280, 720)),
    "head": rep.create.render_product("/World/Cameras/HeadRGB", (640, 480)),
    "overview": rep.create.render_product("/World/Cameras/Overview", (1280, 720)),
}
annot = {}
for k, rp in products.items():
    a = rep.AnnotatorRegistry.get_annotator("rgb")
    a.attach([rp])
    annot[k] = a


# the close-up follows the task's stages (grasp_replay.film): from the first
# tick of `from_stage` the close camera takes `camera`'s pose and focal length
# (a cut). One fixed close-up filmed the scissors' pick at the hook, then an
# empty hook for the 40 s of the close and the open, the hands out of its
# frame after the carry (take 12, 2026-10-15)
FILM = list((gr.get("film") or {}).get("cuts", []))
_film_at = {"camera": "GraspCloseup"}


def _film_cut(stage_name):
    for cut in FILM:
        if stage_name == cut["from_stage"] and _film_at["camera"] != cut["camera"]:
            src = stage.GetPrimAtPath(f"/World/Cameras/{cut['camera']}")
            dst = stage.GetPrimAtPath("/World/Cameras/GraspCloseup")
            if not src or not dst:
                raise SystemExit(f"grasp_replay.film: no camera /World/Cameras/{cut['camera']}")
            M = UsdGeom.XformCache().GetLocalToWorldTransform(src)
            xf = UsdGeom.Xformable(dst)
            xf.ClearXformOpOrder()
            xf.AddTransformOp().Set(M)
            UsdGeom.Camera(dst).GetFocalLengthAttr().Set(UsdGeom.Camera(src).GetFocalLengthAttr().Get())
            _film_at["camera"] = cut["camera"]
            print(f"[film] the close-up cuts to {cut['camera']} at stage {stage_name}", flush=True)


def capture(i, stage_name=None):
    if stage_name is not None and FILM:
        _film_cut(stage_name)
    if args.no_video or (args.frame_every > 1 and i % args.frame_every):
        return
    rep.orchestrator.step(rt_subframes=max(1, args.subframes), delta_time=0.0, pause_timeline=False)
    for k, a in annot.items():
        Image.fromarray(a.get_data()[:, :, :3]).save(frames[k] / f"f{i:05d}.png")


# the fingertip vertices (HomeHero's planning collision cache: the distal
# link's farthest point from its joint), as the pinch planner defines them
def _tip_vertex(link):
    cache = Path("/home/kimate/Documents/Github/HomeHero/artifacts/isaac/aida_r5_20261004/physics_v7_joint_filters/planning_collision")
    man = json.loads(cache.with_suffix(".json").read_text())
    data = np.load(cache.with_suffix(".npz"))
    pts = np.concatenate([data[f"p{i}"] for i, sh in enumerate(man["shapes"]) if sh["link"] == link])
    return pts[np.argmax(np.linalg.norm(pts, axis=1))]


TIP_VERTEX = {d: _tip_vertex(f"psyonic_left_{d}_L2") for d in ("index", "thumb")} if HAND is not None else {}
TIP_VERTEX_R = _tip_vertex("psyonic_right_index_L2") if (HAND is not None and BIMANUAL) else None

# --- replay -------------------------------------------------------------------------------
hz = float(gr.get("control_hz", 30))
rest_c = target_centre()
DEST = json.loads(stage.GetPrimAtPath("/World").GetCustomDataByKey("er:destination") or "null")


def chess_pieces():
    """Every annotated piece of the target's set: its body's centre now."""
    bc = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.default_, UsdGeom.Tokens.render])
    root = target
    while root.GetParent().GetPath().pathString != "/World/Objects":
        root = root.GetParent()
    out = {}
    for q in Usd.PrimRange(root):
        if q.HasCustomDataKey("simReady:chess"):
            info = json.loads(q.GetCustomDataByKey("simReady:chess"))
            out[q.GetPath().pathString] = {"piece": info["piece"], "color": info["color"], "start": info["start_square"],
                                           "centre": np.array(bc.ComputeWorldBound(q).ComputeAlignedRange().GetMidpoint(), float)}
    return out


def board_grid():
    """The board's squares in world now, from the destination's stored corners."""
    Mb = UsdGeom.XformCache().GetLocalToWorldTransform(stage.GetPrimAtPath(DEST["board"]))
    cw = np.array(Mb.Transform(Gf.Vec3d(*DEST["centre_board"])), float)
    cs = [np.array(Mb.Transform(Gf.Vec3d(*c)), float) for c in DEST["corners_board"]]
    sq = DEST["square_m"]
    fd, rd = (cs[1] - cs[0]) / sq, (cs[3] - cs[0]) / sq
    f0, r0 = "abcdefgh".index(DEST["square"][0]), int(DEST["square"][1]) - 1
    return cw - f0 * sq * fd - r0 * sq * rd, fd, rd, sq, cw


def square_of(pt, grid):
    a1, fd, rd, sq, _ = grid
    f, r = (pt - a1) @ fd / sq, (pt - a1) @ rd / sq
    fi, ri = int(np.floor(f + 0.5)), int(np.floor(r + 0.5))
    return f"{'abcdefgh'[fi]}{ri + 1}" if 0 <= fi < 8 and 0 <= ri < 8 else "off"


PIECES0 = chess_pieces() if DEST and DEST.get("kind") == "chess_square" else None
tilt_deg()                                              # the target's attitude at rest: tilt is measured from here
rows = list(ref) + [ref[-1]] * int(float(gr.get("hold_s", 1.5)) * hz)
START_ROW = 0
if args.from_stage:
    if not STAGES or args.from_stage not in STAGES:
        raise SystemExit(f"--from-stage: no stage {args.from_stage!r} in the plan ({sorted(set(STAGES or []))})")
    START_ROW = STAGES.index(args.from_stage)
    # posed as the stage BEFORE leaves it: the stage's own first row may be a
    # command the firmware is meant to work toward (a grip command's 1.74 rad
    # put the fingers through the handle when posed directly, the rig's first try)
    _t = to_targets(rows[max(0, START_ROW - 1)])
    _q = art.get_dof_positions().copy()
    for _n, _v in _t.items():
        _q[0, dof[_n]] = _v
        CMD[0, dof[_n]] = _v
    art.set_dof_positions(_q, idx)
    art.set_dof_velocities(np.zeros_like(_q), idx)
    art.set_dof_position_targets(_q.astype(np.float32), idx)
    if HAND is not None:
        _m = np.asarray(_q, float)[0, MOTOR]
        HAND.cmd = _m.copy()
        HAND.reset_lock(_m)
    simulate(0.5)                                         # the arm settles at the pose, the tool untouched (the plan's open hand is clear of it)
    print(f"[rig] posed at row {max(0, START_ROW - 1)} ({STAGES[max(0, START_ROW - 1)]}), replaying from row {START_ROW} ({args.from_stage})", flush=True)
log = []
sim_t0 = CLOCK[0]
px.update_transformations(False, True, False, False)
for _ in range(3):                                      # the renderer catches up with the posture first
    app.update()                                        # (the first film's frame 0 showed the arm still down)
capture(0)
GRIP_WAIT_S = float(HM.get("grip_wait_s", 3.0))
grip_wait = {"ticks": 0, "complete": None}
acquisition = {"status": "not_attempted", "transport_admitted": False}


def apply_acquisition_outcome(result, receipt):
    """Keep task measurements, but an aborted acquisition cannot pass execution."""
    result["acquisition"] = dict(receipt)
    if receipt["status"] == "aborted":
        result["pass"] = False
        result["failure_reason"] = "grasp_acquisition_failed"


_then = gr.get("then")
THEN = {"stages": ([_then] if isinstance(_then, str) else list(_then or [])), "next": 0, "plan": None, "plans": [], "start_tick": None}
# a loop grip (scissors in one hand, a digit through each loop): the two halves
# whose opening the hand's spread drives, logged each tick as the blades' angle
_gp_spec = spec.get("grasp_plan") or {}
LOOP = dict(_gp_spec.get("loop_grip") or {}, cycles=int(_gp_spec.get("cycles", 5))) if _gp_spec.get("task") == "loop_grip" else None
THEN["kind"] = THEN["stages"][0].split(":")[0] if THEN["stages"] else None      # "finger_push" or "finger_push:close", "finger_push:open": each stage planned live at the end of the one before


def live_bodies():
    """Every body's pose under /World/Objects from PhysX's own state."""
    out = {}
    for _o in stage.GetPrimAtPath("/World/Objects").GetChildren():
        for _q in Usd.PrimRange(_o):
            if _q.HasAPI(UsdPhysics.RigidBodyAPI):
                bp = body_pose(_q.GetPath().pathString)
                if bp is not None:
                    out[_q.GetPath().pathString] = {"object": _o.GetName(), "p": [round(float(v), 5) for v in bp[0]],
                                                    "q_wxyz": [round(float(v), 6) for v in bp[1]]}
    return out


def plan_then(hold_row, at_tick, k=0, stage="finger_push"):
    """A later stage, planned against the scene as it is NOW (the end of the
    plan before: the pick's hold, or the close's hold): the right hand works
    the held object (finger_push_planner.py, its stroke after the colon:
    close, open or both). Returns (rows, stages) to append, or None."""
    import os
    sfx = "" if k == 0 else str(k)
    stroke = stage.split(":")[1] if ":" in stage else "both"
    px.update_transformations(False, True, False, False)
    (scene_dir / f"settled_poses_hold{sfx}.json").write_text(json.dumps({"live_from_replay": True, "tick": at_tick, "stage": stage, "bodies": live_bodies()}, indent=1))
    np.savez(scene_dir / "grasp_plan" / f"hold{sfx}.npz", targets=np.asarray([hold_row], float), stages=np.asarray(["hold"]))
    env = {k: v for k, v in os.environ.items()
           if k not in ("PYTHONPATH", "PYTHONHOME", "LD_LIBRARY_PATH", "LD_PRELOAD") and not k.startswith(("CARB", "OMNI", "ISAAC", "KIT", "EXP_PATH"))}
    env.update({"PYTHONPATH": os.path.expanduser("~/Documents/Github/openusd_build/lib/python") + ":" + os.path.expanduser("~/Documents/Github/HomeHero"),
                "LD_LIBRARY_PATH": os.path.expanduser("~/Documents/Github/openusd_build/lib")})
    out_npz = scene_dir / "grasp_plan" / f"then_targets{sfx}.npz"
    r = subprocess.run([os.path.expanduser("~/Documents/Github/HomeHero/.venv/bin/python"), str(Path(__file__).resolve().parent / "finger_push_planner.py"),
                        str(scene_dir), "--hold", str(scene_dir / "grasp_plan" / f"hold{sfx}.npz"), "--poses", str(scene_dir / f"settled_poses_hold{sfx}.json"),
                        "--out", str(out_npz), "--stroke", stroke],
                       env=env, capture_output=True, text=True, timeout=1200)
    verdict = next((ln for ln in r.stdout.splitlines() if ln.startswith("PUSH_PLAN")), None)
    (scene_dir / "grasp_plan" / f"then_plan{sfx}.log").write_text(r.stdout[-20000:] + "\n--- stderr ---\n" + r.stderr[-20000:])
    print(f"[then {k} {stage}] {verdict or 'the push planner printed no verdict'}", flush=True)
    THEN["plans"].append(f"{stage}: {verdict}")
    THEN["plan"] = " | ".join(THEN["plans"])
    if not (verdict or "").startswith("PUSH_PLAN OK"):
        return None
    z = np.load(out_npz)
    return [np.asarray(x, float) for x in z["targets"][1:]], [str(x) for x in z["stages"][1:]]


def schedule():
    """The plan's rows; with the hand's firmware, the lift waits for the
    grip to complete, as a controller reading the hand's grip-completion
    byte would. A second stage (grasp_replay.then) is planned against the
    live scene when the first plan's hold ends, and its rows appended."""
    for i, row in enumerate(rows):
        if i < START_ROW:
            continue
        stage = STAGES[min(i, len(STAGES) - 1)] if STAGES else ("replay" if i < len(ref) else "hold")
        if (STAGES and stage in ("lift", "carry") and grip_wait["complete"] is None
                and (HAND is not None or GRIP.get("mode") == "force_band")):
            unavailable = GRIP.get("mode") == "force_band" and not FORCE_BAND
            missing_preclose = i == 0
            prev = rows[i - 1]
            for _ in range(0 if unavailable or missing_preclose else int(GRIP_WAIT_S * hz)):
                qa = np.asarray(art.get_dof_positions(), float)[0]
                qda = np.asarray(art.get_dof_velocities(), float)[0]
                if FORCE_BAND:
                    if grip_loaded["ticks"] >= GB["load_ticks"]:   # both digits in band for load_ticks: loaded
                        break
                elif HAND.grip_complete(qa[MOTOR], qda[MOTOR]):
                    break
                grip_wait["ticks"] += 1
                yield i - 1, prev, "grip_wait"
            qa = np.asarray(art.get_dof_positions(), float)[0]
            qda = np.asarray(art.get_dof_velocities(), float)[0]
            grip_wait["complete"] = (False if unavailable or missing_preclose else
                (grip_loaded["ticks"] >= GB["load_ticks"]) if FORCE_BAND else HAND.grip_complete(qa[MOTOR], qda[MOTOR]))
            admitted = bool(grip_wait["complete"])
            acquisition.update(status="admitted" if admitted else "aborted", transport_admitted=admitted,
                reason="grip_complete" if admitted else ("force_feedback_unavailable" if unavailable else
                    "missing_preclose_pose" if missing_preclose else "acquisition_timeout"),
                requested_stage=stage, plan_tick=i, waited_ticks=grip_wait["ticks"],
                waited_s=grip_wait["ticks"] / hz, timeout_s=GRIP_WAIT_S,
                controller="force_band" if GRIP.get("mode") == "force_band" else "firmware",
                loaded_ticks=grip_loaded["ticks"] if FORCE_BAND else None,
                required_loaded_ticks=GB["load_ticks"] if FORCE_BAND else None,
                contact_n=digit_forces() if FORCE_BAND else None,
                action="execute_transport" if admitted else "hold_last_command_and_abort_before_transport")
            if not admitted:
                # No transport target, release, retry, or additional physics tick.
                # Returning normally preserves the source log and observer finalization.
                print(f"[acquisition] ABORT before {stage} row {i}: {acquisition['reason']} "
                      f"after {acquisition['waited_s']:.3f} s; holding the last command and ending replay", flush=True)
                return
        if THEN["stages"] and THEN["next"] < len(THEN["stages"]) and i == len(rows) - 1:
            k = THEN["next"]; THEN["next"] += 1
            more = plan_then(row, i, k, THEN["stages"][k])
            if more:
                if THEN["start_tick"] is None:
                    THEN["start_tick"] = i + 1
                rows.extend(more[0])
                if STAGES:
                    STAGES.extend(more[1])
        yield i, row, stage


# --- the grip: HomeHero's force band (aida_mesh_contact.maintain_grip) on the hand ---
# grasp_replay.grip {"mode": "force_band", ...}: from the close on, the index and
# thumb are commanded as HomeHero commands the real hand - positions at 30 Hz,
# each digit tightened by tightening_step_rad while its contact force is under
# its minimum, backed off by unloading_step_rad above its maximum - instead
# of the plan's grip command. Each digit's creep stops at its own force, so a
# pinch stays balanced (measured without it: the digits crept together and
# rolled a 14 g pawn out at 61 deg). Needs the hand model's contact view.
GRIP = gr.get("grip") or {}
FORCE_BAND = GRIP.get("mode") == "force_band" and HAND is not None and CONTACTS is not None
GB = {"min_n": float(GRIP.get("min_n", 3.0)), "max_n": float(GRIP.get("max_n", 12.0)),
      "tighten": float(GRIP.get("tightening_step_rad", 0.0015)), "unload": float(GRIP.get("unloading_step_rad", 0.002)),
      "free": float(GRIP.get("free_closing_step_rad", 0.004)),       # HomeHero: traverse free space faster, until contact
      "contact_n": float(GRIP.get("contact_n", 0.5)), "load_ticks": int(GRIP.get("load_ticks", 6)),
      "digits": GRIP.get("digits", ["index", "thumb"])}
grip_loaded = {"ticks": 0}
GRIP_DOF = {"index": dof["psyonic_left_index_q1"], "thumb": dof["psyonic_left_thumb_q2"]}
grip_cmd = {}

for tick, (plan_i, row, stage_name) in enumerate(schedule()):
    tg = to_targets(row)
    for n, v in tg.items():
        CMD[0, dof[n]] = v
    if FORCE_BAND and stage_name in ("close", "grip_wait", "lift", "carry", "lower"):
        forces = digit_forces() or {}
        for d in GB["digits"]:
            i = GRIP_DOF[d]
            if d not in grip_cmd:
                grip_cmd[d] = float(np.asarray(art.get_dof_positions(), float)[0, i])   # from where the digit is
            f = float(forces.get(d, 0.0))
            if f < GB["contact_n"] and not grip_cmd.get(d + "_touched"):
                grip_cmd[d] += GB["free"]                  # free space: close at HomeHero's free step
            elif f < GB["min_n"]:
                grip_cmd[d + "_touched"] = True
                grip_cmd[d] += GB["tighten"]
            elif f > GB["max_n"]:
                grip_cmd[d + "_touched"] = True
                grip_cmd[d] -= GB["unload"]
            else:
                grip_cmd[d + "_touched"] = True
            grip_cmd[d] = float(np.clip(grip_cmd[d], 0.0, 1.74))
            CMD[0, i] = grip_cmd[d]
        loaded = all(float(forces.get(d, 0.0)) >= GB["min_n"] for d in GB["digits"])
        grip_loaded["ticks"] = grip_loaded["ticks"] + 1 if loaded else 0
    elif FORCE_BAND and grip_cmd:
        grip_cmd.clear()                                   # release: the plan's hand command again
    if TOOL is not None:
        tool_step()                                        # the motor's target from the joints as they are
    simulate(1.0 / hz)
    c = target_centre()
    palm = body_pose(f"{LINKS}/psyonic_left_base")
    qn = art.get_dof_positions()
    tool_entry = tool_step() if TOOL is not None else None
    entry = {"tick": tick, "plan_tick": plan_i, "t": round(tick / hz, 3), "stage": stage_name, **({"tool": tool_entry} if tool_entry else {}),
             "phase": "replay" if plan_i < len(ref) else "hold",
             "target_rise_m": round(float(c[2] - rest_c[2]), 4),
             "palm_to_target_m": round(float(np.linalg.norm(palm[0] - c)), 4) if palm else None,
             "lift_m": round(float(qn[0, dof["amber_left_shoulder_lift_joint"]]), 4),
             "lift_cmd_m": round(float(CMD[0, dof["amber_left_shoulder_lift_joint"]]), 4),
             "lift_gravity_offset_m": round(float(SENT["offset"][dof["amber_left_shoulder_lift_joint"]]), 4),
             "thumb_flex": round(float(qn[0, dof["psyonic_left_thumb_q2"]]), 3),
             "target_tilt_deg": tilt_deg(),
             "fingers_q1": [round(float(qn[0, dof[f"psyonic_left_{f}_q1"]]), 3) for f in fingers]}
    if MEMBER_GRIP is None and (spec.get("grasp_plan") or {}).get("member_grip") and stage_name in ("lift", "grip_wait"):
        _req = json.loads((scene_dir / "grasp_plan" / "request.json").read_text())
        bpm = body_pose(TARGET_RB.GetPath().pathString)
        if bpm is not None:
            gp_w = np.asarray(_req["task"]["grip_point"], float)
            MEMBER_GRIP = quat_to_R(bpm[1]).T @ (gp_w - bpm[0])
    if CLOTH:
        # the cloth against the pinch and against the shape it was planned on
        tips_ = [body_pose(f"{LINKS}/psyonic_left_{d}_L2") for d in ("index", "thumb")]
        P = cloth_points()
        row = {"lowest_z": round(float(P[:, 2].min()), 4)}
        if CLOTH_SETTLED is not None and len(CLOTH_SETTLED) == len(P):
            row["drift_from_settled_m"] = round(float(np.linalg.norm(P - CLOTH_SETTLED, axis=1).max()), 4)
        if all(tips_):
            mid = (tips_[0][0] + quat_to_R(tips_[0][1]) @ TIP_VERTEX["index"] + tips_[1][0] + quat_to_R(tips_[1][1]) @ TIP_VERTEX["thumb"]) / 2
            row["pinch_mid"] = mid.round(4).tolist()
            row["nearest_to_pinch_m"] = round(float(np.linalg.norm(P - mid, axis=1).min()), 4)
            if TRACK["index"] is None and stage_name in ("lift", "grip_wait"):
                TRACK["index"] = int(np.argmin(np.linalg.norm(P - mid, axis=1)))
                entry["cloth_vertex"] = {"index": TRACK["index"], "to_pinch_m": row["nearest_to_pinch_m"]}
        entry["cloth"] = row
    if MEMBER_GRIP is not None:
        # a pinched rigid member (a scissors' loop): the grip point rides in
        # the body; its distance from the pinch is the slip, whatever the
        # dangling body does about the pinch
        bpm = body_pose(TARGET_RB.GetPath().pathString)
        tips_ = [body_pose(f"{LINKS}/psyonic_left_{d_}_L2") for d_ in ("index", "thumb")]
        if bpm is not None and all(tips_) and TIP_VERTEX:
            gw = bpm[0] + quat_to_R(bpm[1]) @ MEMBER_GRIP
            mid_ = (tips_[0][0] + quat_to_R(tips_[0][1]) @ TIP_VERTEX["index"] + tips_[1][0] + quat_to_R(tips_[1][1]) @ TIP_VERTEX["thumb"]) / 2
            entry["grip_to_pinch_m"] = round(float(np.linalg.norm(gw - mid_)), 4)
    if LOOP is not None:
        ha_, hb_ = BODY_PATHS.get(LOOP.get("thumb_loop")), BODY_PATHS.get(LOOP.get("finger_loop"))
        if ha_ and hb_:
            ba_, bb_ = body_pose(ha_), body_pose(hb_)
            if ba_ is not None and bb_ is not None:
                R_ab = quat_to_R(bb_[1]).T @ quat_to_R(ba_[1])
                # the angle between the halves about the pivot (the finger half's z, the hinge axis)
                entry["blades_deg"] = round(float(np.degrees(np.arctan2(R_ab[1, 0] - R_ab[0, 1], R_ab[0, 0] + R_ab[1, 1]))), 2)
    if THEN["kind"] == "finger_push":
        fp_ = spec.get("finger_push", {})
        bp_a = body_pose(f"/World/Objects/{fp_['object']}/" + next((q.GetPath().pathString.split(f"/World/Objects/{fp_['object']}/")[1] for q in Usd.PrimRange(target) if q.GetName() == fp_["held"]), "")) \
            if False else None
        ha = BODY_PATHS.get(fp_.get("held")); pu = BODY_PATHS.get(fp_.get("pushed"))
        if ha and pu:
            ba, bu = body_pose(ha), body_pose(pu)
            if ba is not None and bu is not None:
                R_rel = quat_to_R(bu[1]).T @ quat_to_R(ba[1])
                ang = np.degrees(np.arccos(np.clip((np.trace(R_rel) - 1) / 2, -1.0, 1.0)))
                entry["pivot_deg"] = round(float(ang), 2)
                # the SIGNED angle about the hinge (the pushed body's z): an unsigned
                # angle reads a half knocked through closed to the far stop as
                # "reopened" (take 6, 2026-10-15)
                entry["pivot_signed_deg"] = round(float(np.degrees(np.arctan2(R_rel[1, 0] - R_rel[0, 1], R_rel[0, 0] + R_rel[1, 1]))), 2)
                # the right index tip against the pushed loop's centre (the loop point rides in the pushed body, scale-free)
                if BIMANUAL and PUSH_LOOP_LOCAL is not None:
                    tip_r = body_pose(f"{LINKS}/psyonic_right_index_L2")
                    if tick == 0:
                        print(f"[instrument] loop local {np.round(PUSH_LOOP_LOCAL, 4).tolist()} tip vertex {TIP_VERTEX_R is not None} right tip pose {tip_r is not None}", flush=True)
                    if tip_r is not None:
                        tipw = tip_r[0] + quat_to_R(tip_r[1]) @ TIP_VERTEX_R
                        loopw = bu[0] + quat_to_R(bu[1]) @ PUSH_LOOP_LOCAL
                        entry["tip_to_loop_m"] = round(float(np.linalg.norm(tipw - loopw)), 4)
                        entry["loop_world"] = loopw.round(4).tolist()
                        entry["tip_right_world"] = tipw.round(4).tolist()        # the right index's tip vertex, for the push's geometry after the fact
    if HAND is not None:
        qv = np.asarray(qn, float)[0]
        entry["hand"] = HAND.status()
        if HAND_R is not None:
            entry["hand_right"] = HAND_R.status()
            entry["arm_right_q"] = [round(float(qv[dof[n]]), 4) for n in arm_names_right]
        entry["contact_n"] = digit_forces()
        if CONTACTS_MORE:
            entry["contact_n_by_body"] = digit_forces_by_body()
        tips = {d: body_pose(f"{LINKS}/psyonic_left_{d}_L2") for d in ("index", "thumb")}
        entry["tips_to_target_m"] = {d: round(float(np.linalg.norm(b[0] - c)), 4) for d, b in tips.items() if b}
        # the fingertips themselves (the cached tip vertex carried by each distal link), and the palm, in world
        entry["tip_world"] = {d: (b[0] + quat_to_R(b[1]) @ TIP_VERTEX[d]).round(4).tolist() for d, b in tips.items() if b}
        if palm:
            entry["palm_world"] = {"p": palm[0].round(4).tolist(), "q_wxyz": palm[1].round(5).tolist()}
        entry["arm_q"] = [round(float(qv[dof[n]]), 4) for n in arm_names]
        entry["target_xy"] = [round(float(c[0]), 4), round(float(c[1]), 4)]
        entry["fingers_q2_over_q1"] = [round(float(qv[dof[f"psyonic_left_{f}_q2"]] / qv[dof[f"psyonic_left_{f}_q1"]]), 3)
                                       if abs(qv[dof[f"psyonic_left_{f}_q1"]]) > 0.05 else None for f in fingers]
    if CONTACT_ALL is not None:
        entry["link_contacts"] = link_contacts()
    log.append(entry)
    if args.max_ticks and tick >= args.max_ticks:
        print(f"[replay] stopped at tick {tick} (--max-ticks)", flush=True)
        break
    if tick % 100 == 0:
        print(f"[replay] tick {tick} plan {plan_i} {stage_name}: rise {entry['target_rise_m']} m, sim {CLOCK[0]:.1f} s"
              + (f", halted {HAND.halted.tolist()}, contact {entry.get('contact_n')}" if HAND is not None else "")
              + (f", links in contact {entry.get('link_contacts')}" if CONTACT_ALL is not None else ""), flush=True)
    capture(tick + 1, stage_name)

# --- outcome ------------------------------------------------------------------------------
rise = [r["target_rise_m"] for r in log]
peak = max(rise) if rise else 0.0
end = rise[-1] if rise else 0.0
# held = the target ends risen AND the hand-to-target distance at the end of
# the run is what it was when the target left the support (within 2 cm): it
# rode with the hand. (An absolute palm distance depends on the grasp - a
# top grasp holds a can's centre 15 cm from the hand base, a side grasp a
# mug's 13 cm.) A missing measurement fails.
near = log[-1]["palm_to_target_m"] if log else None
# liftoff: the tick after the target last rested (rise < 5 mm) before its
# peak - not the first 5 mm, which a bump during the reach can produce
ipk = int(np.argmax(rise)) if rise else 0
rest_i = max((i for i in range(min(ipk + 1, len(log))) if log[i]["target_rise_m"] < 0.005), default=None)
liftoff = log[rest_i + 1] if rest_i is not None and rest_i + 1 < len(log) and peak >= 0.005 else None
before = log[:rest_i + 1] if rest_i is not None else []
disturbed = {"max_tilt_deg": max((r["target_tilt_deg"] or 0.0 for r in before), default=0.0),
             "max_rise_m": max((r["target_rise_m"] for r in before), default=0.0)}
grip_m = liftoff["palm_to_target_m"] if liftoff else None
drift = abs(near - grip_m) if near is not None and grip_m is not None else None
lifted = peak >= 0.05
kept = end >= 0.05 and drift is not None and drift < 0.02
if MEMBER_GRIP is not None:
    # a pinched member: the slip of the pinched point, not of the body's centre (it dangles about the pinch)
    g2p = [r["grip_to_pinch_m"] for r in log if r.get("grip_to_pinch_m") is not None]
    slip = (g2p[-1] - g2p[0]) if len(g2p) > 1 else None
    kept = end >= 0.05 and slip is not None and abs(slip) < 0.02
    drift = round(float(slip), 4) if slip is not None else drift
result = {"scene": spec["name"], "target": target.GetCustomDataByKey("er:label"),
          "recording": gr["reference"], "ticks": len(log), "control_hz": hz,
          "sim_seconds": round(CLOCK[0] - sim_t0, 3), "controller": CONTROLLER,
          "target_peak_rise_m": round(peak, 4), "target_end_rise_m": round(end, 4),
          "palm_to_target_end_m": near, "palm_to_target_at_liftoff_m": grip_m,
          "hand_object_drift_m": round(drift, 4) if drift is not None else None,
          "liftoff_tick": liftoff["tick"] if liftoff else None, "target_before_liftoff": disturbed,
          "grip": {**GRIP, "active": FORCE_BAND} if GRIP else None,
          "hand_model": ({**HM, "halts": [dict(zip(("t_s", "digit", "direction", "speed_rad_s"), h)) for h in HAND.halts],
                          "grip_wait_s": round(grip_wait["ticks"] / hz, 3), "grip_complete": grip_wait["complete"],
                          "contact_n_at_liftoff": liftoff.get("contact_n") if liftoff else None,
                          "contact_n_at_end": log[-1].get("contact_n") if log else None}
                         if HAND is not None else None),
          "lifted": lifted, "held": kept, "pass": bool(lifted and kept)}
if THEN["kind"] == "finger_push" and log:
    pv = [(r["tick"], r["stage"], r.get("pivot_signed_deg", r.get("pivot_deg"))) for r in log if r.get("pivot_deg") is not None]
    st_ = THEN["start_tick"]
    before = [a for t, s_, a in pv if st_ is not None and t < st_]
    sgn = 1.0 if (not before or before[-1] >= 0) else -1.0              # the hang's opening as positive: closed is 0, past closed negative
    pv = [(t, s_, a * sgn) for t, s_, a in pv]
    before = [a for t, s_, a in pv if st_ is not None and t < st_]
    push_ticks = [t for t, s_, a in pv if s_ == "push"]
    during = [a for t, s_, a in pv if s_ == "push"]
    after = [a for t, s_, a in pv if push_ticks and t > push_ticks[-1]]  # after the push itself (the pick's own hold is not "after")
    opened_stage = any(s_ == "open" for _, s_, _ in pv)
    a0 = before[-1] if before else None
    closed_by = (a0 - min(during)) if (a0 is not None and during) else None
    reopened = (max(after) - min(during)) if (during and after) else None
    # the hand's own share of the reopening: the angle gained while the finger
    # is on the loop (the "open" stages), less the half's creep at the rate
    # measured over the close's hold (the loops slip on each other under
    # gravity, 0.03 deg/tick in take 9: half of a 20 deg "reopened" was creep)
    hold_after_push = [(t, a) for t, s_, a in pv if s_ == "hold" and push_ticks and t > push_ticks[-1]]
    first_hold = [x for x in hold_after_push if not any(s_ == "open" and t2 < x[0] for t2, s_, _ in pv)]
    creep = ((first_hold[-1][1] - first_hold[0][1]) / max(1, first_hold[-1][0] - first_hold[0][0])) if len(first_hold) > 5 else 0.0
    by_hand = 0.0
    open_runs = []
    for t, s_, a in pv:
        if s_ == "open":
            if open_runs and open_runs[-1][1] == t - 1:
                open_runs[-1][1] = t; open_runs[-1][3] = a
            else:
                open_runs.append([t, t, a, a])
    for t_a, t_b, a_a, a_b in open_runs:
        by_hand += (a_b - a_a) - creep * (t_b - t_a)
    held_end = log[-1].get("palm_to_target_m")
    # still held: the pinched point's slip (the pick's own rule), not the palm's
    # distance from the half's origin, which is the pivot swinging below the pinch
    still_held = bool(kept)
    push = {"planned": THEN["plan"], "pivot_deg_at_hold": a0, "closed_by_deg": round(closed_by, 1) if closed_by is not None else None,
            "reopened_by_deg": round(reopened, 1) if reopened is not None else None, "opened_by_the_hand": opened_stage,
            "reopened_by_the_hand_deg": round(by_hand, 1) if opened_stage else None, "creep_deg_per_tick": round(creep, 4),
            "open_strokes": [{"ticks": t_b - t_a + 1, "gained_deg": round(a_b - a_a, 1)} for t_a, t_b, a_a, a_b in open_runs],
            "palm_to_held_end_m": held_end, "still_held": still_held,
            "pass": bool(closed_by is not None and closed_by >= 20.0 and still_held
                         and (not opened_stage or (reopened is not None and reopened >= 20.0 and by_hand >= 15.0)))}
    result["push"] = push
    result["pass"] = bool(result.get("pass")) and push["pass"]
    print(f"PUSH {'PASS' if push['pass'] else 'FAIL'} {spec['name']}: the pivot at {a0} deg at the hold, closed by {push['closed_by_deg']} deg, "
          f"reopened by {push['reopened_by_deg']} deg" + (f" ({push['reopened_by_the_hand_deg']} by the hand over {len(open_runs)} strokes, the rest creep at {creep * 30:.2f} deg/s)" if opened_stage else " on its own")
          + f"; the pinch {'kept' if still_held else 'LOST'} the held half "
          f"({drift} m slip), its origin {held_end} m from the palm at the end ({THEN['plan']})", flush=True)
if LOOP is not None:
    # the scissors worked by the holding hand alone: the blades open and close
    # with the hand's spread, cycle after cycle, the scissors held up on the
    # digits throughout (the use ladder's rung 2)
    bl = [(r["tick"], r["stage"], r["blades_deg"], r.get("target_rise_m")) for r in log if r.get("blades_deg") is not None]
    a_start = bl[0][2] if bl else None
    sgn = 1.0 if (a_start is None or a_start >= 0) else -1.0            # the authored opening as positive
    bl = [(t, s_, a * sgn, z) for t, s_, a, z in bl]
    phases = []                                                         # consecutive open(+hold) / close(+hold) runs
    for t, s_, a, z in bl:
        kind = "open" if s_ in ("open", "open_hold") else ("close" if s_ in ("close", "close_hold") else None)
        if kind is None:
            continue
        if phases and phases[-1]["kind"] == kind and phases[-1]["last"] == t - 1:
            phases[-1].update(last=t, end=a, min=min(phases[-1]["min"], a), max=max(phases[-1]["max"], a), rise_min=min(phases[-1]["rise_min"], z if z is not None else 1.0))
        else:
            phases.append({"kind": kind, "first": t, "last": t, "start": a, "end": a, "min": a, "max": a, "rise_min": z if z is not None else 1.0})
    min_swing = float(LOOP.get("min_swing_deg", 8.0))
    swings = []
    for k in range(1, len(phases)):
        prev, ph = phases[k - 1], phases[k]
        d_ = ph["end"] - prev["end"]
        swings.append({"kind": ph["kind"], "deg": round(d_, 1), "with_the_hand": bool((d_ >= min_swing) if ph["kind"] == "open" else (d_ <= -min_swing))})
    cycle_rise = [z for t, s_, a, z in bl if s_ in ("open", "open_hold", "close", "close_hold") and z is not None]
    held_up = bool(cycle_rise) and min(cycle_rise) >= float(LOOP.get("min_rise_m", 0.03))
    good = [w for w in swings if w["with_the_hand"]]
    need = 2 * min(3, LOOP["cycles"]) - 1                                  # at least three opens and closes (the first open swings from the lift's angle)
    loop = {"blades_deg_at_start": a_start, "blades_deg_range": [round(min((a for _, _, a, _ in bl), default=0.0), 1), round(max((a for _, _, a, _ in bl), default=0.0), 1)],
            "phases": len(phases), "swings": swings, "swings_with_the_hand": len(good), "min_rise_during_cycles_m": round(min(cycle_rise), 4) if cycle_rise else None,
            "held_up": held_up, "pass": bool(len(good) >= need and held_up)}
    result["loop"] = loop
    result["pass"] = bool(result.get("lifted")) and loop["pass"]
    print(f"LOOP {'PASS' if loop['pass'] else 'FAIL'} {spec['name']}: {len(good)} of {len(swings)} swings followed the hand's spread by >= {min_swing:.0f} deg "
          f"(blades {loop['blades_deg_range'][0]} to {loop['blades_deg_range'][1]} deg); the scissors {'stayed up' if held_up else 'did NOT stay up'} on the digits "
          f"(lowest rise during the cycles {loop['min_rise_during_cycles_m']} m)", flush=True)
if TOOL is not None:
    # the tool worked in the hand: the trigger pulled, the output at the law's
    # speed for that pull, stopped after the release, the tool held throughout
    tl = [(r["tick"], r["stage"], r["tool"]) for r in log if r.get("tool")]
    run = [t for _, s_, t in tl if s_ == "run"]
    tail = run[len(run) // 2:] if run else []
    after = [t for _, s_, t in tl if s_ == "hold"]
    peak_tr = max((t["throttle"] for _, s_, t in tl if s_ in ("pull", "run")), default=0.0)
    run_rpm = float(np.mean([t["chuck_rpm"] for t in tail])) if tail else 0.0
    law_rpm = float(np.mean([t["law_rpm"] for t in tail])) if tail else 0.0
    end_rpm = float(after[-1]["chuck_rpm"]) if after else None
    before_pull = [t["chuck_rpm"] for _, s_, t in tl if s_ in ("close", "lift", "settle")]
    tool = {"behavior": TOOL["beh"]["type"], "trigger_peak_fraction": round(peak_tr, 3),
            "trigger_peak_mm": round(1000 * max((t["trigger_m"] for _, s_, t in tl if s_ in ("pull", "run")), default=0.0), 2),
            "run_rpm": round(run_rpm, 1), "law_rpm_at_that_pull": round(law_rpm, 1),
            "tracking": round(run_rpm / law_rpm, 3) if law_rpm > 0 else None,
            "rpm_before_pull_max": round(max(before_pull, default=0.0), 1), "rpm_at_end": end_rpm,
            "powered_throughout": all(t["powered"] for _, _, t in tl)}
    tool["pass"] = bool(result["lifted"] and result["held"] and peak_tr >= 0.4 and law_rpm > 0 and run_rpm >= 0.8 * law_rpm
                        and end_rpm is not None and end_rpm <= max(30.0, 0.05 * run_rpm) and tool["rpm_before_pull_max"] < 30.0)
    result["tool"] = tool
    result["pass"] = tool["pass"]
    print(f"TOOL {'PASS' if tool['pass'] else 'FAIL'} {spec['name']}: the index pulled the trigger {tool['trigger_peak_mm']} mm "
          f"({100 * peak_tr:.0f} % of its travel); the chuck ran at {run_rpm:.0f} rpm against the law's {law_rpm:.0f} rpm for that pull "
          f"(tracking {tool['tracking']}), {end_rpm} rpm at the end after the release, {tool['rpm_before_pull_max']} rpm before the pull; "
          f"the tool rose {100 * result['target_peak_rise_m']:.1f} cm and {'stayed' if result['held'] else 'did NOT stay'} in the hand "
          f"(drift {result['hand_object_drift_m']} m); battery seated throughout: {tool['powered_throughout']}", flush=True)
if CLOTH and log:
    held_rows = [r for r in log if r["stage"] == "hold"]
    end_row = held_rows[-1] if held_rows else log[-1]
    P = cloth_points()
    tips_ = [body_pose(f"{LINKS}/psyonic_left_{d}_L2") for d in ("index", "thumb")]
    mid = (tips_[0][0] + quat_to_R(tips_[0][1]) @ TIP_VERTEX["index"] + tips_[1][0] + quat_to_R(tips_[1][1]) @ TIP_VERTEX["thumb"]) / 2
    in_pinch = float(np.linalg.norm(P[TRACK["index"]] - mid)) if TRACK["index"] is not None else None
    at_close = next((r for r in log if r["stage"] in ("lift", "grip_wait") and "cloth" in r), None)
    cloth = {"vertex": TRACK["index"], "rise_at_end_m": round(float(end_row["target_rise_m"]), 4), "peak_rise_m": round(peak, 4),
             "vertex_to_pinch_at_end_m": round(in_pinch, 4) if in_pinch is not None else None,
             "cloth_lowest_z_at_end": round(float(P[:, 2].min()), 4),
             "at_close": (at_close or {}).get("cloth"), "settled_for_s": max(args.settle, CLOTH_SETTLE_S)}
    cloth["pass"] = bool(TRACK["index"] is not None and cloth["rise_at_end_m"] >= 0.15 and in_pinch is not None and in_pinch < 0.03)
    result["cloth"] = cloth
    result["pass"] = cloth["pass"]
if PIECES0 is not None:
    # a chess move: the piece on its square, upright, carried (not pushed),
    # let go; every other piece where it stood
    grid = board_grid()
    pieces1 = chess_pieces()
    tp = target.GetPath().pathString
    tc = pieces1[tp]["centre"]
    on = square_of(tc, grid)
    off_centre = float(np.linalg.norm((tc - grid[4])[:2]))
    others = {k: float(np.linalg.norm(v["centre"] - PIECES0[k]["centre"])) for k, v in pieces1.items() if k != tp}
    moved = {pieces1[k]["start"] + pieces1[k]["piece"]: round(d_, 4) for k, d_ in others.items() if d_ > 0.01}
    displaced = [pieces1[k]["start"] for k, v in pieces1.items() if k != tp and square_of(v["centre"], grid) != v["start"]]
    placement = {}
    for k, v in pieces1.items():
        placement[square_of(v["centre"], grid)] = v["piece"].upper() if v["color"] == "white" else v["piece"]
    rows_ = []
    for r in range(7, -1, -1):
        row, n_ = "", 0
        for f in range(8):
            c_ = placement.get(f"{'abcdefgh'[f]}{r + 1}")
            if c_:
                row += (str(n_) if n_ else "") + c_
                n_ = 0
            else:
                n_ += 1
        rows_.append(row + (str(n_) if n_ else ""))
    tilt_end = log[-1].get("target_tilt_deg") if log else None
    touching = log[-1].get("contact_n") if log else None
    released = touching is None or sum(v for k, v in touching.items()) < 0.05
    move = {"to": DEST["square"], "ended_on": on, "from_centre_m": round(off_centre, 4), "tilt_deg": tilt_end,
            "carried_peak_rise_m": round(peak, 4), "released": released, "others_moved_over_1cm": moved,
            "others_off_their_squares": displaced, "fen_placement": "/".join(rows_)}
    move["pass"] = bool(on == DEST["square"] and (tilt_end or 0) < 10 and peak >= 0.01 and released and not displaced
                        and not moved)
    result["move"] = move
    result["pass"] = move["pass"]
apply_acquisition_outcome(result, acquisition)
(out / "acquisition.json").write_text(json.dumps(acquisition, indent=1))
(out / "grasp.json").write_text(json.dumps(result, indent=1))
(out / "log.json").write_text(json.dumps(log))

# --- video --------------------------------------------------------------------------------
if args.no_video:
    if "cloth" in result:
        cl = result["cloth"]
        print(f"CLOTH {'PASS' if cl['pass'] else 'FAIL'} {spec['name']} (no video): the pinched point rose {100 * cl['rise_at_end_m']:.1f} cm "
              f"(peak {100 * cl['peak_rise_m']:.1f}), {cl['vertex_to_pinch_at_end_m']} m from the pinch at the end", flush=True)
    if "move" in result:
        mv = result["move"]
        print(f"MOVE {'PASS' if mv['pass'] else 'FAIL'} {spec['name']} (no video): {result['target']} ended on {mv['ended_on']} "
              f"({mv['from_centre_m']} m from the centre of {mv['to']}), tilt {mv['tilt_deg']} deg, carried {mv['carried_peak_rise_m']} m up, "
              f"released {mv['released']}, others moved {mv['others_moved_over_1cm']}; {mv['fen_placement']}", flush=True)
    print(f"GRASP {'PASS' if result['pass'] else 'FAIL'} {spec['name']} (no video): rose {100 * peak:.1f} cm at peak, "
          f"{100 * end:.1f} cm at the end; hand-to-object drift since liftoff {drift} m", flush=True)
    print("GRASP_RESULT " + json.dumps(result), flush=True)
    app.close()
    raise SystemExit(0)
if args.frame_every > 1:
    print(f"GRASP_FRAMES {frames['close']} every {args.frame_every} ticks (no video)", flush=True)
    app.close()
    raise SystemExit(0)
OVERLAY_LABEL = str(target.GetCustomDataByKey("er:label") or target.GetName()).removeprefix("the ")
comp = out / "frames_comp"
shutil.rmtree(comp, ignore_errors=True)
comp.mkdir()
n = len(log) + 1
for i in range(n):
    big = Image.open(frames["close"] / f"f{i:05d}.png").convert("RGB")
    head = Image.open(frames["head"] / f"f{i:05d}.png").convert("RGB").resize((384, 288))
    big.paste(head, (big.width - 384 - 16, big.height - 288 - 16))
    d = ImageDraw.Draw(big)
    d.rectangle([big.width - 384 - 16, big.height - 288 - 16, big.width - 16, big.height - 16], outline=(255, 255, 255), width=2)
    rr = log[i - 1] if i else {"t": 0.0, "phase": "start", "target_rise_m": 0.0}
    stage_name = rr.get("stage", rr["phase"])
    d.text((20, 18), f"Aida R5  t={rr['t']:.2f}s  {stage_name}  {OVERLAY_LABEL} rise {100 * rr['target_rise_m']:.1f} cm",
           fill=(255, 255, 255))
    d.text((big.width - 384 - 10, big.height - 288 - 32), "head camera", fill=(255, 255, 255))
    big.save(comp / f"f{i:05d}.png")
ff = shutil.which("ffmpeg") or "ffmpeg"
for name, src in (("grasp.mp4", comp), ("grasp_overview.mp4", frames["overview"])):
    subprocess.run([ff, "-v", "error", "-y", "-framerate", str(int(hz)), "-i", str(src / "f%05d.png"),
                    "-c:v", "libx264", "-preset", "slow", "-crf", "18", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
                    str(out / name)], check=False)
picks = [round((n - 1) * f) for f in (0.0, 0.2, 0.4, 0.6, 0.8, 1.0)]
tiles = [Image.open(comp / f"f{p:05d}.png").resize((640, 360)) for p in picks]
sheet = Image.new("RGB", (3 * 640, 2 * 360))
for i, t in enumerate(tiles):
    sheet.paste(t, ((i % 3) * 640, (i // 3) * 360))
sheet.save(out / "contact_sheet.png")
for d in list(frames.values()) + [comp]:
    shutil.rmtree(d, ignore_errors=True)
if "cloth" in result:
    cl = result["cloth"]
    print(f"CLOTH {'PASS' if cl['pass'] else 'FAIL'} {spec['name']}: the pinched point rose {100 * cl['rise_at_end_m']:.1f} cm "
          f"(peak {100 * cl['peak_rise_m']:.1f}), {cl['vertex_to_pinch_at_end_m']} m from the pinch at the end", flush=True)
if "move" in result:
    mv = result["move"]
    print(f"MOVE {'PASS' if mv['pass'] else 'FAIL'} {spec['name']}: {result['target']} ended on {mv['ended_on']} "
          f"({mv['from_centre_m']} m from the centre of {mv['to']}), tilt {mv['tilt_deg']} deg, carried {mv['carried_peak_rise_m']} m up, "
          f"released {mv['released']}, others moved {mv['others_moved_over_1cm']}; {mv['fen_placement']}", flush=True)
print(f"GRASP {'PASS' if result['pass'] else 'FAIL'} {spec['name']}: {result['target']} rose {100 * peak:.1f} cm at peak, "
      f"{100 * end:.1f} cm at the end of the hold; hand-to-object drift since liftoff {drift} m", flush=True)
print("GRASP_RESULT " + json.dumps(result), flush=True)
app.close()
