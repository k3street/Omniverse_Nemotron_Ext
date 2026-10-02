"""Door drafting and the latch mechanism on a synthetic door (pxr only).

Runs where pxr is importable, e.g.
PYTHONPATH=<openusd>/lib/python LD_LIBRARY_PATH=<openusd>/lib pytest ...
The physics itself (the latch holding, camming shut) is verified in Isaac by
scripts/animate_asset.py; these tests pin down what gets authored.
"""
import json
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.l0
pxr = pytest.importorskip("pxr")

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from pxr import Gf, Usd, UsdGeom, UsdPhysics  # noqa: E402

ROOT = "/World/Door"


def _box(lo, hi):
    pts = [(x, y, z) for z in (lo[2], hi[2]) for y in (lo[1], hi[1]) for x in (lo[0], hi[0])]
    faces = [(0, 2, 3, 1), (4, 5, 7, 6), (0, 1, 5, 4), (2, 6, 7, 3), (0, 4, 6, 2), (1, 3, 7, 5)]
    return pts, faces


def _mesh(stage, path, boxes):
    pts, faces = [], []
    for lo, hi in boxes:
        p, f = _box(lo, hi)
        faces += [tuple(i + len(pts) for i in face) for face in f]
        pts += p
    m = UsdGeom.Mesh.Define(stage, path)
    m.CreatePointsAttr([Gf.Vec3f(*p) for p in pts])
    m.CreateFaceVertexCountsAttr([4] * len(faces))
    m.CreateFaceVertexIndicesAttr([i for f in faces for i in f])
    return m


@pytest.fixture()
def door_stage():
    """A 0.9 x 2.0 m leaf (y 0.03..0.08) modelled INTO its frame, and a push
    bar on the -y face reaching toward +x (so the hinge belongs at -x)."""
    stage = Usd.Stage.CreateInMemory()
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    UsdGeom.Xform.Define(stage, "/World")
    UsdGeom.Xform.Define(stage, ROOT)
    _mesh(stage, f"{ROOT}/Shell", [
        ((-0.50, -0.04, 0.0), (-0.45, 0.04, 2.1)),   # hinge-side jamb
        ((0.45, -0.04, 0.0), (0.50, 0.04, 2.1)),     # strike-side jamb
        ((-0.50, -0.04, 2.02), (0.50, 0.04, 2.1)),   # head
        ((-0.45, 0.03, 0.0), (0.45, 0.08, 2.0)),     # leaf
    ])
    _mesh(stage, f"{ROOT}/Bar", [((-0.2, -0.05, 0.9), (0.42, 0.03, 0.94))])
    return stage


def _articulate(stage, spec):
    """Apply a draft the way the hub does, minus the queue bookkeeping."""
    import types

    from service.isaac_assist_service.chat.tools.handlers.physics import _gen_articulate_asset

    spec = dict(spec)
    for k in ("_analysis", "_instructions", "link_masses", "no_collision", "filtered_pairs"):
        spec.pop(k, None)
    mechanisms = spec.pop("mechanisms", [])
    omni = types.ModuleType("omni")
    omni_usd = types.ModuleType("omni.usd")
    omni_usd.get_context = lambda: type("C", (), {"get_stage": lambda self: stage})()
    omni.usd = omni_usd
    sys.modules["omni"], sys.modules["omni.usd"] = omni, omni_usd
    exec(compile(_gen_articulate_asset(spec), "<articulate>", "exec"), {"__builtins__": __builtins__})
    from add_mechanism import add_latch
    return [add_latch(stage, spec["prim_path"], m) for m in mechanisms]


def test_door_draft_finds_leaf_bar_hinge_and_swing(door_stage):
    from door_draft import propose_door

    spec, notes = propose_door(door_stage, ROOT)
    assert any("split the leaf" in n for n in notes)
    joints = {j["name"]: j for j in spec["joints"]}
    hinge, bar = joints["door_hinge"], joints["crash_bar_push"]
    assert hinge["child_prim"].endswith("DoorLeaf") and hinge["parent_prim"].endswith("DoorFrame")
    # hinge on the edge the bar points away from, on the face it swings toward
    assert hinge["anchor"][0] == pytest.approx(-0.45, abs=1e-3)
    assert hinge["anchor"][1] == pytest.approx(0.08, abs=1e-3)
    # +angle about +Z swings the +x latch edge toward +y: away from the bar
    assert (hinge["lower_limit"], hinge["upper_limit"]) == (0.0, 90.0)
    assert bar["axis"] == "Y" and (bar["lower_limit"], bar["upper_limit"]) == (0.0, 0.02)
    assert spec["mechanisms"][0]["actuator_joint"] == "crash_bar_push"
    leaf = UsdGeom.BBoxCache(0, ["default"]).ComputeWorldBound(
        door_stage.GetPrimAtPath(spec["_analysis"]["leaf"])).ComputeAlignedRange()
    assert leaf.GetMin()[1] == pytest.approx(0.03) and leaf.GetMax()[0] == pytest.approx(0.45)


def test_latch_is_coupled_to_the_bar_and_gates_the_hinge(door_stage):
    from door_draft import propose_door

    spec, _ = propose_door(door_stage, ROOT)
    (made,) = _articulate(door_stage, spec)
    bolt = door_stage.GetPrimAtPath(made["joint"])
    assert bolt.IsA(UsdPhysics.PrismaticJoint)
    assert UsdPhysics.PrismaticJoint(bolt).GetAxisAttr().Get() == "X"
    # full bar travel (+0.02) retracts the bolt fully (-0.025): bolt + g*bar = 0
    g = bolt.GetAttribute("physxMimicJoint:rotX:gearing").Get()
    assert g == pytest.approx(1.25)
    ref = bolt.GetRelationship("physxMimicJoint:rotX:referenceJoint").GetTargets()
    assert str(ref[0]).endswith("crash_bar_push")
    gate = json.loads(door_stage.GetPrimAtPath(f"{ROOT}/Joints/door_hinge").GetCustomDataByKey("simReady:gate"))
    assert gate["actuator_joint"] == "crash_bar_push" and gate["engage"] == pytest.approx(0.02)
    cache = UsdGeom.BBoxCache(0, ["default"])
    b = cache.ComputeWorldBound(door_stage.GetPrimAtPath(made["bolt"])).ComputeAlignedRange()
    k = cache.ComputeWorldBound(door_stage.GetPrimAtPath(made["keeper"])).ComputeAlignedRange()
    # bolt stands proud of the latch edge; keeper is outboard of the leaf,
    # with its lip on the swing side beyond the leaf's back face
    assert b.GetMax()[0] > 0.45 > b.GetMin()[0]
    assert k.GetMin()[0] > 0.45 and k.GetMax()[1] > 0.08
    # the web that mounts the lip to the jamb stays clear of the bolt's tip
    keeper_pts = UsdGeom.Mesh(door_stage.GetPrimAtPath(made["keeper"])).GetPointsAttr().Get()
    assert all(p[0] > b.GetMax()[0] for p in keeper_pts if p[1] < 0.08)
    # the bolt overlaps the keeper along the throw, so an opening leaf meets it
    assert b.GetMax()[0] > k.GetMin()[0]


def test_bar_pushed_toward_minus_axis_flips_the_gearing(door_stage):
    """A bar on the +y face pushes toward -y: negative travel, negative gearing."""
    from add_mechanism import add_latch

    UsdGeom.Xformable(door_stage.GetPrimAtPath(f"{ROOT}/Bar")).AddTranslateOp().Set(Gf.Vec3d(0, 0.13, 0))
    from door_draft import propose_door

    spec, _ = propose_door(door_stage, ROOT)
    joints = {j["name"]: j for j in spec["joints"]}
    assert (joints["crash_bar_push"]["lower_limit"], joints["crash_bar_push"]["upper_limit"]) == (-0.02, 0.0)
    assert (joints["door_hinge"]["lower_limit"], joints["door_hinge"]["upper_limit"]) == (-90.0, 0.0)
    (made,) = _articulate(door_stage, spec)
    assert made["gearing"] == pytest.approx(-1.25)
