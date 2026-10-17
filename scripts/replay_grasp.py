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
sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_er_scene import foreign_reference as _foreign_reference, stale_build as _stale_build  # noqa: E402
if _stale_build(scene_dir) and not args.allow_stale_spec:
    raise SystemExit(f"{scene_dir.name}: the scene was {_stale_build(scene_dir)}: rebuild with plan_er_grasp.py (the live plan rebuilds it "
                     "mid-run, and the first run on an old build differs from every run after), or pass --allow-stale-spec")
if _foreign_reference(spec, scene_dir):
    raise SystemExit(f"{scene_dir.name}: {_foreign_reference(spec, scene_dir)} - it would replay another scene's plan")
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
from pxr import Gf, Sdf, Usd, UsdGeom, UsdPhysics, Vt  # noqa: E402

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
# every body of the object measured against the hand, body by body (a loop grip's
# halves, an appliance's button and cabinet): the contact report goes on each body
# before the simulation starts, and its contact view after - one test for both (the
# views were made for an appliance's bodies without the report: all four came back
# empty, 2026-10-16)
ALL_BODY_CONTACTS = (spec.get("grasp_plan") or {}).get("task") in ("loop_grip", "button_press", "door_open") or bool(gr.get("door"))
if HM and not CLOTH:
    # the grip force each digit puts on the target, read like HomeHero's
    # object contact sensor (a sensor on the object, filtered by hand link)
    TARGET_RB.AddAppliedSchema("PhysxContactReportAPI")
    TARGET_RB.CreateAttribute("physxContactReport:threshold", Sdf.ValueTypeNames.Float).Set(0.0)
    if gr.get("tool") or ALL_BODY_CONTACTS:
        # a tool's other bodies too (its trigger): a digit on the trigger, not the
        # body around it, is what a pull needs, and a contact view needs the
        # report on its body before the simulation starts
        # the object's every body - the bodies the views are made for (BODY_PATHS, from the
        # object's root: a microwave's target is its door, and its button and cabinet are
        # the door's siblings, outside it)
        for _bp in BODY_PATHS.values():
            _q = stage.GetPrimAtPath(_bp)
            if _q != TARGET_RB:
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
    # the scene's own settings of the tool (a two-speed drill in its low gear: max_rpm)
    _beh = {**_beh, **(gr["tool"].get("behavior_overrides") or {})}
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


# a screw driven by the tool's bit (grasp_replay.screw): the screw is a kinematic
# fixture the law moves each physics step - it turns with the chuck while the bit
# sits in its head and advances a pitch a turn; the wood's torque loads the chuck
# (behaviors.screw_drive), the motor's drive pushing back against it through the
# drill's body into the hand
SCREW = None
if gr.get("screw") and TOOL is not None:
    from behaviors import screw_drive as _screw_law
    _sc = gr["screw"]
    _spec_sd = spec.get("screw_drive") or {}
    _bitc = _spec_sd.get("bit") or _sc["bit"]
    _chuck_body = next((q for q in Usd.PrimRange(_tobj) if q.GetName() == _bitc["body"]), None)
    _bit_prim = _chuck_body.GetChild(_bitc["attachment"]) if _chuck_body else None
    if _bit_prim is None or not _bit_prim.IsValid():
        raise SystemExit(f"grasp_replay.screw: no bit {_bitc['attachment']} on {_bitc['body']}")
    _rec = json.loads(_bit_prim.GetCustomDataByKey("er:attachment"))
    if "tip_body" not in _rec:
        raise SystemExit("grasp_replay.screw: the bit's record has no tip_body - rebuild the scene (build_er_scene._attach)")
    _tip_local = np.asarray(_rec["tip_body"], float)                  # in the chuck body's own frame, as built
    _dir_local = np.asarray(_rec["axis_body"], float)
    _sv = sim_view.create_rigid_body_view(f"/World/Fixtures/{_sc['fixture']}")
    _head0 = np.asarray(_spec_sd.get("head") or _sc["head"], float)
    _out = np.asarray(_spec_sd.get("out") or _sc["out"], float); _out /= np.linalg.norm(_out)
    _fx = next(f for f in spec["environment"].get("fixtures", []) if f["name"] == _sc["fixture"])
    _links = list(TOOL["view"].shared_metatype.link_names)
    if _bitc["body"] not in _links:
        raise SystemExit(f"grasp_replay.screw: {_bitc['body']} is not a link of the tool's articulation ({_links})")
    # the screw's prim, for the renderer: a kinematic target set through the tensors moves the
    # body in PhysX but not its USD transform - the film showed the screw standing proud of the
    # board while the bit ran through it, the physics having driven it home (2026-10-16)
    _sprim = stage.GetPrimAtPath(f"/World/FixtureVisuals/{_sc['fixture']}")       # its look, not its body (build_er_scene)
    if not _sprim:
        raise SystemExit(f"grasp_replay.screw: no visual twin /World/FixtureVisuals/{_sc['fixture']} - rebuild the scene")
    _sx = UsdGeom.Xformable(_sprim)
    _sops = {op.GetOpName(): op for op in _sx.GetOrderedXformOps()}
    _s_tr = _sops.get("xformOp:translate") or _sx.AddTranslateOp()
    _s_or = _sops.get("xformOp:orient") or _sx.AddOrientOp(UsdGeom.XformOp.PrecisionDouble)
    SCREW = {"view": _sv, "idx": np.arange(1, dtype=np.int32), "chuck_path": _chuck_body.GetPath().pathString,
             "usd_translate": _s_tr, "usd_orient": _s_or,
             "link": _links.index(_bitc["body"]), "n_links": len(_links),
             "tip_local": _tip_local, "dir_local": _dir_local, "head0": _head0, "out": _out,
             "origin0": np.asarray(_fx["position"], float), "state": {}, "q_prev": None, "last": {},
             "fwd": float(TOOL["beh"].get("forward_sign", 1.0)), "out_dof": TOOL["dof"][TOOL["beh"]["output"]],
             "kw": dict(pitch_m=float(_spec_sd.get("pitch_m", _sc.get("pitch_m"))), seat_depth_m=float(_spec_sd.get("seat_depth_m", _sc.get("seat_depth_m"))),
                        start_depth_m=float(_spec_sd.get("start_depth_m", _sc.get("start_depth_m"))),
                        drive_n_m=float(_sc["drive_n_m"]), tighten_n_m=float(_sc["tighten_n_m"]), strip_n_m=float(_sc["strip_n_m"])),
             "tol": {"lateral_m": float(_sc.get("engage_lateral_m", 0.0012)), "gap_m": float(_sc.get("engage_gap_m", 0.0015)),
                     "angle_deg": float(_sc.get("engage_angle_deg", 8.0))}}
    print(f"[screw] {_sc['fixture']}: head {_head0.round(4).tolist()} out {_out.round(3).tolist()}; pitch {SCREW['kw']['pitch_m'] * 1000:.2f} mm, "
          f"{SCREW['kw']['start_depth_m'] * 1000:.1f} -> {SCREW['kw']['seat_depth_m'] * 1000:.1f} mm; wood {SCREW['kw']['drive_n_m']} / "
          f"{SCREW['kw']['tighten_n_m']} / {SCREW['kw']['strip_n_m']} N m; bit {_bitc['attachment']} on {_bitc['body']}", flush=True)


def screw_step():
    """The screw law on the bit's turn and pose, every physics step. The screw turns
    with the bit's ABSOLUTE turn about the screw's axis (not the chuck's angle on a
    housing that may twist in the hand), and the wood's torque is an external torque
    on the chuck from the world: the motor drives the chuck against it and its
    reaction goes into the housing - the hand holds that, as a person holds a drill.
    (Applied as a force on the chuck's joint, the load's reaction went into the
    housing too and cancelled the motor's: the hand felt nothing, 2026-10-16.)"""
    S = SCREW
    v = TOOL["view"]
    into = -S["out"]
    vel = np.asarray(v.get_link_velocities(), float).reshape(S["n_links"], 6)
    w_fwd = float(vel[S["link"], 3:] @ into)                           # the bit's turn about the screw's axis, driving it in positive
    qd = float(np.asarray(v.get_dof_velocities(), float).reshape(-1)[S["out_dof"]])
    dphi = w_fwd * DT
    st = S["state"]
    depth = st.get("depth_m", S["kw"]["start_depth_m"])
    head = S["head0"] - S["out"] * (depth - S["kw"]["start_depth_m"])
    bp_ = body_pose(S["chuck_path"])
    engaged, lat, gap, ang = False, None, None, None
    if bp_ is not None:
        Rb = quat_to_R(bp_[1])
        tip = bp_[0] + Rb @ S["tip_local"]
        bd = Rb @ S["dir_local"]
        rel = tip - head
        gap = float(rel @ S["out"])                                    # the tip's height above the head, along the screw
        lat = float(np.linalg.norm(rel - S["out"] * gap))
        ang = float(np.degrees(np.arccos(np.clip(-bd @ S["out"], -1.0, 1.0))))
        engaged = lat <= S["tol"]["lateral_m"] and -0.003 <= gap <= S["tol"]["gap_m"] and ang <= S["tol"]["angle_deg"]
    T = _screw_law(st, dphi, engaged, **S["kw"])
    # the wood's torque on the bit, from the world, against its turn (smoothed through zero:
    # it resists, never drives)
    tq = np.zeros((1, S["n_links"], 3), np.float32)
    tq[0, S["link"]] = -into * T * np.tanh(w_fwd / np.radians(5.0))
    v.apply_forces_and_torques_at_position(np.zeros((1, S["n_links"], 3), np.float32), tq, None, TOOL["idx"], True)
    # the screw where the law has it: down its axis by the depth gained, turned by its angle
    depth = st.get("depth_m", S["kw"]["start_depth_m"])
    from scipy.spatial.transform import Rotation as _Rot
    qx = _Rot.from_rotvec(-S["out"] * st.get("theta_rad", 0.0)).as_quat()
    pos = S["origin0"] - S["out"] * (depth - S["kw"]["start_depth_m"])
    S["view"].set_kinematic_targets(np.asarray([[*pos, *qx]], np.float32), S["idx"])
    S["pose"] = (pos, qx)
    S["last"] = {"engaged": engaged, "torque_n_m": round(float(T), 3), "depth_mm": round(depth * 1000, 2),
                 "turns": round(st.get("theta_rad", 0.0) / (2 * np.pi), 2), "seated": bool(st.get("seated")), "stripped": bool(st.get("stripped")),
                 "lateral_mm": None if lat is None else round(lat * 1000, 2), "gap_mm": None if gap is None else round(gap * 1000, 2),
                 "angle_deg": None if ang is None else round(ang, 2), "chuck_rpm": round(abs(qd) * 60 / (2 * np.pi), 1),
                 "bit_rpm": round(w_fwd * 60 / (2 * np.pi), 1)}


# a hole drilled in a board (grasp_replay.hole): the bit's colliders pass the
# board in PhysX (build_er_scene._filter_bit) and the wood is the cutting law
# (material_laws.drill_cut) every physics step - the uncut wood at the hole's
# bottom pushes the bit back (the thrust), the hole's wall holds it to its line,
# the chip the thrust buys advances the bottom with the chuck's turn and its
# torque loads the chuck. Both pushes are stiff damped springs the step's
# explicit force can carry (k, c below at PHYS_HZ). The board's look takes the
# hole as it deepens (its twin's pieces); the hole is written as the take's
# outcome, which a later scene builds as exact colliders (holes_from).
HOLE = None
if gr.get("hole") and TOOL is not None:
    from material_laws import bit_reaction as _bit_reaction, board_frame as _board_frame, drill_coefficients as _drill_coeff, \
        drill_cut as _drill_cut, embedment_strength_n_mm2 as _embed, hole_pieces as _hole_pieces
    _hc = gr["hole"]
    _hb = (spec.get("screw_drive") or {}).get("bit") or _hc["bit"]
    _hchuck = next((q for q in Usd.PrimRange(_tobj) if q.GetName() == _hb["body"]), None)
    _hbit = _hchuck.GetChild(_hb["attachment"]) if _hchuck else None
    if _hbit is None or not _hbit.IsValid():
        raise SystemExit(f"grasp_replay.hole: no bit {_hb['attachment']} on {_hb['body']}")
    _hrec = json.loads(_hbit.GetCustomDataByKey("er:attachment"))
    _hfx = next(f for f in spec["environment"].get("fixtures", []) if f["name"] == _hc["fixture"])
    if not _hfx.get("drillable"):
        raise SystemExit(f"grasp_replay.hole: fixture {_hc['fixture']} is not drillable - its bit would strike it")
    _hlinks = list(TOOL["view"].shared_metatype.link_names)
    _board = _board_frame(_hfx["size"], _hfx["position"], _hc["out"])
    _bd = float(_hc["bit_d_m"])
    HOLE = {"link": _hlinks.index(_hb["body"]), "n_links": len(_hlinks), "chuck_path": _hchuck.GetPath().pathString,
            "tip_local": np.asarray(_hrec["tip_body"], float), "dir_local": np.asarray(_hrec["axis_body"], float),
            "board": _board, "n": np.asarray(_board["n"], float), "t": float(_board["t"]),
            "face": np.asarray(_board["centre"], float) + np.asarray(_board["n"], float) * float(_board["t"]) / 2,
            "coeff": _drill_coeff(_bd, float(_hc["density_kg_m3"]), int(_hc.get("blades", 2))), "bit_d_m": _bd,
            "bearing": _embed(_bd, float(_hc["density_kg_m3"])) * 1e6 * _bd, "axis": None,
            "k": float(_hc.get("k_n_per_m", 2.0e4)), "c": float(_hc.get("c_n_s_per_m", 150.0)),
            "target_depth_m": float(_hc.get("target_depth_m", _board["t"])), "sides": int(_hfx["drillable"].get("sides", 16)),
            "state": {"bit_d_m": _bd}, "entry": None, "lat_cut": [], "last": {}, "vis": f"/World/FixtureVisuals/{_hc['fixture']}",
            "vis_depth": -1.0}
    print(f"[hole] {_hc['fixture']}: a {_bd * 1000:.1f} mm bit ({_hb['attachment']}) into {_hc.get('material')} "
          f"({_hc['density_kg_m3']} kg/m3, {HOLE['t'] * 1000:.0f} mm): thrust {HOLE['coeff']['f0_n']:.1f} N + "
          f"{HOLE['coeff']['kf_n_per_mm']:.0f} N/mm of chip, torque {HOLE['coeff']['m0_nm']:.4f} + {HOLE['coeff']['km_nm_per_mm']:.3f} N m/mm; "
          f"the wood's push k {HOLE['k']:.0f} N/m c {HOLE['c']:.0f} N s/m at {int(spec.get('physics_hz', 120))} Hz; "
          f"the wall bears {HOLE['bearing'] / 1000:.0f} N per mm of bit in it", flush=True)


def hole_step():
    """The cutting law on the bit's pose and turn, every physics step (see HOLE)."""
    H = HOLE
    v = TOOL["view"]
    bp_ = body_pose(H["chuck_path"])
    if bp_ is None:
        return
    Rb = quat_to_R(bp_[1])
    tip = bp_[0] + Rb @ H["tip_local"]
    bdir = Rb @ H["dir_local"]
    n, into = H["n"], -H["n"]
    vel = np.asarray(v.get_link_velocities(), float).reshape(H["n_links"], 6)
    w_fwd = float(vel[H["link"], 3:] @ into)                             # the chuck's spin about the hole's axis
    depth_tip = float((H["face"] - tip) @ n)                             # how far the tip is into the board
    st = H["state"]
    # the hole starts where a forward-turning bit first meets the face: its axis the face's normal there
    if H["entry"] is None and depth_tip > -0.0003 and w_fwd > np.radians(360.0):
        H["entry"] = tip + n * depth_tip
        H["axis"] = H["entry"].copy()
        H["tilt_deg"] = float(np.degrees(np.arccos(np.clip(bdir @ into, -1.0, 1.0))))
        H["start_tick"] = CLOCK[0]
    # once cut, the bit's floor is the cut's bottom (the wall keeps it over it); before, the face
    inside = H["entry"] is not None
    F, thrust, _, shift = _bit_reaction(tip, H.get("tip_prev"), DT, H["face"], n, H["axis"],
                                        st.get("depth_m", 0.0) if inside else 0.0, H["k"], H["c"],
                                        cutting=not st.get("through"), bearing_n_per_m=H["bearing"])
    H["tip_prev"] = tip
    if inside:
        H["axis"] = H["axis"] + shift                                    # the wall crushed: the hole's line yields
        rel0 = tip - H["entry"]
        lat_m = float(np.linalg.norm(rel0 - into * float(rel0 @ into)))  # from the line it entered on: the hole's reach
    else:
        lat_m = 0.0
    dphi = w_fwd * DT
    was = st.get("depth_m", 0.0)
    T = _drill_cut(st, thrust, dphi if inside else 0.0, H["coeff"], H["t"])
    if st.get("depth_m", 0.0) > was:
        H["lat_cut"].append(lat_m)                                       # the bit's reach while it cut: the hole's size
    tq = np.zeros((1, H["n_links"], 3), np.float32)
    tq[0, H["link"]] = -into * T * np.tanh(w_fwd / np.radians(5.0))
    fo = np.zeros((1, H["n_links"], 3), np.float32)
    fo[0, H["link"]] = F
    po = np.zeros((1, H["n_links"], 3), np.float32)
    po[0, H["link"]] = tip
    v.apply_forces_and_torques_at_position(fo, tq, po, TOOL["idx"], True)
    H["last"] = {"depth_mm": round(st.get("depth_m", 0.0) * 1000, 3), "tip_mm": round(depth_tip * 1000, 2),
                 "thrust_n": round(thrust, 2), "torque_n_m": round(float(T), 4), "chip_mm": round(st.get("chip_mm", 0.0), 4),
                 "lateral_mm": round(lat_m * 1000, 3), "wall_n": round(float(np.linalg.norm(F - n * thrust)), 2),
                 "bit_rpm": round(w_fwd * 60 / (2 * np.pi), 1), "through": bool(st.get("through")), "started": H["entry"] is not None}


def hole_look():
    """The board's twin shaped to the hole as it is (each control tick it deepened)."""
    H = HOLE
    d = HOLE["state"].get("depth_m", 0.0)
    if H["entry"] is None or abs(d - H["vis_depth"]) < 2.5e-4:
        return
    H["vis_depth"] = d
    r = 0.5 * H["bit_d_m"] + (float(np.percentile(H["lat_cut"], 95)) if H["lat_cut"] else 0.0)
    pieces = {pc["name"]: pc for pc in _hole_pieces(H["board"], {"entry": H["entry"].tolist(), "r": r, "depth_m": max(d, 1e-5)}, H["sides"])}
    for q in stage.GetPrimAtPath(H["vis"]).GetChildren():
        nm = q.GetName()
        if nm == "Solid":
            UsdGeom.Imageable(q).MakeInvisible()
            continue
        pc = pieces.get(nm)
        m = UsdGeom.Mesh(q)
        if pc is None:
            UsdGeom.Imageable(q).MakeInvisible()
            continue
        m.GetPointsAttr().Set(Vt.Vec3fArray([Gf.Vec3f(*x) for x in pc["points"]]))
        m.GetFaceVertexCountsAttr().Set(Vt.IntArray([len(f) for f in pc["faces"]]))
        m.GetFaceVertexIndicesAttr().Set(Vt.IntArray([i for f in pc["faces"] for i in f]))
        UsdGeom.Imageable(q).MakeVisible()


# a hinged door held shut by its gasket (a refrigerator's magnetic seal): the
# law runs every physics step on the door's joint - a closing torque of the
# stated opening force at the handle, falling with the gap at the latch edge
DOOR = None
if gr.get("door"):
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from behaviors import gasket as _gasket_law, hinge_friction as _hinge_friction, latch_kick as _latch_law
    _dc = gr["door"]
    _dj = next((q for q in Usd.PrimRange(_tobj) if q.IsA(UsdPhysics.RevoluteJoint) and q.GetName() == _dc["joint"]), None)
    _droot = next((q for q in Usd.PrimRange(_tobj) if q.HasAPI(UsdPhysics.ArticulationRootAPI)), None)
    if _dj is None or _droot is None:
        raise SystemExit(f"grasp_replay.door: no revolute joint {_dc['joint']!r} or no articulation root under {_tobj.GetPath()}")
    _dv = sim_view.create_articulation_view(_droot.GetPath().pathString)
    _dnames = list(_dv.shared_metatype.dof_names)
    _rj = UsdPhysics.RevoluteJoint(_dj)
    _door_prim = stage.GetPrimAtPath(_rj.GetBody1Rel().GetTargets()[0])
    _cab_t = _rj.GetBody0Rel().GetTargets()
    _cab_name = _cab_t[0].name if _cab_t else None                   # the body the door hangs on: the critic reads the hand on it
    _xc = UsdGeom.XformCache()
    _M1 = _xc.GetLocalToWorldTransform(_door_prim)
    _lp, _lr = _rj.GetLocalPos1Attr().Get(), _rj.GetLocalRot1Attr().Get()
    _ax = {"X": Gf.Vec3d(1, 0, 0), "Y": Gf.Vec3d(0, 1, 0), "Z": Gf.Vec3d(0, 0, 1)}[str(_rj.GetAxisAttr().Get() or "Z")]
    _hinge = np.asarray(list(_M1.Transform(Gf.Vec3d(_lp))), float)
    _axis = np.asarray(list(_M1.TransformDir(Gf.Rotation(Gf.Quatd(_lr.GetReal(), Gf.Vec3d(_lr.GetImaginary()))).TransformDir(_ax))), float)
    _axis /= np.linalg.norm(_axis)
    _hname = _dc.get("handle") or ((spec.get("grasp_plan") or {}).get("door_open") or {}).get("handle")
    _hprim = next((q for q in Usd.PrimRange(_door_prim) if q.GetName() == _hname), None)
    _bbc = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.default_, UsdGeom.Tokens.render])

    def _perp(v):
        v = np.asarray(v, float) - _hinge
        return float(np.linalg.norm(v - _axis * (v @ _axis)))
    _hc = np.asarray(_bbc.ComputeWorldBound(_hprim).ComputeAlignedRange().GetMidpoint(), float) if _hprim else None
    _dr = _bbc.ComputeWorldBound(_door_prim).ComputeAlignedRange()
    _corners = [np.array([x_, y_, z_]) for x_ in (_dr.GetMin()[0], _dr.GetMax()[0]) for y_ in (_dr.GetMin()[1], _dr.GetMax()[1])
                for z_ in (_dr.GetMin()[2], _dr.GetMax()[2])]
    _r_edge = max(_perp(c_) for c_ in _corners)
    _r_h = _perp(_hc) if _hc is not None else _r_edge
    _lo, _hi = float(_rj.GetLowerLimitAttr().Get()), float(_rj.GetUpperLimitAttr().Get())
    DOOR = {"view": _dv, "dof": _dnames.index(_dc["joint"]), "n": len(_dnames), "idx": np.arange(1, dtype=np.int32),
            "sign": 1.0 if abs(_hi) >= abs(_lo) else -1.0, "r_h": _r_h, "r_edge": _r_edge,
            "force": float(_dc.get("opening_force_n", 35.0)), "decay": float(_dc.get("decay_m", 0.003)),
            "door_path": _door_prim.GetPath().pathString, "root_path": _droot.GetPath().pathString, "last": (0.0, 0.0),
            "law": _dc.get("law", "magnetic_gasket"), "state": {}, "button": None, "button_m": 0.0,
            "friction": float(_dc.get("hinge_friction_n_m", 0.0))}
    if DOOR["law"] == "latch_kick":
        # the release button: a prismatic joint of the same articulation, its travel inward positive
        _bj = next((q for q in Usd.PrimRange(_tobj) if q.IsA(UsdPhysics.PrismaticJoint) and q.GetName() == _dc["button_joint"]), None)
        if _bj is None or _dc["button_joint"] not in _dnames:
            raise SystemExit(f"grasp_replay.door: no button joint {_dc['button_joint']!r} in {_droot.GetPath()}")
        _blo, _bhi = float(_bj.GetAttribute("physics:lowerLimit").Get()), float(_bj.GetAttribute("physics:upperLimit").Get())
        DOOR["button"] = _dnames.index(_dc["button_joint"])
        _bb1 = UsdPhysics.PrismaticJoint(_bj).GetBody1Rel().GetTargets()
        DOOR["button_body"] = _bb1[0].name if _bb1 else None          # the critic reads the press on it
        DOOR["button_sign"] = 1.0 if abs(_bhi) >= abs(_blo) else -1.0
        DOOR["trip_m"] = float(_dc.get("trip_m", 0.006))
        DOOR["kw"] = dict(trip_m=DOOR["trip_m"], latch_torque_n_m=float(_dc.get("latch_torque_n_m", 30.0)),
                          kick_torque_n_m=float(_dc.get("kick_torque_n_m", 0.6)), kick_end_rad=np.radians(float(_dc.get("kick_end_deg", 10.0))))
    print(f"[door] {_dj.GetName()} ({DOOR['law']}): hinge {np.round(_hinge, 3).tolist()} axis {np.round(_axis, 3).tolist()}; handle {_r_h:.3f} m from the hinge, "
          f"latch edge {_r_edge:.3f} m" + (f"; hinge friction {DOOR['friction']} N m" if DOOR["friction"] else "") + (f"; gasket {DOOR['force']} N at the handle ({DOOR['force'] * _r_h:.1f} N m), decaying over {DOOR['decay'] * 1000:.1f} mm"
                                           if DOOR["law"] == "magnetic_gasket" else f"; latched until {_dc['button_joint']} travels {DOOR['trip_m'] * 1000:.1f} mm, "
                                           f"then kicked {DOOR['kw']['kick_torque_n_m']} N m to {np.degrees(DOOR['kw']['kick_end_rad']):.0f} deg"), flush=True)


def door_step():
    """The gasket law on the door's measured angle: a closing torque of the
    opening force at the handle, times exp(-gap / decay) with the gap the
    latch edge's travel from closed. Every physics step."""
    v = DOOR["view"]
    qd_ = np.asarray(v.get_dof_positions(), float).reshape(-1)
    th = float(qd_[DOOR["dof"]]) * DOOR["sign"]
    if DOOR["law"] == "latch_kick":
        DOOR["button_m"] = float(qd_[DOOR["button"]]) * DOOR["button_sign"]
        T = _latch_law(th, DOOR["button_m"], DOOR["state"], **DOOR["kw"])
    else:
        T = _gasket_law(th, DOOR["force"], DOOR["r_h"], DOOR["r_edge"], DOOR["decay"])
    f = np.zeros((1, DOOR["n"]), np.float32)
    # the gasket's law gives a closing magnitude; the latch's a signed torque, positive
    # opening (negated, the latch's 30 N m hold flung a microwave's door open, 2026-10-16)
    f[0, DOOR["dof"]] = (DOOR["sign"] * T) if DOOR["law"] == "latch_kick" else (-DOOR["sign"] * T)
    if DOOR["friction"] > 0.0:
        # the hinge's friction, against the door's turning (behaviors.hinge_friction)
        om = float(np.asarray(v.get_dof_velocities(), float).reshape(-1)[DOOR["dof"]]) * DOOR["sign"]
        f[0, DOOR["dof"]] += DOOR["sign"] * _hinge_friction(om, DOOR["friction"])
    v.set_dof_actuation_forces(f, DOOR["idx"])
    DOOR["last"] = (th, T)

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
        if ALL_BODY_CONTACTS:
            # every body of the object, logged body by body: the scissors' index is in the
            # OTHER half's loop (the target body is the thumb's half); a microwave's index
            # pressed its release button 8.76 mm while the force on the door - the target -
            # read 0.0 N, and the critic took the press for untouched (Gemini 4, 2026-10-16)
            CONTACTS_MORE = []
            for bp_ in BODY_PATHS.values():
                if bp_ == TARGET_RB.GetPath().pathString:
                    continue
                cv_ = sim_view.create_rigid_contact_view(bp_, filter_patterns=HAND_LINKS)
                if getattr(cv_, "_backend", True) is None:
                    # a body PhysX gives no contact view (a microwave's lamp, filtered against its
                    # cabinet: the view came back empty and the replay died at its first read)
                    print(f"[hand] no contact view on {bp_.split('/')[-1]}: its contacts go unmeasured", flush=True)
                    continue
                cv_.body_path = bp_
                CONTACTS_MORE.append(cv_)
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
    views = [(TARGET_RB.GetPath().pathString, CONTACTS)] + [(cv_.body_path, cv_) for cv_ in CONTACTS_MORE]
    for bp_, cv_ in views:
        try:
            n = np.linalg.norm(np.asarray(cv_.get_contact_force_matrix(DT), float).reshape(-1, 3), axis=1)
        except AttributeError as e:                              # an empty view: unmeasured, not a crash
            print(f"[hand] contact view on {names[bp_]} unreadable ({e}): dropped", flush=True)
            if cv_ in CONTACTS_MORE:
                CONTACTS_MORE.remove(cv_)
            continue
        per = [round(float(n[2 * k] + n[2 * k + 1]), 3) for k in range(5)]
        out[names[bp_]] = {"index": per[0], "middle": per[1], "ring": per[2], "pinky": per[3], "thumb": per[4], "palm": round(float(n[10]), 3)}
    return out


def digit_forces():
    """Each digit's contact force on the target (N; L1 + L2), and the palm's."""
    if CONTACTS is None:
        return None
    m = np.asarray(CONTACTS.get_contact_force_matrix(DT), float).reshape(-1, 3)
    n = np.linalg.norm(m, axis=1)
    if (spec.get("grasp_plan") or {}).get("task") == "loop_grip":
        for cv_ in CONTACTS_MORE:                               # the object's other bodies (a loop grip's other half)
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
        if DOOR is not None:
            door_step()                                 # the door's gasket, every physics step
        if SCREW is not None:
            screw_step()                                # the screw on the bit, every physics step
        if HOLE is not None:
            hole_step()                                 # the wood under the bit, every physics step
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
    door_stage = stage.split(":")[0] == "door_edge"
    screw_stage = stage.split(":")[0] == "screw_drive"
    tool_stage = stage.split(":")[0] == "tool_grip"
    # a door's next stage (door_then.py: the door where it is now, its free edge the handle),
    # or the other hand's push on a held object (finger_push_planner.py)
    tool_ = ("door_then.py" if door_stage else "screw_then.py" if screw_stage else "tool_then.py" if tool_stage
             else "finger_push_planner.py")
    cmd = [os.path.expanduser("~/Documents/Github/HomeHero/.venv/bin/python"), str(Path(__file__).resolve().parent / tool_),
           str(scene_dir), "--hold", str(scene_dir / "grasp_plan" / f"hold{sfx}.npz"), "--poses", str(scene_dir / f"settled_poses_hold{sfx}.json"),
           "--out", str(out_npz)] + ([] if door_stage or tool_stage else ["--part", stage.split(":")[1] if ":" in stage else "drive"] if screw_stage
                                     else ["--stroke", stroke])
    r = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=1800)
    verdict = next((ln for ln in r.stdout.splitlines() if ln.startswith(("PUSH_PLAN", "DOOR_PLAN", "SCREW_PLAN", "TOOL_PLAN"))), None)
    (scene_dir / "grasp_plan" / f"then_plan{sfx}.log").write_text(r.stdout[-20000:] + "\n--- stderr ---\n" + r.stderr[-20000:])
    print(f"[then {k} {stage}] {verdict or 'the push planner printed no verdict'}", flush=True)
    THEN["plans"].append(f"{stage}: {verdict}")
    THEN["plan"] = " | ".join(THEN["plans"])
    if not (verdict or "").startswith(("PUSH_PLAN OK", "DOOR_PLAN OK", "SCREW_PLAN OK", "TOOL_PLAN OK")):
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
    if SCREW is not None:
        tool_entry = dict(tool_entry or {}, screw=dict(SCREW["last"]))
        if SCREW.get("pose") is not None:
            # the render follows the physics: the screw's visual twin set where the law put it (its
            # body's transform is never written: PhysX took that as a teleport)
            _pp, _qq = SCREW["pose"]
            SCREW["usd_translate"].Set(Gf.Vec3d(*[float(v) for v in _pp]))
            _o = SCREW["usd_orient"]
            _qv = Gf.Quatd(float(_qq[3]), Gf.Vec3d(float(_qq[0]), float(_qq[1]), float(_qq[2])))
            _o.Set(_qv if _o.GetPrecision() == UsdGeom.XformOp.PrecisionDouble else Gf.Quatf(_qv))
    if HOLE is not None:
        tool_entry = dict(tool_entry or {}, hole=dict(HOLE["last"]))
        hole_look()
    if DOOR is not None:
        th_, T_ = DOOR["last"]
        bp_ = body_pose(DOOR["root_path"])
        door_entry = {"deg": round(float(np.degrees(th_)), 3), "seal_n_m": round(float(T_), 3),
                      "seal_at_handle_n": round(float(T_) / DOOR["r_h"], 2) if DOOR["r_h"] > 0 else None,
                      "appliance_p": np.round(np.asarray(bp_[0], float), 4).tolist() if bp_ is not None else None}
        if DOOR["law"] == "latch_kick":
            door_entry.update(button_mm=round(DOOR["button_m"] * 1000, 2), latched=bool(DOOR["state"].get("latched", True)))
        tool_entry = dict(tool_entry or {}, door=door_entry)
        db_ = body_pose(DOOR["door_path"])
        if db_ is not None and "body" not in tool_entry:
            # the door's own pose: the critic reads the handle's motion in the palm from it
            tool_entry["body"] = {"p": np.round(np.asarray(db_[0], float), 5).tolist(), "q_wxyz": np.round(np.asarray(db_[1], float), 6).tolist()}
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
if DOOR is not None:
    # the door opened by the hand at its handle: the gasket broken, the door
    # swung at least the plan's least angle, the hand on the door while it
    # pulled, the appliance where it stood
    dl = [(r["stage"], r["tool"]["door"], r.get("contact_n") or {}) for r in log if (r.get("tool") or {}).get("door")]
    degs = [d["deg"] for _, d, _ in dl]
    pull_rows = [(d, c) for s_, d, c in dl if s_ == "pull"]
    plan_ = json.loads(Path(gr["reference"]).expanduser().with_name("plan.json").read_text()) if Path(gr["reference"]).expanduser().with_name("plan.json").exists() else {}
    planned = float(plan_.get("pull_deg") or ((spec.get("grasp_plan") or {}).get("door_open") or {}).get("open_deg", 40.0))
    least = float(((spec.get("grasp_plan") or {}).get("door_open") or {}).get("min_open_deg", 25.0))
    if THEN.get("kind") == "door_edge" and spec.get("door_edge"):
        # the door swung by a later stage (a microwave's, pushed from inside after its pop): that
        # stage's plan, from where the door stood when it began; its least is the door's angle
        _tp = scene_dir / "grasp_plan" / "then_targets.plan.json"
        _tpj = json.loads(_tp.read_text()) if _tp.exists() else {}
        _de = spec["door_edge"]
        _deg0 = pull_rows[0][0]["deg"] if pull_rows else 0.0
        planned = round(float(_deg0) + float(_tpj.get("pull_deg") or _de.get("open_deg", 40.0)), 1)
        least = float(_de.get("min_open_deg", least))
    hand_n = [sum(v for k, v in c.items() if k != "palm") for _, c in pull_rows]
    p0 = next((d["appliance_p"] for _, d, _ in dl if d.get("appliance_p")), None)
    p1 = next((d["appliance_p"] for _, d, _ in reversed(dl) if d.get("appliance_p")), None)
    moved = float(np.linalg.norm(np.asarray(p1) - np.asarray(p0))) if p0 and p1 else None
    door = {"opening_force_n": DOOR["force"], "handle_radius_m": round(DOOR["r_h"], 3), "planned_deg": planned, "least_deg": least,
            "max_deg": round(max(degs), 1) if degs else None, "deg_at_end_of_pull": round(pull_rows[-1][0]["deg"], 1) if pull_rows else None,
            "deg_at_end": round(degs[-1], 1) if degs else None,
            "seal_broken": bool(degs and max(degs) > np.degrees(3 * DOOR["decay"] / DOOR["r_edge"])),
            "hand_on_door_mean_n": round(float(np.mean(hand_n)), 2) if hand_n else None,
            "hand_on_door_min_n": round(float(np.min(hand_n)), 2) if hand_n else None,
            "appliance_moved_m": round(moved, 4) if moved is not None else None}
    door["followed_the_hand"] = bool(door["deg_at_end_of_pull"] is not None and door["deg_at_end_of_pull"] >= 0.8 * planned)
    door["pass"] = bool(door["seal_broken"] and (door["max_deg"] or 0) >= least and (door["hand_on_door_mean_n"] or 0) >= 1.0
                        and (moved is None or moved < 0.01))
    if DOOR["law"] == "latch_kick":
        # the button pressed past its trip, the latch held until then, the door kicked ajar
        bt = [(d.get("button_mm") or 0.0, d["deg"], d.get("latched")) for _, d, _ in dl]
        trip_i = next((i for i, (b_, _, _) in enumerate(bt) if b_ >= DOOR["trip_m"] * 1000), None)
        before = [g_ for b_, g_, _ in bt[:trip_i]] if trip_i is not None else [g_ for _, g_, _ in bt]
        # the pop: from the trip to the end of the press's own plan (a later stage's
        # rows are the hand's work, not the kick's: a door creeping open on its tilted
        # hinge read as a pop to 90 deg, 2026-10-16)
        _pt = [r.get("plan_tick", r["tick"]) for r in log if (r.get("tool") or {}).get("door")]
        _st = THEN.get("start_tick")
        _end_i = next((i for i, t_ in enumerate(_pt) if _st is not None and t_ >= _st), len(bt))
        after = [g_ for _, g_, _ in bt[trip_i:_end_i]] if trip_i is not None else []
        door.update(button_body=DOOR.get("button_body"), cabinet_body=_cab_name, button_max_mm=round(max((b_ for b_, _, _ in bt), default=0.0), 2), tripped=trip_i is not None,
                    latch_held_deg=round(max(before, default=0.0), 2), popped_deg=round(max(after, default=0.0), 1),
                    seal_broken=None)
        kick_end = float(np.degrees(DOOR["kw"]["kick_end_rad"]))
        popped = door["popped_deg"] >= 0.5 * kick_end
        # opened by the hand: the door at least `least` open, and the hand's push the cause -
        # the door turned through the pull with the hand on it (the creep above passed on
        # max_deg alone, the hand 0.0 N on a door already at 90 deg)
        if pull_rows:
            # turned only while the hand was on it (door_verdict: a force floor fails a light door)
            from door_verdict import hand_drove_door
            _v = hand_drove_door([d_["deg"] for d_, _ in pull_rows], hand_n, least, planned - float(pull_rows[0][0]["deg"]))
            door.update(deg_at_start_of_pull=round(float(pull_rows[0][0]["deg"]), 1), **{k: v for k, v in _v.items() if k != "opened_by_hand"})
            opened_by_hand = _v["opened_by_hand"]
        else:
            # a later stage was to swing the door and never ran (its plan failed): not opened
            opened_by_hand = False if (THEN.get("kind") == "door_edge" and spec.get("door_edge")) else None
        door["opened_by_hand"] = opened_by_hand
        door["pass"] = bool(door["tripped"] and door["latch_held_deg"] <= 1.0 and popped and (moved is None or moved < 0.01)
                            and (opened_by_hand is not False))
    result["door"] = door
    result["pass"] = door["pass"]
    if DOOR["law"] == "latch_kick":
        print(f"BUTTON {'PASS' if door['tripped'] else 'FAIL'} {spec['name']}: the button pressed {door['button_max_mm']} mm (trips at {DOOR['trip_m'] * 1000:.1f}); "
              f"the latch held the door within {door['latch_held_deg']} deg until then; the door popped to {door['popped_deg']} deg", flush=True)
    _seal = (f"the hand found it at {door.get('deg_at_start_of_pull')} deg and pushed it {door.get('pushed_by_hand_deg')} deg, "
             f"on the door through {100 * (door.get('turning_with_hand_fraction') or 0):.0f} % of its turning ({door.get('hand_n_while_turning')} N)"
             if DOOR["law"] == "latch_kick" else f"gasket {'broken' if door['seal_broken'] else 'HELD'} against {DOOR['force']} N at the handle")
    print(f"DOOR {'PASS' if door['pass'] else 'FAIL'} {spec['name']}: the door opened {door['max_deg']} deg at most ({door['deg_at_end_of_pull']} deg at the end of "
          f"the pull, planned {planned}, least {least}); {_seal}; "
          f"the hand on the door {door['hand_on_door_mean_n']} N mean through the pull; the appliance moved {door['appliance_moved_m']} m; "
          f"{door['deg_at_end']} deg at the end", flush=True)
if TOOL is not None:
    # the tool worked in the hand: the trigger pulled, the output at the law's
    # speed for that pull, stopped after the release, the tool held throughout
    tl = [(r["tick"], r["stage"], r["tool"]) for r in log if r.get("tool")]
    # the run: the plain trigger test's "run" stage - or a screw's drive, the trigger held through
    # "spin", "drive" and "seat" (no "run": the record read 0 rpm for a chuck that turned a screw
    # 15.8 times, and Gemini 4 took the screw for pushed in without turning, 2026-10-16)
    run = [t for _, s_, t in tl if s_ == "run"] or [t for _, s_, t in tl if s_ in ("spin", "drive")]
    tail = run[len(run) // 2:] if run else []
    after = [t for _, s_, t in tl if s_ == "hold"]
    _TRIG = ("pull", "run", "spin", "drive", "seat")                 # a screw's drive holds the trigger through these
    peak_tr = max((t["throttle"] for _, s_, t in tl if s_ in _TRIG), default=0.0)
    run_rpm = float(np.mean([t["chuck_rpm"] for t in tail])) if tail else 0.0
    law_rpm = float(np.mean([t["law_rpm"] for t in tail])) if tail else 0.0
    end_rpm = float(after[-1]["chuck_rpm"]) if after else None
    before_pull = [t["chuck_rpm"] for _, s_, t in tl if s_ in ("close", "lift", "settle")]
    tool = {"behavior": TOOL["beh"]["type"], "trigger_peak_fraction": round(peak_tr, 3),
            "trigger_peak_mm": round(1000 * max((t["trigger_m"] for _, s_, t in tl if s_ in _TRIG), default=0.0), 2),
            "run_rpm": round(run_rpm, 1), "law_rpm_at_that_pull": round(law_rpm, 1),
            "tracking": round(run_rpm / law_rpm, 3) if law_rpm > 0 else None,
            "rpm_before_pull_max": round(max(before_pull, default=0.0), 1), "rpm_at_end": end_rpm,
            "powered_throughout": all(t["powered"] for _, _, t in tl)}
    # the switch: where the law read it at the start and the end, and whether the hand set it to
    # forward in its own stage (the thumb's press) - authored there, the critic caps the ladder
    _dirs = [(s_, t.get("direction")) for _, s_, t in tl if t.get("direction") is not None]
    if _dirs:
        tool.update(direction_at_start=_dirs[0][1], direction_at_end=_dirs[-1][1],
                    switch_set_by_hand=bool(_dirs[0][1] != 1 and any(d_ == 1 for s_, d_ in _dirs
                                                                     if s_ in ("switch", "switch_release", "press", "pressed", "release"))
                                            and _dirs[-1][1] == 1))
    # the use ladder's rung 2, in its own words: the chuck at the motor law's speed for the pull it got, stopped on
    # the release, still before the pull - apart from how far the trigger went (the 40 % floor below; the grip's
    # full_trigger_travel grades the reach): a 35 % pull that ran the chuck to the law read as "did not run" (2026-10-17)
    tool["ran_per_law"] = bool(law_rpm > 0 and run_rpm >= 0.8 * law_rpm and end_rpm is not None
                               and end_rpm <= max(30.0, 0.05 * run_rpm) and tool["rpm_before_pull_max"] < 30.0)
    tool["pull_reach_ok"] = bool(peak_tr >= 0.4)
    tool["pass"] = bool(result["lifted"] and result["held"] and tool["pull_reach_ok"] and tool["ran_per_law"])
    result["tool"] = tool
    result["pass"] = tool["pass"]
    print(f"TOOL {'PASS' if tool['pass'] else 'FAIL'} {spec['name']}: the index pulled the trigger {tool['trigger_peak_mm']} mm "
          f"({100 * peak_tr:.0f} % of its travel); the chuck ran at {run_rpm:.0f} rpm against the law's {law_rpm:.0f} rpm for that pull "
          f"(tracking {tool['tracking']}), {end_rpm} rpm at the end after the release, {tool['rpm_before_pull_max']} rpm before the pull; "
          f"the tool rose {100 * result['target_peak_rise_m']:.1f} cm and {'stayed' if result['held'] else 'did NOT stay'} in the hand "
          f"(drift {result['hand_object_drift_m']} m); battery seated throughout: {tool['powered_throughout']}", flush=True)
if SCREW is not None:
    # the screw driven by the bit: seated flush, not stripped, the whole length driven,
    # the drill still in the hand - and it turned only with the bit in its head
    sl = [(r["stage"], r["tool"]["screw"]) for r in log if (r.get("tool") or {}).get("screw")]
    kw_ = SCREW["kw"]
    turned = [(s_, x) for k_, (s_, x) in enumerate(sl) if k_ and x["turns"] > sl[k_ - 1][1]["turns"] + 1e-6]
    drove_mm = (sl[-1][1]["depth_mm"] - kw_["start_depth_m"] * 1000) if sl else 0.0
    need_mm = (kw_["seat_depth_m"] - kw_["start_depth_m"]) * 1000
    drv = [x for s_, x in sl if s_ in ("drive", "seat")]
    screw_v = {"start_mm": round(kw_["start_depth_m"] * 1000, 2), "end_mm": sl[-1][1]["depth_mm"] if sl else None, "drove_mm": round(drove_mm, 2),
               "needed_mm": round(need_mm, 2), "turns": sl[-1][1]["turns"] if sl else 0.0,
               "seated": bool(SCREW["state"].get("seated")), "stripped": bool(SCREW["state"].get("stripped")),
               "turned_with_bit_in_head": round(float(np.mean([x["engaged"] for _, x in turned])), 3) if turned else None,
               "engaged_in_drive": round(float(np.mean([x["engaged"] for x in drv])), 3) if drv else None,
               "peak_torque_n_m": round(max((x["torque_n_m"] for _, x in sl), default=0.0), 3),
               "chuck_rpm_in_drive": round(float(np.mean([x["chuck_rpm"] for x in drv])), 1) if drv else None,
               "closest_lateral_mm": min((x["lateral_mm"] for _, x in sl if x["lateral_mm"] is not None), default=None),
               "held": bool(result.get("held"))}
    screw_v["pass"] = bool(screw_v["seated"] and not screw_v["stripped"] and drove_mm >= 0.95 * need_mm and screw_v["held"])
    result["screw"] = screw_v
    result["pass"] = screw_v["pass"]
    print(f"SCREW {'PASS' if screw_v['pass'] else 'FAIL'} {spec['name']}: the screw went {screw_v['drove_mm']} of {screw_v['needed_mm']} mm in "
          f"{screw_v['turns']} turns, {'seated' if screw_v['seated'] else 'NOT seated'}{', STRIPPED' if screw_v['stripped'] else ''}; "
          f"the bit in its head through {100 * (screw_v['engaged_in_drive'] or 0):.0f} % of the drive (closest {screw_v['closest_lateral_mm']} mm off its axis), "
          f"peak {screw_v['peak_torque_n_m']} N m at {screw_v['chuck_rpm_in_drive']} rpm; the drill {'held' if screw_v['held'] else 'NOT held'}", flush=True)
if HOLE is not None:
    # the hole the bit cut: as deep as asked (through, for a through hole), the bit's size and
    # no more than the play it cut with, on its line; the drill still in the hand. Its shape is
    # the take's outcome: a later scene builds the board with it (build_er_scene holes_from)
    hl = [(r["stage"], r["tool"]["hole"]) for r in log if (r.get("tool") or {}).get("hole")]
    st_ = HOLE["state"]
    cutting = [x for _, x in hl if x["chip_mm"] > 0]
    r_ = 0.5 * HOLE["bit_d_m"] + (float(np.percentile(HOLE["lat_cut"], 95)) if HOLE["lat_cut"] else 0.0)
    need = HOLE["target_depth_m"]
    hole_v = {"depth_mm": round(st_.get("depth_m", 0.0) * 1000, 2), "needed_mm": round(need * 1000, 2), "through": bool(st_.get("through")),
              "diameter_mm": round(2000 * r_, 3), "bit_mm": round(HOLE["bit_d_m"] * 1000, 3),
              "lateral_max_mm": round(1000 * max(HOLE["lat_cut"], default=0.0), 3), "tilt_deg": round(HOLE.get("tilt_deg") or 0.0, 2),
              "turns": round(st_.get("turns", 0.0), 2), "cut_cm3": round(st_.get("cut_m3", 0.0) * 1e6, 4),
              "thrust_mean_n": round(float(np.mean([x["thrust_n"] for x in cutting])), 1) if cutting else None,
              "thrust_peak_n": round(max((x["thrust_n"] for _, x in hl), default=0.0), 1),
              "chip_mean_mm": round(float(np.mean([x["chip_mm"] for x in cutting])), 4) if cutting else None,
              "torque_peak_n_m": round(max((x["torque_n_m"] for _, x in hl), default=0.0), 4),
              "rpm_cutting": round(float(np.mean([x["bit_rpm"] for x in cutting])), 1) if cutting else None,
              "entry": None if HOLE["entry"] is None else np.round(HOLE["entry"], 5).tolist(), "held": bool(result.get("held"))}
    hole_v["pass"] = bool(HOLE["entry"] is not None and st_.get("depth_m", 0.0) >= need - 5e-4 and r_ * 2 <= HOLE["bit_d_m"] + 5e-4
                          and hole_v["held"])
    result["hole"] = hole_v
    result["pass"] = hole_v["pass"]
    if HOLE["entry"] is not None:
        (out / "outcome.json").write_text(json.dumps({
            "kind": "drill_hole_wood", "scene": spec["name"], "fixture": gr["hole"]["fixture"], "material": gr["hole"].get("material"),
            "board_size": next(f for f in spec["environment"]["fixtures"] if f["name"] == gr["hole"]["fixture"])["size"],
            "holes": [{"entry": np.round(HOLE["entry"], 6).tolist(), "r": round(r_, 6), "depth_m": round(st_.get("depth_m", 0.0), 6),
                       "out": HOLE["n"].tolist(), "sides": HOLE["sides"], "through": bool(st_.get("through")),
                       "bit_d_m": HOLE["bit_d_m"], "tilt_deg": hole_v["tilt_deg"]}],
            "law": "material_laws.drill_cut", "sources": gr["hole"].get("sources")}, indent=1))
    print(f"HOLE {'PASS' if hole_v['pass'] else 'FAIL'} {spec['name']}: {hole_v['depth_mm']} of {hole_v['needed_mm']} mm"
          f"{' (through)' if hole_v['through'] else ''}, {hole_v['diameter_mm']} mm across for a {hole_v['bit_mm']} mm bit (tilt {hole_v['tilt_deg']} deg); "
          f"thrust {hole_v['thrust_mean_n']} N mean / {hole_v['thrust_peak_n']} N peak, chip {hole_v['chip_mean_mm']} mm a blade at "
          f"{hole_v['rpm_cutting']} rpm, {hole_v['turns']} turns; the drill {'held' if hole_v['held'] else 'NOT held'}", flush=True)
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
# a container's seal, by its structure: a target with no joints is one rigid body - its cap or lid
# cannot open (HomeHero's container_sealed criterion; a jointed lid needs its joint's state read)
_tjs = [q.GetName() for q in Usd.PrimRange(target) if q.IsA(UsdPhysics.Joint)]
result["container"] = {"sealed": not _tjs, "joints": _tjs[:8],
                       "why": "one rigid body: its cap cannot open" if not _tjs else "jointed: its lid's state is not read here"}
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
OVERLAY_LABEL = str(target.GetCustomDataByKey("er:label") or target.GetName()).removeprefix("the ")
# the film, from every tick's frames or every Nth (compose_film.py: plays at the control rate / N)
sys.path.insert(0, str(Path(__file__).resolve().parent))
from compose_film import compose as _compose_film  # noqa: E402
print("FILM " + json.dumps(_compose_film(out, log, OVERLAY_LABEL, hz)), flush=True)
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
