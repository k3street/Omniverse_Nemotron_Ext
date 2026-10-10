#!/usr/bin/env python3
"""Per-part soft bodies: a mixed asset is a rigid part with soft parts on it.

A shoe is a rubber sole with a knit or leather upper and laces; a boot has a
suede shaft and a fur cuff on a lugged sole; a chair has foam cushions on a
steel frame. The pipeline had two bins - one rigid body, or one cloth - and
put every shoe in the first.

This authors, on the asset's derivative (pxr only: nothing here needs Kit to
WRITE), the deformable schema this Isaac Sim 6 build carries - the OmniPhysics
deformables that PhysX cooks when the stage loads:

  rigid parts   the biggest keeps the asset's rigid body and mass; every other
                rigid mesh becomes a body fixed to it. The one body the asset
                root carried comes off: a deformable may not sit under a
                rigid body.
  soft parts    each soft mesh is copied under its own body Xform
                (<root>/SoftBodies/<name>) that carries
                OmniPhysicsDeformableBodyAPI, PhysxAutoDeformableBodyAPI
                (cooking source: the copy) and PhysxAutoDeformableMesh-
                SimplificationAPI (a light simulation mesh), with a simMesh
                child (OmniPhysicsSurfaceDeformableSimAPI, CollisionAPI,
                guide purpose). The copy is bind-posed
                (OmniPhysicsDeformablePoseAPI) and PhysX skins it to the
                simulation, so the look (UVs, textures) is kept. The original
                mesh is deactivated.
  riders        trims that cannot be cloth on their own - fur tufts of four
                triangles, a bow, a label, laces - ride under the soft part
                they sit on and are skinned with it.
  materials     an OmniPhysics surface deformable material per soft part,
                from its surveyed material (fabric, leather, foam), bound
                with the physics purpose.
  attachments   PhysxAutoDeformableAttachmentAPI between each soft body and
                the rigid part it touches: PhysX attaches the overlapping
                vertices and filters their collisions.

Volume deformables (foam cushions) are approximated as stiff surfaces for
now; that is recorded. Verification is in Kit (scripts/verify_mixed_body.py):
the asset is dropped, the soft parts must deform and stay attached while the
rigid part lands.

    python scripts/soft_body_parts.py <asset_id> [...]
    python scripts/soft_body_parts.py --plan <asset_id>     # what would be done
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))
QUEUE = REPO / "workspace" / "review_queue"

# surveyed material -> surface deformable parameters (stage units are metres
# below the asset root's own scale; stiffnesses follow PhysX's own demo values)
SOFT_PRESETS = {
    "fabric_cotton": {"kind": "cloth", "density": 300.0, "thickness_m": 0.0015, "stretch": 1000.0, "shear": 0.1,
                      "bend": 0.2, "friction": 0.6},
    # a boot's sheepskin shaft stands up on its own: thick, and stiff in bending
    "leather": {"kind": "cloth", "density": 900.0, "thickness_m": 0.004, "stretch": 8000.0, "shear": 2.0,
                "bend": 40.0, "friction": 0.5},
    "paper_kraft": {"kind": "cloth", "density": 700.0, "thickness_m": 0.0005, "stretch": 2000.0, "shear": 0.3,
                    "bend": 0.5, "friction": 0.4},
    "foam_polyurethane": {"kind": "sponge", "density": 60.0, "thickness_m": 0.01, "stretch": 4000.0, "shear": 2.0,
                          "bend": 60.0, "friction": 0.7, "note": "a volume deformable approximated as a stiff surface"},
}
HIDDEN = re.compile(r"\b(hidden|internal|inside|inner (block|core|filler))\b", re.I)
STRUCTURE = re.compile(r"\b(sole|outsole|midsole|frame|chassis|base plate|with moulded|moulded)\b", re.I)
# a shoe's upper, tongue or collar is a padded, structured textile: it bends,
# it does not balloon like a T-shirt (a knit sneaker upper inflated as cotton)
PADDED = re.compile(r"\b(upper|tongue|collar|vamp|shaft|quarter|heel counter|strap|cushion|padded|padding)\b", re.I)
PADDED_TEXTILE = {"kind": "cloth", "density": 400.0, "thickness_m": 0.004, "stretch": 6000.0, "shear": 2.0,
                  "bend": 30.0, "friction": 0.6, "note": "padded textile: stiffer than the plain cloth preset"}
RIDER = re.compile(r"\b(fur|tuft|strand|trim|bow|ribbon|label|patch|lace|stitch|tag|logo|pull tab|eyelet)s?\b", re.I)
MIN_CLOTH_FACES = 200          # fewer: a trim, not a cloth of its own
TARGET_TRIANGLES = 2500        # the simulation mesh PhysX cooks from the copy


def plan(entry: dict, stage=None) -> dict:
    """What the asset's parts are, physically: rigid, soft carriers, riders.
    Raises when the asset is not a mixed body or is already articulated."""
    from processing import soft_parts

    from articulation_draft import collect_parts
    from segment_mesh import mesh_components

    sp = soft_parts(entry)
    if sp["whole"] or not sp["kind"]:
        raise RuntimeError("not a mixed body (whole soft, or rigid)")
    spec = json.loads(entry.get("articulation_draft") or "{}")
    if any(j.get("joint_type") in ("revolute", "prismatic") for j in spec.get("joints", [])):
        raise RuntimeError("articulated with soft parts: not handled yet (soft parts on a moving link)")
    survey = entry.get("part_survey") or {}
    if not survey.get("parts"):
        raise RuntimeError("no part survey: which parts are soft is not known")
    from pxr import Usd, UsdGeom

    close = stage is None
    stage = stage or Usd.Stage.Open(entry["file"])
    root = survey.get("root") or str(next(iter(stage.GetPrimAtPath("/World").GetChildren())).GetPath())
    geo = {g["path"]: g for g in collect_parts(stage, root)}
    rigid, soft, hidden_soft = [], [], []
    for p in survey["parts"]:
        for path in (p.get("copies") or [p["path"]]):
            g = geo.get(path)
            if not g:
                continue
            mesh = UsdGeom.Mesh(stage.GetPrimAtPath(path))
            counts = mesh.GetFaceVertexCountsAttr().Get() or []
            islands = mesh_components(counts, mesh.GetFaceVertexIndicesAttr().Get(), mesh.GetPointsAttr().Get()) \
                if counts else []
            biggest = max((len(c) for c in islands), default=0)
            rec = {"path": path, "role": p.get("role") or "", "material": p.get("material"), "faces": len(counts),
                   "biggest_island": biggest, "min": g["min"], "max": g["max"], "centroid": g["centroid"],
                   "size": g["size"]}
            hidden = p.get("seen") is False or HIDDEN.search(p.get("role") or "")
            if (p.get("material") in SOFT_PRESETS or p.get("motion") == "flex") and not hidden:
                soft.append(rec)
            else:
                # a part nothing can see (a foam footbed modelled inside a boot)
                # stays rigid with the base: as a deformable it starts inside the
                # sole's collider and is thrown out of it
                rec["hidden"] = bool(hidden)
                rigid.append(rec)
                if hidden and p.get("material") in SOFT_PRESETS:
                    hidden_soft.append(p.get("role") or path)
    if not soft:
        raise RuntimeError("no soft part found among the surveyed meshes")
    # a soft part that is also the structure (a leather upper "with moulded
    # sole") is the sole as much as the upper: the soft and the rigid are one
    # mesh, and no cloth can be made of it. A boot's suede shaft is the
    # biggest part and soft all the same: only the role's own words decide.
    biggest = max(rigid + soft, key=lambda r: float(np.prod(r["size"])))
    if biggest in soft and (not rigid or STRUCTURE.search(biggest["role"])):
        raise RuntimeError(f"the soft part '{biggest['role']}' holds the rigid structure too: not separable "
                           "from this file (needs a split by region, not by islands)")
    if not rigid:
        raise RuntimeError("no rigid part: a whole soft body, not a mixed one")

    def gap(a, b):
        d = np.maximum(0.0, np.maximum(np.array(a["min"]) - b["max"], np.array(b["min"]) - a["max"]))
        return float(np.linalg.norm(d))

    carriers = [s for s in soft if s["biggest_island"] >= MIN_CLOTH_FACES and not RIDER.search(s["role"])]
    riders = [s for s in soft if s not in carriers]
    if not carriers:
        raise RuntimeError("the soft parts are all trims (fur, laces, labels): nothing to make a cloth of")
    for c in carriers:
        c["preset"] = SOFT_PRESETS.get(c["material"]) or SOFT_PRESETS["fabric_cotton"]
        if c["material"] in ("fabric_cotton", "paper_kraft") and PADDED.search(c["role"]):
            c["preset"] = PADDED_TEXTILE
        c["attach_to"] = min(rigid, key=lambda r: (gap(c, r), -float(np.prod(r["size"]))))["path"]
        c["riders"] = []
    for r in riders:
        host = min(carriers, key=lambda c: gap(r, c))
        host["riders"].append(r["path"])
    base = max(rigid, key=lambda r: float(np.prod(r["size"])))
    if close:
        del stage
    return {"root": root, "base": base["path"], "rigid": [r["path"] for r in rigid],
            "hidden": [r["path"] for r in rigid if r.get("hidden")], "carriers": carriers,
            "notes": [f"{len(rigid)} rigid part(s), {len(carriers)} soft, {len(riders)} trim(s) riding on them"]
            + [f"{c['role']}: {c['preset'].get('note')}" for c in carriers if c["preset"].get("note")]
            + [f"{r}: hidden inside, kept rigid with the base" for r in hidden_soft]}


def _copy_mesh(stage, src_path: str, dst_path: str, to_root) -> None:
    """A copy of a composed mesh under a new path, its points in the asset
    root's frame (the body Xform above it is identity), its look kept."""
    from pxr import Gf, Sdf, UsdGeom, UsdShade, Vt

    src = UsdGeom.Mesh(stage.GetPrimAtPath(src_path))
    dst = UsdGeom.Mesh.Define(stage, dst_path)
    m = to_root
    pts = src.GetPointsAttr().Get()
    dst.CreatePointsAttr(Vt.Vec3fArray([Gf.Vec3f(m.Transform(Gf.Vec3d(p))) for p in pts]))
    dst.CreateFaceVertexCountsAttr(src.GetFaceVertexCountsAttr().Get())
    dst.CreateFaceVertexIndicesAttr(src.GetFaceVertexIndicesAttr().Get())
    n = src.GetNormalsAttr().Get()
    if n:
        nm = m.ExtractRotationMatrix().GetInverse().GetTranspose()
        dst.CreateNormalsAttr(Vt.Vec3fArray([Gf.Vec3f((Gf.Vec3d(v) * nm).GetNormalized()) for v in n]))
        dst.SetNormalsInterpolation(src.GetNormalsInterpolation())
    if m.ExtractRotationMatrix().GetDeterminant() < 0:
        o = src.GetOrientationAttr().Get() or UsdGeom.Tokens.rightHanded
        dst.CreateOrientationAttr().Set(UsdGeom.Tokens.leftHanded if o == UsdGeom.Tokens.rightHanded
                                        else UsdGeom.Tokens.rightHanded)
    for attr in ("doubleSided", "subdivisionScheme"):
        v = src.GetPrim().GetAttribute(attr).Get()
        if v is not None:
            dst.GetPrim().CreateAttribute(attr, src.GetPrim().GetAttribute(attr).GetTypeName()).Set(v)
    sp, dp = UsdGeom.PrimvarsAPI(src.GetPrim()), UsdGeom.PrimvarsAPI(dst.GetPrim())
    for pv in sp.GetPrimvarsWithValues():
        if pv.GetName() == "primvars:normals":
            continue
        npv = dp.CreatePrimvar(pv.GetPrimvarName(), pv.GetTypeName(), pv.GetInterpolation(), pv.GetElementSize())
        npv.Set(pv.Get())
        if pv.IsIndexed():
            npv.SetIndices(pv.GetIndices())
    mat, _ = UsdShade.MaterialBindingAPI(src.GetPrim()).ComputeBoundMaterial()
    if mat:
        UsdShade.MaterialBindingAPI.Apply(dst.GetPrim()).Bind(mat)
    ext = UsdGeom.PointBased.ComputeExtent(dst.GetPointsAttr().Get())
    if ext:
        dst.CreateExtentAttr().Set(ext)


def _bind_pose(prim) -> None:
    from pxr import Sdf

    prim.AddAppliedSchema("OmniPhysicsDeformablePoseAPI:default")
    prim.CreateAttribute("deformablePose:default:omniphysics:purposes", Sdf.ValueTypeNames.TokenArray).Set(["bindPose"])
    pts = prim.GetAttribute("points").Get()
    if pts is not None:
        prim.CreateAttribute("deformablePose:default:omniphysics:points", Sdf.ValueTypeNames.Point3fArray).Set(pts)


def _soft_material(stage, path: str, preset: dict, mpu: float):
    from pxr import Sdf, UsdShade

    mat = UsdShade.Material.Define(stage, path)
    prim = mat.GetPrim()
    prim.AddAppliedSchema("OmniPhysicsDeformableMaterialAPI")
    prim.AddAppliedSchema("OmniPhysicsSurfaceDeformableMaterialAPI")
    prim.AddAppliedSchema("PhysxSurfaceDeformableMaterialAPI")
    F = Sdf.ValueTypeNames.Float
    prim.CreateAttribute("omniphysics:density", F).Set(float(preset["density"]))
    prim.CreateAttribute("omniphysics:staticFriction", F).Set(float(preset["friction"]))
    prim.CreateAttribute("omniphysics:dynamicFriction", F).Set(float(preset["friction"]) * 0.8)
    prim.CreateAttribute("omniphysics:youngsModulus", F).Set(5.0e5 * mpu)
    prim.CreateAttribute("omniphysics:poissonsRatio", F).Set(0.45)
    prim.CreateAttribute("omniphysics:surfaceThickness", F).Set(float(preset["thickness_m"]) / mpu)
    prim.CreateAttribute("omniphysics:surfaceStretchStiffness", F).Set(float(preset["stretch"]))
    prim.CreateAttribute("omniphysics:surfaceShearStiffness", F).Set(float(preset["shear"]))
    prim.CreateAttribute("omniphysics:surfaceBendStiffness", F).Set(float(preset["bend"]))
    prim.CreateAttribute("physxDeformableMaterial:elasticityDamping", F).Set(0.0)
    prim.CreateAttribute("physxDeformableMaterial:bendDamping", F).Set(0.0)
    return mat


def strip(stage, root_path: str, fallback: dict | None = None) -> int:
    """Undo apply(): the copies, bodies, joints and attachments go, the
    originals come back, the root's single rigid body is restored. Returns
    how many soft bodies were taken off. `fallback` is the entry's own record,
    for a file whose customData was lost (promotion once replaced it)."""
    from pxr import UsdPhysics

    root = stage.GetPrimAtPath(root_path)
    rec = root.GetCustomDataByKey("simReady:softParts") if root else None
    if not rec and fallback and stage.GetPrimAtPath(f"{root_path}/SoftBodies"):
        rec = fallback
    if not rec:
        return 0
    rec = json.loads(rec) if isinstance(rec, str) else dict(rec)
    for sb in rec.get("soft", []):
        for src in [sb["source"], *sb.get("riders", [])]:
            prim = stage.GetPrimAtPath(src)
            if prim:
                prim.ClearActive()
        for path in (sb["body"], sb["body"] + "_attach"):
            if stage.GetPrimAtPath(path):
                stage.RemovePrim(path)
    if stage.GetPrimAtPath(f"{root_path}/SoftBodies"):
        stage.RemovePrim(f"{root_path}/SoftBodies")
    joints = stage.GetPrimAtPath(f"{root_path}/Joints")
    if joints:
        for c in list(joints.GetChildren()):
            if c.GetName().startswith("soft_fixed_"):
                stage.RemovePrim(c.GetPath())
    mass = None
    for path in rec.get("rigid", []):
        prim = stage.GetPrimAtPath(path)
        if not prim:
            continue
        if prim.HasAPI(UsdPhysics.MassAPI):
            mass = mass or UsdPhysics.MassAPI(prim).GetMassAttr().Get()
            prim.RemoveAPI(UsdPhysics.MassAPI)
        if prim.HasAPI(UsdPhysics.RigidBodyAPI):
            prim.RemoveAPI(UsdPhysics.RigidBodyAPI)
        if prim.HasAPI(UsdPhysics.FilteredPairsAPI):
            prim.RemoveAPI(UsdPhysics.FilteredPairsAPI)
            rel = prim.GetRelationship("physics:filteredPairs")
            if rel:
                rel.ClearTargets(True)
        if path in rec.get("hidden", []) and not prim.HasAPI(UsdPhysics.CollisionAPI):
            UsdPhysics.CollisionAPI.Apply(prim)          # its collider, back
    UsdPhysics.RigidBodyAPI.Apply(root)
    if mass:
        UsdPhysics.MassAPI.Apply(root).CreateMassAttr().Set(float(mass))
    root.ClearCustomDataByKey("simReady:softParts")
    return len(rec.get("soft", []))


def apply(asset_id: str) -> dict:
    """Author the mixed body on the derivative (again, if it was before);
    returns what was done."""
    from pxr import Gf, Sdf, Usd, UsdGeom, UsdPhysics, UsdShade

    from add_mechanism import _joint_frames
    from processing import owned, record

    qf = QUEUE / f"{asset_id}.json"
    entry = json.loads(qf.read_text())
    if not owned(entry["file"]):
        raise RuntimeError("not ours to write (a source file): build its derivative first")
    stage = Usd.Stage.Open(entry["file"])
    survey_root = (entry.get("part_survey") or {}).get("root")
    if survey_root:
        strip(stage, survey_root, entry.get("soft_body_parts"))
    p = plan(entry, stage)
    root = stage.GetPrimAtPath(p["root"])
    xf = UsdGeom.XformCache(Usd.TimeCode.Default())
    root_inv = xf.GetLocalToWorldTransform(root).GetInverse()
    mpu = UsdGeom.GetStageMetersPerUnit(stage)
    # the root's own scale (a 0.01 wrapper on a centimetre model): lengths
    # authored in the root's frame are in these units
    root_scale = float(np.cbrt(abs(xf.GetLocalToWorldTransform(root).GetDeterminant()))) or 1.0

    # 1. rigid parts: the one body the root carried moves to the base mesh
    mass = None
    if root.HasAPI(UsdPhysics.MassAPI):
        mass = UsdPhysics.MassAPI(root).GetMassAttr().Get()
        root.RemoveAPI(UsdPhysics.MassAPI)
    if root.HasAPI(UsdPhysics.RigidBodyAPI):
        root.RemoveAPI(UsdPhysics.RigidBodyAPI)
    base = stage.GetPrimAtPath(p["base"])
    joints_scope = f"{p['root']}/Joints"
    UsdGeom.Scope.Define(stage, joints_scope)
    for i, path in enumerate(p["rigid"]):
        prim = stage.GetPrimAtPath(path)
        UsdPhysics.RigidBodyAPI.Apply(prim)
        if path in p.get("hidden", []):
            # inside the object: it has mass, nothing to touch (its collider
            # sat inside the sole's and the two fought, a footbed jittering out)
            for api in (UsdPhysics.CollisionAPI, UsdPhysics.MeshCollisionAPI):
                if prim.HasAPI(api):
                    prim.RemoveAPI(api)
        elif not prim.HasAPI(UsdPhysics.CollisionAPI):
            UsdPhysics.CollisionAPI.Apply(prim)
            mc = UsdPhysics.MeshCollisionAPI.Apply(prim)
            mc.CreateApproximationAttr().Set("convexDecomposition")
        # one solid object: its rigid parts never collide with each other
        # (fixed joints between plain rigid bodies do not filter contact)
        fp = UsdPhysics.FilteredPairsAPI.Apply(prim)
        for other in p["rigid"]:
            if other != path:
                fp.CreateFilteredPairsRel().AddTarget(other)
        if path == p["base"]:
            if mass:
                UsdPhysics.MassAPI.Apply(prim).CreateMassAttr().Set(float(mass))
            continue
        j = UsdPhysics.FixedJoint.Define(stage, f"{joints_scope}/soft_fixed_{i:02d}")
        j.CreateBody0Rel().SetTargets([p["base"]])
        j.CreateBody1Rel().SetTargets([path])
        c = xf.GetLocalToWorldTransform(prim).Transform(Gf.Vec3d(0, 0, 0))
        _joint_frames(j, xf.GetLocalToWorldTransform(base), xf.GetLocalToWorldTransform(prim), c)

    # 2. soft parts: a deformable body each, the mesh copied under it
    soft_scope = f"{p['root']}/SoftBodies"
    UsdGeom.Scope.Define(stage, soft_scope)
    made = []
    for k, c in enumerate(p["carriers"]):
        name = re.sub(r"\W+", "_", Path(c["path"]).name)[:40] or f"soft_{k:02d}"
        body_path = f"{soft_scope}/{name}"
        body = UsdGeom.Xform.Define(stage, body_path)
        bp = body.GetPrim()
        for src in [c["path"], *c["riders"]]:
            src_prim = stage.GetPrimAtPath(src)
            to_root = xf.GetLocalToWorldTransform(src_prim) * root_inv
            dst = f"{body_path}/{re.sub(r'\\W+', '_', Path(src).name)[:40]}"
            _copy_mesh(stage, src, dst, to_root)
            _bind_pose(stage.GetPrimAtPath(dst))
            for api in (UsdPhysics.CollisionAPI, UsdPhysics.MeshCollisionAPI, UsdPhysics.RigidBodyAPI,
                        UsdPhysics.MassAPI):
                if src_prim.HasAPI(api):
                    src_prim.RemoveAPI(api)
            src_prim.SetActive(False)
        cooking_src = f"{body_path}/{re.sub(r'\\W+', '_', Path(c['path']).name)[:40]}"
        sim = UsdGeom.Mesh.Define(stage, f"{body_path}/simMesh")
        sim.GetPrim().AddAppliedSchema("OmniPhysicsSurfaceDeformableSimAPI")
        UsdPhysics.CollisionAPI.Apply(sim.GetPrim())
        sim.GetPurposeAttr().Set(UsdGeom.Tokens.guide)
        sim.GetPrim().AddAppliedSchema("OmniPhysicsDeformablePoseAPI:default")
        sim.GetPrim().CreateAttribute("deformablePose:default:omniphysics:purposes",
                                      Sdf.ValueTypeNames.TokenArray).Set(["bindPose"])
        # body: cooked by PhysX from the copy, simplified to a light simulation mesh
        bp.AddAppliedSchema("PhysxAutoDeformableBodyAPI")
        bp.CreateAttribute("physxDeformableBody:autoDeformableBodyEnabled", Sdf.ValueTypeNames.Bool).Set(True)
        bp.CreateRelationship("physxDeformableBody:cookingSourceMesh").SetTargets([cooking_src])
        bp.AddAppliedSchema("PhysxAutoDeformableMeshSimplificationAPI")
        bp.CreateAttribute("physxDeformableBody:autoDeformableMeshSimplificationEnabled",
                           Sdf.ValueTypeNames.Bool).Set(True)
        bp.CreateAttribute("physxDeformableBody:remeshingEnabled", Sdf.ValueTypeNames.Bool).Set(True)
        bp.CreateAttribute("physxDeformableBody:targetTriangleCount", Sdf.ValueTypeNames.UInt).Set(TARGET_TRIANGLES)
        bp.AddAppliedSchema("OmniPhysicsDeformableBodyAPI")
        bp.CreateAttribute("omniphysics:deformableBodyEnabled", Sdf.ValueTypeNames.Bool).Set(True)
        bp.AddAppliedSchema("PhysxSurfaceDeformableBodyAPI")
        bp.CreateAttribute("physxDeformableBody:selfCollision", Sdf.ValueTypeNames.Bool).Set(True)
        bp.CreateAttribute("physxDeformableBody:enableSpeculativeCCD", Sdf.ValueTypeNames.Bool).Set(True)
        bp.CreateAttribute("physxDeformableBody:solverPositionIterationCount", Sdf.ValueTypeNames.UInt).Set(16)
        bp.CreateAttribute("physxDeformableBody:collisionPairUpdateFrequency", Sdf.ValueTypeNames.UInt).Set(4)
        bp.CreateAttribute("physxDeformableBody:collisionIterationMultiplier", Sdf.ValueTypeNames.UInt).Set(4)
        # material, bound for physics
        mat = _soft_material(stage, f"{body_path}/Material", c["preset"], mpu * root_scale)
        UsdShade.MaterialBindingAPI.Apply(bp).Bind(mat, UsdShade.Tokens.weakerThanDescendants, "physics")
        # attachment to the rigid part it sits on
        att = UsdGeom.Scope.Define(stage, f"{body_path}_attach")
        ap = att.GetPrim()
        ap.AddAppliedSchema("PhysxAutoDeformableAttachmentAPI")
        ap.CreateRelationship("physxAutoDeformableAttachment:attachable0").SetTargets([body_path])
        ap.CreateRelationship("physxAutoDeformableAttachment:attachable1").SetTargets([c["attach_to"]])
        ap.CreateAttribute("physxAutoDeformableAttachment:enableDeformableVertexAttachments",
                           Sdf.ValueTypeNames.Bool).Set(True)
        ap.CreateAttribute("physxAutoDeformableAttachment:enableRigidSurfaceAttachments",
                           Sdf.ValueTypeNames.Bool).Set(True)
        ap.CreateAttribute("physxAutoDeformableAttachment:enableCollisionFiltering", Sdf.ValueTypeNames.Bool).Set(True)
        overlap = 0.02 * float(max(c["size"])) / root_scale
        ap.CreateAttribute("physxAutoDeformableAttachment:deformableVertexOverlapOffset",
                           Sdf.ValueTypeNames.Float).Set(overlap)
        ap.CreateAttribute("physxAutoDeformableAttachment:collisionFilteringOffset",
                           Sdf.ValueTypeNames.Float).Set(overlap)
        made.append({"body": body_path, "source": c["path"], "role": c["role"], "material": c["material"],
                     "kind": c["preset"]["kind"], "riders": c["riders"], "attached_to": c["attach_to"],
                     "note": c["preset"].get("note")})
    record_soft = {"base": p["base"], "rigid": p["rigid"], "hidden": p.get("hidden", []), "soft": made,
                   "notes": p["notes"]}
    root.SetCustomDataByKey("simReady:softParts", json.dumps(record_soft))
    stage.GetRootLayer().Save()
    del stage
    entry = json.loads(qf.read_text())
    entry["soft_body_parts"] = record_soft
    entry.setdefault("applied_fixes", []).append(
        f"mixed body: {len(made)} soft part(s) as deformables on {len(p['rigid'])} rigid part(s)")
    record(entry, "soft", kind="mixed", soft=len(made), rigid=len(p["rigid"]))
    qf.write_text(json.dumps(entry, indent=1))
    return record_soft


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if "--plan" in sys.argv:
        from pxr import Usd

        for a in args:
            e = json.loads((QUEUE / f"{a}.json").read_text())
            # planned as the asset was before any soft bodies (in memory only)
            st = Usd.Stage.Open(e["file"])
            if (e.get("part_survey") or {}).get("root"):
                strip(st, e["part_survey"]["root"])
            try:
                p = plan(e, st)
            except RuntimeError as ex:
                print(a, "not a mixed body:", ex)
                continue
            print(a, json.dumps({k: v for k, v in p.items() if k != "carriers"}, indent=1))
            for c in p["carriers"]:
                print("  soft:", c["role"], c["material"], "riders", len(c["riders"]), "on", c["attach_to"].split("/")[-1])
        return 0
    for a in args:
        print(a, json.dumps(apply(a), indent=1)[:600])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
