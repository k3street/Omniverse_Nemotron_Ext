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


def _object_physics(stage, prim, ph: dict, material_path: str) -> None:
    """Friction (a physics material bound with the physics purpose), mass,
    contact offsets and solver iterations on an object, in this scene's layer."""
    from pxr import Sdf, Usd, UsdGeom, UsdPhysics, UsdShade

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
    from pxr import Gf, Usd, UsdGeom

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


def build(spec: dict, out_dir: Path) -> Path:
    from pxr import Gf, Sdf, Usd, UsdGeom, UsdPhysics

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
    if rb.get("collision_exceptions"):
        # the asset's recorded collision-model exceptions (r5_collision_exceptions.py),
        # filtered in PhysX exactly as the planners drop them
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from r5_collision_exceptions import physx_filtered_pairs
        for a, b in physx_filtered_pairs("/World/Robot", str(rroot)):
            pa = stage.OverridePrim(a)
            pa.AddAppliedSchema("PhysicsFilteredPairsAPI")
            rel = pa.GetRelationship("physics:filteredPairs") or pa.CreateRelationship("physics:filteredPairs")
            rel.AddTarget(Sdf.Path(b))
            filtered.append([a, b])
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
        # the asset's footprint centre over (x, y), its lowest point 2 mm above the top
        z = top - float(orng.GetMin()[2]) + 0.002
        yaw_deg = o.get("yaw_deg", 0.0)
        if (o.get("chess") or {}).get("white_toward_robot"):
            # turned so the ranks run away from the robot: white on its side
            rd = _chess_meta(odef)["rank_dir"]
            away = math.atan2(y - rb["position"][1], x - rb["position"][0])
            yaw_deg = math.degrees(away - math.atan2(rd[1], rd[0]))
        o["yaw_deg"] = yaw_deg
        yaw = math.radians(float(o.get("yaw_deg", 0.0)))
        cx, cy = mid[0] * math.cos(yaw) - mid[1] * math.sin(yaw), mid[0] * math.sin(yaw) + mid[1] * math.cos(yaw)
        oxf = UsdGeom.XformCommonAPI(prim)
        oxf.SetTranslate(Gf.Vec3d(x - cx, y - cy, z))
        oxf.SetRotate(Gf.Vec3f(0, 0, float(o.get("yaw_deg", 0.0))))
        if not (srng.GetMin()[0] < x < srng.GetMax()[0] and srng.GetMin()[1] < y < srng.GetMax()[1]):
            raise SystemExit(f"{o['name']} at {o['xy']} is off the support")
        prim.GetPrim().SetCustomDataByKey("er:asset_id", o["asset_id"])
        prim.GetPrim().SetCustomDataByKey("er:label", o.get("label", o["asset_id"].replace("_", " ")))
        if o.get("target"):
            prim.GetPrim().SetCustomDataByKey("er:target", True)
        chess_info = _chess(stage, prim.GetPrim(), o["chess"], o.get("target_label")) if o.get("chess") else None
        if o.get("physics"):
            _object_physics(stage, prim.GetPrim(), o["physics"], f"/World/PhysicsMaterials/{o['name']}")
        placed.append({"name": o["name"], "asset_id": o["asset_id"], "category": a["category"],
                       "xy": [x, y], "z": round(z, 4), "target": bool(o.get("target")),
                       "yaw_deg": round(float(o.get("yaw_deg", 0.0)), 2), "chess": chess_info,
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

    world.GetPrim().SetCustomDataByKey("er:spec", json.dumps(spec))
    world.GetPrim().SetCustomDataByKey("er:support_top_m", round(top, 4))
    stage.GetRootLayer().Save()
    (out_dir / "build.json").write_text(json.dumps(
        {"scene": str(out), "support": env["support"], "support_top_m": round(top, 4),
         "support_range": [[round(v, 3) for v in srng.GetMin()], [round(v, 3) for v in srng.GetMax()]],
         "robot_joints_posed": posed, "hand_mechanics": hand_mechanics, "collision_filtered_pairs": filtered,
         "objects": placed}, indent=1))
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
