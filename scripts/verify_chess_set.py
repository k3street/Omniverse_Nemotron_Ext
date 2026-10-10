#!/usr/bin/env python3
"""Verify a chess set in PhysX: the pieces rest on their squares, and the
opening moves the convention describes can be played.

Runs headless in Isaac Sim. The set is placed on a floor and released; a
camera records from white's side. Measured:

  settled   after a second and a half every piece stands where it started
            (within three tenths of a square), upright, on the board (none
            fell through a tile), and the board itself has not moved
  played    three pieces are carried as a hand would (lifted, moved, set
            down, let go): 1. e4 e5 2. Nf3. The position is then read back
            from the live piece poses through the board's grid
            (simReady:chessboard, simReady:chess) and must be the position
            those moves give; the other pieces must not have moved

    source scripts/isaac_slot.sh && <isaac python.sh> scripts/verify_chess_set.py <asset_id>
Writes workspace/asset_animations/<id>/chess/{chess.mp4, contact_sheet.png, summary.json, log.json}
and prints a CHESS PASS|FAIL line.
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
QUEUE = REPO / "workspace" / "review_queue"
OUT_ROOT = REPO / "workspace" / "asset_animations"
sys.path.insert(0, str(REPO / "scripts"))

ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
ap.add_argument("asset_id")
ap.add_argument("--settle", type=float, default=1.5, help="seconds at rest before the moves")
ap.add_argument("--fps", type=int, default=24)
ap.add_argument("--width", type=int, default=1280)
ap.add_argument("--height", type=int, default=720)
args = ap.parse_args()

MOVES = [("e2", "e4"), ("e7", "e5"), ("g1", "f3")]          # 1. e4 e5 2. Nf3
AFTER = "rnbqkbnr/pppp1ppp/8/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R"

entry = json.loads((QUEUE / f"{args.asset_id}.json").read_text())
usd_path = entry["file"]
out_dir = OUT_ROOT / args.asset_id / "chess"
frames_dir = out_dir / "frames"
shutil.rmtree(frames_dir, ignore_errors=True)
frames_dir.mkdir(parents=True)

from isaacsim import SimulationApp  # noqa: E402

app = SimulationApp({"headless": True, "width": args.width, "height": args.height})

import omni.physx  # noqa: E402
import omni.replicator.core as rep  # noqa: E402
import omni.usd  # noqa: E402
from PIL import Image  # noqa: E402
from pxr import Gf, PhysxSchema, Sdf, Usd, UsdGeom, UsdLux, UsdPhysics  # noqa: E402

from chess_board import fen_placement, letter  # noqa: E402

omni.usd.get_context().open_stage(usd_path)
for _ in range(20):
    app.update()
stage = omni.usd.get_context().get_stage()
px = omni.physx.get_physx_interface()
HZ = 120
DT = 1.0 / HZ

# --- the convention, as authored on the file ------------------------------------
board_prim = next((p for p in stage.Traverse() if p.GetCustomDataByKey("simReady:chessboard")), None)
if board_prim is None:
    raise SystemExit(f"{args.asset_id}: no simReady:chessboard on the file (run the chess convention first)")
grid = json.loads(board_prim.GetCustomDataByKey("simReady:chessboard"))
pieces = {}
for p in stage.Traverse():
    tag = p.GetCustomDataByKey("simReady:chess")
    if tag and p.HasAPI(UsdPhysics.RigidBodyAPI):
        pieces[str(p.GetPath())] = json.loads(tag)
if len(pieces) != 32:
    raise SystemExit(f"{args.asset_id}: {len(pieces)} tagged piece bodies, not 32")
SIZE = float(grid["square_m"])
A1 = Gf.Vec2d(*grid["a1_center"])
FILE_DIR = Gf.Vec2d(*grid["file_dir"])
RANK_DIR = Gf.Vec2d(*grid["rank_dir"])
TOP = float(grid["surface_z"])


def square_xy(name: str) -> Gf.Vec2d:
    f, r = "abcdefgh".index(name[0]), int(name[1]) - 1
    return A1 + FILE_DIR * (f * SIZE) + RANK_DIR * (r * SIZE)


def to_square(xy: Gf.Vec2d):
    d = xy - A1
    f = Gf.Dot(d, FILE_DIR) / SIZE
    r = Gf.Dot(d, RANK_DIR) / SIZE
    fi, ri = round(f), round(r)
    return fi, ri, math.hypot(f - fi, r - ri) * SIZE


def pose(path):
    r = px.get_rigidbody_transformation(path)
    if not r.get("ret_val", True):
        return None
    q = r["rotation"]
    return Gf.Vec3d(*r["position"]), Gf.Quatd(q[3], q[0], q[1], q[2])


xf = UsdGeom.XformCache(Usd.TimeCode.Default())
# a body's box centre is what sits on the square; its prim origin may lie
# elsewhere (Sketchfab pieces share the board's origin), so the offset is
# kept in the body's own frame and turned with it
centre_off = {}
bb = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.default_, UsdGeom.Tokens.render])
for path in pieces:
    prim = stage.GetPrimAtPath(path)
    c = bb.ComputeWorldBound(prim).ComputeAlignedRange().GetMidpoint()
    m = xf.GetLocalToWorldTransform(prim)
    origin = m.ExtractTranslation()
    # the world matrix carries the root's scale: a proper decomposition, not
    # ExtractRotation (which assumes an unscaled matrix)
    centre_off[path] = Gf.Transform(m).GetRotation().GetInverse().TransformDir(Gf.Vec3d(c) - origin)
at_load = {path: xf.GetLocalToWorldTransform(stage.GetPrimAtPath(path)) for path in pieces}
# a piece's own up: the file's root turns the bodies (Y-up sources carry a
# quarter turn), so tilt is measured against each body's rest rotation
up_local = {path: Gf.Transform(m).GetRotation().GetInverse().TransformDir(Gf.Vec3d(0, 0, 1)) for path, m in at_load.items()}
pieces_height = {path: bb.ComputeWorldBound(stage.GetPrimAtPath(path)).ComputeAlignedRange().GetSize()[2] for path in pieces}


def centre_xy(path):
    p = pose(path)
    if p is None:
        return None, None
    pos, q = p
    off = Gf.Rotation(q).TransformDir(centre_off[path])
    c = pos + off
    return Gf.Vec2d(c[0], c[1]), c[2]


def tilt_deg(path):
    p = pose(path)
    if p is None:
        return None
    up = Gf.Rotation(p[1]).TransformDir(up_local[path])
    return math.degrees(math.acos(max(-1.0, min(1.0, up[2]))))


# --- scene: floor under the board, light, camera from white's side ---------------
rng = bb.ComputeWorldBound(stage.GetDefaultPrim() or stage.GetPseudoRoot()).ComputeAlignedRange()
lo, hi = rng.GetMin(), rng.GetMax()
center, size = (lo + hi) * 0.5, hi - lo
radius = 0.5 * size.GetLength()
with Usd.EditContext(stage, stage.GetSessionLayer()):
    scenes = [p for p in stage.Traverse() if p.IsA(UsdPhysics.Scene)]
    scene = UsdPhysics.Scene(scenes[0]) if scenes else UsdPhysics.Scene.Define(stage, "/ChessView/PhysicsScene")
    scene.CreateGravityDirectionAttr().Set(Gf.Vec3f(0, 0, -1))
    scene.CreateGravityMagnitudeAttr().Set(9.81)
    sp = PhysxSchema.PhysxSceneAPI.Apply(scene.GetPrim())
    sp.CreateTimeStepsPerSecondAttr().Set(HZ)
    for extra in scenes[1:]:
        extra.SetActive(False)
    UsdLux.DomeLight.Define(stage, "/ChessView/Dome").CreateIntensityAttr(1200)
    key = UsdLux.DistantLight.Define(stage, "/ChessView/Key")
    key.CreateIntensityAttr(2500)
    UsdGeom.XformCommonAPI(key.GetPrim()).SetRotate(Gf.Vec3f(-45, 0, -35))
    z0 = lo[2] - 0.002
    f = 4.0 * radius
    floor = UsdGeom.Mesh.Define(stage, "/ChessView/Floor")
    floor.CreatePointsAttr([(center[0] - f, center[1] - f, z0), (center[0] + f, center[1] - f, z0),
                            (center[0] + f, center[1] + f, z0), (center[0] - f, center[1] + f, z0)])
    floor.CreateFaceVertexCountsAttr([4])
    floor.CreateFaceVertexIndicesAttr([0, 1, 2, 3])
    floor.CreateDisplayColorAttr([(0.35, 0.35, 0.37)])
    ground = UsdGeom.Cube.Define(stage, "/ChessView/Ground")
    ground.CreateSizeAttr(1.0)
    UsdGeom.XformCommonAPI(ground.GetPrim()).SetTranslate(Gf.Vec3d(center[0], center[1], z0 - 0.05))
    UsdGeom.XformCommonAPI(ground.GetPrim()).SetScale(Gf.Vec3f(2 * f, 2 * f, 0.1))
    ground.CreateVisibilityAttr("invisible")
    UsdPhysics.CollisionAPI.Apply(ground.GetPrim())
    cam = UsdGeom.Camera.Define(stage, "/ChessView/Camera")
    cam.CreateFocalLengthAttr(24.0)
    cam.CreateHorizontalApertureAttr(20.955)
    cam.CreateClippingRangeAttr(Gf.Vec2f(0.01, 1000.0))
    hfov = 2 * math.atan(20.955 / (2 * 24.0))
    vfov = 2 * math.atan(math.tan(hfov / 2) * args.height / args.width)
    dist = 1.15 * radius / math.tan(min(hfov, vfov) / 2)
    # behind white's right-hand corner, looking across the board
    back = Gf.Vec3d(-RANK_DIR[0], -RANK_DIR[1], 0) * 0.8 + Gf.Vec3d(FILE_DIR[0], FILE_DIR[1], 0) * 0.6
    back = back.GetNormalized()
    target = Gf.Vec3d(center[0], center[1], TOP + 0.02)
    eye = target + back * (dist * math.cos(math.radians(38))) + Gf.Vec3d(0, 0, dist * math.sin(math.radians(38)))
    UsdGeom.Xformable(cam.GetPrim()).AddTransformOp().Set(Gf.Matrix4d().SetLookAt(eye, target, Gf.Vec3d(0, 0, 1)).GetInverse())

# --- run -------------------------------------------------------------------------
px.start_simulation()
for _ in range(10):
    app.update()
render_product = rep.create.render_product("/ChessView/Camera", (args.width, args.height))
rgb = rep.AnnotatorRegistry.get_annotator("rgb")
rgb.attach([render_product])

start = {}
for path, tag in pieces.items():
    xy, z = centre_xy(path)
    start[path] = {"square": tag["start_square"], "xy": xy, "z": z}
board0 = pose(str(board_prim.GetPath()))
steps_per_frame = max(1, HZ // args.fps)
log, frame, clock = [], 0, 0.0


def step_frame(note=""):
    global frame, clock
    for _ in range(steps_per_frame):
        px.update_simulation(DT, clock)
        clock += DT
    px.update_transformations(False, True, False, False)
    xf.Clear()
    rep.orchestrator.step(rt_subframes=2, delta_time=0.0, pause_timeline=False)
    Image.fromarray(rgb.get_data()[:, :, :3]).save(frames_dir / f"f{frame:05d}.png")
    row = {"t": round(frame / args.fps, 3), "note": note}
    frame += 1
    log.append(row)
    return row


def placement():
    """Every piece's square from its live pose, with how far off it sits."""
    out = {}
    for path, tag in pieces.items():
        xy, z = centre_xy(path)
        if xy is None:
            continue
        fi, ri, off = to_square(xy)
        out[path] = {"file": fi, "rank": ri, "off_m": round(off, 4), "z": round(z, 4),
                     "tilt_deg": round(tilt_deg(path) or 0.0, 1), "piece": tag["piece"], "color": tag["color"]}
    return out


def fen_of(pl):
    placed = {}
    for v in pl.values():
        if 0 <= v["file"] < 8 and 0 <= v["rank"] < 8:
            placed[(v["file"], v["rank"])] = letter(v["piece"], v["color"])
    return fen_placement(placed)


# settle
for i in range(int(args.settle * args.fps)):
    step_frame("settle")
settled_pl = placement()
by_square = {}
for path, v in settled_pl.items():
    by_square.setdefault(f"{'abcdefgh'[v['file']] if 0 <= v['file'] < 8 else '?'}{v['rank'] + 1}", []).append(path)
bad_settle = {}
for path, v in settled_pl.items():
    s = start[path]
    want = s["square"]
    here = f"{'abcdefgh'[v['file']] if 0 <= v['file'] < 8 else '?'}{v['rank'] + 1}"
    through = v["z"] < TOP - 0.5 * pieces_height[path]
    if here != want or v["off_m"] > 0.3 * SIZE or v["tilt_deg"] > 15 or through:
        bad_settle[Path(path).name] = {"wanted": want, "at": here, "off_m": v["off_m"], "tilt_deg": v["tilt_deg"],
                                       "fell_through": through}
board1 = pose(str(board_prim.GetPath()))
board_moved = (board1[0] - board0[0]).GetLength() if board0 and board1 else None
settled = not bad_settle and board_moved is not None and board_moved < 0.01


# play: a kinematic carry, as a hand would
def carry(frm: str, to: str, seconds=1.2):
    paths = by_square.get(frm) or []
    if len(paths) != 1:
        return {"from": frm, "to": to, "ok": False, "why": f"{len(paths)} pieces on {frm}"}
    path = paths[0]
    prim = stage.GetPrimAtPath(path)
    rb = UsdPhysics.RigidBodyAPI(prim)
    p0 = pose(path)
    if p0 is None:
        return {"from": frm, "to": to, "ok": False, "why": "no pose"}
    pos0, q0 = p0
    off = Gf.Rotation(q0).TransformDir(centre_off[path])
    dest = square_xy(to)
    goal = Gf.Vec3d(dest[0] - off[0], dest[1] - off[1], pos0[2])   # the box centre over the square
    lift = max(pieces_height.values()) + 0.01       # clear of every piece it passes over
    parent_world = xf.GetLocalToWorldTransform(prim.GetParent())
    xfm = UsdGeom.Xformable(prim)
    with Usd.EditContext(stage, stage.GetSessionLayer()):
        rb.CreateKinematicEnabledAttr().Set(True)
    n = int(seconds * args.fps)
    for i in range(n):
        u = (i + 1) / n
        # up, across, down
        if u < 0.25:
            pos = pos0 + Gf.Vec3d(0, 0, lift * (u / 0.25))
        elif u < 0.75:
            w = (u - 0.25) / 0.5
            pos = pos0 + (goal - pos0) * w + Gf.Vec3d(0, 0, lift)
        else:
            pos = goal + Gf.Vec3d(0, 0, lift * (1 - (u - 0.75) / 0.25))
        # the body's world matrix with its scale kept, rotation and place set
        t = Gf.Transform(at_load[path])
        t.SetRotation(Gf.Rotation(q0))
        t.SetTranslation(pos)
        with Usd.EditContext(stage, stage.GetSessionLayer()):
            xfm.ClearXformOpOrder()
            xfm.AddTransformOp().Set(t.GetMatrix() * parent_world.GetInverse())
        step_frame(f"{frm}-{to}")
    with Usd.EditContext(stage, stage.GetSessionLayer()):
        rb.CreateKinematicEnabledAttr().Set(False)
    for _ in range(int(0.4 * args.fps)):
        step_frame("let go")
    xy, z = centre_xy(path)
    fi, ri, offm = to_square(xy) if xy is not None else (-1, -1, 1.0)
    here = f"{'abcdefgh'[fi] if 0 <= fi < 8 else '?'}{ri + 1}"
    ok = here == to and offm <= 0.3 * SIZE and (tilt_deg(path) or 0) < 15
    by_square[frm].remove(path)
    by_square.setdefault(here, []).append(path)
    return {"from": frm, "to": to, "ok": ok, "landed": here, "off_m": round(offm, 4), "tilt_deg": round(tilt_deg(path) or 0, 1),
            "piece": Path(path).name}


moves = [carry(a, b) for a, b in MOVES] if settled else []
for _ in range(int(0.6 * args.fps)):
    step_frame("rest")
final_pl = placement()
fen = fen_of(final_pl)
movers = {m["piece"] for m in moves if "piece" in m}
disturbed = {Path(p).name: round(v["off_m"], 4) for p, v in final_pl.items()
             if Path(p).name not in movers and settled_pl.get(p)
             and (v["file"], v["rank"]) != (settled_pl[p]["file"], settled_pl[p]["rank"])}
played = bool(moves) and all(m["ok"] for m in moves) and fen == AFTER and not disturbed
ok = settled and played
summary = {"asset": args.asset_id, "settled": settled, "not_settled": bad_settle, "board_moved_m": board_moved,
           "moves": moves, "fen_after": fen, "fen_expected": AFTER, "disturbed": disturbed, "played": played,
           "pass": ok, "frames": len(log), "fps": args.fps, "square_m": SIZE,
           "max_off_after_settle_m": round(max((v["off_m"] for v in settled_pl.values()), default=0.0), 4)}
out_dir.mkdir(parents=True, exist_ok=True)
(out_dir / "summary.json").write_text(json.dumps(summary, indent=1, default=str))
(out_dir / "log.json").write_text(json.dumps(log))
ff = shutil.which("ffmpeg") or "ffmpeg"
mp4 = out_dir / "chess.mp4"
subprocess.run([ff, "-v", "error", "-y", "-framerate", str(args.fps), "-i", str(frames_dir / "f%05d.png"),
                "-c:v", "libx264", "-preset", "slow", "-crf", "20", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
                str(mp4)], check=False)
picks = [round((len(log) - 1) * f) for f in (0.0, 0.2, 0.4, 0.6, 0.8, 1.0)] if log else []
if picks:
    tiles = [Image.open(frames_dir / f"f{p:05d}.png").resize((args.width // 2, args.height // 2)) for p in picks]
    sheet = Image.new("RGB", (3 * tiles[0].width, 2 * tiles[0].height))
    for i, t in enumerate(tiles):
        sheet.paste(t, ((i % 3) * t.width, (i // 3) * t.height))
    sheet.save(out_dir / "contact_sheet.png")
shutil.rmtree(frames_dir, ignore_errors=True)
why = []
if not settled:
    why.append(f"did not settle: {bad_settle or f'board moved {board_moved} m'}")
elif not played:
    why.append("moves: " + "; ".join(f"{m['from']}-{m['to']} " + ("ok" if m["ok"] else f"landed {m.get('landed')} ({m.get('why', '')})")
                                       for m in moves))
    if fen != AFTER:
        why.append(f"position read back {fen}, expected {AFTER}")
    if disturbed:
        why.append(f"other pieces moved: {disturbed}")
print(f"CHESS {'PASS' if ok else 'FAIL'} {args.asset_id}: " + ("; ".join(why) or
      f"settled on their squares (max {summary['max_off_after_settle_m']} m off), 1. e4 e5 2. Nf3 played and read back"),
      flush=True)
print("CHESS_SUMMARY " + json.dumps(summary, default=str), flush=True)
app.close()
