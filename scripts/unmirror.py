#!/usr/bin/env python3
"""Bake mirroring transforms into mesh points: no negative scale left.

Modellers mirror half an object (an engine crane's right leg, brace and
casters are its left ones under a Z -> -Z transform). Two things break on a
reflecting transform:

  - PhysX does not take negative scale on rigid bodies or their colliders:
    the engine crane tore itself apart at the first step.
  - The survey render (Storm) drew the mirrored parts as nothing, and the
    vision model called them "hidden mirror" parts that do not move.

For each prim whose own transform reflects (determinant < 0), this makes
the transform proper (M' = R * M, R a reflection in its local Z) and puts
the reflection into the points of every mesh under it instead
(p' = p * L * R * L^-1, L the mesh's transform below the prim), so the
world geometry is unchanged. Points reflected flip the faces' winding, so
each mesh's orientation is toggled (face order and subsets stay as they
are); normals and extents follow the points.

Writes to the edit target (an asset's own derivative, never a download).

    python scripts/unmirror.py <asset_id> [...]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
QUEUE = REPO / "workspace" / "review_queue"


def _det3(m) -> float:
    return m.ExtractRotationMatrix().GetDeterminant()


def unmirror(stage, root: str) -> int:
    """Reflecting transforms under root made proper; returns how many."""
    from pxr import Gf, Usd, UsdGeom, Vt

    R = Gf.Matrix4d(1.0).SetScale(Gf.Vec3d(1.0, 1.0, -1.0))
    fixed = 0
    top = stage.GetPrimAtPath(root)
    if not top:
        return 0
    for prim in Usd.PrimRange(top):        # parents before children
        if not prim.IsA(UsdGeom.Xformable):
            continue
        xf = UsdGeom.Xformable(prim)
        local = xf.GetLocalTransformation()
        if _det3(local) >= 0:
            continue
        cache = UsdGeom.XformCache()
        to_world = cache.GetLocalToWorldTransform(prim)
        inv_world = to_world.GetInverse()
        for q in Usd.PrimRange(prim):
            if not q.IsA(UsdGeom.Mesh):
                continue
            mesh = UsdGeom.Mesh(q)
            pts_attr = mesh.GetPointsAttr()
            if pts_attr.GetNumTimeSamples() > 0:
                continue                    # animated points: left as modelled
            pts = pts_attr.Get()
            if not pts:
                continue
            # L: the mesh's transform below prim (identity when it is prim)
            L = cache.GetLocalToWorldTransform(q) * inv_world if q != prim else Gf.Matrix4d(1.0)
            C = L * R * L.GetInverse()
            pts_attr.Set(Vt.Vec3fArray([Gf.Vec3f(C.Transform(Gf.Vec3d(p))) for p in pts]))
            N = C.ExtractRotationMatrix().GetInverse().GetTranspose()
            normal_attrs = [mesh.GetNormalsAttr()]
            pv = UsdGeom.PrimvarsAPI(q).GetPrimvar("normals")
            if pv:
                normal_attrs.append(pv.GetAttr())
            for na in normal_attrs:
                ns = na.Get() if na and na.HasValue() else None
                if ns:
                    out = []
                    for n in ns:
                        v = Gf.Vec3d(n) * N
                        out.append(Gf.Vec3f(v.GetNormalized() if v.GetLength() > 0 else v))
                    na.Set(Vt.Vec3fArray(out))
            o = mesh.GetOrientationAttr().Get() or UsdGeom.Tokens.rightHanded
            mesh.CreateOrientationAttr().Set(UsdGeom.Tokens.leftHanded if o == UsdGeom.Tokens.rightHanded
                                             else UsdGeom.Tokens.rightHanded)
            ext = UsdGeom.PointBased.ComputeExtent(pts_attr.Get())
            if ext:
                mesh.CreateExtentAttr().Set(ext)
        xf.ClearXformOpOrder()
        xf.AddTransformOp().Set(R * local)
        fixed += 1
    return fixed


def unmirror_asset(asset_id: str) -> str:
    from pxr import Usd

    qf = QUEUE / f"{asset_id}.json"
    entry = json.loads(qf.read_text())
    sys.path.insert(0, str(REPO / "scripts"))
    from processing import owned
    if not owned(entry["file"]):
        return f"{asset_id}: 0 mirroring transform(s) baked into points (a source file: not ours to write)"
    stage = Usd.Stage.Open(entry["file"])
    root = str(stage.GetDefaultPrim().GetPath()) if stage.GetDefaultPrim() else "/"
    n = unmirror(stage, root)
    if n:
        stage.GetRootLayer().Save()
        entry = json.loads(qf.read_text())
        entry.setdefault("applied_fixes", []).append(f"unmirror: {n} mirroring transform(s) baked into points")
        qf.write_text(json.dumps(entry, indent=1))
    return f"{asset_id}: {n} mirroring transform(s) baked into points"


if __name__ == "__main__":
    for a in sys.argv[1:]:
        print(unmirror_asset(a))
