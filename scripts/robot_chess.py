#!/usr/bin/env python3
"""The DROID Franka plays chess moves on a sim-ready chess set, in PhysX.

Uses the planner's RoboLab table scene (robolab_sim6_env, Isaac Sim 6). The
task's own objects are moved off the table and the chess set (ingested and
annotated: scripts/ingest_asset.py --class-hint chess_set, chess_board.py)
is spawned on it, white facing the robot. Every move is made by the robot:
joint-position control with damped-least-squares IK, as the scripted oracle
does, and the Robotiq 2F-85 under CONTINUOUS finger control, because the
binary open/close action opens to 85 mm and pieces stand 60 mm apart.

Per move:
  grasp direction  of eight directions, the one whose fingers clear the
                   neighbouring pieces by the most (diagonals included)
  opening          the piece's width along that direction + 12 mm each side
  grasp height     short pieces low, tall ones by their top 5 cm
  path             above, descend, close, lift clear of the tallest piece,
                   carry, lower, open, retreat
After each move the position is read from the simulation (live poses ->
squares -> FEN) and compared with the expected one; pieces that were not
moved must not have shifted.

Usage (via launch_robot_chess.sh, which waits for the Isaac slot):
    ./launch_robot_chess.sh --asset lewis_chess_set --moves "e2e4 e7e5 g1f3"
"""
from __future__ import annotations

import argparse
import json
import math
import shutil
import subprocess
import sys
from pathlib import Path

import cv2  # noqa: F401  Must precede Isaac Lab imports.
import numpy as np
import torch
from isaaclab.app import AppLauncher

REPO = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
parser.add_argument("--asset", default="lewis_chess_set")
parser.add_argument("--moves", default="e2e4 e7e5 g1f3")
parser.add_argument("--board-center", nargs=2, type=float, default=(0.52, 0.0))
parser.add_argument("--task", default="BlocksInBinTask")
parser.add_argument("--settle-steps", type=int, default=6)
parser.add_argument("--max-iterations", type=int, default=60)
parser.add_argument("--closeup", help="frame the camera on this square, low and close (debugging grasps)")
parser.add_argument("--width", type=int, default=1280)
parser.add_argument("--height", type=int, default=720)
AppLauncher.add_app_launcher_args(parser)
args = parser.parse_args()
args.enable_cameras = True
simulation_app = AppLauncher(args).app

sys.path.insert(0, str(Path(__file__).resolve().parent))
import isaaclab.sim as sim_utils  # noqa: E402
from isaaclab.assets import AssetBaseCfg  # noqa: E402
from isaaclab.envs import mdp  # noqa: E402
from robolab.core.environments.runtime import create_env, end_episode  # noqa: E402

from adaptive_pick_place import (  # noqa: E402
    quaternion_error_axis_angle_wxyz,
    quaternion_multiply_wxyz,
    yaw_quaternion_wxyz,
)
from chess_board import START, analyse, fen_placement, letter  # noqa: E402
from residual_centering import bounded_vector_step, damped_least_squares_delta  # noqa: E402
from robolab_sim6_env import build_env_cfg  # noqa: E402

entry = json.loads((REPO / "workspace/review_queue" / f"{args.asset}.json").read_text())
out_dir = REPO / "workspace/asset_animations" / f"{args.asset}_robot"
frames_dir = out_dir / "frames"
shutil.rmtree(frames_dir, ignore_errors=True)
frames_dir.mkdir(parents=True)

# The planner's top-down grasp; the Robotiq jaws close along its local -y and
# the fingertips reach 0.150 m below base_link.
DOWNWARD_GRASP_WXYZ = torch.tensor([0.555, 0.385, 0.616, -0.406])
DOWNWARD_GRASP_WXYZ = DOWNWARD_GRASP_WXYZ / torch.linalg.norm(DOWNWARD_GRASP_WXYZ)
TIP_BELOW_BASE_M = 0.150
FINGER_WIDTH_M, FINGER_DEPTH_M = 0.027, 0.020   # a pad, across and along the closing axis
FULL_OPEN_M, CLOSED_RAD = 0.085, math.pi / 4
TABLE_TOP_Z = 0.003
FLOOR_Z = -0.697  # the scene's ground plane (the table legs stand on it)


def opening_rad(width_m: float) -> float:
    """Robotiq 2F-85 finger_joint for a jaw opening (close to linear)."""
    return float(min(max(0.8 * (1.0 - width_m / FULL_OPEN_M), 0.0), CLOSED_RAD))


# --- scene: the planner's table, the chess set on it, white toward the robot ------
env_cfg = build_env_cfg(args.task)
env_cfg.actions.finger_joint = mdp.JointPositionActionCfg(
    asset_name="robot", joint_names=["finger_joint"], scale=1.0, use_default_offset=False)
half = math.radians(-90) / 2  # the set's white side is its -y; turn it to face -x (the robot)
# The robot stands on its own platform: the scene's table (and everything the
# arm reaches for) is placed relative to the robot's root, not the env origin.
ROOT = tuple(float(v) for v in env_cfg.scene.robot.init_state.pos)
env_cfg.scene.chess_set = AssetBaseCfg(
    prim_path="{ENV_REGEX_NS}/ChessSet",
    spawn=sim_utils.UsdFileCfg(usd_path=entry["file"]),
    # board underside 1 mm above the table top; this stack takes (x, y, z, w)
    # Parked on the floor away from the table: at creation the task's objects
    # are still on the table (their off-table start only applies at reset),
    # and a board spawned among them is blown apart in the first step. It is
    # moved onto the cleared table after the reset.
    init_state=AssetBaseCfg.InitialStateCfg(pos=(ROOT[0] + 3.0, ROOT[1], FLOOR_Z + 0.012),
                                            rot=(0.0, 0.0, math.sin(half), math.cos(half))),
)
# The task's objects spawn where the board goes; collisions at reset would
# scatter the pieces before anything moves. Start them on the floor instead.
for i, name in enumerate(n for n in env_cfg.contact_object_list if "table" not in n.lower()):
    getattr(env_cfg.scene, name).init_state.pos = (ROOT[0] + 1.6 + 0.35 * (i % 4), ROOT[1] - 0.9 + 0.45 * (i // 4),
                                                    ROOT[2] - 0.6)
env, _ = create_env(env_cfg, use_fabric=True, policy="robot-chess")
obs, _ = env.reset()

import omni.physx  # noqa: E402
import omni.replicator.core as rep  # noqa: E402
import omni.usd  # noqa: E402
from PIL import Image  # noqa: E402
from pxr import Gf, Usd, UsdGeom, UsdLux  # noqa: E402

stage = omni.usd.get_context().get_stage()
px = omni.physx.get_physx_interface()
robot = env.scene["robot"]


def tv(x):
    return getattr(x, "torch", x)


# Everything below is in the robot-root frame, where eef() and the IK work.
origin = [float(v) for v in tv(robot.data.root_pos_w)[0].detach().cpu()]


arm_ids = [robot.data.joint_names.index(f"panda_joint{i}") for i in range(1, 8)]
finger_id = robot.data.joint_names.index("finger_joint")
body = robot.data.body_names.index("base_link")
jac_body = body - 1 if robot.is_fixed_base else body
jac_joints = [i + robot.num_base_dofs for i in arm_ids]


def eef():
    pos = tv(robot.data.body_pos_w)[0, body] - tv(robot.data.root_pos_w)[0]
    q = tv(robot.data.body_quat_w)[0, body]
    return pos.detach().cpu().float(), q[[3, 0, 1, 2]].detach().cpu().float()


hold = torch.zeros((1, 8))
hold[0, :7] = tv(robot.data.joint_pos)[0, arm_ids].detach().cpu()
hold[0, 7] = 0.0

# --- camera -------------------------------------------------------------------------
cx, cy = args.board_center
with Usd.EditContext(stage, stage.GetSessionLayer()):
    cam = UsdGeom.Camera.Define(stage, "/RobotChessCam")
    cam.CreateFocalLengthAttr(18.0)
    cam.CreateHorizontalApertureAttr(20.955)
    cam.CreateClippingRangeAttr(Gf.Vec2f(0.01, 100.0))
    eye = Gf.Vec3d(cx + 0.55 + origin[0], cy - 0.85 + origin[1], 0.62 + origin[2])
    UsdGeom.Xformable(cam.GetPrim()).AddTransformOp().Set(Gf.Matrix4d().SetLookAt(
        eye, Gf.Vec3d(cx - 0.08 + origin[0], cy + origin[1], 0.08 + origin[2]), Gf.Vec3d(0, 0, 1)).GetInverse())
rp = rep.create.render_product("/RobotChessCam", (args.width, args.height))
rgb = rep.AnnotatorRegistry.get_annotator("rgb")
rgb.attach([rp])
frame = [0]


def step(action, n=1):
    global obs
    for _ in range(n):
        obs, *_ = env.step(action.to(env.device))
        data = rgb.get_data()
        if data is not None and getattr(data, "size", 0):
            Image.fromarray(np.asarray(data)[:, :, :3]).save(frames_dir / f"f{frame[0]:05d}.png")
            frame[0] += 1


step(hold, 30)  # the parked set settles, the task's objects land on the floor

# --- the board, read from the stage --------------------------------------------------
board = analyse(stage, "/World/envs/env_0/ChessSet")
_paths = [i["path"] for i in board["pieces"]] + [board["board_path"]]
_view = env.sim.physics_sim_view.create_rigid_body_view(_paths)
_index = {p: k for k, p in enumerate(_paths)}


def live_pose(path):
    """(position, Gf.Rotation) from the PhysX tensors, in world."""
    t = _view.get_transforms()
    t = t.numpy() if hasattr(t, "numpy") else np.asarray(t)
    row = np.asarray(t).reshape(len(_paths), 7)[_index[path]]
    return Gf.Vec3d(*map(float, row[:3])), Gf.Rotation(Gf.Quatd(float(row[6]), *map(float, row[3:6])))


SQ = board["size"]
FD, RD = board["file_dir"], board["rank_dir"]
# The stage still holds the parked spawn layout (the simulation does not write
# poses back to USD). Move every body by one offset so the board lies on the
# table centred where asked; the pieces keep their places on it.
_a1 = np.array(board["a1"])
_mid = _a1 + 3.5 * SQ * (np.array(FD) + np.array(RD))
delta = np.array([origin[0] + cx - _mid[0], origin[1] + cy - _mid[1],
                  origin[2] + TABLE_TOP_Z + 0.0105 - board["top_z"] + 0.0])
import warp as wp  # noqa: E402
_t = _view.get_transforms()
_np = (_t.numpy() if hasattr(_t, "numpy") else np.asarray(_t)).reshape(len(_paths), 7).copy()
_np[:, :3] += delta
_dev = getattr(_t, "device", "cuda:0")
_idx = wp.array(np.arange(len(_paths), dtype=np.int32), dtype=wp.int32, device=_dev)
_view.set_transforms(wp.array(_np, dtype=wp.float32, device=_dev), _idx)
_view.set_velocities(wp.zeros((len(_paths), 6), dtype=wp.float32, device=_dev), _idx)
step(hold, 30)  # settle on the table
A1 = (board["a1"][0] + delta[0] - origin[0], board["a1"][1] + delta[1] - origin[1])
SURFACE = board["top_z"] + delta[2] - origin[2]
_board_usd = UsdGeom.Xformable(board["board"]).ComputeLocalToWorldTransform(0).ExtractTranslation() + Gf.Vec3d(*delta)
_board_live = live_pose(board["board_path"])[0]
if (_board_live - _board_usd).GetLength() > 0.01:
    raise SystemExit(f"the board did not land where it was put ({(_board_live - _board_usd).GetLength():.3f} m off)")


def _usd_pose(prim):
    m = Gf.Matrix4d(UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(0))
    pos = m.ExtractTranslation()
    m.Orthonormalize()
    return pos, m.ExtractRotation()


pieces = {}
for i in board["pieces"]:
    lo_hi = UsdGeom.BBoxCache(0, ["default", "render"]).ComputeWorldBound(i["prim"]).ComputeAlignedRange()
    p_usd, r_usd = _usd_pose(i["prim"])  # the spawn pose: "upright" means this orientation
    size = lo_hi.GetSize()
    pieces[i["square"]] = {"path": i["path"], "letter": letter(i["kind"], i["color"]),
                           "offset": r_usd.GetInverse().TransformDir(Gf.Vec3d(lo_hi.GetMidpoint()) - p_usd),
                           "height": float(size[2]), "footprint": (float(size[0]), float(size[1])),
                           "r0": r_usd}
print("BOARD", json.dumps({"a1": A1, "file_dir": FD, "rank_dir": RD, "square": SQ, "surface_z": SURFACE,
                           "a1_dark": board["a1_dark"]}), flush=True)
TALLEST = max(p["height"] for p in pieces.values())


def centre(pc):
    pos, rot = live_pose(pc["path"])
    c = pos + rot.TransformDir(pc["offset"])
    return np.array([c[0] - origin[0], c[1] - origin[1], c[2] - origin[2]]), rot


def square_xy(name):
    f, r = "abcdefgh".index(name[0]), int(name[1]) - 1
    return np.array([A1[0] + SQ * (f * FD[0] + r * RD[0]), A1[1] + SQ * (f * FD[1] + r * RD[1])])


if args.closeup:
    _sq = square_xy(args.closeup)
    _rank = np.array(RD)
    _eye = np.array([_sq[0], _sq[1], 0.0]) - np.r_[_rank, 0] * 0.32 + np.array([0.0, 0.0, SURFACE + 0.12])
    _eye[:2] += np.array(FD) * 0.12
    with Usd.EditContext(stage, stage.GetSessionLayer()):
        _cam = UsdGeom.Xformable(stage.GetPrimAtPath("/RobotChessCam"))
        _cam.ClearXformOpOrder()
        _cam.AddTransformOp().Set(Gf.Matrix4d().SetLookAt(
            Gf.Vec3d(*(_eye + np.array(origin))),
            Gf.Vec3d(_sq[0] + origin[0], _sq[1] + origin[1], SURFACE + 0.03 + origin[2]),
            Gf.Vec3d(0, 0, 1)).GetInverse())


def to_square(xy):
    d = xy - np.array(A1)
    f, r = float(d @ np.array(FD)) / SQ, float(d @ np.array(RD)) / SQ
    return round(f), round(r), math.hypot(f - round(f), r - round(r)) * SQ


def read_board(skip=()):
    placed, tilt_max, off_max = {}, 0.0, 0.0
    for sq, pc in pieces.items():
        c, rot = centre(pc)
        up = (pc["r0"].GetInverse() * rot).TransformDir(Gf.Vec3d(0, 0, 1))
        f, r, off = to_square(c[:2])
        if 0 <= f < 8 and 0 <= r < 8:
            placed[(f, r)] = pc["letter"]
        tilt_max = max(tilt_max, math.degrees(math.acos(max(-1.0, min(1.0, up[2])))))
        off_max = max(off_max, off)
    return fen_placement(placed), round(tilt_max, 2), round(off_max, 4)


# --- choosing a grasp ------------------------------------------------------------------

def grasp_plan(src: str):
    """Direction, opening and clearance for the piece on `src`."""
    pc = pieces[src]
    c, _ = centre(pc)
    others = [(centre(p)[0][:2], 0.5 * max(p["footprint"])) for s, p in pieces.items() if s != src]
    best = None
    for k in range(8):
        phi = k * math.pi / 8
        d = np.array([math.cos(phi), math.sin(phi)])
        fx, fy = pc["footprint"]
        width = abs(fx * d[0]) + abs(fy * d[1])  # bounding width across the jaws
        opening = width + 0.024
        clear = min(
            (np.linalg.norm(c[:2] + s * d * (opening / 2 + FINGER_DEPTH_M / 2) - xy)
             - r - math.hypot(FINGER_DEPTH_M, FINGER_WIDTH_M) / 2)
            for xy, r in others for s in (-1, 1)) if others else 1.0
        if best is None or clear > best["clearance"]:
            best = {"phi": phi, "opening": opening, "clearance": clear, "width": width}
    return best


def grasp_quat(phi: float):
    # world direction the jaws close along for the base grasp, then turn it onto phi
    w, x, y, z = (float(v) for v in DOWNWARD_GRASP_WXYZ)
    rot = Gf.Rotation(Gf.Quatd(w, x, y, z))
    c0 = rot.TransformDir(Gf.Vec3d(0, -1, 0))
    turn = phi - math.atan2(c0[1], c0[0])
    turn = (turn + math.pi / 2) % math.pi - math.pi / 2  # jaws are symmetric: the smaller turn
    return quaternion_multiply_wxyz(yaw_quaternion_wxyz(turn, like=DOWNWARD_GRASP_WXYZ), DOWNWARD_GRASP_WXYZ)


# --- moving the arm ---------------------------------------------------------------------

def go(target_xyz, quat, finger_rad, label, tol=0.004, dwell=0):
    target = torch.tensor(target_xyz, dtype=torch.float32)
    err = deg = float("nan")
    action = None
    for _ in range(1 if dwell else args.max_iterations):
        pos, q = eef()
        error = target - pos
        rot_error = quaternion_error_axis_angle_wxyz(quat, q)
        twist = torch.cat((bounded_vector_step(error, 0.02), bounded_vector_step(rot_error, math.radians(8))))
        jac = tv(robot.data.body_link_jacobian_w)[0, jac_body][:, jac_joints].detach().cpu().float()
        delta = damped_least_squares_delta(jac, twist, 0.05, 0.07)
        joints = tv(robot.data.joint_pos)[0, arm_ids].detach().cpu().float()
        limits = tv(robot.data.soft_joint_pos_limits)[0, arm_ids].detach().cpu().float()
        action = torch.zeros((1, 8))
        action[0, :7] = torch.clamp(joints + delta, limits[:, 0] + 1e-3, limits[:, 1] - 1e-3)
        action[0, 7] = finger_rad
        step(action, dwell or args.settle_steps)
        pos, q = eef()
        err = float(torch.linalg.vector_norm(target - pos))
        deg = math.degrees(float(torch.linalg.vector_norm(quaternion_error_axis_angle_wxyz(quat, q))))
        if not dwell and err < tol and deg < 2.0:
            break
    print(f"[robot-chess] {label}: error {err * 1000:.1f} mm {deg:.1f} deg", flush=True)
    return err


FINGERS = [robot.data.body_names.index(n) for n in ("left_inner_finger", "right_inner_finger")]
# Each finger link's own geometry, in its frame: with the link's live pose this
# gives where the fingertips really are. The Robotiq's linkage moves them
# down as the jaw closes, so no fixed offset below base_link is right.
_finger_corners = []
for _name in ("left_inner_finger", "right_inner_finger"):
    _prim = next(p for p in Usd.PrimRange(stage.GetPrimAtPath("/World/envs/env_0/robot")) if p.GetName() == _name)
    _r = UsdGeom.BBoxCache(0, ["default", "render"]).ComputeUntransformedBound(_prim).ComputeAlignedRange()
    _lo, _hi = _r.GetMin(), _r.GetMax()
    _finger_corners.append(np.array([[x, y, z] for x in (_lo[0], _hi[0]) for y in (_lo[1], _hi[1]) for z in (_lo[2], _hi[2])]))


def _finger_world():
    out = []
    for idx, corners in zip(FINGERS, _finger_corners):
        p = tv(robot.data.body_pos_w)[0, idx].detach().cpu().numpy()
        w, x, y, z = (float(v) for v in tv(robot.data.body_quat_w)[0, idx][[3, 0, 1, 2]])
        rot = np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                        [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                        [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])
        out.append(corners @ rot.T + p)
    return out


def jaw_gap() -> float:
    """Distance between the two fingers' inner faces, along the line joining them."""
    a, b = _finger_world()
    d = b.mean(0) - a.mean(0)
    d[2] = 0.0
    d /= np.linalg.norm(d)
    return float((b @ d).min() - (a @ d).max())


def set_opening(width, hold_xyz, quat):
    """Servo the finger joint until the measured jaw gap is `width`."""
    rad = opening_rad(width)
    for _ in range(5):
        go(hold_xyz, quat, rad, "set opening", dwell=8)
        gap = jaw_gap()
        if abs(gap - width) < 0.002:
            break
        rad = float(min(max(rad + (gap - width) * (0.8 / FULL_OPEN_M), 0.0), CLOSED_RAD))
    print(f"[robot-chess] jaw gap {gap * 1000:.1f} mm for {width * 1000:.1f} mm wanted at {rad:.3f} rad", flush=True)
    return rad


# Every other gripper link: the palm and the knuckles hang between the fingers
# too, and for a short piece they reach its top before the fingertips reach
# the board.
_palm = []
_robot_root = stage.GetPrimAtPath("/World/envs/env_0/robot")
for _i, _name in enumerate(robot.data.body_names):
    if _name in ("left_inner_finger", "right_inner_finger") or not (
            "knuckle" in _name or "finger" in _name or _name == "base_link"):
        continue
    _prim = next((p for p in Usd.PrimRange(_robot_root) if p.GetName() == _name), None)
    if _prim is None:
        continue
    _r = UsdGeom.BBoxCache(0, ["default", "render"]).ComputeUntransformedBound(_prim).ComputeAlignedRange()
    if _r.IsEmpty():
        continue
    _lo, _hi = _r.GetMin(), _r.GetMax()
    _palm.append((_i, _name, np.array([[x, y, z] for x in (_lo[0], _hi[0]) for y in (_lo[1], _hi[1])
                                       for z in (_lo[2], _hi[2])])))


def _world(idx, corners):
    p = tv(robot.data.body_pos_w)[0, idx].detach().cpu().numpy()
    w, x, y, z = (float(v) for v in tv(robot.data.body_quat_w)[0, idx][[3, 0, 1, 2]])
    rot = np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                    [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                    [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])
    return corners @ rot.T + p


def palm_drop() -> tuple[float, str]:
    """Lowest point, below base_link, of any non-finger gripper link that
    overlaps the space between the fingers."""
    a, b = _finger_world()
    d = b.mean(0) - a.mean(0)
    d[2] = 0.0
    d /= np.linalg.norm(d)
    lo_face, hi_face = float((a @ d).max()), float((b @ d).min())
    base_z = float(tv(robot.data.body_pos_w)[0, body][2])
    worst, who = 0.0, "base_link"
    for idx, name, corners in _palm:
        w = _world(idx, corners)
        proj = w @ d
        if proj.max() < lo_face or proj.min() > hi_face:
            continue  # wholly outside the gap: beside the fingers, not over the piece
        drop = base_z - float(w[:, 2].min())
        if drop > worst:
            worst, who = drop, name
    return worst, who


def tip_drop() -> float:
    """How far the lowest fingertip point hangs below base_link, right now."""
    base_z = float(tv(robot.data.body_pos_w)[0, body][2])
    lowest = []
    for idx, corners in zip(FINGERS, _finger_corners):
        p = tv(robot.data.body_pos_w)[0, idx].detach().cpu().numpy()
        w, x, y, z = (float(v) for v in tv(robot.data.body_quat_w)[0, idx][[3, 0, 1, 2]])
        rot = np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                        [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                        [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])
        lowest.append(float((corners @ rot.T + p)[:, 2].min()))
    return base_z - min(lowest)


def report_contact(pc, before, label):
    """Where the fingertips are against the piece, and what else moved."""
    c, _ = centre(pc)
    tips = [(tv(robot.data.body_pos_w)[0, i] - tv(robot.data.root_pos_w)[0]).detach().cpu().numpy() for i in FINGERS]
    moved = {sq: round(float(np.linalg.norm(centre(p)[0] - before[sq])) * 1000, 1)
             for sq, p in pieces.items() if np.linalg.norm(centre(p)[0] - before[sq]) > 0.002}
    finger = float(tv(robot.data.joint_pos)[0, finger_id])
    print(f"[robot-chess] {label}: finger_joint {finger:.3f} rad; finger bodies rel. piece centre "
          f"{[np.round(t - c, 3).tolist() for t in tips]}; piece centre z above surface {c[2] - SURFACE:.3f}; "
          f"neighbours moved (mm) {moved}", flush=True)


def log_held(pc, label):
    c, rot = centre(pc)
    up = (pc["r0"].GetInverse() * rot).TransformDir(Gf.Vec3d(0, 0, 1))
    tilt = math.degrees(math.acos(max(-1.0, min(1.0, up[2]))))
    pos, _ = eef()
    print(f"[robot-chess]   {label}: piece tilt {tilt:.1f} deg, base {1000 * (c[2] - pc['height'] / 2 - SURFACE):.1f} mm "
          f"above the board, {1000 * float(np.linalg.norm(c[:2] - pos[:2].numpy())):.1f} mm from the jaw axis", flush=True)


def play(move: str):
    src, dst = move[:2], move[2:4]
    pc = pieces.pop(src)
    pieces[src] = pc  # still on the board while we plan
    plan = grasp_plan(src)
    del pieces[src]
    quat = grasp_quat(plan["phi"])
    c, _ = centre(pc)
    tip_z = SURFACE + max(0.004, pc["height"] - 0.05)          # where the fingertips stop
    grasp_z = tip_z + TIP_BELOW_BASE_M
    travel_z = grasp_z + TALLEST + 0.03  # the carried piece's base clears the tallest piece
    open_rad, closed = opening_rad(plan["opening"]), CLOSED_RAD
    dest = square_xy(dst)
    print(f"[robot-chess] {move}: {pc['letter']} grasp at {math.degrees(plan['phi']):.1f} deg, "
          f"open {plan['opening'] * 1000:.0f} mm, finger clearance {plan['clearance'] * 1000:.1f} mm", flush=True)
    go([c[0], c[1], travel_z], quat, open_rad, "above")
    open_rad = set_opening(plan["opening"], [c[0], c[1], travel_z], quat)
    drop = tip_drop()
    palm, palm_link = palm_drop()
    piece_top = c[2] + pc["height"] / 2
    # fingertips as low as the piece allows: 4 mm above the board, unless that
    # would bring the palm onto the piece's top (then the palm stops 5 mm above it)
    grasp_z = max(SURFACE + 0.004 + drop, piece_top + 0.005 + palm)
    print(f"[robot-chess] fingertips {drop * 1000:.1f} mm and {palm_link} {palm * 1000:.1f} mm below base_link; "
          f"fingers reach {(grasp_z - drop - SURFACE) * 1000:.1f} mm above the board, "
          f"{(piece_top - (grasp_z - drop)) * 1000:.1f} mm of the piece between the pads", flush=True)
    before = {sq: centre(p)[0] for sq, p in pieces.items()}
    go([c[0], c[1], grasp_z], quat, open_rad, "descend", tol=0.003)
    report_contact(pc, before, "descend")
    go([c[0], c[1], grasp_z], quat, closed, "close", dwell=20)
    log_held(pc, "after close")
    go([c[0], c[1], travel_z], quat, closed, "lift")
    log_held(pc, "after lift")
    held, _ = centre(pc)
    lifted = held[2] - c[2]
    # aim the piece, not the jaw: where it sits in the grip is measured, not assumed
    _held_c, _ = centre(pc)
    _pos, _ = eef()
    dest = dest - (_held_c[:2] - _pos[:2].numpy())
    go([dest[0], dest[1], travel_z], quat, closed, "carry")
    log_held(pc, "after carry")
    # set it down: the base on the board before the jaws let go (released even a
    # few millimetres up, it drops while the opening pads drag on it, and tips)
    go([dest[0], dest[1], grasp_z - 0.001], quat, closed, "lower", tol=0.003)
    log_held(pc, "after lower")
    go([dest[0], dest[1], grasp_z - 0.001], quat, open_rad, "open", dwell=15)
    log_held(pc, "after open")
    go([dest[0], dest[1], travel_z], quat, open_rad, "retreat")
    log_held(pc, "after retreat")
    pieces[dst] = pc
    return {"lifted_m": round(float(lifted), 4), **{k: round(v, 4) for k, v in plan.items()}}


expected = dict(START)
fen, tilt, off = read_board()
log = [{"move": "start", "expected": fen_placement(expected), "simulated": fen, "match": fen == fen_placement(expected),
        "max_tilt_deg": tilt, "max_offset_m": off}]
print("POSITION", json.dumps(log[-1]), flush=True)
for mv in args.moves.split():
    detail = play(mv)
    still = torch.zeros((1, 8))
    still[0, :7] = tv(robot.data.joint_pos)[0, arm_ids].detach().cpu()
    still[0, 7] = opening_rad(0.06)
    step(still, 10)  # let the board settle before reading it
    fa, ra = "abcdefgh".index(mv[0]), int(mv[1]) - 1
    fb, rb = "abcdefgh".index(mv[2]), int(mv[3]) - 1
    expected[(fb, rb)] = expected.pop((fa, ra))
    fen, tilt, off = read_board()
    log.append({"move": mv, **detail, "expected": fen_placement(expected), "simulated": fen,
                "match": fen == fen_placement(expected), "max_tilt_deg": tilt, "max_offset_m": off})
    print("POSITION", json.dumps(log[-1]), flush=True)

# Park the arm off to the side so it does not hide the board, then a top-down
# view of the final position, readable square by square.
_pos, _q = eef()
go([0.15, 0.45, 0.45], _q, opening_rad(0.06), "park")
_mid_xy = np.array(A1) + 3.5 * SQ * (np.array(FD) + np.array(RD))
with Usd.EditContext(stage, stage.GetSessionLayer()):
    _cam = UsdGeom.Xformable(stage.GetPrimAtPath("/RobotChessCam"))
    _cam.ClearXformOpOrder()
    # rank 1 at the bottom of the image, files a->h left to right
    _cam.AddTransformOp().Set(Gf.Matrix4d().SetLookAt(
        Gf.Vec3d(_mid_xy[0] + origin[0], _mid_xy[1] + origin[1], SURFACE + 0.95 + origin[2]),
        Gf.Vec3d(_mid_xy[0] + origin[0], _mid_xy[1] + origin[1], SURFACE + origin[2]),
        Gf.Vec3d(RD[0], RD[1], 0)).GetInverse())
_shot = frame[0]
step(still, 4)
shutil.copy(frames_dir / f"f{frame[0] - 1:05d}.png", out_dir / "final_topdown.png")
for _f in range(_shot, frame[0]):  # keep the top-down frames out of the video
    (frames_dir / f"f{_f:05d}.png").unlink(missing_ok=True)
frame[0] = _shot

summary = {"asset": args.asset, "moves": args.moves.split(), "positions": log,
           "all_match": all(x["match"] for x in log), "frames": frame[0]}
(out_dir / "positions.json").write_text(json.dumps(summary, indent=1))
mp4 = out_dir / f"{args.asset}_robot.mp4"
if frame[0]:
    subprocess.run([shutil.which("ffmpeg") or "ffmpeg", "-v", "error", "-y", "-framerate", "15",
                    "-i", str(frames_dir / "f%05d.png"), "-c:v", "libx264", "-preset", "slow", "-crf", "20",
                    "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(mp4)], check=True)
print("ROBOT_CHESS", json.dumps({"mp4": str(mp4), "all_match": summary["all_match"], "frames": frame[0]}), flush=True)
end_episode(env)
env.close()
simulation_app.close()
