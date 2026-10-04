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


def add_couple(stage, asset_root: str, spec: dict) -> dict:
    """One joint follows another: follower + gearing * leader + offset = 0.

    A PhysX mimic joint, so the coupling is physical and two-way: pushing
    either moves both. Bi-parting doors (gearing 1, opposite axes), screws
    (a revolute leader, a prismatic follower, gearing = -pitch / 360 m/deg)
    and linked levers are all this.
    """
    from pxr import Sdf

    joints = f"{asset_root}/Joints"
    follower = stage.GetPrimAtPath(f"{joints}/{spec['follower']}")
    leader = stage.GetPrimAtPath(f"{joints}/{spec['leader']}")
    if not follower.IsValid() or not leader.IsValid():
        raise ValueError(f"couple: joints {spec['follower']!r} / {spec['leader']!r} not found under {joints}")
    # the instance name is ignored for single-axis joints (omni.physx.demos MimicJointDemo)
    follower.AddAppliedSchema("PhysxMimicJointAPI:rotX")
    follower.CreateAttribute("physxMimicJoint:rotX:gearing", Sdf.ValueTypeNames.Float).Set(float(spec.get("gearing", 1.0)))
    follower.CreateAttribute("physxMimicJoint:rotX:offset", Sdf.ValueTypeNames.Float).Set(float(spec.get("offset", 0.0)))
    follower.CreateRelationship("physxMimicJoint:rotX:referenceJoint").SetTargets([leader.GetPath()])
    return {"follower": str(follower.GetPath()), "leader": str(leader.GetPath()), "gearing": spec.get("gearing", 1.0)}


HELIX_CARRIER_MASS_FRACTION = 0.05


def add_helix(stage, asset_root: str, spec: dict) -> dict:
    """A thread: the bolt turns about its axis and advances pitch per turn.

    PhysX articulations have no screw joint, so the thread is two joints in
    series through a light carrier body, a prismatic (nut -> carrier) that
    follows a revolute (carrier -> bolt) by a mimic coupling. Any joint the
    articulation already had between nut and bolt is replaced.

    Spec (world coordinates, metres): {"nut": <prim>, "bolt": <prim>,
    "axis": "Z", "anchor": [x, y, z], "pitch_m": 0.0008,
    "turns": [lo, hi]} - turns from the modelled pose; a right-hand thread
    (the default, "hand": "right") advances toward +axis turning positively.
    """
    from pxr import Gf, Sdf, Usd, UsdGeom, UsdPhysics

    joints = f"{asset_root}/Joints"
    nut, bolt = stage.GetPrimAtPath(spec["nut"]), stage.GetPrimAtPath(spec["bolt"])
    if not nut.IsValid() or not bolt.IsValid():
        raise ValueError(f"helix: nut {spec['nut']!r} / bolt {spec['bolt']!r} not found")
    axis = spec.get("axis", "Z")
    pitch = float(spec["pitch_m"])
    lo_turns, hi_turns = (float(t) for t in spec.get("turns", (-2.0, 2.0)))
    hand = 1.0 if spec.get("hand", "right") == "right" else -1.0
    for j in list(stage.GetPrimAtPath(joints).GetChildren()) if stage.GetPrimAtPath(joints) else []:
        bodies = {str(t) for r in ("physics:body0", "physics:body1")
                  for t in (j.GetRelationship(r).GetTargets() if j.GetRelationship(r) else [])}
        if bodies == {spec["nut"], spec["bolt"]}:
            stage.RemovePrim(j.GetPath())

    xf = UsdGeom.XformCache(Usd.TimeCode.Default())
    anchor = Gf.Vec3d(*spec["anchor"])
    mech = f"{asset_root}/Mechanisms"
    UsdGeom.Scope.Define(stage, mech)
    carrier = UsdGeom.Xform.Define(stage, f"{mech}/{bolt.GetName()}_thread")
    to_local = xf.GetLocalToWorldTransform(stage.GetPrimAtPath(asset_root)).GetInverse()
    carrier.AddTranslateOp().Set(to_local.Transform(anchor))
    cp = carrier.GetPrim()
    UsdPhysics.RigidBodyAPI.Apply(cp)
    bolt_mass = UsdPhysics.MassAPI(bolt).GetMassAttr().Get() if bolt.HasAPI(UsdPhysics.MassAPI) else None
    mass = HELIX_CARRIER_MASS_FRACTION * (bolt_mass or 0.01)
    m = UsdPhysics.MassAPI.Apply(cp)
    m.CreateMassAttr().Set(mass)
    # a small solid: it has no geometry, so give it inertia explicitly
    r = 0.005
    m.CreateDiagonalInertiaAttr().Set(Gf.Vec3f(*(0.4 * mass * r * r,) * 3))
    xf.Clear()
    carrier_w = xf.GetLocalToWorldTransform(cp)

    advance = UsdPhysics.PrismaticJoint.Define(stage, f"{joints}/{bolt.GetName()}_advance")
    advance.CreateBody0Rel().SetTargets([nut.GetPath()])
    advance.CreateBody1Rel().SetTargets([cp.GetPath()])
    advance.CreateAxisAttr().Set(axis)
    travel = sorted([hand * pitch * lo_turns, hand * pitch * hi_turns])
    # a hair beyond the turn limits so the revolute's limit is the one that stops it
    advance.CreateLowerLimitAttr().Set(travel[0] - 0.1 * pitch)
    advance.CreateUpperLimitAttr().Set(travel[1] + 0.1 * pitch)
    _joint_frames(advance, xf.GetLocalToWorldTransform(nut), carrier_w, anchor)

    turn = UsdPhysics.RevoluteJoint.Define(stage, f"{joints}/{bolt.GetName()}_turn")
    turn.CreateBody0Rel().SetTargets([cp.GetPath()])
    turn.CreateBody1Rel().SetTargets([bolt.GetPath()])
    turn.CreateAxisAttr().Set(axis)
    turn.CreateLowerLimitAttr().Set(360.0 * lo_turns)
    turn.CreateUpperLimitAttr().Set(360.0 * hi_turns)
    _joint_frames(turn, carrier_w, xf.GetLocalToWorldTransform(bolt), anchor)

    # A real thread is self-locking: friction on the flanks holds a load that
    # a frictionless helix turns into spinning (an M8 bolt hanging in a fixed
    # nut unscrews two turns in a second). PhysX joint friction only slows
    # that, so the turn also gets the thread's running torque as a damper:
    # run_torque_nm at one turn per second.
    friction = float(spec.get("friction", 0.3))
    for jp in (advance.GetPrim(), turn.GetPrim()):
        jp.AddAppliedSchema("PhysxJointAPI")
        jp.CreateAttribute("physxJoint:jointFriction", Sdf.ValueTypeNames.Float).Set(friction)
    run_torque = float(spec.get("run_torque_nm", 0.05))
    drive = UsdPhysics.DriveAPI.Apply(turn.GetPrim(), "angular")
    drive.CreateTypeAttr().Set("force")
    drive.CreateStiffnessAttr().Set(0.0)
    drive.CreateDampingAttr().Set(run_torque / 360.0)  # N m per deg/s
    # advance + gearing * turn = 0, in the joints' USD units (metres, degrees)
    gearing = -hand * pitch / 360.0
    ap = advance.GetPrim()
    ap.AddAppliedSchema("PhysxMimicJointAPI:rotX")
    ap.CreateAttribute("physxMimicJoint:rotX:gearing", Sdf.ValueTypeNames.Float).Set(gearing)
    ap.CreateAttribute("physxMimicJoint:rotX:offset", Sdf.ValueTypeNames.Float).Set(0.0)
    ap.CreateRelationship("physxMimicJoint:rotX:referenceJoint").SetTargets([turn.GetPath()])
    return {"carrier": str(cp.GetPath()), "advance": str(ap.GetPath()), "turn": str(turn.GetPath()),
            "gearing": gearing}


def _carrier(stage, asset_root, name, anchor, mass):
    """A light massless-looking body between two joints in series."""
    from pxr import Gf, Usd, UsdGeom, UsdPhysics

    xf = UsdGeom.XformCache(Usd.TimeCode.Default())
    UsdGeom.Scope.Define(stage, f"{asset_root}/Mechanisms")
    carrier = UsdGeom.Xform.Define(stage, f"{asset_root}/Mechanisms/{name}")
    to_local = xf.GetLocalToWorldTransform(stage.GetPrimAtPath(asset_root)).GetInverse()
    carrier.AddTranslateOp().Set(to_local.Transform(Gf.Vec3d(*anchor)))
    cp = carrier.GetPrim()
    UsdPhysics.RigidBodyAPI.Apply(cp)
    m = UsdPhysics.MassAPI.Apply(cp)
    m.CreateMassAttr().Set(mass)
    m.CreateDiagonalInertiaAttr().Set(Gf.Vec3f(*(0.4 * mass * 0.005 ** 2,) * 3))
    return cp


def add_caster(stage, asset_root: str, spec: dict) -> dict:
    """A swivel caster from a wheel that only spins: a wheelchair's front
    wheel modelled in one piece with its fork cannot steer, and four wheels
    fixed in direction scrub instead of turning. The wheel gets a carrier
    that swivels freely about the vertical through a point a little ahead of
    the wheel (the trail: the wheel follows behind it, so it turns to follow
    the travel) and spins on the carrier as before.

    Spec (world, metres): {"frame": <prim>, "wheel": <prim>, "spin_joint":
    <its joint's name>, "axle": "Y", "centre": [x, y, z], "forward": [fx, fy,
    0], "trail_m": 0.03, "up": "Z", "carrier_kg": 0.5} - the carrier is about
    as heavy as the wheel (a light carrier between two joints fails in PhysX).
    """
    from pxr import Gf, Usd, UsdGeom, UsdPhysics

    joints = f"{asset_root}/Joints"
    frame, wheel = spec["frame"], spec["wheel"]
    _remove_joints_between(stage, joints, frame, wheel)
    # the wheel is a link of its own (drafted as a caster, it was never one of
    # articulate_asset's links): a body that collides, as a convex hull
    wp = stage.GetPrimAtPath(wheel)
    UsdPhysics.RigidBodyAPI.Apply(wp)
    UsdPhysics.CollisionAPI.Apply(wp)
    if wp.IsA(UsdGeom.Mesh):
        mc = UsdPhysics.MeshCollisionAPI.Apply(wp)
        if mc.GetApproximationAttr().Get() in (None, "none", "meshSimplification"):
            mc.CreateApproximationAttr().Set("convexHull")
    c = Gf.Vec3d(*spec["centre"])
    f = Gf.Vec3d(*spec["forward"])
    pivot = c + f * float(spec.get("trail_m", 0.03))
    name = spec.get("spin_joint") or f"{Path(wheel).name}_spin"
    cp = _carrier(stage, asset_root, f"{name}_caster", list(pivot), float(spec.get("carrier_kg", 0.5)))
    xf = UsdGeom.XformCache(Usd.TimeCode.Default())
    carrier_w = xf.GetLocalToWorldTransform(cp)
    swivel = UsdPhysics.RevoluteJoint.Define(stage, f"{joints}/caster_swivel_{name}")
    swivel.CreateBody0Rel().SetTargets([frame])
    swivel.CreateBody1Rel().SetTargets([cp.GetPath()])
    swivel.CreateAxisAttr().Set(spec.get("up", "Z"))
    _joint_frames(swivel, xf.GetLocalToWorldTransform(stage.GetPrimAtPath(frame)), carrier_w, pivot)
    spin = UsdPhysics.RevoluteJoint.Define(stage, f"{joints}/{name}")
    spin.CreateBody0Rel().SetTargets([cp.GetPath()])
    spin.CreateBody1Rel().SetTargets([wheel])
    spin.CreateAxisAttr().Set(spec["axle"])
    _joint_frames(spin, carrier_w, xf.GetLocalToWorldTransform(stage.GetPrimAtPath(wheel)), c)
    for jp, damp in ((swivel.GetPrim(), 0.002), (spin.GetPrim(), 0.0005)):
        d = UsdPhysics.DriveAPI.Apply(jp, "angular")
        d.CreateTypeAttr().Set("force")
        d.CreateStiffnessAttr().Set(0.0)
        d.CreateDampingAttr().Set(damp)
    return {"swivel": str(swivel.GetPath()), "spin": str(spin.GetPath()), "carrier": str(cp.GetPath())}


def _remove_joints_between(stage, joints, a, b):
    scope = stage.GetPrimAtPath(joints)
    for j in list(scope.GetChildren()) if scope else []:
        bodies = {str(t) for r in ("physics:body0", "physics:body1")
                  for t in (j.GetRelationship(r).GetTargets() if j.GetRelationship(r) else [])}
        if bodies == {a, b}:
            stage.RemovePrim(j.GetPath())


def add_two_stop(stage, asset_root: str, spec: dict) -> dict:
    """A plunger with two stops, as a pipette's: a soft spring to the first
    stop (the measured stroke), then a stiffer one to blow-out.

    Two prismatic joints in series through a light carrier: body -> carrier
    (blow-out) and carrier -> plunger (stroke). Each spring is preloaded
    against its rest stop, so the blow-out stage does not move until the
    thumb pushes harder than the whole stroke spring - a felt first stop.

    Spec (world, metres, newtons): {"body": <prim>, "plunger": <prim>,
    "axis": "Z", "press": -1, "anchor": [x, y, z], "first_stop_m": 0.004,
    "blowout_m": 0.0015, "first_force_n": 2.0, "blowout_force_n": 6.0}.
    "press" is the direction (+1/-1 along the axis) the plunger goes in.
    """
    from pxr import Gf, Usd, UsdGeom, UsdPhysics

    joints = f"{asset_root}/Joints"
    body, plunger = spec["body"], spec["plunger"]
    _remove_joints_between(stage, joints, body, plunger)
    axis, press = spec.get("axis", "Z"), float(spec.get("press", -1))
    stroke, blow = float(spec["first_stop_m"]), float(spec["blowout_m"])
    f1, f2 = float(spec["first_force_n"]), float(spec["blowout_force_n"])
    # The carrier weighs what the plunger does. Two prismatics in series on
    # one axis through a much lighter link starve the solver: verified in
    # PhysX, a carrier at 5% of the load lets the body-side stage run
    # through its preload and its stop (4.8 mm at 6 N for a 4 mm stop held
    # by a 12 N preload); matched, both stages land within 0.1 mm.
    mass = UsdPhysics.MassAPI(stage.GetPrimAtPath(plunger)).GetMassAttr().Get() \
        if stage.GetPrimAtPath(plunger).HasAPI(UsdPhysics.MassAPI) else None
    if not mass:
        bound = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.default_]) \
            .ComputeWorldBound(stage.GetPrimAtPath(plunger)).ComputeAlignedRange().GetSize()
        mass = 0.5 * 1100.0 * bound[0] * bound[1] * bound[2]  # half-solid plastic
    cp = _carrier(stage, asset_root, f"{Path(plunger).name}_stop", spec["anchor"], max(float(mass), 0.001))
    xf = UsdGeom.XformCache(Usd.TimeCode.Default())
    anchor = Gf.Vec3d(*spec["anchor"])
    made = {}
    # (name, parent, child, travel, force at full travel, preload force)
    stages = [("blowout", body, str(cp.GetPath()), blow, f2, 1.2 * f1),
              ("stroke", str(cp.GetPath()), plunger, stroke, f1, 0.2 * f1)]
    for name, parent, child, travel, force, preload in stages:
        j = UsdPhysics.PrismaticJoint.Define(stage, f"{joints}/{Path(plunger).name}_{name}")
        j.CreateBody0Rel().SetTargets([parent])
        j.CreateBody1Rel().SetTargets([child])
        j.CreateAxisAttr().Set(axis)
        lo, hi = sorted([0.0, press * travel])
        j.CreateLowerLimitAttr().Set(lo)
        j.CreateUpperLimitAttr().Set(hi)
        _joint_frames(j, xf.GetLocalToWorldTransform(stage.GetPrimAtPath(parent)),
                      xf.GetLocalToWorldTransform(stage.GetPrimAtPath(child)), anchor)
        # the spring passes the preload force at rest: its target sits
        # behind the rest stop, and the stop holds it there
        k = (force - preload) / travel
        drive = UsdPhysics.DriveAPI.Apply(j.GetPrim(), "linear")
        drive.CreateTypeAttr().Set("force")
        drive.CreateStiffnessAttr().Set(k)
        drive.CreateDampingAttr().Set(0.02 * k)
        drive.CreateTargetPositionAttr().Set(-press * preload / k)
        made[name] = str(j.GetPath())
    return {"carrier": str(cp.GetPath()), **made}


# PhysX (omni.physx 110.1, this build) breaks a joint at about 1/31 of its
# authored physics:breakForce: measured by hanging weights from a press-fit
# on a fixed base - authored 15/150/300/450 N broke at 0.4-0.6/3.9-5.9/
# 9.3-9.8/9.8-14.7 N, the same at 60 and 120 Hz and at any body scale. The
# intended force is kept as customData simReady:breakForceN; re-measure
# (tests/ ... hang a weight) when the PhysX version changes.
PHYSX_BREAK_FORCE_SCALE = 31.0


def add_press_fit(stage, asset_root: str, spec: dict) -> dict:
    """A part pushed onto another (a pipette tip on its shaft, a cap on a
    pen): held by a fixed joint that breaks above break_force_n, so a robot
    or an ejector can push it off. The part stays its own body, outside the
    articulation (articulation joints cannot break).

    Spec: {"holder": <prim>, "part": <prim>, "anchor": [x, y, z],
    "break_force_n": 8.0, "mass_kg": 0.001, "released_by": {"joint":
    "tip_ejector", "travel_m": 0.014}}. A pusher on a light part cannot build
    the break force (the joint gives instead), so "released_by" records the
    joint whose travel releases it; tools that drive joints honour it.
    """
    from pxr import Gf, Usd, UsdGeom, UsdPhysics

    joints = f"{asset_root}/Joints"
    holder, part = spec["holder"], spec["part"]
    _remove_joints_between(stage, joints, holder, part)
    pp = stage.GetPrimAtPath(part)
    UsdPhysics.RigidBodyAPI.Apply(pp)
    UsdPhysics.CollisionAPI.Apply(pp)
    if pp.IsA(UsdGeom.Mesh):
        # a dynamic body cannot collide as a triangle mesh
        mc = UsdPhysics.MeshCollisionAPI.Apply(pp)
        if mc.GetApproximationAttr().Get() in (None, "none", "meshSimplification"):
            mc.CreateApproximationAttr().Set(spec.get("approximation", "convexHull"))
    if spec.get("mass_kg"):
        UsdPhysics.MassAPI.Apply(pp).CreateMassAttr().Set(float(spec["mass_kg"]))
    xf = UsdGeom.XformCache(Usd.TimeCode.Default())
    j = UsdPhysics.FixedJoint.Define(stage, f"{joints}/{pp.GetName()}_press_fit")
    j.CreateBody0Rel().SetTargets([holder])
    j.CreateBody1Rel().SetTargets([part])
    _joint_frames(j, xf.GetLocalToWorldTransform(stage.GetPrimAtPath(holder)),
                  xf.GetLocalToWorldTransform(pp), Gf.Vec3d(*spec["anchor"]))
    want = float(spec.get("break_force_n", 8.0))
    j.CreateBreakForceAttr().Set(want * PHYSX_BREAK_FORCE_SCALE)
    j.GetPrim().SetCustomDataByKey("simReady:breakForceN", want)
    if spec.get("released_by"):
        # {"joint": name, "travel_m": x}: a pusher releases it by rule
        j.GetPrim().SetCustomDataByKey("simReady:releasedBy", dict(spec["released_by"]))
    j.CreateExcludeFromArticulationAttr().Set(True)
    return {"joint": str(j.GetPath()), "break_force_n": want}


def add_rule(stage, gated_joint: str, actuator_joint: str, engage: float, action: str = "open") -> dict:
    """A dependency with no physical form, enforced by the tools that drive
    joints: the gated joint stays where it is until the actuator has moved
    `engage` (metres or degrees) from rest - an elevator's door and its
    call button, a drawer and the key that unlocks it. Joints are prim
    paths, so the two may belong to different assets in one scene.

    The rule is customData simReady:gate on the gated joint, the same record
    a latch writes, with mechanism "rule"; animate_asset.py keeps the gated
    joint locked until the actuator engages, then works it ("open": through
    its range and back).
    """
    gated = stage.GetPrimAtPath(gated_joint)
    actuator = stage.GetPrimAtPath(actuator_joint)
    if not gated.IsValid() or not actuator.IsValid():
        raise ValueError(f"rule: joints {gated_joint!r} / {actuator_joint!r} not found")
    gate = {"mechanism": "rule", "actuator_joint": str(actuator.GetPath()), "engage": float(engage), "action": action}
    gated.SetCustomDataByKey("simReady:gate", gate)
    return {"gated": str(gated.GetPath()), **gate}


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
