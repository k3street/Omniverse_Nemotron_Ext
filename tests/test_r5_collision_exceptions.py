"""The asset's collision exceptions reach PhysX the same way they reach the
planners: applied to a scene by the one module, and audited there before a
replay (a plan with the pairs dropped, in a scene that still models them,
jams the shoulder in the housing band - the towel reach of 2026-10-15)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
pxr = pytest.importorskip("pxr")
from pxr import Usd, UsdGeom  # noqa: E402

from r5_collision_exceptions import EXCEPTIONS, LINK_EXCEPTIONS, apply_to_stage, missing_in_scene, physx_filtered_pairs  # noqa: E402

ROOT = "/aida_revz71_r5_articulated"


def _scene(with_right_arm=True):
    stage = Usd.Stage.CreateInMemory()
    for e in EXCEPTIONS:
        for key in ("a", "b"):
            path = "/World/Robot" + e[key][len(ROOT):]
            if not with_right_arm and "right" in path:
                continue
            UsdGeom.Xform.Define(stage, path)
    for e in LINK_EXCEPTIONS:
        path = "/World/Robot" + e["a_link"][len(ROOT):]
        if not with_right_arm and "right" in path:
            continue
        UsdGeom.Xform.Define(stage, path)
    return stage


def test_a_fresh_scene_is_missing_every_exception_it_models():
    stage = _scene()
    assert missing_in_scene(stage) == [list(p) for p in physx_filtered_pairs()]


def test_applying_the_exceptions_leaves_nothing_missing():
    stage = _scene()
    applied = apply_to_stage(stage)
    assert len(applied) == len(EXCEPTIONS) + len(LINK_EXCEPTIONS)
    assert missing_in_scene(stage) == []
    a, b = applied[0]
    prim = stage.GetPrimAtPath(a)
    assert "PhysicsFilteredPairsAPI" in prim.GetAppliedSchemas()
    assert [str(t) for t in prim.GetRelationship("physics:filteredPairs").GetTargets()] == [b]


def test_a_pair_the_scene_lacks_is_neither_applied_nor_reported():
    stage = _scene(with_right_arm=False)
    applied = apply_to_stage(stage)
    assert len(applied) == 2 and all("left" in a for a, _ in applied)
    assert missing_in_scene(stage) == []


def test_the_filter_counts_in_either_direction():
    stage = _scene()
    for a, b in physx_filtered_pairs():
        pb = stage.GetPrimAtPath(b)
        pb.AddAppliedSchema("PhysicsFilteredPairsAPI")
        pb.CreateRelationship("physics:filteredPairs").AddTarget(a)
    assert missing_in_scene(stage) == []
