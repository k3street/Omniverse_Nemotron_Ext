#!/usr/bin/env python3
"""Character rigging: a humanoid skeleton and skin weights for a character mesh.

A character asset is either rigged (a UsdSkel Skeleton binds its meshes - a
Mixamo export) or a static figure (a sculpt in T- or A-pose). Ingest treated
both as rigid statues. This:

  detect   finds a UsdSkel skeleton and its joints, maps them to humanoid roles
           (hips, spine, head, arms, legs) by name, and records the rig.
  autorig  for a static humanoid: a standard 21-joint skeleton placed from the
           mesh's own landmarks - slices of the body at fractions of its height
           find the hips, chest, neck and head; each arm is the points beside
           the torso above the waist, its shoulder the inner top, its wrist the
           far end less a hand; legs from the two masses below the crotch -
           and skin weights from distance to the bone segments (the four
           nearest, inverse-distance^4; a left limb's vertex never takes a
           right bone). Authored as UsdSkel on the derivative: the asset's
           root becomes a SkelRoot, a Skeleton under it, every mesh bound.
  verify   poses the skeleton (arms raised, a knee bent) in a SkelAnimation,
           renders the pose and asks a vision judge whether the body follows
           without tearing.

    python scripts/character_rig.py <asset_id> [...] [--verify]
Evidence goes to entry["character_rig"].
"""
from __future__ import annotations

import argparse
import base64
import json
import math
import re
import sys
from datetime import date
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))
QUEUE = REPO / "workspace" / "review_queue"
UP = 2

# the skeleton: (name, parent)
JOINTS = [("Hips", None), ("Spine", "Hips"), ("Chest", "Spine"), ("Neck", "Chest"), ("Head", "Neck"),
          ("LeftShoulder", "Chest"), ("LeftArm", "LeftShoulder"), ("LeftForeArm", "LeftArm"), ("LeftHand", "LeftForeArm"),
          ("RightShoulder", "Chest"), ("RightArm", "RightShoulder"), ("RightForeArm", "RightArm"),
          ("RightHand", "RightForeArm"),
          ("LeftUpLeg", "Hips"), ("LeftLeg", "LeftUpLeg"), ("LeftFoot", "LeftLeg"), ("LeftToe", "LeftFoot"),
          ("RightUpLeg", "Hips"), ("RightLeg", "RightUpLeg"), ("RightFoot", "RightLeg"), ("RightToe", "RightFoot")]
ROLE_PATTERNS = {"hips": r"hips|pelvis|root", "spine": r"spine|chest|torso", "head": r"head|neck",
                 "left_arm": r"left.?(shoulder|arm|forearm|hand)|(shoulder|arm|hand)_?l\b|l_(arm|hand)",
                 "right_arm": r"right.?(shoulder|arm|forearm|hand)|(shoulder|arm|hand)_?r\b|r_(arm|hand)",
                 "left_leg": r"left.?(up)?(leg|thigh|knee|foot|toe)|(leg|foot)_?l\b|l_(leg|foot)",
                 "right_leg": r"right.?(up)?(leg|thigh|knee|foot|toe)|(leg|foot)_?r\b|r_(leg|foot)"}


def _asset_root(stage, asset_id):
    root = "/World/" + "".join(w.capitalize() for w in asset_id.split("_"))
    if stage.GetPrimAtPath(root):
        return root
    return str(next(iter(stage.GetPrimAtPath("/World").GetChildren())).GetPath())


def detect(stage) -> dict | None:
    """An authored skeleton: its path, joints and the humanoid roles they cover."""
    from pxr import UsdSkel

    skels = [p for p in stage.Traverse() if p.IsA(UsdSkel.Skeleton)]
    if not skels:
        return None
    sk = max(skels, key=lambda p: len(UsdSkel.Skeleton(p).GetJointsAttr().Get() or []))
    joints = [str(j) for j in UsdSkel.Skeleton(sk).GetJointsAttr().Get() or []]
    leaf = [j.rsplit("/", 1)[-1].lower() for j in joints]
    roles = {r: sum(1 for n in leaf if re.search(pat, n)) for r, pat in ROLE_PATTERNS.items()}
    bound = sum(1 for p in stage.Traverse() if p.HasAPI(UsdSkel.BindingAPI))
    return {"skeleton": str(sk.GetPath()), "joints": len(joints), "roles": roles, "bound_prims": bound,
            "humanoid": all(roles[k] for k in ("hips", "spine", "head", "left_arm", "right_arm", "left_leg",
                                               "right_leg"))}


def _mesh_points(stage, root):
    from pxr import Gf, Usd, UsdGeom

    out = []
    for p in Usd.PrimRange(stage.GetPrimAtPath(root)):
        if p.IsA(UsdGeom.Mesh) and p.IsActive():
            m = UsdGeom.Xformable(p).ComputeLocalToWorldTransform(0)
            v = np.array([list(m.Transform(Gf.Vec3d(*q))) for q in UsdGeom.Mesh(p).GetPointsAttr().Get() or []])
            out.append((p, m, v))
    return out


def landmarks(P: np.ndarray) -> dict:
    """Joint positions (world) for a humanoid standing upright, facing -Y or +Y,
    left-right along X, from its points P."""
    lo, hi = P.min(0), P.max(0)
    H = hi[UP] - lo[UP]
    cx = float(np.median(P[:, 0]))
    cy = float(np.median(P[:, 1]))

    def z(f):
        return lo[UP] + f * H

    def core(f, half=0.06):
        """Centre of the body's slice at height fraction f, inside the torso."""
        s = P[np.abs(P[:, UP] - z(f)) < 0.015 * H]
        s = s[np.abs(s[:, 0] - cx) < half * H]
        return np.array([s[:, 0].mean(), s[:, 1].mean(), z(f)]) if len(s) else np.array([cx, cy, z(f)])

    J = {"Hips": core(0.53), "Spine": core(0.62), "Chest": core(0.72), "Neck": core(0.86, 0.05),
         "Head": core(0.92, 0.05)}
    torso_half = 0.11 * H                                     # beyond this from the centre line: an arm
    for side, sgn in (("Left", 1.0), ("Right", -1.0)):        # the figure's left is +X when it faces -Y
        arm = P[(P[:, UP] > z(0.45)) & ((P[:, 0] - cx) * sgn > torso_half)]
        sh = np.array([cx + sgn * 0.12 * H, J["Chest"][1], z(0.82)])
        if len(arm) > 20:
            far = arm[np.argmax((arm[:, 0] - cx) * sgn + 0.2 * (z(0.82) - arm[:, UP]) * 0)]
            d = far - sh
            L = np.linalg.norm(d)
            u = d / max(L, 1e-9)
            wrist = sh + u * (L - 0.1 * H)                    # the hand is ~10% of the height
            elbow = sh + u * (0.5 * (L - 0.1 * H))
            hand = far
        else:                                                 # arms at the sides (no points beside the torso)
            elbow = np.array([cx + sgn * 0.15 * H, J["Chest"][1], z(0.62)])
            wrist = np.array([cx + sgn * 0.16 * H, J["Chest"][1], z(0.48)])
            hand = np.array([cx + sgn * 0.16 * H, J["Chest"][1], z(0.42)])
        J[f"{side}Shoulder"] = np.array([cx + sgn * 0.05 * H, J["Chest"][1], z(0.81)])
        J[f"{side}Arm"], J[f"{side}ForeArm"], J[f"{side}Hand"] = sh, elbow, wrist
        J[f"_{side}HandEnd"] = hand
        leg = P[(P[:, UP] < z(0.45)) & ((P[:, 0] - cx) * sgn > 0)]
        lx = float(np.median(leg[:, 0])) if len(leg) else cx + sgn * 0.08 * H
        ly = float(np.median(leg[:, 1])) if len(leg) else cy
        foot = P[(P[:, UP] < z(0.06)) & ((P[:, 0] - cx) * sgn > 0)]
        # toes point where the foot reaches furthest from the ankle, in Y
        ty = (foot[:, 1].min() if len(foot) and abs(foot[:, 1].min() - ly) > abs(foot[:, 1].max() - ly)
              else foot[:, 1].max() if len(foot) else ly - 0.08 * H)
        J[f"{side}UpLeg"] = np.array([lx, ly, z(0.50)])
        J[f"{side}Leg"] = np.array([lx, ly, z(0.27)])
        J[f"{side}Foot"] = np.array([lx, ly, z(0.045)])
        J[f"{side}Toe"] = np.array([lx, ty, z(0.01)])
    return J


def _seg_dist(P, a, b):
    ab = b - a
    t = np.clip(((P - a) @ ab) / max(ab @ ab, 1e-12), 0, 1)
    return np.linalg.norm(P - (a + t[:, None] * ab), axis=1)


def weights(P: np.ndarray, J: dict, cx: float, H: float, k: int = 4):
    """Skin weights: each vertex to its k nearest bones (a bone runs from a
    joint to its child, the leaves to their ends), inverse-distance^4; a vertex
    clearly on one side never takes the other side's limb bones."""
    names = [n for n, _ in JOINTS]
    ends = {"Head": J["Head"] + np.array([0, 0, 0.08 * H]), "LeftHand": J["_LeftHandEnd"],
            "RightHand": J["_RightHandEnd"]}
    child = {}
    for n, par in JOINTS:
        if par:
            child.setdefault(par, n)
    D = np.zeros((len(P), len(names)))
    for i, n in enumerate(names):
        a = J[n]
        b = ends.get(n, J[child[n]] if n in child else a + np.array([0, 0, 0.01]))
        d = _seg_dist(P, a, b)
        side = 1.0 if n.startswith("Left") else -1.0 if n.startswith("Right") else 0.0
        if side:
            wrong = (P[:, 0] - cx) * side < -0.02 * H
            d = np.where(wrong, 1e9, d)
        D[:, i] = d
    idx = np.argsort(D, axis=1)[:, :k]
    dd = np.take_along_axis(D, idx, axis=1)
    w = 1.0 / np.maximum(dd, 1e-4) ** 4
    w[dd > 1e8] = 0.0
    w /= np.maximum(w.sum(1, keepdims=True), 1e-12)
    return idx.astype(np.int32), w.astype(np.float32)


def autorig(entry: dict) -> dict:
    """Author a humanoid UsdSkel rig on the entry's derivative."""
    from pxr import Gf, Sdf, Usd, UsdGeom, UsdSkel, Vt

    stage = Usd.Stage.Open(entry["file"])
    root = _asset_root(stage, entry["asset_id"])
    meshes = _mesh_points(stage, root)
    P = np.vstack([v for _, _, v in meshes if len(v)])
    lo, hi = P.min(0), P.max(0)
    H = float(hi[UP] - lo[UP])
    J = landmarks(P)
    cx = float(np.median(P[:, 0]))
    rp = stage.GetPrimAtPath(root)
    rp.SetTypeName("SkelRoot")
    rootw = UsdGeom.Xformable(rp).ComputeLocalToWorldTransform(0)
    to_root = rootw.GetInverse()
    skel = UsdSkel.Skeleton.Define(stage, f"{root}/Rig")
    paths, world = [], {}
    for n, par in JOINTS:
        paths.append(f"{paths[[m for m, _ in JOINTS].index(par)]}/{n}" if par else n)
        world[n] = J[n]
    skel.CreateJointsAttr(Vt.TokenArray(paths))
    # joints carry no rotation at bind: translations only, in the root's space
    bind, rest = [], []
    for n, par in JOINTS:
        pw = to_root.Transform(Gf.Vec3d(*world[n]))
        bind.append(Gf.Matrix4d().SetTranslate(pw))
        if par:
            pp = to_root.Transform(Gf.Vec3d(*world[par]))
            rest.append(Gf.Matrix4d().SetTranslate(pw - pp))
        else:
            rest.append(Gf.Matrix4d().SetTranslate(pw))
    skel.CreateBindTransformsAttr(Vt.Matrix4dArray(bind))
    skel.CreateRestTransformsAttr(Vt.Matrix4dArray(rest))
    for prim, m, v in meshes:
        if not len(v):
            continue
        idx, w = weights(v, J, cx, H)
        b = UsdSkel.BindingAPI.Apply(prim)
        b.CreateSkeletonRel().SetTargets([skel.GetPath()])
        b.CreateJointIndicesPrimvar(False, 4).Set(Vt.IntArray(idx.reshape(-1).tolist()))
        b.CreateJointWeightsPrimvar(False, 4).Set(Vt.FloatArray(w.reshape(-1).tolist()))
        b.CreateGeomBindTransformAttr(to_root * Gf.Matrix4d(m) if False else Gf.Matrix4d(m) * to_root)
    stage.GetRootLayer().Save()
    return {"skeleton": str(skel.GetPath()), "joints": len(JOINTS), "height_m": round(H, 3),
            "landmarks": {n: [round(float(x), 4) for x in J[n]] for n, _ in JOINTS}, "meshes": len(meshes)}


def pose_and_render(entry: dict, rig: dict) -> list[str]:
    """A SkelAnimation raising both arms and bending a knee, rendered at rest and posed."""
    from pxr import Gf, Sdf, Usd, UsdGeom, UsdSkel, Vt

    from ingest_asset import THREE_QUARTER_AZ_DEG, _orbit_render

    out = QUEUE / "thumbs" / f"{entry['asset_id']}__pose.usda"
    layer = Sdf.Layer.FindOrOpen(str(out))
    if layer:
        layer.Clear()
    else:
        layer = Sdf.Layer.CreateNew(str(out))
    layer.subLayerPaths.append(entry["file"])
    stage = Usd.Stage.Open(layer)
    stage.SetDefaultPrim(stage.GetPrimAtPath("/World"))
    # the layer over the asset carries the asset's own up axis and units: left
    # out, the renderer takes Y up and lays a Z-up model on its side (a toaster
    # read as lying down, a rocker's axis judged in a tipped-over view)
    src = Usd.Stage.Open(entry["file"])
    UsdGeom.SetStageUpAxis(stage, UsdGeom.GetStageUpAxis(src))
    UsdGeom.SetStageMetersPerUnit(stage, UsdGeom.GetStageMetersPerUnit(src))
    skel = UsdSkel.Skeleton(stage.GetPrimAtPath(rig["skeleton"]))
    joints = list(skel.GetJointsAttr().Get())
    rest = list(skel.GetRestTransformsAttr().Get())
    anim = UsdSkel.Animation.Define(stage, rig["skeleton"] + "_Pose")
    anim.CreateJointsAttr(Vt.TokenArray(joints))
    names = [j.rsplit("/", 1)[-1] for j in joints]
    trans = [Gf.Vec3f(m.ExtractTranslation()) for m in rest]
    rots = [Gf.Quatf(1, 0, 0, 0)] * len(joints)
    posed = list(rots)

    # the joints live in the asset root's space, which may be turned (a Y-up
    # source under a Z-up wrapper): the pose's world axes are turned into it
    root = stage.GetPrimAtPath(rig["skeleton"]).GetParent()
    to_root = UsdGeom.Xformable(root).ComputeLocalToWorldTransform(0).GetInverse()

    def q(axis, deg):
        a = to_root.TransformDir(Gf.Vec3d(*axis)).GetNormalized()
        r = Gf.Rotation(a, deg).GetQuat()
        return Gf.Quatf(r.GetReal(), *r.GetImaginary())
    for i, n in enumerate(names):
        if n == "LeftArm":
            posed[i] = q((0, 1, 0), -50)        # arms swing up (about the front-back axis)
        elif n == "RightArm":
            posed[i] = q((0, 1, 0), 50)
        elif n == "LeftLeg":
            posed[i] = q((1, 0, 0), 45)         # a knee bends
        elif n == "LeftForeArm":
            posed[i] = q((0, 0, 1), 30)
    anim.CreateTranslationsAttr().Set(Vt.Vec3fArray(trans))
    anim.CreateScalesAttr().Set(Vt.Vec3hArray([Gf.Vec3h(1, 1, 1)] * len(joints)))
    anim.GetRotationsAttr().Set(Vt.QuatfArray(rots), 1)
    anim.GetRotationsAttr().Set(Vt.QuatfArray(posed), 2)
    UsdSkel.BindingAPI.Apply(skel.GetPrim()).CreateAnimationSourceRel().SetTargets([anim.GetPath()])
    stage.SetStartTimeCode(1)
    stage.SetEndTimeCode(2)
    layer.Save()
    # _orbit_render renders one frame per azimuth; render rest (t=1) and posed (t=2) at the same azimuth
    frames = _orbit_render(str(out), f"{entry['asset_id']}__pose", [THREE_QUARTER_AZ_DEG, THREE_QUARTER_AZ_DEG])
    return frames


def judge_pose(entry: dict, images: list[str]) -> dict:
    import anthropic

    content = [{"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                            "data": base64.b64encode(Path(i).read_bytes()).decode()}}
               for i in images]
    content.append({"type": "text", "text": (
        "A character mesh was given a skeleton automatically. Image 1: the character at rest. Image 2: the "
        "skeleton posed - both arms raised about 50 degrees, the left knee bent 45 degrees, the left "
        "forearm turned 30 degrees. Judge the RIG, not the art: do the arms, the knee and the forearm "
        "move as described, with the body following smoothly - no torn or stretched-out triangles, no "
        "part left behind, no piece of the torso or the other leg dragged along?")})
    schema = {"type": "object", "properties": {
        "rig_ok": {"type": "boolean"}, "moved_as_described": {"type": "boolean"},
        "problems": {"type": "string"}, "confidence": {"type": "number"}},
        "required": ["rig_ok", "moved_as_described", "problems", "confidence"], "additionalProperties": False}
    r = anthropic.Anthropic().messages.create(model="claude-opus-5", max_tokens=16000,
                                              messages=[{"role": "user", "content": content}],
                                              output_config={"format": {"type": "json_schema", "schema": schema}})
    return json.loads(next(b.text for b in r.content if b.type == "text"))


def rig_entry(asset_id: str, verify: bool = True) -> dict:
    from pxr import Usd

    qf = QUEUE / f"{asset_id}.json"
    entry = json.loads(qf.read_text())
    found = detect(Usd.Stage.Open(entry["file"]))
    if found and found["skeleton"].endswith("/Rig") and (entry.get("character_rig") or {}).get("kind") != "authored":
        found = None                          # the rig this script authored: re-rig (rules may have changed)
    if found:
        res = {"date": date.today().isoformat(), "kind": "authored", **found}
    else:
        res = {"date": date.today().isoformat(), "kind": "autorig", **autorig(entry)}
    if verify and res.get("skeleton") and res["kind"] == "autorig":
        frames = pose_and_render(entry, res)
        if len(frames) >= 2:
            res["pose_images"] = [str(Path(f).relative_to(REPO)) for f in frames]
            res["pose_check"] = judge_pose(entry, frames)
    entry = json.loads(qf.read_text())
    entry["character_rig"] = res
    from processing import record
    record(entry, "rig", kind=res["kind"], ok=(res.get("pose_check") or {}).get("rig_ok", found is not None))
    qf.write_text(json.dumps(entry, indent=1))
    return res


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("assets", nargs="+")
    ap.add_argument("--no-verify", action="store_true")
    args = ap.parse_args()
    for a in args.assets:
        r = rig_entry(a, not args.no_verify)
        print(a, json.dumps({k: v for k, v in r.items() if k not in ("landmarks",)})[:600])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
