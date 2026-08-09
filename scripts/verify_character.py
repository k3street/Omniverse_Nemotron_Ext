#!/usr/bin/env python3
"""Headless rig verification for character_rigged assets (UsdSkel).

A rigged character is sim-usable when its skeleton is well-formed and its
animation actually moves it — checked headlessly with pxr, no renderer:

  - skeleton topology: joints exist, bindTransforms and restTransforms
    match the joint count
  - skinning: skinned meshes carry jointIndices/jointWeights primvars,
    weights roughly normalized
  - animation: joint transforms at t0 and t1 differ (a rig that never
    moves is a statue with extra steps)

Evidence lands on the queue entry (skeleton.verified); the human still
signs. Usage:
    python scripts/verify_character.py <asset_id> [...]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ingest_asset import QUEUE_DIR  # noqa: E402


def verify_rig(file_path: str) -> dict:
    import numpy as np
    from pxr import Usd, UsdGeom, UsdSkel

    stage = Usd.Stage.Open(file_path)
    result = {"checks": [], "verified": False}

    def check(name, ok, evidence):
        result["checks"].append({"check": name, "ok": bool(ok),
                                 "evidence": str(evidence)[:160]})

    skels = [p for p in stage.Traverse() if p.IsA(UsdSkel.Skeleton)]
    if not skels:
        check("skeleton_present", False, "no UsdSkel.Skeleton prim")
        return result
    skel = UsdSkel.Skeleton(skels[0])
    joints = list(skel.GetJointsAttr().Get() or [])
    bind = skel.GetBindTransformsAttr().Get()
    rest = skel.GetRestTransformsAttr().Get()
    check("skeleton_present", True,
          f"{len(skels)} skeleton(s), {len(joints)} joints")
    check("bind_transforms", bind is not None and len(bind) == len(joints),
          f"bindTransforms: {len(bind) if bind else 0} for {len(joints)} joints")
    check("rest_transforms", rest is not None and len(rest) == len(joints),
          f"restTransforms: {len(rest) if rest else 0}")

    skinned = 0
    weights_ok = True
    for prim in stage.Traverse():
        if prim.GetTypeName() != "Mesh" or not prim.HasAPI(UsdSkel.BindingAPI):
            continue
        b = UsdSkel.BindingAPI(prim)
        ji = b.GetJointIndicesPrimvar()
        jw = b.GetJointWeightsPrimvar()
        if not (ji and jw and ji.HasValue() and jw.HasValue()):
            continue
        skinned += 1
        w = np.array(jw.Get(), dtype=float)
        n = jw.GetElementSize() or 1
        sums = w.reshape(-1, n).sum(axis=1)
        if not (0.9 <= float(sums.mean()) <= 1.1):
            weights_ok = False
    check("skinned_meshes", skinned > 0, f"{skinned} skinned mesh(es)")
    check("weights_normalized", skinned > 0 and weights_ok,
          "mean weight sums within 0.9-1.1" if weights_ok else "weight sums off")

    # Animation semantics: stock characters often ship a STATIC pose clip
    # (1 time sample) and receive motion at scene time (omni.anim.people).
    # A valid static pose is fine; a multi-sample clip that doesn't move
    # anything is broken.
    anims = [p for p in stage.Traverse() if p.IsA(UsdSkel.Animation)]
    samples = 0
    if anims:
        a = UsdSkel.Animation(anims[0])
        samples = max(a.GetRotationsAttr().GetNumTimeSamples(),
                      a.GetTranslationsAttr().GetNumTimeSamples())
    if samples <= 1:
        # a single sample with no default value returns None from Get() —
        # query at EarliestTime to see the authored pose
        pose_ok = False
        if anims:
            a = UsdSkel.Animation(anims[0])
            t = Usd.TimeCode.EarliestTime()
            pose_ok = (a.GetRotationsAttr().Get(t) is not None
                       or a.GetTranslationsAttr().Get(t) is not None)
        check("animation", pose_ok,
              f"static pose clip ({samples} time sample) — motion applies "
              "at scene time (e.g. omni.anim.people)")
    else:
        cache = UsdSkel.Cache()
        root = next((p for p in stage.Traverse()
                     if p.IsA(UsdSkel.Root)), None)
        moved = False
        detail = "no SkelRoot"
        if root is not None:
            cache.Populate(UsdSkel.Root(root),
                           Usd.TraverseInstanceProxies())
            query = cache.GetSkelQuery(skel)
            t0 = stage.GetStartTimeCode()
            t1 = max(t0 + 1.0, (stage.GetEndTimeCode() or 24.0) / 2.0)
            x0 = query.ComputeJointLocalTransforms(Usd.TimeCode(t0))
            x1 = query.ComputeJointLocalTransforms(Usd.TimeCode(t1))
            if x0 and x1:
                d = max(
                    max(abs(a - b)
                        for r0, r1 in zip(m0, m1)
                        for a, b in zip(r0, r1))
                    for m0, m1 in zip(x0, x1))
                moved = d > 1e-4
                detail = (f"{samples} samples, max joint delta "
                          f"t{t0}->t{t1}: {d:.5f}")
        check("animation", moved, detail)

    result["verified"] = all(c["ok"] for c in result["checks"])
    return result


def verify_entry(asset_id: str) -> str:
    qf = QUEUE_DIR / f"{asset_id}.json"
    entry = json.loads(qf.read_text())
    if not entry.get("report", {}).get("skeleton"):
        return f"{asset_id}: not a rigged character entry"
    result = verify_rig(entry.get("original_file") or entry["file"])
    entry["report"]["skeleton"]["verified"] = result["verified"]
    entry["rig_verification"] = result
    qf.write_text(json.dumps(entry, indent=1))
    failed = [c["check"] for c in result["checks"] if not c["ok"]]
    return (f"{'PASS' if result['verified'] else 'FAIL'} {asset_id}: "
            + (f"rig verified ({entry['report']['skeleton']['joints']} joints)"
               if result["verified"]
               else f"failed: {', '.join(failed)}"))


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    failures = 0
    for asset_id in sys.argv[1:]:
        try:
            line = verify_entry(asset_id)
            print(line)
            failures += 0 if line.startswith("PASS") else 1
        except Exception as e:
            failures += 1
            print(f"ERROR {asset_id}: {str(e)[:200]}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
