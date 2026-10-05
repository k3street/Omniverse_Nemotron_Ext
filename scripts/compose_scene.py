#!/usr/bin/env python3
"""Compose sim-ready assets into one scene, with rules between them.

Each asset's derivative (workspace/assets_fixed/<id>_simready.usda) is
referenced into its own prim and placed; a rule makes a joint of one asset
wait on a joint of another (add_mechanism.add_rule).

Usage:
    python scripts/compose_scene.py OUT.usda \
        --asset ID[@X,Y,Z[,YAW_DEG]] ... \
        [--rule GATED_ID:JOINT=ACTUATOR_ID:JOINT@ENGAGE ...]

The scene can be animated like an asset: python.sh scripts/animate_asset.py OUT.usda
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).resolve().parent))


def compose(out: str, assets: list[tuple[str, tuple]], rules: list[tuple]) -> dict:
    from pxr import Gf, Usd, UsdGeom, UsdPhysics

    from add_mechanism import add_rule
    from ingest_asset import QUEUE_DIR, _camel

    stage = Usd.Stage.CreateNew(out)
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    world = UsdGeom.Xform.Define(stage, "/World")
    stage.SetDefaultPrim(world.GetPrim())
    UsdPhysics.Scene.Define(stage, "/PhysicsScene")
    placed = {}
    for asset_id, pose in assets:
        entry = json.loads((QUEUE_DIR / f"{asset_id}.json").read_text())
        prim = stage.DefinePrim(f"/World/{_camel(asset_id)}", "Xform")
        prim.GetReferences().AddReference(entry["file"])
        x, y, z, yaw = (list(pose) + [0.0, 0.0, 0.0, 0.0])[:4]
        api = UsdGeom.XformCommonAPI(prim)
        api.SetTranslate(Gf.Vec3d(x, y, z))
        api.SetRotate(Gf.Vec3f(0.0, 0.0, yaw))
        placed[asset_id] = str(prim.GetPath())

    def joint(asset_id, name):
        root = stage.GetPrimAtPath(placed[asset_id])
        found = [p for p in Usd.PrimRange(root) if p.IsA(UsdPhysics.Joint) and p.GetName() == name]
        if len(found) != 1:
            raise ValueError(f"{asset_id}: {len(found)} joints named {name!r}")
        return str(found[0].GetPath())

    made = []
    for gated_id, gated_name, act_id, act_name, engage in rules:
        made.append(add_rule(stage, joint(gated_id, gated_name), joint(act_id, act_name), engage))
    stage.GetRootLayer().Save()
    return {"scene": out, "assets": placed, "rules": made}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("out")
    ap.add_argument("--asset", action="append", default=[], help="ID[@X,Y,Z[,YAW_DEG]]")
    ap.add_argument("--rule", action="append", default=[], help="GATED_ID:JOINT=ACTUATOR_ID:JOINT@ENGAGE")
    args = ap.parse_args()
    assets = []
    for a in args.asset:
        aid, _, pose = a.partition("@")
        assets.append((aid, tuple(float(v) for v in pose.split(",")) if pose else ()))
    rules = []
    for r in args.rule:
        gated, _, rest = r.partition("=")
        act, _, engage = rest.partition("@")
        rules.append((*gated.split(":", 1), *act.split(":", 1), float(engage)))
    print(json.dumps(compose(args.out, assets, rules), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
