#!/usr/bin/env python3
"""Play moves on a sim-ready chess set in PhysX and check the board after each.

Headless Isaac Sim (the build's python.sh, under scripts/isaac_slot.sh). The
set must have been annotated by chess_board.py (piece identities and the
board grid are read from customData).

Each move is carried out physically: the piece becomes kinematic, lifts clear
of the tallest piece, travels, lowers onto the target square and is released
to settle under gravity. A capture first lifts the captured piece off the
board. After every move the position is READ FROM THE SIMULATION (each
piece's live pose -> square) and compared with the expected position; every
piece must also stand upright and near its square's centre.

Usage:
    python.sh scripts/play_chess.py <queue_asset_id>
        [--moves "e2e4 e7e5 g1f3 b8c6 f1b5 a7a6 b5c6 d7c6"]

Writes workspace/asset_animations/<id>_game/: <id>_game.mp4, positions.json
(per move: expected and simulated FEN, worst tilt and offset) and frames/.
"""
from __future__ import annotations

import argparse
import json
import math
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
QUEUE_DIR = REPO / "workspace" / "review_queue"

ap = argparse.ArgumentParser()
ap.add_argument("asset")
ap.add_argument("--moves", default="e2e4 e7e5 g1f3 b8c6 f1b5 a7a6 b5c6 d7c6")
ap.add_argument("--fps", type=int, default=30)
ap.add_argument("--width", type=int, default=1280)
ap.add_argument("--height", type=int, default=720)
args = ap.parse_args()

entry = json.loads((QUEUE_DIR / f"{args.asset}.json").read_text())
out_dir = REPO / "workspace" / "asset_animations" / f"{args.asset}_game"
frames_dir = out_dir / "frames"
shutil.rmtree(frames_dir, ignore_errors=True)
frames_dir.mkdir(parents=True)

from isaacsim import SimulationApp  # noqa: E402

app = SimulationApp({"headless": True, "width": args.width, "height": args.height})

import omni.physx  # noqa: E402
import omni.replicator.core as rep  # noqa: E402
import omni.usd  # noqa: E402
from PIL import Image  # noqa: E402
from pxr import Gf, Usd, UsdGeom, UsdLux, UsdPhysics  # noqa: E402

from chess_board import START, fen_placement, letter, square_name  # noqa: E402

omni.usd.get_context().open_stage(entry["file"])
for _ in range(20):
    app.update()
stage = omni.usd.get_context().get_stage()
px = omni.physx.get_physx_interface()
HZ, DT = 120, 1.0 / 120  # small, light pieces: a finer step keeps contacts calm

# --- what the set knows about itself ---------------------------------------------
board_prim = next(p for p in stage.Traverse() if p.GetCustomDataByKey("simReady:chessboard"))
B = json.loads(board_prim.GetCustomDataByKey("simReady:chessboard"))
SQ = B["square_m"]
A1, FD, RD = B["a1_center"], B["file_dir"], B["rank_dir"]
pieces = []
for p in stage.Traverse():
    meta = p.GetCustomDataByKey("simReady:chess")
    if meta:
        m = json.loads(meta)
        pieces.append({"path": str(p.GetPath()), "letter": letter(m["piece"], m["color"]),
                       "start": m["start_square"], "captured": False})
by_square = {pc["start"]: pc for pc in pieces}


def square_center(name: str):
    f, r = "abcdefgh".index(name[0]), int(name[1]) - 1
    return (A1[0] + SQ * (f * FD[0] + r * RD[0]), A1[1] + SQ * (f * FD[1] + r * RD[1]))


def to_square(x, y):
    dx, dy = x - A1[0], y - A1[1]
    f, r = (dx * FD[0] + dy * FD[1]) / SQ, (dx * RD[0] + dy * RD[1]) / SQ
    fi, ri = round(f), round(r)
    return fi, ri, math.hypot(f - fi, r - ri) * SQ


CENTRE_OFFSET = {}  # body frame -> the piece's geometric centre (origins are wherever the file put them)


def centre(path):
    p, r = pose(path)
    return p + r.TransformDir(CENTRE_OFFSET.get(path, Gf.Vec3d(0, 0, 0)))


def pose(path):
    r = px.get_rigidbody_transformation(path)
    q = r["rotation"]
    return Gf.Vec3d(*r["position"]), Gf.Rotation(Gf.Quatd(q[3], q[0], q[1], q[2]))


# --- scene dressing ----------------------------------------------------------------
xf = UsdGeom.XformCache()
blo = UsdGeom.BBoxCache(0, ["default", "render"]).ComputeWorldBound(board_prim).ComputeAlignedRange()
with Usd.EditContext(stage, stage.GetSessionLayer()):
    UsdLux.DomeLight.Define(stage, "/Game/Dome").CreateIntensityAttr(1100)
    key = UsdLux.DistantLight.Define(stage, "/Game/Key")
    key.CreateIntensityAttr(2200)
    UsdGeom.XformCommonAPI(key.GetPrim()).SetRotate(Gf.Vec3f(-50, 0, -30))
    ground = UsdGeom.Cube.Define(stage, "/Game/Ground")  # a table under the board
    ground.CreateSizeAttr(1.0)  # a Cube is 2 m by default: top would sit inside the board
    UsdGeom.XformCommonAPI(ground.GetPrim()).SetTranslate(Gf.Vec3d(0, 0, blo.GetMin()[2] - 0.05))
    UsdGeom.XformCommonAPI(ground.GetPrim()).SetScale(Gf.Vec3f(1.6, 1.6, 0.1))
    ground.CreateDisplayColorAttr([(0.42, 0.33, 0.25)])
    UsdPhysics.CollisionAPI.Apply(ground.GetPrim())
    cam = UsdGeom.Camera.Define(stage, "/Game/Camera")
    cam.CreateFocalLengthAttr(20.0)
    cam.CreateHorizontalApertureAttr(20.955)
    cam.CreateClippingRangeAttr(Gf.Vec2f(0.01, 100.0))
    board_mid = Gf.Vec3d(A1[0] + 3.5 * SQ * (FD[0] + RD[0]), A1[1] + 3.5 * SQ * (FD[1] + RD[1]), B["surface_z"])
    # from behind white, high: files read left to right, the far side is black
    # far and high enough for the whole board plus both rows of captured pieces
    eye = board_mid - Gf.Vec3d(RD[0], RD[1], 0) * 0.78 + Gf.Vec3d(0, 0, 0.78)
    UsdGeom.Xformable(cam.GetPrim()).AddTransformOp().Set(
        Gf.Matrix4d().SetLookAt(eye, board_mid + Gf.Vec3d(RD[0], RD[1], 0) * 0.04, Gf.Vec3d(0, 0, 1)).GetInverse())
rp = rep.create.render_product("/Game/Camera", (args.width, args.height))
rgb = rep.AnnotatorRegistry.get_annotator("rgb")
rgb.attach([rp])

px.start_simulation()
clock = [0.0]
frame = [0]
steps_per_frame = HZ // args.fps


def step_frames(n_frames: int, before_step=None):
    for i in range(n_frames):
        for _ in range(steps_per_frame):
            if before_step:
                before_step()
            px.update_simulation(DT, clock[0])
            clock[0] += DT
        px.update_transformations(False, True, False, False)
        rep.orchestrator.step(rt_subframes=2, delta_time=0.0, pause_timeline=False)
        Image.fromarray(rgb.get_data()[:, :, :3]).save(frames_dir / f"f{frame[0]:05d}.png")
        frame[0] += 1


rest_rot = {}


def read_board():
    """Squares from live poses; upright = little tilt since the start."""
    placed, worst_tilt, worst_off, off_board = {}, 0.0, 0.0, []
    for pc in pieces:
        if pc["captured"]:
            continue
        _, r = pose(pc["path"])
        p = centre(pc["path"])
        up = (rest_rot[pc["path"]].GetInverse() * r).TransformDir(Gf.Vec3d(0, 0, 1))
        tilt = math.degrees(math.acos(max(-1.0, min(1.0, up[2]))))
        f, rk, off = to_square(p[0], p[1])
        if not (0 <= f < 8 and 0 <= rk < 8):
            off_board.append(pc["letter"])
            continue
        placed[(f, rk)] = pc["letter"]
        worst_tilt, worst_off = max(worst_tilt, tilt), max(worst_off, off)
    return fen_placement(placed), round(worst_tilt, 2), round(worst_off, 4), off_board


# --- moving a piece the way a hand or a gripper would -----------------------------

def set_kinematic(path, on):
    UsdPhysics.RigidBodyAPI(stage.GetPrimAtPath(path)).CreateKinematicEnabledAttr().Set(on)


def carry(path, waypoints, seconds):
    """Kinematic carry of the piece's CENTRE through world-space waypoints,
    holding its orientation."""
    prim = stage.GetPrimAtPath(path)
    xformable = UsdGeom.Xformable(prim)
    parent_inv = xf.GetLocalToWorldTransform(prim.GetParent()).GetInverse()
    # keep the piece's own rotation and scale; move only its origin
    local = Gf.Matrix4d(xformable.GetLocalTransformation())
    xformable.ClearXformOpOrder()
    op = xformable.AddTransformOp()
    start, _ = pose(path)
    to_origin = start - centre(path)  # waypoints name the centre; the body moves by its origin
    pts = [start] + [Gf.Vec3d(*w) + to_origin for w in waypoints]
    lengths = [(pts[i + 1] - pts[i]).GetLength() for i in range(len(pts) - 1)]
    total = sum(lengths) or 1e-9
    n = max(2, round(seconds * args.fps))
    k = [0]

    def at(u):
        d = u * total
        for i, L in enumerate(lengths):
            if d <= L or i == len(lengths) - 1:
                s = 0.0 if L == 0 else min(1.0, d / L)
                s = s * s * (3 - 2 * s)
                return pts[i] + (pts[i + 1] - pts[i]) * s
            d -= L

    def before():
        k[0] += 1
        u = min(1.0, k[0] / (n * steps_per_frame))
        local.SetTranslateOnly(parent_inv.Transform(at(u)))
        op.Set(local)

    step_frames(n, before)


def lift_height():
    tallest = max(UsdGeom.BBoxCache(0, ["default", "render"]).ComputeWorldBound(
        stage.GetPrimAtPath(pc["path"])).ComputeAlignedRange().GetSize()[2] for pc in pieces)
    return B["surface_z"] + 2.0 * tallest + 0.02


CLEAR_Z = None
graveyard = {"white": 0, "black": 0}


def take_off(pc, color_of_capturer):
    """Captured pieces go beside the board on the capturing side."""
    set_kinematic(pc["path"], True)
    p = centre(pc["path"])
    slot = graveyard[color_of_capturer]
    graveyard[color_of_capturer] += 1
    dest = Gf.Vec3d(*square_center("a1"), 0) + Gf.Vec3d(FD[0], FD[1], 0) * SQ * (slot * 0.8) \
        + Gf.Vec3d(RD[0], RD[1], 0) * SQ * (-1.4 if color_of_capturer == "white" else 8.4)
    carry(pc["path"], [(p[0], p[1], CLEAR_Z), (dest[0], dest[1], CLEAR_Z),
                       (dest[0], dest[1], p[2] + 0.004)], 1.6)
    set_kinematic(pc["path"], False)
    pc["captured"] = True


# --- play -------------------------------------------------------------------------
expected = dict(START)
step_frames(45)  # settle
for pc in pieces:
    p0, r0 = pose(pc["path"])
    rest_rot[pc["path"]] = r0
    mid = UsdGeom.BBoxCache(0, ["default", "render"]).ComputeWorldBound(
        stage.GetPrimAtPath(pc["path"])).ComputeAlignedRange().GetMidpoint()
    CENTRE_OFFSET[pc["path"]] = r0.GetInverse().TransformDir(Gf.Vec3d(mid) - p0)
CLEAR_Z = lift_height()
fen0, tilt0, off0, ob0 = read_board()
log = [{"move": "start", "expected": fen_placement(expected), "simulated": fen0,
        "match": fen0 == fen_placement(expected), "max_tilt_deg": tilt0, "max_offset_m": off0}]
print("POSITION start", json.dumps(log[-1]), flush=True)
for ply, mv in enumerate(args.moves.split()):
    a, b = mv[:2], mv[2:4]
    pc = by_square.pop(a)
    color = "white" if pc["letter"].isupper() else "black"
    if b in by_square:
        take_off(by_square.pop(b), color)
    set_kinematic(pc["path"], True)
    p = centre(pc["path"])
    dest = square_center(b)
    drop_z = p[2] + 0.004  # set it down from 4 mm above where it rested
    carry(pc["path"], [(p[0], p[1], CLEAR_Z), (dest[0], dest[1], CLEAR_Z), (dest[0], dest[1], drop_z)], 2.0)
    set_kinematic(pc["path"], False)
    step_frames(18)  # let it settle under gravity
    by_square[b] = pc
    fa, ra = "abcdefgh".index(a[0]), int(a[1]) - 1
    fb, rb = "abcdefgh".index(b[0]), int(b[1]) - 1
    expected[(fb, rb)] = expected.pop((fa, ra))
    fen, tilt, off, ob = read_board()
    log.append({"move": mv, "expected": fen_placement(expected), "simulated": fen,
                "match": fen == fen_placement(expected), "max_tilt_deg": tilt, "max_offset_m": off,
                "off_board": ob})
    print("POSITION", mv, json.dumps(log[-1]), flush=True)
step_frames(20)

summary = {"asset": args.asset, "moves": args.moves.split(), "positions": log,
           "all_match": all(x["match"] for x in log),
           "max_tilt_deg": max(x["max_tilt_deg"] for x in log),
           "max_offset_m": max(x["max_offset_m"] for x in log), "frames": frame[0]}
(out_dir / "positions.json").write_text(json.dumps(summary, indent=1))
mp4 = out_dir / f"{args.asset}_game.mp4"
subprocess.run([shutil.which("ffmpeg") or "ffmpeg", "-v", "error", "-y", "-framerate", str(args.fps),
                "-i", str(frames_dir / "f%05d.png"), "-c:v", "libx264", "-preset", "slow", "-crf", "20",
                "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(mp4)], check=True)
print("GAME", json.dumps({"mp4": str(mp4), **{k: summary[k] for k in ("all_match", "max_tilt_deg", "max_offset_m", "frames")}}), flush=True)
app.close()
