#!/usr/bin/env python3
"""Compose an egocentric robot test scene from a spec (pxr only, no Kit).

A spec (config/scenes/*.json) names an environment, a robot and its posture,
a support surface, and objects from the sim-ready library. This writes one
stage that references them all - nothing is copied, nothing is written into
a source - with:

  /World/Environment   the environment, prims named in the spec switched off
  /World/Robot         the robot at its pose; each posture joint gets its
                       drive target and an initial joint state, so PhysX
                       starts it there
  /World/Objects/<n>   library assets set down on the support (their lowest
                       point 2 mm above its top); the target is tagged
  /World/Cameras       the robot's head camera (posed at render time from
                       the head link) and an overview camera
  /World/PhysicsScene

render_er_scene.py then settles it in PhysX, renders the head camera and
writes ground truth for scoring a model's points and boxes.

    python scripts/build_er_scene.py config/scenes/aida_r5_dining_grasp.json
writes workspace/scenes/<name>/scene.usda
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
REGISTRY = REPO / "workspace" / "knowledge" / "sim_ready_assets.json"


def _path(p: str) -> str:
    return str(Path(p).expanduser())


def support_edge(points, axis: str, side: str, across_centre: float, across_half: float, top: float, slab_depth: float = 0.015):
    """Where a support's top surface ends: the extreme coordinate along `axis`
    ('x'|'y') on `side` ('-'|'+') among its points within slab_depth of the top
    and within across_half of across_centre on the other axis. The dining
    table's bounding box starts at y 0.523 (its leg panels) while its slab edge
    at the towel's x is y 0.549: a towel placed "10 cm past 0.523" hung 13 cm."""
    import numpy as np
    P = np.asarray(points, float)
    i = 0 if axis == "x" else 1
    j = 1 - i
    sel = P[(P[:, 2] >= top - slab_depth) & (np.abs(P[:, j] - across_centre) <= across_half)]
    if not len(sel):
        raise ValueError(f"no support points within {across_half} m of {across_centre} on the slab")
    return float(sel[:, i].min() if side == "-" else sel[:, i].max())


def mesh_volume_centroid(points, counts, indices):
    """A closed polygon mesh's volume and centroid (polygons fanned into
    triangles; signed tetrahedra from the points' mean, so a mesh far from
    the origin keeps its precision). Negative volume: the winding is inward."""
    import numpy as np
    P = np.asarray(points, float)
    o = P.mean(0)
    Q = P - o
    tris = []
    k = 0
    for c in counts:
        poly = indices[k:k + c]
        k += c
        tris += [(poly[0], poly[j], poly[j + 1]) for j in range(1, c - 1)]
    T = Q[np.asarray(tris, int)]
    v6 = np.einsum("ij,ij->i", T[:, 0], np.cross(T[:, 1], T[:, 2]))
    vol = float(v6.sum() / 6.0)
    if abs(vol) < 1e-15:
        return 0.0, o
    return vol, o + (v6[:, None] * T.sum(1)).sum(0) / (4.0 * v6.sum())


def mass_from_parts(parts):
    """A body's mass and centre of mass from its parts [(volume m^3, centroid,
    density kg/m^3)]: the parts' masses summed, their centroids weighted."""
    import numpy as np
    m = sum(abs(v) * d for v, _, d in parts)
    if m <= 0:
        raise ValueError("no mass in the parts")
    return m, sum(abs(v) * d * np.asarray(c, float) for v, c, d in parts) / m


def com_over_edge(com_xy, side: str, axis: str, edge: float, margin: float) -> float:
    """How far a centre of mass lies inside a support's edge (m; negative:
    past it, the object tips off): an object hung past the -x edge has its
    support at x > edge."""
    c = float(com_xy[0] if axis == "x" else com_xy[1])
    return (c - edge if side == "-" else edge - c) - margin


def centre_by_overhang(edge: float, side: str, extent: float, overhang: float) -> float:
    """The centre coordinate that puts an object's far side `overhang` past a
    support edge on `side`: centre = edge -/+ overhang +/- extent/2."""
    return edge - overhang + extent / 2 if side == "-" else edge + overhang - extent / 2


def _filter_overlapping_unjointed(stage, obj):
    """An articulated asset's bodies that overlap at rest and share no joint
    are filtered: PhysX disables collisions only between jointed (adjacent)
    links, and a pivot pin fixed to one half of a scissors passes through
    the other half, where the convex decomposition is solid - a standing
    interpenetration that threw the scissors across the room in the first
    quarter second (2026-10-15). Returns the filtered (prim, prim) pairs."""
    from pxr import Sdf, Usd, UsdGeom, UsdPhysics
    bodies = [q for q in Usd.PrimRange(obj) if q.HasAPI(UsdPhysics.RigidBodyAPI)]
    if len(bodies) < 2:
        return []
    jointed = set()
    for j in Usd.PrimRange(obj):
        if j.IsA(UsdPhysics.Joint):
            b0 = [t.pathString for t in j.GetRelationship("physics:body0").GetTargets()]
            b1 = [t.pathString for t in j.GetRelationship("physics:body1").GetTargets()]
            for a in b0:
                for b in b1:
                    jointed.add((a, b)); jointed.add((b, a))
    bc = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.default_, UsdGeom.Tokens.render])
    boxes = {q.GetPath().pathString: bc.ComputeWorldBound(q).ComputeAlignedRange() for q in bodies}
    pairs = []
    for i, a in enumerate(bodies):
        for b in bodies[i + 1:]:
            pa, pb = a.GetPath().pathString, b.GetPath().pathString
            if (pa, pb) in jointed:
                continue
            ra, rb = boxes[pa], boxes[pb]
            if all(ra.GetMin()[k] < rb.GetMax()[k] and rb.GetMin()[k] < ra.GetMax()[k] for k in range(3)):
                over = stage.OverridePrim(pa)
                over.AddAppliedSchema("PhysicsFilteredPairsAPI")
                rel = over.GetRelationship("physics:filteredPairs") or over.CreateRelationship("physics:filteredPairs")
                rel.AddTarget(Sdf.Path(pb))
                pairs.append((pa, pb))
    return pairs


def _deformable_physics(stage, prim, physics_hz: int):
    """A cloth's speed cap follows its contact offset at this scene's rate: a
    vertex must not cross its own contact offset in one step (PhysX's
    SurfaceDeformableDemo sets maxLinearVelocity = contactOffset * steps per
    second). The offset is the asset's (make_towel.py); the rate is the scene's."""
    from pxr import Sdf, Usd
    sim = next((q for q in Usd.PrimRange(prim) if q.GetName() == "simMesh"), None)
    body = next((q for q in Usd.PrimRange(prim) if "PhysxSurfaceDeformableBodyAPI" in q.GetAppliedSchemas()), None)
    if sim is None or body is None:
        return
    contact = sim.GetAttribute("physxCollision:contactOffset").Get()
    if contact is None or contact <= 0:
        raise SystemExit(f"{prim.GetName()}: its simMesh carries no physxCollision:contactOffset - a surface deformable rests at "
                         f"PhysX's default 2 cm from every rigid (make_towel.py sets it from the fabric's thickness)")
    stage.OverridePrim(body.GetPath()).CreateAttribute("physxDeformableBody:maxLinearVelocity",
                                                       Sdf.ValueTypeNames.Float).Set(float(contact) * physics_hz)


def _object_physics(stage, prim, ph: dict, material_path: str) -> None:
    """Friction (a physics material bound with the physics purpose), mass,
    contact offsets and solver iterations on an object, in this scene's layer."""
    from pxr import Sdf, Usd, UsdGeom, UsdPhysics, UsdShade, Vt

    if "static_friction" in ph:
        mat = UsdShade.Material.Get(stage, material_path) or UsdShade.Material.Define(stage, material_path)
        api = UsdPhysics.MaterialAPI.Apply(mat.GetPrim())
        api.CreateStaticFrictionAttr(float(ph["static_friction"]))
        api.CreateDynamicFrictionAttr(float(ph.get("dynamic_friction", ph["static_friction"])))
        api.CreateRestitutionAttr(float(ph.get("restitution", 0.0)))
        targets = [prim] if ph.get("material_only") else [q for q in Usd.PrimRange(prim) if q.HasAPI(UsdPhysics.CollisionAPI)]
        for q in targets:
            UsdShade.MaterialBindingAPI.Apply(q).Bind(mat, bindingStrength=UsdShade.Tokens.strongerThanDescendants,
                                                       materialPurpose="physics")
    if ph.get("material_only"):
        return
    for body_name, col in (ph.get("collision_overrides") or {}).items():
        # a body's collider made exact: a convex decomposition fills concavities,
        # and a drill body's 16 hulls swallowed 25 of its trigger's 46 vertices -
        # the finger pressing the trigger met the body's hull first (cooked and
        # measured, probe_decomp, 2026-10-15). An SDF follows the mesh itself
        body = next((q for q in Usd.PrimRange(prim) if q.GetName() == body_name), None)
        if body is None:
            raise SystemExit(f"{prim.GetName()}: collision_overrides names no body {body_name!r}")
        for q in Usd.PrimRange(body):
            if q != body and q.HasAPI(UsdPhysics.RigidBodyAPI):
                continue
            if q.HasAPI(UsdPhysics.CollisionAPI) and q.IsA(UsdGeom.Mesh):
                UsdPhysics.MeshCollisionAPI.Apply(q).CreateApproximationAttr().Set(col.get("approximation", "sdf"))
                if col.get("approximation", "sdf") == "sdf":
                    q.AddAppliedSchema("PhysxSDFMeshCollisionAPI")
                    q.CreateAttribute("physxSDFMeshCollision:sdfResolution", Sdf.ValueTypeNames.UInt).Set(int(col.get("sdf_resolution", 256)))
    if "mass_kg" in ph:
        body = next((q for q in Usd.PrimRange(prim) if q.HasAPI(UsdPhysics.RigidBodyAPI)), prim)
        UsdPhysics.MassAPI.Apply(body).CreateMassAttr(float(ph["mass_kg"]))
    for q in Usd.PrimRange(prim):
        if q.HasAPI(UsdPhysics.CollisionAPI) and ("contact_offset" in ph or "rest_offset" in ph):
            q.AddAppliedSchema("PhysxCollisionAPI")
            if "contact_offset" in ph:
                q.CreateAttribute("physxCollision:contactOffset", Sdf.ValueTypeNames.Float).Set(float(ph["contact_offset"]))
            if "rest_offset" in ph:
                q.CreateAttribute("physxCollision:restOffset", Sdf.ValueTypeNames.Float).Set(float(ph["rest_offset"]))
        if q.HasAPI(UsdPhysics.RigidBodyAPI) and ("position_iterations" in ph or "max_depenetration" in ph):
            q.AddAppliedSchema("PhysxRigidBodyAPI")
            if "position_iterations" in ph:
                q.CreateAttribute("physxRigidBody:solverPositionIterationCount", Sdf.ValueTypeNames.Int).Set(int(ph["position_iterations"]))
            if "max_depenetration" in ph:
                q.CreateAttribute("physxRigidBody:maxDepenetrationVelocity", Sdf.ValueTypeNames.Float).Set(float(ph["max_depenetration"]))


def _ability_hand(stage, ah: dict, joints: dict, rroot) -> dict:
    """The PSYONIC Ability Hand's mechanics that live in the scene: each
    finger's four-bar as a mimic joint (q2 = c q1, so a load on the distal
    link reaches the motor, as through the linkage), and its silicone pads as
    compliant contact on the digit links (what a halted digit's squeeze is
    stored in while the worm holds it). The motors, worms and firmware are
    run by replay_grasp.py (ability_hand_model.py)."""
    import re as _re

    from pxr import Sdf, Usd, UsdPhysics, UsdShade

    side = ah.get("side", "left")
    c = float(ah.get("coupling", 1.05851325))                  # official URDF mimic multiplier
    out = {"mimic": [], "pads": []}

    def scene_path(src):
        return Sdf.Path("/World/Robot").AppendPath(src.GetPath().MakeRelativePath(rroot))

    if ah.get("mimic", True):
        for d in ("index", "middle", "ring", "pinky"):
            j1, j2 = joints[f"psyonic_{side}_{d}_q1"], joints[f"psyonic_{side}_{d}_q2"]
            p2 = stage.OverridePrim(scene_path(j2))
            # follower + gearing * leader = 0; revolute to revolute, so the
            # ratio is the same in degrees (the instance name is ignored for
            # single-axis joints, omni.physx.demos MimicJointDemo)
            p2.AddAppliedSchema("PhysxMimicJointAPI:rotX")
            p2.CreateRelationship("physxMimicJoint:rotX:referenceJoint").SetTargets([scene_path(j1)])
            p2.CreateAttribute("physxMimicJoint:rotX:gearing", Sdf.ValueTypeNames.Float).Set(-c)
            p2.CreateAttribute("physxMimicJoint:rotX:offset", Sdf.ValueTypeNames.Float).Set(0.0)
            drive = UsdPhysics.DriveAPI.Apply(p2, "angular")     # the linkage drives it, not a servo
            drive.CreateStiffnessAttr(0.0)
            drive.CreateDampingAttr(0.0)
            out["mimic"].append(f"{j2.GetName()} = {c} x {j1.GetName()}")
    pads = ah.get("pads")
    if pads:
        mat = UsdShade.Material.Define(stage, "/World/PhysicsMaterials/AbilityHandPad")
        api = UsdPhysics.MaterialAPI.Apply(mat.GetPrim())
        api.CreateStaticFrictionAttr(float(pads["static_friction"]))
        api.CreateDynamicFrictionAttr(float(pads["dynamic_friction"]))
        api.CreateRestitutionAttr(0.0)
        mp = mat.GetPrim()
        mp.AddAppliedSchema("PhysxMaterialAPI")
        mp.CreateAttribute("physxMaterial:compliantContactStiffness", Sdf.ValueTypeNames.Float).Set(
            float(pads["compliant_stiffness_n_per_m"]))
        mp.CreateAttribute("physxMaterial:compliantContactDamping", Sdf.ValueTypeNames.Float).Set(
            float(pads["compliant_damping_n_s_per_m"]))
        links = [q for q in stage.Traverse() if q.HasAPI(UsdPhysics.RigidBodyAPI)
                 and _re.fullmatch(pads["links"], q.GetName()) and str(q.GetPath()).startswith("/World/Robot/")]
        for link in links:
            for q in [x for x in Usd.PrimRange(link) if x.HasAPI(UsdPhysics.CollisionAPI)]:
                UsdShade.MaterialBindingAPI.Apply(q).Bind(mat, bindingStrength=UsdShade.Tokens.strongerThanDescendants,
                                                           materialPurpose="physics")
                out["pads"].append(str(q.GetPath()))
    return out


def _chess_meta(stage_or_prim_root) -> dict:
    """The board's annotated grid (chess_board.annotate), in the asset's frame."""
    from pxr import Usd
    for q in Usd.PrimRange(stage_or_prim_root):
        if q.HasCustomDataKey("simReady:chessboard"):
            return {"prim": q, **json.loads(q.GetCustomDataByKey("simReady:chessboard"))}
    raise SystemExit("the chess object has no annotated board (scripts/chess_board.py annotate)")


def _chess(stage, obj, chess: dict, label: str | None) -> dict:
    """A chess set's target piece and destination square, in the scene.

    The board's grid is annotated in the asset's frame (a1 centre, file and
    rank directions, square size, surface height). The target piece is the
    member annotated with the given colour and start square; the destination
    square's centre and corners are stored in the board body's own frame
    (/World customData er:destination), so they follow the board if it moves
    while settling. Checked: the target sits on its square here.
    """
    from pxr import Gf, Usd, UsdGeom, Vt

    meta = _chess_meta(obj)
    board = meta["prim"]
    xc = UsdGeom.XformCache()
    M_obj = xc.GetLocalToWorldTransform(obj)              # the asset's frame -> world (its root is identity)
    M_board_inv = xc.GetLocalToWorldTransform(board).GetInverse()
    sq = float(meta["square_m"])
    fd, rd = meta["file_dir"], meta["rank_dir"]

    def square_world(name, du=0.0, dv=0.0):
        f, r = "abcdefgh".index(name[0]) + du, int(name[1]) - 1 + dv
        x = meta["a1_center"][0] + (f * fd[0] + r * rd[0]) * sq
        y = meta["a1_center"][1] + (f * fd[1] + r * rd[1]) * sq
        return M_obj.Transform(Gf.Vec3d(x, y, float(meta["surface_z"])))

    out = {"square_m": sq}
    want = chess["target"]
    piece = None
    for q in Usd.PrimRange(obj):
        if q.HasCustomDataKey("simReady:chess"):
            info = json.loads(q.GetCustomDataByKey("simReady:chess"))
            if info["color"] == want["color"] and info["start_square"] == want["square"] \
                    and (not want.get("piece") or info["piece"] == want["piece"]):
                piece = q
                break
    if piece is None:
        raise SystemExit(f"no {want} in the chess set")
    piece.SetCustomDataByKey("er:target", True)
    piece.SetCustomDataByKey("er:label", label or f"the {want['color']} piece on {want['square']}")
    piece.SetCustomDataByKey("er:asset_id", obj.GetCustomDataByKey("er:asset_id"))
    bc = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.default_, UsdGeom.Tokens.render])
    pc = bc.ComputeWorldBound(piece).ComputeAlignedRange().GetMidpoint()
    home = square_world(want["square"])
    off = float(Gf.Vec2d(pc[0] - home[0], pc[1] - home[1]).GetLength())
    if off > 0.4 * sq:
        raise SystemExit(f"the target is {off:.3f} m from {want['square']}: the board's grid and the pieces disagree")
    out["target"] = {"path": str(piece.GetPath()), "square": want["square"], "off_square_m": round(off, 4)}
    dest = chess.get("destination")
    if dest:
        corners = [square_world(dest["square"], du, dv) for du, dv in ((-.5, -.5), (.5, -.5), (.5, .5), (-.5, .5))]
        centre = square_world(dest["square"])
        d = {"kind": "chess_square", "square": dest["square"], "label": dest.get("label", f"square {dest['square']}"),
             "board": str(board.GetPath()), "square_m": sq,
             "centre_board": list(M_board_inv.Transform(centre)),
             "corners_board": [list(M_board_inv.Transform(c)) for c in corners],
             "centre_world_at_build": [round(v, 4) for v in centre]}
        stage.GetPrimAtPath("/World").SetCustomDataByKey("er:destination", json.dumps(d))
        out["destination"] = d
    out["instruction"] = chess.get("instruction")
    if chess.get("instruction"):
        stage.GetPrimAtPath("/World").SetCustomDataByKey("er:instruction", chess["instruction"])
    return out


def robot_overlaps(stage, robot_root="/World/Robot", others=("/World/Environment", "/World/Fixtures"), min_depth=0.01):
    """The robot's link colliders whose world boxes overlap a collider of the
    environment by more than min_depth on every axis, at the robot's authored
    pose: a robot built inside a chair blew up in PhysX's first step (every
    link non-finite, 2026-10-15) - the planner's checker knows only the
    support, so nothing else caught it. Returns [(robot collider, other, depth)]."""
    from pxr import Usd, UsdGeom, UsdPhysics

    bc = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.default_, UsdGeom.Tokens.render])

    def colliders(root):
        prim = stage.GetPrimAtPath(root)
        if not prim:
            return []
        out = []
        for q in Usd.PrimRange(prim):
            if not q.IsActive() or not q.HasAPI(UsdPhysics.CollisionAPI):
                continue
            en = q.GetAttribute("physics:collisionEnabled")
            if en and en.Get() is False:
                continue
            r = bc.ComputeWorldBound(q).ComputeAlignedRange()
            if not r.IsEmpty():
                out.append((q.GetPath().pathString, r))
        return out

    mine = colliders(robot_root)
    if not mine:
        return []
    lo = [min(r.GetMin()[k] for _, r in mine) for k in range(3)]
    hi = [max(r.GetMax()[k] for _, r in mine) for k in range(3)]
    near = [(pth, r) for root in others for pth, r in colliders(root)
            if all(r.GetMax()[k] > lo[k] and r.GetMin()[k] < hi[k] for k in range(3))]
    hits = []
    for a, ra in mine:
        for b, rb in near:
            depth = min(min(ra.GetMax()[k], rb.GetMax()[k]) - max(ra.GetMin()[k], rb.GetMin()[k]) for k in range(3))
            if depth > min_depth:
                hits.append((a, b, round(float(depth), 4)))
    return hits


def build(spec: dict, out_dir: Path) -> Path:
    from pxr import Gf, Sdf, Usd, UsdGeom, UsdPhysics, Vt

    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "scene.usda"
    layer = Sdf.Layer.FindOrOpen(str(out))
    if layer:
        layer.Clear()
        stage = Usd.Stage.Open(layer)
    else:
        stage = Usd.Stage.CreateNew(str(out))
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    stage.SetTimeCodesPerSecond(60)
    world = UsdGeom.Xform.Define(stage, "/World")
    stage.SetDefaultPrim(world.GetPrim())

    # --- environment ----------------------------------------------------------
    env = spec["environment"]
    env_prim = stage.DefinePrim("/World/Environment", "Xform")
    env_prim.GetReferences().AddReference(_path(env["usd"]), env.get("prim", ""))
    src_root = env.get("prim", "")

    def in_env(p: str) -> str:
        return "/World/Environment" + p[len(src_root):] if src_root and p.startswith(src_root) else p

    for p in env.get("deactivate", []):
        stage.OverridePrim(in_env(p)).SetActive(False)
    for p, attrs in (env.get("attribute_overrides") or {}).items():
        over = stage.GetPrimAtPath(in_env(p))
        for name, value in attrs.items():
            a = over.GetAttribute(name)
            if not a:
                raise SystemExit(f"{p} has no attribute {name}")
            a.Set(value)                                        # written in this scene's layer, not the source
    # --- fixtures: static boxes of the scene's own (a drying rail on a wall) ----
    for fx in env.get("fixtures") or []:
        if fx.get("shape") == "cylinder":
            # a round peg or rail: axis X|Y|Z, radius, length (an exact
            # cylinder collider, no decomposition to bulge into a loop's hole)
            cube = UsdGeom.Cylinder.Define(stage, f"/World/Fixtures/{fx['name']}")
            cube.CreateAxisAttr(fx.get("axis", "X"))
            cube.CreateRadiusAttr(float(fx["radius"]))
            cube.CreateHeightAttr(float(fx["length"]))
            cube.AddTranslateOp().Set(Gf.Vec3d(*fx["position"]))
        else:
            cube = UsdGeom.Cube.Define(stage, f"/World/Fixtures/{fx['name']}")
            cube.CreateSizeAttr(1.0)
            cube.AddTranslateOp().Set(Gf.Vec3d(*fx["position"]))
            cube.AddScaleOp().Set(Gf.Vec3f(*fx["size"]))
        if fx.get("color"):
            cube.CreateDisplayColorAttr(Vt.Vec3fArray([Gf.Vec3f(*fx["color"])]))
        UsdPhysics.CollisionAPI.Apply(cube.GetPrim())
        if fx.get("physics"):
            _object_physics(stage, cube.GetPrim(), {**fx["physics"], "material_only": True}, f"/World/PhysicsMaterials/{fx['name']}")
    bc = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.default_, UsdGeom.Tokens.render])
    support = stage.GetPrimAtPath(in_env(env["support"]))
    if not support:
        raise SystemExit(f"support {env['support']} not found in {env['usd']}")
    srng = bc.ComputeWorldBound(support).ComputeAlignedRange()
    top = float(srng.GetMax()[2])

    # --- physics scene --------------------------------------------------------
    scene = UsdPhysics.Scene.Define(stage, "/World/PhysicsScene")
    scene.CreateGravityDirectionAttr(Gf.Vec3f(0, 0, -1))
    scene.CreateGravityMagnitudeAttr(9.81)
    # PhysX's own schemas by name: a bare OpenUSD build does not register them
    scene.GetPrim().AddAppliedSchema("PhysxSceneAPI")
    if any(o.get("deformable") for o in spec["objects"]):
        # cloth is GPU-only in PhysX (verify_mixed_body.py): GPU dynamics and
        # broadphase on the one scene, with room for the cloth's contacts
        scene.GetPrim().AddAppliedSchema("PhysxSceneAPI")
        scene.GetPrim().CreateAttribute("physxScene:enableGPUDynamics", Sdf.ValueTypeNames.Bool).Set(True)
        scene.GetPrim().CreateAttribute("physxScene:broadphaseType", Sdf.ValueTypeNames.Token).Set("GPU")
        scene.GetPrim().CreateAttribute("physxScene:gpuMaxDeformableSurfaceContacts", Sdf.ValueTypeNames.UInt).Set(1 << 21)
        scene.GetPrim().CreateAttribute("physxScene:gpuCollisionStackSize", Sdf.ValueTypeNames.UInt).Set(1 << 27)
        scene.GetPrim().CreateAttribute("physxScene:gpuFoundLostAggregatePairsCapacity", Sdf.ValueTypeNames.UInt).Set(1 << 16)
        scene.GetPrim().CreateAttribute("physxScene:gpuFoundLostPairsCapacity", Sdf.ValueTypeNames.UInt).Set(1 << 20)
    else:
        # rigid scenes on the CPU pipeline: with GPU dynamics (Isaac's default)
        # the robot's hundred-odd shapes against a convex-decomposed scissors
        # overflowed the found/lost aggregate pairs (5231 > 1024) and the GPU
        # narrowphase faulted (CUDA 700), leaving every pose invalid (2026-10-15)
        scene.GetPrim().CreateAttribute("physxScene:enableGPUDynamics", Sdf.ValueTypeNames.Bool).Set(False)
        scene.GetPrim().CreateAttribute("physxScene:broadphaseType", Sdf.ValueTypeNames.Token).Set("MBP")
    scene.GetPrim().CreateAttribute("physxScene:timeStepsPerSecond", Sdf.ValueTypeNames.UInt).Set(
        int(spec.get("physics_hz", 120)))

    # --- robot ----------------------------------------------------------------
    rb = spec["robot"]
    robot = UsdGeom.Xform.Define(stage, "/World/Robot")
    robot.GetPrim().GetReferences().AddReference(_path(rb["usd"]))
    xf = UsdGeom.XformCommonAPI(robot)
    xf.SetTranslate(Gf.Vec3d(*rb["position"]))
    xf.SetRotate(Gf.Vec3f(0, 0, float(rb.get("cad_yaw_deg", 0.0))))
    rstage = Usd.Stage.Open(_path(rb["usd"]))
    rroot = rstage.GetDefaultPrim().GetPath()
    joints = {p.GetName(): p for p in rstage.Traverse() if p.IsA(UsdPhysics.Joint)}
    posed = {}
    for name, value in rb.get("posture_rad_or_m", {}).items():
        src = joints.get(name)
        if src is None:
            raise SystemExit(f"posture joint {name} is not in {rb['usd']}")
        path = Sdf.Path("/World/Robot").AppendPath(src.GetPath().MakeRelativePath(rroot))
        jp = stage.OverridePrim(path)
        prismatic = src.IsA(UsdPhysics.PrismaticJoint)
        kind = "linear" if prismatic else "angular"
        v = float(value) if prismatic else math.degrees(float(value))     # USD: metres, degrees
        lo = src.GetAttribute("physics:lowerLimit").Get()
        hi = src.GetAttribute("physics:upperLimit").Get()
        if lo is not None and hi is not None and not (lo - 1e-6 <= v <= hi + 1e-6):
            raise SystemExit(f"posture {name}={v:.4f} is outside its limits [{lo}, {hi}]")
        UsdPhysics.DriveAPI.Apply(jp, kind).CreateTargetPositionAttr(v)
        jp.AddAppliedSchema(f"PhysxJointStateAPI:{kind}")                  # PhysX starts the joint here
        jp.CreateAttribute(f"state:{kind}:physics:position", Sdf.ValueTypeNames.Float).Set(v)
        jp.CreateAttribute(f"state:{kind}:physics:velocity", Sdf.ValueTypeNames.Float).Set(0.0)
        posed[name] = round(v, 4)

    import re as _re
    for pattern, gains in (rb.get("drive_overrides") or {}).items():
        for name, src in joints.items():
            if not _re.fullmatch(pattern, name):
                continue
            prismatic = src.IsA(UsdPhysics.PrismaticJoint)
            kind = "linear" if prismatic else "angular"
            per = 1.0 if prismatic else math.pi / 180.0          # USD angular gains are per degree
            jp = stage.OverridePrim(Sdf.Path("/World/Robot").AppendPath(src.GetPath().MakeRelativePath(rroot)))
            d = UsdPhysics.DriveAPI.Apply(jp, kind)
            if "stiffness" in gains:
                d.CreateStiffnessAttr(float(gains["stiffness"]) * per)
            if "damping" in gains:
                d.CreateDampingAttr(float(gains["damping"]) * per)
            if "max_force" in gains:
                d.CreateMaxForceAttr(float(gains["max_force"]))
            if "armature" in gains:
                # reflected rotor inertia: a few-gram finger link on a 30 Nm/rad
                # drive rings far above the physics rate without it (the hand
                # thrashed open and shut and threw the mug 1.8 m)
                jp.AddAppliedSchema("PhysxJointAPI")
                jp.CreateAttribute("physxJoint:armature", Sdf.ValueTypeNames.Float).Set(float(gains["armature"]))
    hand_mechanics = _ability_hand(stage, rb["ability_hand"], joints, rroot) if rb.get("ability_hand") else None
    filtered = []
    if rb.get("collision_exceptions", True):
        # the asset's recorded collision-model exceptions (r5_collision_exceptions.py),
        # filtered in PhysX exactly as the planners drop them. On by default: the
        # planners drop them in every scene, so a scene without them disagrees
        # with its own plan (the towel reach jammed the shoulder in the housing
        # band when the flag was a per-spec opt-in). "collision_exceptions": false
        # builds the asset as modelled, for measuring the jam itself.
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from r5_collision_exceptions import apply_to_stage
        filtered = apply_to_stage(stage, "/World/Robot", str(rroot))
    root_link = rb.get("articulation_root", "Links/amber_base_link")
    art = stage.OverridePrim(f"/World/Robot/{root_link}")
    if rb.get("solver_iterations"):
        art.AddAppliedSchema("PhysxArticulationAPI")
        pos_it, vel_it = rb["solver_iterations"]
        art.CreateAttribute("physxArticulation:solverPositionIterationCount", Sdf.ValueTypeNames.Int).Set(int(pos_it))
        art.CreateAttribute("physxArticulation:solverVelocityIterationCount", Sdf.ValueTypeNames.Int).Set(int(vel_it))
    if rb.get("fix_base"):
        # the base held where it spawns: the recorded arm targets assume it
        # (HomeHero: the wheeled base tilts under arm load; fixing it is simpler)
        fj = UsdPhysics.FixedJoint.Define(stage, "/World/BaseLock")
        fj.CreateBody1Rel().SetTargets([Sdf.Path(f"/World/Robot/{root_link}")])
        fj.CreateLocalPos0Attr(Gf.Vec3f(*rb["position"]))
        yaw = math.radians(float(rb.get("cad_yaw_deg", 0.0)))
        fj.CreateLocalRot0Attr(Gf.Quatf(math.cos(yaw / 2), 0, 0, math.sin(yaw / 2)))
        fj.CreateLocalPos1Attr(Gf.Vec3f(0, 0, 0))
        fj.CreateLocalRot1Attr(Gf.Quatf(1, 0, 0, 0))

    # --- support friction ------------------------------------------------------
    if env.get("support_physics"):
        for q in Usd.PrimRange(support):
            if q.HasAPI(UsdPhysics.CollisionAPI):
                _object_physics(stage, q, {**env["support_physics"], "material_only": True},
                                "/World/PhysicsMaterials/Support")
    support_collision = None
    if env.get("support_collision") == "proxy":
        # the support's PhysX collision as the boxes the planners use (slab +
        # leg panels, support_geometry.py), its own collision meshes switched
        # off: the dining table's top is two overlapping meshes with a seam
        # where a towel's flap pulls, and the towel crept off it
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from support_geometry import mesh_samples, support_boxes
        support_collision = support_boxes(mesh_samples(support, top), top)
        UsdGeom.Scope.Define(stage, "/World/SupportCollision")
        for b in support_collision:
            lo, hi = Gf.Vec3d(*b["min"]), Gf.Vec3d(*b["max"])
            cube = UsdGeom.Cube.Define(stage, "/World/SupportCollision/" + b["id"].replace(":", "_"))
            cube.CreateSizeAttr(1.0)
            cube.CreatePurposeAttr(UsdGeom.Tokens.guide)
            cube.AddTranslateOp().Set((lo + hi) / 2)
            cube.AddScaleOp().Set(Gf.Vec3f(*(hi - lo)))
            UsdPhysics.CollisionAPI.Apply(cube.GetPrim())
            if env.get("support_physics"):
                _object_physics(stage, cube.GetPrim(), {**env["support_physics"], "material_only": True}, "/World/PhysicsMaterials/Support")
        for q in Usd.PrimRange(support):
            if q.HasAPI(UsdPhysics.CollisionAPI):
                stage.OverridePrim(q.GetPath()).CreateAttribute("physics:collisionEnabled", Sdf.ValueTypeNames.Bool).Set(False)
        print(f"[support] collision proxy: {len(support_collision)} boxes " + ", ".join(b["id"] for b in support_collision))

    # --- objects --------------------------------------------------------------
    reg = {a["asset_id"]: a for a in json.loads(REGISTRY.read_text())["assets"]}
    placed = []
    UsdGeom.Xform.Define(stage, "/World/Objects")
    for o in spec["objects"]:
        a = reg.get(o["asset_id"])
        if o.get("usd"):                       # an asset from elsewhere (HomeHero's audited grasp object)
            a = {"file": _path(o["usd"]), "category": o.get("category", "external")}
        if a is None:
            raise SystemExit(f"{o['asset_id']} is not in the sim-ready registry")
        ostage = Usd.Stage.Open(a["file"])
        odef = ostage.GetDefaultPrim()
        orng = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.default_, UsdGeom.Tokens.render]) \
            .ComputeWorldBound(odef).ComputeAlignedRange()
        mid = orng.GetMidpoint()
        prim = UsdGeom.Xform.Define(stage, f"/World/Objects/{o['name']}")
        prim.GetPrim().GetReferences().AddReference(a["file"])
        x, y = o["xy"]
        yaw_deg = o.get("yaw_deg", 0.0)
        overhang_info = None
        if o.get("overhang"):
            # placed by how far it hangs past the support's edge (measured on the
            # support's own slab at the object's position), not by a coordinate
            ov = o["overhang"]
            side, axis = ov["edge"][0], ov["edge"][1]
            th = math.radians(float(yaw_deg))
            ext = {"x": abs(orng.GetSize()[0] * math.cos(th)) + abs(orng.GetSize()[1] * math.sin(th)),
                   "y": abs(orng.GetSize()[0] * math.sin(th)) + abs(orng.GetSize()[1] * math.cos(th))}
            sys.path.insert(0, str(Path(__file__).resolve().parent))
            from support_geometry import mesh_samples
            across = x if axis == "y" else y
            edge = support_edge(mesh_samples(support, top), axis, side, across, ext["x" if axis == "y" else "y"] / 2, top)
            c = centre_by_overhang(edge, side, ext[axis], float(ov["m"]))
            x, y = (x, c) if axis == "y" else (c, y)
            overhang_info = {"edge": ov["edge"], "m": float(ov["m"]), "edge_coordinate": round(edge, 4), "centre": round(c, 4)}
            print(f"[place] {o['name']}: the support's {ov['edge']} edge at {edge:.4f}, {float(ov['m']) * 100:.1f} cm overhang -> {axis} = {c:.4f}")
        # the asset's footprint centre over (x, y), its lowest point 2 mm above the top
        z = top - float(orng.GetMin()[2]) + 0.002
        if (o.get("chess") or {}).get("white_toward_robot"):
            # turned so the ranks run away from the robot: white on its side
            rd = _chess_meta(odef)["rank_dir"]
            away = math.atan2(y - rb["position"][1], x - rb["position"][0])
            yaw_deg = math.degrees(away - math.atan2(rd[1], rd[0]))
        o["yaw_deg"] = yaw_deg
        yaw = math.radians(float(o.get("yaw_deg", 0.0)))
        cx, cy = mid[0] * math.cos(yaw) - mid[1] * math.sin(yaw), mid[0] * math.sin(yaw) + mid[1] * math.cos(yaw)
        oxf = UsdGeom.XformCommonAPI(prim)
        hang_info = None
        if o.get("hang"):
            # hung on a peg: a point of the asset (its ring's centre, in the
            # asset's frame) put at a world point, the asset turned by rpy
            # (degrees, XYZ); PhysX settles it on the peg from there
            hg = o["hang"]
            rpy = [float(v) for v in hg.get("rpy_deg", [0, 0, 0])]
            R = Gf.Matrix3d(Gf.Rotation(Gf.Vec3d(1, 0, 0), rpy[0]) * Gf.Rotation(Gf.Vec3d(0, 1, 0), rpy[1]) * Gf.Rotation(Gf.Vec3d(0, 0, 1), rpy[2]))
            at = Gf.Vec3d(*hg["at"])
            pos = at - R * Gf.Vec3d(*hg["point"]) if False else at - Gf.Vec3d(*(R.GetTranspose() * Gf.Vec3d(*hg["point"])))
            oxf.SetTranslate(pos)
            oxf.SetRotate(Gf.Vec3f(*rpy))
            x, y, z = pos[0], pos[1], pos[2]
            hang_info = {"at": list(hg["at"]), "point": list(hg["point"]), "rpy_deg": rpy, "translate": [round(v, 4) for v in pos]}
            print(f"[hang] {o['name']}: point {hg['point']} at {hg['at']} rpy {rpy} -> translate {[round(v, 4) for v in pos]}")
        else:
            oxf.SetTranslate(Gf.Vec3d(x - cx, y - cy, z))
            oxf.SetRotate(Gf.Vec3f(0, 0, float(o.get("yaw_deg", 0.0))))
        if o.get("hang"):
            pass                                                   # off the support by design
        elif o.get("overhang"):
            # hung over an edge: the footprint must overlap the support (over a
            # 6 cm rail the towel's centre lies past the rail, 28 cm hanging)
            hx, hy = ext["x"] / 2, ext["y"] / 2
            if not (x + hx > srng.GetMin()[0] and x - hx < srng.GetMax()[0] and y + hy > srng.GetMin()[1] and y - hy < srng.GetMax()[1]):
                raise SystemExit(f"{o['name']} at {[round(x, 4), round(y, 4)]} does not overlap the support")
        elif not (srng.GetMin()[0] < x < srng.GetMax()[0] and srng.GetMin()[1] < y < srng.GetMax()[1]):
            raise SystemExit(f"{o['name']} at {o['xy']} is off the support")
        prim.GetPrim().SetCustomDataByKey("er:asset_id", o["asset_id"])
        prim.GetPrim().SetCustomDataByKey("er:label", o.get("label", o["asset_id"].replace("_", " ")))
        if o.get("target_member"):
            # the pinch's target is one body of the asset (a scissors' free
            # half); the rest of the asset is an obstacle, as a chess set's
            # other pieces are
            member = next((q for q in Usd.PrimRange(prim.GetPrim()) if q.GetName() == o["target_member"]), None)
            if member is None:
                raise SystemExit(f"{o['name']}: no member named {o['target_member']}")
            member.SetCustomDataByKey("er:target", True)
            member.SetCustomDataByKey("er:label", o.get("label", o["asset_id"]))
        elif o.get("target"):
            prim.GetPrim().SetCustomDataByKey("er:target", True)
        chess_info = _chess(stage, prim.GetPrim(), o["chess"], o.get("target_label")) if o.get("chess") else None
        for pname in o.get("deactivate", []):
            # a prim of the asset switched off in this scene: an ArtVIP scissors'
            # pivot screw is fixed to the WORLD (a display fixture) and would hold
            # the scissors in the air where they were authored
            q_ = next((q for q in Usd.PrimRange(prim.GetPrim()) if q.GetName() == pname), None)
            if q_ is None:
                raise SystemExit(f"{o['name']}: deactivate names no prim {pname!r}")
            stage.OverridePrim(q_.GetPath()).SetActive(False)
            print(f"[deactivate] {o['name']}: {pname}")
        for bname, kg in (o.get("body_masses_kg") or {}).items():
            b_ = next((q for q in Usd.PrimRange(prim.GetPrim()) if q.GetName() == bname and q.HasAPI(UsdPhysics.RigidBodyAPI)), None)
            if b_ is None:
                raise SystemExit(f"{o['name']}: body_masses_kg names no body {bname!r}")
            UsdPhysics.MassAPI.Apply(stage.OverridePrim(b_.GetPath())).CreateMassAttr(float(kg))
        if o.get("part_densities_kg_m3"):
            # each body's mass and centre of mass from its parts' materials: a
            # scissors' steel blades and plastic loops at one density put the
            # pair's centre of mass in the loops (the mesh's volume is there), 2 cm
            # past the stand's edge, and it tipped off (2026-10-15)
            import fnmatch as _fn
            dens = o["part_densities_kg_m3"]
            xc_ = UsdGeom.XformCache()
            for b_ in [q for q in Usd.PrimRange(prim.GetPrim()) if q.HasAPI(UsdPhysics.RigidBodyAPI) and q.IsActive()]:
                parts, named = [], []
                Mb_inv = xc_.GetLocalToWorldTransform(b_).GetInverse()
                it = iter(Usd.PrimRange(b_))
                for m_ in it:
                    if m_ != b_ and m_.HasAPI(UsdPhysics.RigidBodyAPI):
                        it.PruneChildren()
                        continue
                    if not m_.IsA(UsdGeom.Mesh):
                        continue
                    chain = []                                          # the mesh, its parents, and the body itself
                    q_ = m_
                    while q_ != b_:
                        chain.append(q_.GetName())
                        q_ = q_.GetParent()
                    chain.append(b_.GetName())
                    d_ = next((float(v) for pat, v in dens.items() if any(_fn.fnmatch(nm, pat) for nm in chain)), None)
                    if d_ is None:
                        raise SystemExit(f"{o['name']}: part_densities_kg_m3 gives no density for {m_.GetPath()} (names {chain})")
                    M = xc_.GetLocalToWorldTransform(m_)
                    mesh = UsdGeom.Mesh(m_)
                    Vw = [list(M.Transform(Gf.Vec3d(*v))) for v in mesh.GetPointsAttr().Get()]
                    vol, cen = mesh_volume_centroid(Vw, list(mesh.GetFaceVertexCountsAttr().Get()), list(mesh.GetFaceVertexIndicesAttr().Get()))
                    if vol <= 0:
                        raise SystemExit(f"{o['name']}: {m_.GetPath()} has no positive volume ({vol:.3e} m^3): not a closed, outward mesh")
                    parts.append((vol, cen, d_))
                    named.append(f"{m_.GetParent().GetName()} {vol * 1e6:.2f} cm3 x {d_:.0f}")
                if not parts:
                    continue
                kg, com_w = mass_from_parts(parts)
                com_l = Mb_inv.Transform(Gf.Vec3d(*[float(v) for v in com_w]))
                ma = UsdPhysics.MassAPI.Apply(stage.OverridePrim(b_.GetPath()))
                ma.CreateMassAttr(float(kg))
                ma.CreateCenterOfMassAttr(Gf.Vec3f(*[float(v) for v in com_l]))
                print(f"[mass] {o['name']}.{b_.GetName()}: {kg * 1000:.1f} g, centre of mass {[round(float(v), 4) for v in com_w]} ({'; '.join(named)})")
        if o.get("overhang") and overhang_info and (o.get("part_densities_kg_m3") or o.get("body_masses_kg")):
            # an object hung past an edge stays only if its centre of mass is
            # over the support: checked here, not found by the settle
            xc_ = UsdGeom.XformCache()
            tot, acc = 0.0, None
            for b_ in [q for q in Usd.PrimRange(prim.GetPrim()) if q.HasAPI(UsdPhysics.RigidBodyAPI) and q.IsActive()]:
                kg = UsdPhysics.MassAPI(b_).GetMassAttr().Get() if b_.HasAPI(UsdPhysics.MassAPI) else None
                cl = UsdPhysics.MassAPI(b_).GetCenterOfMassAttr().Get() if b_.HasAPI(UsdPhysics.MassAPI) else None
                if not kg:
                    continue
                cw = xc_.GetLocalToWorldTransform(b_).Transform(Gf.Vec3d(*cl) if cl is not None and tuple(cl) != (-float("inf"),) * 3 else Gf.Vec3d(0, 0, 0))
                acc = (Gf.Vec3d(0, 0, 0) if acc is None else acc) + cw * float(kg)
                tot += float(kg)
            if tot > 0:
                com = acc / tot
                inside_m = com_over_edge([com[0], com[1]], overhang_info["edge"][0], overhang_info["edge"][1], overhang_info["edge_coordinate"],
                                         float(o["overhang"].get("com_margin_m", 0.01)))
                overhang_info["com_inside_edge_m"] = round(inside_m + float(o["overhang"].get("com_margin_m", 0.01)), 4)
                print(f"[place] {o['name']}: centre of mass {[round(com[0], 4), round(com[1], 4), round(com[2], 4)]}, "
                      f"{overhang_info['com_inside_edge_m'] * 100:.1f} cm inside the {overhang_info['edge']} edge")
                if inside_m < 0:
                    raise SystemExit(f"{o['name']}: its centre of mass is {overhang_info['com_inside_edge_m'] * 100:.1f} cm inside the support's "
                                     f"{overhang_info['edge']} edge, less than the {float(o['overhang'].get('com_margin_m', 0.01)) * 100:.0f} cm margin: "
                                     "it would tip off - hang it less far")
        for jname, gains in (o.get("joint_drives") or {}).items():
            # a joint's drive as this scene needs it: an ArtVIP scissors' pivot
            # carries a display spring (10 per degree toward 20 deg open); a real
            # pivot has none, only its friction
            joint = next((q for q in Usd.PrimRange(prim.GetPrim()) if q.IsA(UsdPhysics.Joint) and q.GetName() == jname), None)
            if joint is None:
                raise SystemExit(f"{o['name']}: joint_drives names no joint {jname!r}")
            kind = "linear" if joint.IsA(UsdPhysics.PrismaticJoint) else "angular"
            jo = stage.OverridePrim(joint.GetPath())
            dr = UsdPhysics.DriveAPI.Apply(jo, kind)
            for key, attr in (("stiffness", dr.CreateStiffnessAttr), ("damping", dr.CreateDampingAttr), ("max_force", dr.CreateMaxForceAttr),
                              ("target", dr.CreateTargetPositionAttr)):
                if key in gains:
                    attr(float(gains[key]))
            if "type" in gains:
                dr.CreateTypeAttr(gains["type"])
            if "joint_friction" in gains:
                jo.AddAppliedSchema("PhysxJointAPI")
                jo.CreateAttribute("physxJoint:jointFriction", Sdf.ValueTypeNames.Float).Set(float(gains["joint_friction"]))
            print(f"[joint] {o['name']}.{jname} drive {({k: v for k, v in gains.items() if k != 'why'})}")
        if o.get("physics") and not o.get("deformable"):
            _object_physics(stage, prim.GetPrim(), o["physics"], f"/World/PhysicsMaterials/{o['name']}")
        for jname, angle in (o.get("joint_state") or {}).items():
            # the asset's joint authored at this state (degrees for a revolute joint):
            # a scissors hung handles-up stays closed, its loops stacked with
            # no free face; hung open, the free half's loop stands clear
            joint = next((q for q in Usd.PrimRange(prim.GetPrim()) if q.IsA(UsdPhysics.Joint) and q.GetName() == jname), None)
            if joint is None:
                raise SystemExit(f"{o['name']}: no joint named {jname}")
            kind = "linear" if joint.IsA(UsdPhysics.PrismaticJoint) else "angular"
            jo = stage.OverridePrim(joint.GetPath())
            jo.AddAppliedSchema(f"PhysicsJointStateAPI:{kind}")
            jo.CreateAttribute(f"state:{kind}:physics:position", Sdf.ValueTypeNames.Float).Set(float(angle))
            jo.CreateAttribute(f"state:{kind}:physics:velocity", Sdf.ValueTypeNames.Float).Set(0.0)
            print(f"[joint] {o['name']}.{jname} authored at {angle} ({kind})")
        filtered_pairs = _filter_overlapping_unjointed(stage, prim.GetPrim())
        if filtered_pairs:
            print(f"[filter] {o['name']}: {len(filtered_pairs)} overlapping unjointed body pairs filtered: "
                  + ", ".join(f"{a.split('/')[-1]}-{b.split('/')[-1]}" for a, b in filtered_pairs))
        if o.get("deformable"):
            _deformable_physics(stage, prim.GetPrim(), int(spec.get("physics_hz", 120)))
        placed.append({"name": o["name"], "asset_id": o["asset_id"], "category": a["category"],
                       "xy": [x, y], "z": round(z, 4), "target": bool(o.get("target")),
                       "yaw_deg": round(float(o.get("yaw_deg", 0.0)), 2), "chess": chess_info, "overhang": overhang_info, "hang": hang_info,
                       "filtered_body_pairs": [[a.split("/")[-1], b.split("/")[-1]] for a, b in filtered_pairs],
                       "size_m": [round(float(s), 4) for s in orng.GetSize()]})

    # --- lights the spec adds (pointing down: UsdLux area lights emit along -Z) --
    if spec.get("lights"):
        from pxr import UsdLux
        UsdGeom.Xform.Define(stage, "/World/Lights")
        for li in spec["lights"]:
            path = f"/World/Lights/{li['name']}"
            if li["type"] == "rect":
                light = UsdLux.RectLight.Define(stage, path)
                light.CreateWidthAttr(float(li["size"][0]))
                light.CreateHeightAttr(float(li["size"][1]))
            elif li["type"] == "disk":
                light = UsdLux.DiskLight.Define(stage, path)
                light.CreateRadiusAttr(float(li.get("radius", 0.2)))
            else:
                light = UsdLux.SphereLight.Define(stage, path)
                light.CreateRadiusAttr(float(li.get("radius", 0.1)))
            light.CreateIntensityAttr(float(li["intensity"]))
            if li.get("color_temperature_k"):
                light.CreateEnableColorTemperatureAttr(True)
                light.CreateColorTemperatureAttr(float(li["color_temperature_k"]))
            UsdGeom.XformCommonAPI(light).SetTranslate(Gf.Vec3d(*li["position"]))

    # --- cameras --------------------------------------------------------------
    hc = spec["head_camera"]
    UsdGeom.Xform.Define(stage, "/World/Cameras")
    head = UsdGeom.Camera.Define(stage, "/World/Cameras/HeadRGB")
    w, h = hc["resolution"]
    head.CreateFocalLengthAttr(float(hc["focal_length_mm"]))
    head.CreateHorizontalApertureAttr(float(hc["horizontal_aperture_mm"]))
    head.CreateVerticalApertureAttr(float(hc["horizontal_aperture_mm"]) * h / w)
    head.CreateClippingRangeAttr(Gf.Vec2f(*hc["clipping_m"]))
    oc = spec["overview_camera"]
    ov = UsdGeom.Camera.Define(stage, "/World/Cameras/Overview")
    ow, oh = oc["resolution"]
    ov.CreateFocalLengthAttr(float(oc["focal_length_mm"]))
    ov.CreateHorizontalApertureAttr(20.955)
    ov.CreateVerticalApertureAttr(20.955 * oh / ow)
    ov.CreateClippingRangeAttr(Gf.Vec2f(0.05, 100.0))
    look = Gf.Matrix4d().SetLookAt(Gf.Vec3d(*oc["eye"]), Gf.Vec3d(*oc["target"]), Gf.Vec3d(0, 0, 1)).GetInverse()
    UsdGeom.Xformable(ov.GetPrim()).AddTransformOp().Set(look)

    for cam in spec.get("extra_cameras", []):
        c = UsdGeom.Camera.Define(stage, f"/World/Cameras/{cam['name']}")
        cw, ch = cam["resolution"]
        c.CreateFocalLengthAttr(float(cam["focal_length_mm"]))
        c.CreateHorizontalApertureAttr(20.955)
        c.CreateVerticalApertureAttr(20.955 * ch / cw)
        c.CreateClippingRangeAttr(Gf.Vec2f(0.02, 100.0))
        UsdGeom.Xformable(c.GetPrim()).AddTransformOp().Set(
            Gf.Matrix4d().SetLookAt(Gf.Vec3d(*cam["eye"]), Gf.Vec3d(*cam["target"]), Gf.Vec3d(0, 0, 1)).GetInverse())

    clash = robot_overlaps(stage)
    if clash:
        worst = {}
        for a, b, dpt in clash:
            worst[b] = max(worst.get(b, 0.0), dpt)
        raise SystemExit("the robot starts inside the environment: " + "; ".join(f"{b} ({100 * v:.1f} cm deep)" for b, v in
                         sorted(worst.items(), key=lambda kv: -kv[1])[:6]) + " - move the robot or list the prop in environment.deactivate")
    world.GetPrim().SetCustomDataByKey("er:spec", json.dumps(spec))
    world.GetPrim().SetCustomDataByKey("er:support_top_m", round(top, 4))
    stage.GetRootLayer().Save()
    (out_dir / "build.json").write_text(json.dumps(
        {"scene": str(out), "support": env["support"], "support_top_m": round(top, 4),
         "support_range": [[round(v, 3) for v in srng.GetMin()], [round(v, 3) for v in srng.GetMax()]],
         "robot_joints_posed": posed, "hand_mechanics": hand_mechanics, "collision_filtered_pairs": filtered,
         "support_collision": support_collision,
         "objects": placed}, indent=1))
    return out


def config_drift(config_spec, built_spec, path=""):
    """The keys whose values differ between a scene's config (load_spec of
    config/scenes/<name>.json) and the spec the scene was built from
    (<scene>/spec.json): every value the config sets must be in the build
    ('why' notes skipped). A live-plan replay rebuilt its plan from a stale
    spec.json and ran the old task twice (scissors, 2026-10-15)."""
    out = []
    if isinstance(config_spec, dict):
        if not isinstance(built_spec, dict):
            return [path or "/"]
        for k, v in config_spec.items():
            if k == "extends" or k.endswith("why"):
                continue
            if k not in built_spec:
                out.append(f"{path}/{k}")
                continue
            out += config_drift(v, built_spec[k], f"{path}/{k}")
    elif isinstance(config_spec, list):
        if not isinstance(built_spec, list) or len(built_spec) != len(config_spec):
            return [path]
        for i, (a, b) in enumerate(zip(config_spec, built_spec)):
            out += config_drift(a, b, f"{path}[{i}]")
    elif isinstance(config_spec, (int, float)) and isinstance(built_spec, (int, float)) and not isinstance(config_spec, bool):
        if abs(float(config_spec) - float(built_spec)) > 1e-9:
            out.append(path)
    elif config_spec != built_spec:
        out.append(path)
    return out


def load_spec(path: str | Path) -> dict:
    """A spec, with "extends" resolved: the base spec (relative to this one)
    with this one's keys laid over it; "robot" and "environment" merge one
    level deep, so a variant can change a posture or add an override and
    keep the rest."""
    path = Path(path)
    spec = json.loads(path.read_text())
    base_name = spec.pop("extends", None)
    if not base_name:
        return spec
    base = load_spec(path.parent / base_name)
    for k, v in spec.items():
        if k in ("robot", "environment") and isinstance(v, dict):
            base[k] = {**base.get(k, {}), **v}
        else:
            base[k] = v
    return base


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("spec")
    ap.add_argument("--out", help="output folder (default workspace/scenes/<name>)")
    args = ap.parse_args()
    spec = load_spec(args.spec)
    out_dir = Path(args.out) if args.out else REPO / "workspace" / "scenes" / spec["name"]
    out = build(spec, out_dir)
    (out_dir / "spec.json").write_text(json.dumps(spec, indent=1))
    print(f"scene: {out}")
    print((out_dir / "build.json").read_text())
    return 0


if __name__ == "__main__":
    sys.exit(main())
