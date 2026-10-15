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
args = ap.parse_args()

scene_dir = Path(args.scene_dir).resolve()
spec = json.loads((scene_dir / "spec.json").read_text())
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


def target_centre():
    bc = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.default_, UsdGeom.Tokens.render])
    return np.array(bc.ComputeWorldBound(target).ComputeAlignedRange().GetMidpoint(), float)


TILT_REF = {}


def tilt_deg():
    """How far the target has leaned since the replay began (its own axes
    are the asset's: a Y-up can reads 90 degrees from world up at rest)."""
    b = body_pose(target_body.GetPath().pathString)
    if b is None:
        return None
    R = quat_to_R(b[1])
    R0 = TILT_REF.setdefault("R", R)
    up0 = R0.T @ np.array([0.0, 0.0, 1.0])                 # world up, in the target's frame at the start
    return round(math.degrees(math.acos(max(-1.0, min(1.0, float((R @ up0)[2]))))), 1)


HM = gr.get("hand_model") or {}
if HM.get("kind") not in (None, "ability_hand"):
    raise SystemExit(f"unknown grasp_replay.hand_model.kind {HM.get('kind')!r}")
TARGET_RB = next((q for q in Usd.PrimRange(target) if q.HasAPI(UsdPhysics.RigidBodyAPI)), target_body)
if HM:
    # the grip force each digit puts on the target, read like HomeHero's
    # object contact sensor (a sensor on the object, filtered by hand link)
    TARGET_RB.AddAppliedSchema("PhysxContactReportAPI")
    TARGET_RB.CreateAttribute("physxContactReport:threshold", Sdf.ValueTypeNames.Float).Set(0.0)

# --- start: posture, then settle --------------------------------------------------------
timeline = omni.timeline.get_timeline_interface()
timeline.play()
for _ in range(10):
    app.update()
sim_view = tensors.create_simulation_view("numpy", stage_id=omni.usd.get_context().get_stage_id())
sim_view.set_subspace_roots("/")
art = sim_view.create_articulation_view(f"{LINKS}/amber_base_link")
dof = {n: i for i, n in enumerate(art.shared_metatype.dof_names)}
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
CONTACTS = None
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
    HAND_LIM = LIMITS.astype(np.float32).copy()
    # set_dof_limits writes every joint; the wheels and casters have none,
    # and the tensor plugin warns for each of them on every call (480 Hz)
    carb.settings.get_settings().set("/log/channels/omni.physx.tensors.plugin", "error")
    HAND_VT = np.zeros((1, NDOF), np.float32)
    HAND_LINKS = [f"{LINKS}/psyonic_left_{d}_{part}" for d in FINGERS + ("thumb",) for part in ("L1", "L2")] \
        + [f"{LINKS}/psyonic_left_base"]
    try:
        CONTACTS = sim_view.create_rigid_contact_view(TARGET_RB.GetPath().pathString, filter_patterns=HAND_LINKS)
    except Exception as e:                              # the grip still runs; its forces go unmeasured
        print(f"[hand] no contact view: {e}", flush=True)


def hand_step():
    qa = np.asarray(art.get_dof_positions(), float)[0]
    qda = np.asarray(art.get_dof_velocities(), float)[0]
    v_t, lo, hi = HAND.step(DT, CMD[0, MOTOR], qa[MOTOR], qda[MOTOR])
    HAND_VT[0, MOTOR] = v_t
    HAND_LIM[0, MOTOR, 0], HAND_LIM[0, MOTOR, 1] = lo, hi
    art.set_dof_velocity_targets(HAND_VT, idx)
    art.set_dof_limits(HAND_LIM, idx)


def digit_forces():
    """Each digit's contact force on the target (N; L1 + L2), and the palm's."""
    if CONTACTS is None:
        return None
    m = np.asarray(CONTACTS.get_contact_force_matrix(DT), float).reshape(-1, 3)
    n = np.linalg.norm(m, axis=1)
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


simulate(args.settle)

# --- the recording ---------------------------------------------------------------------
_rec = np.load(Path(gr["reference"]).expanduser())
ref = _rec["targets"]
STAGES = [str(x) for x in _rec["stages"]] if "stages" in _rec.files else None   # a planned path names its stages
robot = json.loads(Path(gr["robot"]).expanduser().read_text())
arm_names = robot["arm_names"]
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


def capture(i):
    if args.no_video:
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
log = []
sim_t0 = CLOCK[0]
px.update_transformations(False, True, False, False)
for _ in range(3):                                      # the renderer catches up with the posture first
    app.update()                                        # (the first film's frame 0 showed the arm still down)
capture(0)
GRIP_WAIT_S = float(HM.get("grip_wait_s", 3.0))
grip_wait = {"ticks": 0, "complete": None}


def schedule():
    """The plan's rows; with the hand's firmware, the lift waits for the
    grip to complete, as a controller reading the hand's grip-completion
    byte would."""
    for i, row in enumerate(rows):
        stage = STAGES[min(i, len(STAGES) - 1)] if STAGES else ("replay" if i < len(ref) else "hold")
        if HAND is not None and STAGES and stage == "lift" and grip_wait["complete"] is None:
            prev = rows[i - 1]
            for _ in range(int(GRIP_WAIT_S * hz)):
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
            grip_wait["complete"] = (grip_loaded["ticks"] >= GB["load_ticks"]) if FORCE_BAND else HAND.grip_complete(qa[MOTOR], qda[MOTOR])
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
    simulate(1.0 / hz)
    c = target_centre()
    palm = body_pose(f"{LINKS}/psyonic_left_base")
    qn = art.get_dof_positions()
    entry = {"tick": tick, "plan_tick": plan_i, "t": round(tick / hz, 3), "stage": stage_name,
             "phase": "replay" if plan_i < len(ref) else "hold",
             "target_rise_m": round(float(c[2] - rest_c[2]), 4),
             "palm_to_target_m": round(float(np.linalg.norm(palm[0] - c)), 4) if palm else None,
             "lift_m": round(float(qn[0, dof["amber_left_shoulder_lift_joint"]]), 4),
             "lift_cmd_m": round(float(CMD[0, dof["amber_left_shoulder_lift_joint"]]), 4),
             "lift_gravity_offset_m": round(float(SENT["offset"][dof["amber_left_shoulder_lift_joint"]]), 4),
             "thumb_flex": round(float(qn[0, dof["psyonic_left_thumb_q2"]]), 3),
             "target_tilt_deg": tilt_deg(),
             "fingers_q1": [round(float(qn[0, dof[f"psyonic_left_{f}_q1"]]), 3) for f in fingers]}
    if HAND is not None:
        qv = np.asarray(qn, float)[0]
        entry["hand"] = HAND.status()
        entry["contact_n"] = digit_forces()
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
    log.append(entry)
    if tick % 100 == 0:
        print(f"[replay] tick {tick} plan {plan_i} {stage_name}: rise {entry['target_rise_m']} m, sim {CLOCK[0]:.1f} s"
              + (f", halted {HAND.halted.tolist()}, contact {entry.get('contact_n')}" if HAND is not None else ""), flush=True)
    capture(tick + 1)

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
rest_i = max((i for i in range(ipk + 1) if log[i]["target_rise_m"] < 0.005), default=None)
liftoff = log[rest_i + 1] if rest_i is not None and rest_i + 1 < len(log) and peak >= 0.005 else None
before = log[:rest_i + 1] if rest_i is not None else []
disturbed = {"max_tilt_deg": max((r["target_tilt_deg"] or 0.0 for r in before), default=0.0),
             "max_rise_m": max((r["target_rise_m"] for r in before), default=0.0)}
grip_m = liftoff["palm_to_target_m"] if liftoff else None
drift = abs(near - grip_m) if near is not None and grip_m is not None else None
lifted = peak >= 0.05
kept = end >= 0.05 and drift is not None and drift < 0.02
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
(out / "grasp.json").write_text(json.dumps(result, indent=1))
(out / "log.json").write_text(json.dumps(log))

# --- video --------------------------------------------------------------------------------
if args.no_video:
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
if "move" in result:
    mv = result["move"]
    print(f"MOVE {'PASS' if mv['pass'] else 'FAIL'} {spec['name']}: {result['target']} ended on {mv['ended_on']} "
          f"({mv['from_centre_m']} m from the centre of {mv['to']}), tilt {mv['tilt_deg']} deg, carried {mv['carried_peak_rise_m']} m up, "
          f"released {mv['released']}, others moved {mv['others_moved_over_1cm']}; {mv['fen_placement']}", flush=True)
print(f"GRASP {'PASS' if result['pass'] else 'FAIL'} {spec['name']}: {result['target']} rose {100 * peak:.1f} cm at peak, "
      f"{100 * end:.1f} cm at the end of the hold; hand-to-object drift since liftoff {drift} m", flush=True)
print("GRASP_RESULT " + json.dumps(result), flush=True)
app.close()
