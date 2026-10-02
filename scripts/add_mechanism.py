#!/usr/bin/env python3
"""Author coupled mechanisms on an articulated asset (pxr only, no Kit).

A joint graph says how parts move; it cannot say that one joint only works
when another has moved. Some of that is physical and belongs in physics:

  latch   A bolt on a door leaf, coupled to an actuator joint (a panic bar,
          a lever) by a PhysX mimic joint, against a keeper on the frame.
          With the actuator at rest the bolt is out and the keeper stops the
          leaf; pushing the actuator retracts the bolt and the leaf swings.
          The bolt's tip is bevelled on the closing side, as a real latch is,
          so a closing leaf cams it in and it springs back behind the keeper.

Every mechanism also records a gate on the joint it guards, as customData
`simReady:gate`, so tools that drive joints (animate_asset.py) know to work
the actuator first. A gate without a physical mechanism is a rule only those
tools enforce; a latch enforces itself.

Usage:
    python scripts/add_mechanism.py <queue_asset_id> latch '<json spec>'

Spec (world coordinates, metres; joint names under <asset>/Joints):
    {"hinge_joint": "door_hinge", "actuator_joint": "crash_bar_push",
     "leaf": "<leaf link prim>", "frame": "<frame link prim>"}
"actuator_travel" (signed, metres or degrees) defaults to the actuator's
larger limit.
The edge, throw direction, height and swing side are derived from the hinge
and the leaf's bounds; override with "height_z" if the actuator is not at
the latch height.
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).resolve().parent))

BOLT_THROW_M = 0.025      # how far the bolt retracts
BOLT_PROTRUDE_M = 0.022   # how far it stands proud of the leaf edge when out
BOLT_LENGTH_M = 0.045
BOLT_HEIGHT_M = 0.03
BOLT_MASS_KG = 0.15
KEEPER_GAP_M = 0.004      # clearance between the leaf and the keeper
KEEPER_DEPTH_M = 0.014


def _unit_rot(m):
    from pxr import Gf

    m = Gf.Matrix4d(m)
    m.Orthonormalize()
    return m.ExtractRotationQuat()


def _box_points(lo, hi, to_local):
    from pxr import Gf

    return [Gf.Vec3f(to_local.Transform(Gf.Vec3d(x, y, z)))
            for z in (lo[2], hi[2]) for y in (lo[1], hi[1]) for x in (lo[0], hi[0])]


def _define_box(stage, path, lo, hi, color, to_local):
    """A box mesh given by world bounds; points are stored in the parent's
    space (`to_local` = inverse of the parent's world transform), because an
    ingest wrapper rotates and scales everything under the asset root."""
    from pxr import UsdGeom

    mesh = UsdGeom.Mesh.Define(stage, path)
    mesh.CreatePointsAttr(_box_points(lo, hi, to_local))
    # vertex i = x + 2y + 4z bits
    faces = [(0, 2, 3, 1), (4, 5, 7, 6), (0, 1, 5, 4), (2, 6, 7, 3), (0, 4, 6, 2), (1, 3, 7, 5)]
    mesh.CreateFaceVertexCountsAttr([4] * 6)
    mesh.CreateFaceVertexIndicesAttr([i for f in faces for i in f])
    mesh.CreateDisplayColorAttr([color])
    return mesh


def _define_bevelled_bolt(stage, path, lo, hi, tip_sign, bevel_sign, bevel, color, to_local):
    """A bolt prism whose tip corner on the closing side is cut at 45 degrees.

    tip_sign: +1/-1 along the throw axis (x here, after the caller's frame
    mapping); bevel_sign: the side (+1/-1 along y) the keeper approaches from
    when the leaf closes.
    """
    from pxr import Gf, UsdGeom

    x_tip = hi[0] if tip_sign > 0 else lo[0]
    x_back = lo[0] if tip_sign > 0 else hi[0]
    y_close = lo[1] if bevel_sign < 0 else hi[1]
    y_far = hi[1] if bevel_sign < 0 else lo[1]
    # cross-section (x, y), counter-clockwise not required for a convex hull collider
    section = [(x_back, y_close), (x_tip - tip_sign * bevel, y_close),
               (x_tip, y_close - bevel_sign * bevel), (x_tip, y_far), (x_back, y_far)]
    pts = [Gf.Vec3f(to_local.Transform(Gf.Vec3d(x, y, z))) for z in (lo[2], hi[2]) for (x, y) in section]
    n = len(section)
    faces = [list(range(n))[::-1], [n + i for i in range(n)]]
    faces += [[i, (i + 1) % n, n + (i + 1) % n, n + i] for i in range(n)]
    mesh = UsdGeom.Mesh.Define(stage, path)
    mesh.CreatePointsAttr(pts)
    mesh.CreateFaceVertexCountsAttr([len(f) for f in faces])
    mesh.CreateFaceVertexIndicesAttr([i for f in faces for i in f])
    mesh.CreateDisplayColorAttr([color])
    return mesh


def _joint_frames(joint, parent_w, child_w, anchor):
    from pxr import Gf

    joint.CreateLocalPos0Attr().Set(Gf.Vec3f(parent_w.GetInverse().Transform(anchor)))
    joint.CreateLocalPos1Attr().Set(Gf.Vec3f(child_w.GetInverse().Transform(anchor)))
    joint.CreateLocalRot0Attr().Set(Gf.Quatf(_unit_rot(parent_w).GetInverse()))
    joint.CreateLocalRot1Attr().Set(Gf.Quatf(_unit_rot(child_w).GetInverse()))


def add_latch(stage, asset_root: str, spec: dict) -> dict:
    """Author bolt, keeper, mimic coupling and gate. Returns what was made."""
    from pxr import Gf, Sdf, Usd, UsdGeom, UsdPhysics

    joints = f"{asset_root}/Joints"
    hinge = stage.GetPrimAtPath(f"{joints}/{spec['hinge_joint']}")
    actuator = stage.GetPrimAtPath(f"{joints}/{spec['actuator_joint']}")
    if not hinge.IsA(UsdPhysics.RevoluteJoint):
        raise ValueError(f"{spec['hinge_joint']} is not a revolute joint")
    if not actuator.IsA(UsdPhysics.PrismaticJoint) and not actuator.IsA(UsdPhysics.RevoluteJoint):
        raise ValueError(f"{spec['actuator_joint']} is not a single-axis joint")
    leaf, frame = stage.GetPrimAtPath(spec["leaf"]), stage.GetPrimAtPath(spec["frame"])
    xf = UsdGeom.XformCache(Usd.TimeCode.Default())
    bbox = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.default_, UsdGeom.Tokens.render])
    lo, hi = bbox.ComputeWorldBound(leaf).ComputeAlignedRange().GetMin(), \
        bbox.ComputeWorldBound(leaf).ComputeAlignedRange().GetMax()

    # Hinge geometry in world: pivot, axis, and the leaf's width axis.
    hj = UsdPhysics.RevoluteJoint(hinge)
    hinge_axis = {"X": 0, "Y": 1, "Z": 2}[hj.GetAxisAttr().Get()]
    if hinge_axis != 2:
        raise ValueError("latch v1 supports vertical (Z) hinges")
    body0 = xf.GetLocalToWorldTransform(stage.GetPrimAtPath(hj.GetBody0Rel().GetTargets()[0]))
    pivot = body0.Transform(Gf.Vec3d(hj.GetLocalPos0Attr().Get()))
    # width axis: the horizontal leaf extent that is long (the other is thickness)
    w = 0 if (hi[0] - lo[0]) >= (hi[1] - lo[1]) else 1
    t = 1 - w
    # latch edge: the leaf extreme farthest from the pivot along the width axis
    tip_sign = 1 if abs(hi[w] - pivot[w]) > abs(lo[w] - pivot[w]) else -1
    edge = hi[w] if tip_sign > 0 else lo[w]
    # opening a +angle about +Z moves the latch edge along +(Z x r): its sign on
    # the thickness axis is the swing side
    r = [0.0, 0.0]
    r[w] = edge - pivot[w]
    swing_vec = (-r[1], r[0])  # Z x r in the xy plane
    upper = hj.GetUpperLimitAttr().Get() or 0.0
    lower = hj.GetLowerLimitAttr().Get() or 0.0
    opening = 1 if abs(upper) >= abs(lower) else -1
    swing_sign = 1 if opening * swing_vec[t] > 0 else -1

    z = spec.get("height_z")
    if z is None:
        act_child = UsdPhysics.Joint(actuator).GetBody1Rel().GetTargets()[0]
        ar = bbox.ComputeWorldBound(stage.GetPrimAtPath(act_child)).ComputeAlignedRange()
        z = 0.5 * (ar.GetMin()[2] + ar.GetMax()[2])
    t_mid = 0.5 * (lo[t] + hi[t])
    t_half = 0.3 * (hi[t] - lo[t])
    leaf_swing_face = hi[t] if swing_sign > 0 else lo[t]

    def world(wv, tv, zv):
        v = [0.0, 0.0, zv]
        v[w], v[t] = wv, tv
        return v

    def span(a0, a1, b0, b1, z0, z1):
        p, q = world(a0, b0, z0), world(a1, b1, z1)
        return [min(p[k], q[k]) for k in range(3)], [max(p[k], q[k]) for k in range(3)]

    mech = f"{asset_root}/Mechanisms"
    UsdGeom.Scope.Define(stage, mech)
    to_local = xf.GetLocalToWorldTransform(stage.GetPrimAtPath(asset_root)).GetInverse()
    # Bolt: back inside the leaf, tip BOLT_PROTRUDE_M past the edge.
    b_lo, b_hi = span(edge - tip_sign * (BOLT_LENGTH_M - BOLT_PROTRUDE_M), edge + tip_sign * BOLT_PROTRUDE_M,
                      t_mid - t_half, t_mid + t_half, z - BOLT_HEIGHT_M / 2, z + BOLT_HEIGHT_M / 2)
    bolt_path = f"{mech}/LatchBolt"
    if w == 0:
        # A closing leaf brings the keeper onto the bolt's face opposite the
        # swing side; bevel that face across the whole protrusion so the
        # keeper's corner always lands on the slope, never on a flat.
        bolt = _define_bevelled_bolt(stage, bolt_path, b_lo, b_hi, tip_sign, -swing_sign,
                                     BOLT_PROTRUDE_M, Gf.Vec3f(0.75, 0.75, 0.78), to_local)
    else:
        raise ValueError("latch v1 expects the leaf's width along world X")
    bolt_prim = bolt.GetPrim()
    UsdPhysics.RigidBodyAPI.Apply(bolt_prim)
    UsdPhysics.CollisionAPI.Apply(bolt_prim)
    UsdPhysics.MeshCollisionAPI.Apply(bolt_prim).CreateApproximationAttr().Set("convexHull")
    UsdPhysics.MassAPI.Apply(bolt_prim).CreateMassAttr().Set(BOLT_MASS_KG)
    UsdPhysics.FilteredPairsAPI.Apply(bolt_prim)

    # Keeper: a static strike on the swing side of the bolt, clear of the
    # leaf, plus a web back to the frame so it reads as mounted.
    k_near = leaf_swing_face + swing_sign * KEEPER_GAP_M
    k_far = k_near + swing_sign * KEEPER_DEPTH_M
    fr = bbox.ComputeWorldBound(frame).ComputeAlignedRange()
    frame_outer = fr.GetMax()[w] if tip_sign > 0 else fr.GetMin()[w]
    keeper_path = f"{mech}/LatchKeeper"
    kz0, kz1 = z - 2 * BOLT_HEIGHT_M, z + 2 * BOLT_HEIGHT_M
    k_lo, k_hi = span(edge + tip_sign * KEEPER_GAP_M, frame_outer, k_near, k_far, kz0, kz1)
    keeper = _define_box(stage, keeper_path, k_lo, k_hi, Gf.Vec3f(0.55, 0.55, 0.58), to_local)
    # web: ties the lip back to the jamb's face so the strike is mounted, not
    # floating; it stays outboard of the bolt's tip and the leaf's swing
    frame_face = fr.GetMax()[t] if swing_sign > 0 else fr.GetMin()[t]
    web_inner = edge + tip_sign * (BOLT_PROTRUDE_M + 2 * KEEPER_GAP_M)
    if (frame_face - k_near) * swing_sign < 0:
        w_lo, w_hi = span(web_inner, frame_outer, frame_face, k_near, kz0, kz1)
        lip_pts = list(keeper.GetPointsAttr().Get())
        web = _box_points(w_lo, w_hi, to_local)
        faces = [(0, 2, 3, 1), (4, 5, 7, 6), (0, 1, 5, 4), (2, 6, 7, 3), (0, 4, 6, 2), (1, 3, 7, 5)]
        keeper.CreatePointsAttr(lip_pts + web)
        keeper.CreateFaceVertexCountsAttr([4] * 12)
        keeper.CreateFaceVertexIndicesAttr([i for f in faces for i in f] + [i + 8 for f in faces for i in f])
    UsdPhysics.CollisionAPI.Apply(keeper.GetPrim())  # static: no RigidBodyAPI

    # Prismatic bolt joint along the throw axis; 0 = out, -throw = retracted.
    jpath = f"{joints}/latch_bolt"
    joint = UsdPhysics.PrismaticJoint.Define(stage, jpath)
    joint.CreateBody0Rel().SetTargets([leaf.GetPath()])
    joint.CreateBody1Rel().SetTargets([bolt_prim.GetPath()])
    axis_token = "XYZ"[w]
    joint.CreateAxisAttr().Set(axis_token)
    if tip_sign > 0:
        joint.CreateLowerLimitAttr().Set(-BOLT_THROW_M)
        joint.CreateUpperLimitAttr().Set(0.0)
    else:
        joint.CreateLowerLimitAttr().Set(0.0)
        joint.CreateUpperLimitAttr().Set(BOLT_THROW_M)
    anchor = Gf.Vec3d(*world(edge, t_mid, z))
    _joint_frames(joint, xf.GetLocalToWorldTransform(leaf), xf.GetLocalToWorldTransform(bolt_prim), anchor)

    # Mimic: bolt + gearing * actuator = 0 -> full actuator travel = full throw.
    # signed: a bar pushed toward -axis has negative travel, and the gearing
    # flips with it so a full push always retracts the bolt fully
    a_lo = actuator.GetAttribute("physics:lowerLimit").Get() or 0.0
    a_hi = actuator.GetAttribute("physics:upperLimit").Get() or 0.0
    travel = float(spec.get("actuator_travel") or (a_hi if abs(a_hi) >= abs(a_lo) else a_lo))
    gearing = tip_sign * BOLT_THROW_M / travel
    jp = joint.GetPrim()
    jp.AddAppliedSchema("PhysxMimicJointAPI:rotX")
    jp.CreateAttribute("physxMimicJoint:rotX:gearing", Sdf.ValueTypeNames.Float).Set(gearing)
    jp.CreateAttribute("physxMimicJoint:rotX:offset", Sdf.ValueTypeNames.Float).Set(0.0)
    jp.CreateRelationship("physxMimicJoint:rotX:referenceJoint").SetTargets([actuator.GetPath()])

    # The bolt must meet only the keeper, not the frame's decomposed hulls;
    # and the leaf never needs the keeper (it clears it by KEEPER_GAP_M), but a
    # convex decomposition can bulge past the leaf's true face and start the
    # simulation in penetration, which shoves the leaf past its limit.
    UsdPhysics.FilteredPairsAPI.Apply(frame).CreateFilteredPairsRel().AddTarget(bolt_prim.GetPath())
    UsdPhysics.FilteredPairsAPI.Apply(keeper.GetPrim()).CreateFilteredPairsRel().AddTarget(leaf.GetPath())
    # The actuator drives the bolt through the device, not by touching it; a
    # bar ending millimetres from the bolt would otherwise jam on its contact
    # offset before it moved.
    act_child = UsdPhysics.Joint(actuator).GetBody1Rel().GetTargets()[0]
    UsdPhysics.FilteredPairsAPI(bolt_prim).CreateFilteredPairsRel().AddTarget(act_child)

    gate = {"mechanism": "latch", "actuator_joint": spec["actuator_joint"],
            "engage": travel, "bolt_joint": "latch_bolt"}
    hinge.SetCustomDataByKey("simReady:gate", json.dumps(gate))
    return {"bolt": bolt_path, "keeper": keeper_path, "joint": jpath, "gearing": gearing,
            "edge": edge, "height_z": z, "swing_sign": swing_sign, "gate": gate}


def main() -> int:
    if len(sys.argv) != 4 or sys.argv[2] != "latch":
        print(__doc__)
        return 1
    from pxr import Usd

    from ingest_asset import QUEUE_DIR, run_report

    asset_id, spec = sys.argv[1], json.loads(sys.argv[3])
    qf = QUEUE_DIR / f"{asset_id}.json"
    entry = json.loads(qf.read_text())
    stage = Usd.Stage.Open(entry["file"])
    root = stage.GetDefaultPrim().GetChildren()[0].GetPath()
    result = add_latch(stage, str(root), spec)
    stage.GetRootLayer().Save()
    entry.setdefault("applied_fixes", []).append(
        f"latch: {spec['actuator_joint']} gates {spec['hinge_joint']} (bolt + keeper + mimic)")
    entry.setdefault("mechanisms", []).append({"type": "latch", **spec})
    entry["report"] = run_report(entry["file"], entry.get("class_hint"))
    qf.write_text(json.dumps(entry, indent=1))
    print(json.dumps(result, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
