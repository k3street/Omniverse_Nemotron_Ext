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


# --- chess sets: one body per piece, a board that knows its squares -----------

def _chess_stage():
    """Board of 8 x 0.06 m squares at z<=0; white king on e1, black king on e8,
    white pawns on rank 2 named only by shape (as in a real Sketchfab export)."""
    stage = Usd.Stage.CreateInMemory()
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    UsdGeom.Xform.Define(stage, "/World")
    root = "/World/Set/Pieces"
    UsdGeom.Xform.Define(stage, "/World/Set")
    UsdGeom.Xform.Define(stage, root)
    UsdGeom.Xform.Define(stage, f"{root}/Board_0")
    _mesh(stage, f"{root}/Board_0/Squares", [((-0.24, -0.24, -0.01), (0.24, 0.24, 0.0))])
    sq = lambda f, r: (-0.21 + 0.06 * f, -0.21 + 0.06 * r)  # noqa: E731

    def piece(name, f, r, w, h):
        x, y = sq(f, r)
        UsdGeom.Xform.Define(stage, f"{root}/{name}")
        _mesh(stage, f"{root}/{name}/Geo", [((x - w, y - w, 0.0), (x + w, y + w, h))])

    piece("King_WHITE_1", 4, 0, 0.02, 0.09)
    piece("King_BLACK_2", 4, 7, 0.02, 0.09)
    piece("Pawn_BLACK_3", 0, 6, 0.01, 0.04)
    for f in range(3):
        piece(f"Mesh_0_00{f}_{f + 4}", f, 1, 0.01, 0.04)
    return stage


def test_set_members_are_the_separate_objects():
    from ingest_asset import set_members

    stage = _chess_stage()
    names = sorted(p.GetName() for p in set_members(stage, "/World/Set"))
    assert names[0] == "Board_0" and len(names) == 7


def test_chess_board_finds_a1_and_reads_the_position():
    from chess_board import analyse, fen_placement, letter

    a = analyse(_chess_stage(), "/World/Set")
    assert a["size"] == pytest.approx(0.06)
    assert a["rank_dir"] == (0.0, 1.0) and a["file_dir"] == (1.0, -0.0)
    assert a["a1"] == pytest.approx((-0.21, -0.21))
    by = {Path(i["path"]).name: i for i in a["pieces"]}
    assert by["King_WHITE_1"]["square"] == "e1" and by["King_BLACK_2"]["square"] == "e8"
    # unnamed meshes: pawn by shape, white by side of the board
    assert by["Mesh_0_000_4"]["kind"] == "p" and by["Mesh_0_000_4"]["color"] == "white"
    placed = {(i["file"], i["rank"]): letter(i["kind"], i["color"]) for i in a["pieces"]}
    assert fen_placement(placed) == "4k3/p7/8/8/8/8/PPP5/4K3"


# --- doors without a push bar ------------------------------------------------

def _plain_door(with_barrels: bool, leaves: int):
    stage = Usd.Stage.CreateInMemory()
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    UsdGeom.Xform.Define(stage, "/World")
    UsdGeom.Xform.Define(stage, ROOT)
    # a thin frame around a thicker leaf: the frame has the larger face
    _mesh(stage, f"{ROOT}/Frame", [((-0.55, -0.03, 0.0), (-0.5, 0.03, 2.1)), ((0.5, -0.03, 0.0), (0.55, 0.03, 2.1)),
                                    ((-0.55, -0.03, 2.05), (0.55, 0.03, 2.1))])
    if leaves == 1:
        _mesh(stage, f"{ROOT}/Leaf", [((-0.45, -0.05, 0.0), (0.45, 0.05, 2.0))])
    else:
        _mesh(stage, f"{ROOT}/LeafA", [((-0.5, -0.02, 0.0), (0.0, 0.02, 2.0))])
        _mesh(stage, f"{ROOT}/LeafB", [((0.0, -0.02, 0.0), (0.5, 0.02, 2.0))])
    if with_barrels:
        for i, z in enumerate((0.3, 1.7)):
            _mesh(stage, f"{ROOT}/HingeFrame{i}", [((-0.495, 0.05, z), (-0.455, 0.07, z + 0.15))])
            _mesh(stage, f"{ROOT}/HingeLeaf{i}", [((-0.48, 0.05, z + 0.03), (-0.43, 0.07, z + 0.12))])
    return stage


def test_hinge_barrels_give_the_hinge_side_and_swing():
    from door_draft import propose_door

    spec, notes = propose_door(_plain_door(True, 1), ROOT)
    hinge = next(j for j in spec["joints"] if j["name"] == "door_hinge")
    assert hinge["parent_prim"].endswith("Frame") and hinge["child_prim"].endswith("Leaf")
    assert hinge["anchor"][0] < -0.44            # the barrels' edge
    assert hinge["anchor"][1] > 0.05             # their side: the door opens toward +y
    assert (hinge["lower_limit"], hinge["upper_limit"]) == (0.0, 90.0)
    fixed = {j["child_prim"].rsplit("/", 1)[-1]: j["parent_prim"].rsplit("/", 1)[-1]
             for j in spec["joints"] if j["joint_type"] == "fixed"}
    # of each hinge's halves, the one reaching onto the leaf rides on the leaf
    assert fixed["HingeLeaf0"] == "Leaf" and fixed["HingeFrame0"] == "Frame"
    assert any("hinge barrel" in n for n in notes)


def test_two_equal_leaves_without_barrels_slide_apart_coupled():
    from door_draft import propose_door

    spec, _ = propose_door(_plain_door(False, 2), ROOT)
    joints = {j["name"]: j for j in spec["joints"]}
    left, right = joints["leaf_left_slide"], joints["leaf_right_slide"]
    assert left["joint_type"] == right["joint_type"] == "prismatic" and left["axis"] == "X"
    assert left["upper_limit"] == 0.0 and left["lower_limit"] < -0.4
    assert right["lower_limit"] == 0.0 and right["upper_limit"] > 0.4
    (couple,) = spec["mechanisms"]
    assert couple["type"] == "couple" and couple["follower"] == "leaf_right_slide" and couple["gearing"] == 1.0
    _articulate_with_couple(spec)


def _articulate_with_couple(spec):
    stage = _plain_door(False, 2)
    spec, _ = __import__("door_draft").propose_door(stage, ROOT)
    from add_mechanism import add_couple

    import types
    from service.isaac_assist_service.chat.tools.handlers.physics import _gen_articulate_asset
    body = {k: v for k, v in spec.items() if not k.startswith("_") and k not in
            ("link_masses", "no_collision", "filtered_pairs", "mechanisms")}
    omni = types.ModuleType("omni")
    omni_usd = types.ModuleType("omni.usd")
    omni_usd.get_context = lambda: type("C", (), {"get_stage": lambda self: stage})()
    omni.usd = omni_usd
    sys.modules["omni"], sys.modules["omni.usd"] = omni, omni_usd
    exec(compile(_gen_articulate_asset(body), "<articulate>", "exec"), {"__builtins__": __builtins__})
    add_couple(stage, ROOT, spec["mechanisms"][0])
    follower = stage.GetPrimAtPath(f"{ROOT}/Joints/leaf_right_slide")
    # authored metadata: a bare OpenUSD build does not register PhysX schemas,
    # so GetAppliedSchemas() hides them (Isaac lists them)
    assert "PhysxMimicJointAPI:rotX" in follower.GetMetadata("apiSchemas").GetAddedOrExplicitItems()
    assert str(follower.GetRelationship("physxMimicJoint:rotX:referenceJoint").GetTargets()[0]).endswith("leaf_left_slide")
    root = stage.GetPrimAtPath(ROOT)
    assert root.GetAttribute("physxArticulation:enabledSelfCollisions").Get() is False


# --- buttons -----------------------------------------------------------------

def test_buttons_press_into_their_face_and_carry_their_labels():
    from button_draft import propose_buttons

    stage = Usd.Stage.CreateInMemory()
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    UsdGeom.Xform.Define(stage, "/World")
    UsdGeom.Xform.Define(stage, "/World/Remote")
    _mesh(stage, "/World/Remote/Body", [((-0.02, -0.09, -0.006), (0.02, 0.09, 0.006))])
    for i, y in enumerate((-0.03, 0.0, 0.03)):
        _mesh(stage, f"/World/Remote/Key{i}", [((-0.006, y - 0.006, 0.004), (0.006, y + 0.006, 0.0085))])
    _mesh(stage, "/World/Remote/KeyCopy", [((-0.006, -0.006, 0.004), (0.006, 0.006, 0.0085))])  # Key1 again
    _mesh(stage, "/World/Remote/Label", [((-0.003, 0.027, 0.0085), (0.003, 0.033, 0.009))])     # on Key2
    spec, notes = propose_buttons(stage, "/World/Remote", {"press_force_n": 2.0, "max_travel_m": 0.0015})
    buttons = [j for j in spec["joints"] if j.get("_role") == "button"]
    assert len(buttons) == 3 and all(j["axis"] == "Z" for j in buttons)
    assert all((j["lower_limit"], j["upper_limit"]) == (-0.0015, 0.0) for j in buttons)  # into the +Z face
    assert buttons[0]["stiffness"] == pytest.approx(2.0 / 0.0015, rel=1e-3)
    fixed = {j["child_prim"].rsplit("/", 1)[-1]: j["parent_prim"].rsplit("/", 1)[-1]
             for j in spec["joints"] if j["joint_type"] == "fixed"}
    assert fixed["Label"] == "Key2" and fixed["KeyCopy"] == "Key1"
    assert spec["fixed_base"] is False
