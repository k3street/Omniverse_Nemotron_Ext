"""Door drafting and the latch mechanism on a synthetic door (pxr only).

Runs where pxr is importable, e.g.
PYTHONPATH=<openusd>/lib/python LD_LIBRARY_PATH=<openusd>/lib pytest ...
The physics itself (the latch holding, camming shut) is verified in Isaac by
scripts/animate_asset.py; these tests pin down what gets authored.
"""
import json
import math
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


def test_pivot_turns_one_arm_about_the_pin_from_closed_to_open():
    import math

    from pivot_draft import propose_pivot

    stage = Usd.Stage.CreateInMemory()
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    UsdGeom.Xform.Define(stage, "/World")
    UsdGeom.Xform.Define(stage, "/World/Scissors")
    _mesh(stage, "/World/Scissors/ArmA", [((-0.05, -0.004, 0.0), (0.10, 0.004, 0.002))])
    arm_b = _mesh(stage, "/World/Scissors/ArmB", [((-0.05, -0.004, 0.002), (0.10, 0.004, 0.004))])
    # the second arm modelled 30 degrees open about the pin
    c, s = math.cos(math.radians(30)), math.sin(math.radians(30))
    arm_b.GetPointsAttr().Set([Gf.Vec3f(c * p[0] - s * p[1], s * p[0] + c * p[1], p[2])
                               for p in arm_b.GetPointsAttr().Get()])
    _mesh(stage, "/World/Scissors/Pin", [((-0.003, -0.003, -0.001), (0.003, 0.003, 0.005))])
    spec, notes = propose_pivot(stage, "/World/Scissors", {"open_deg": 60})
    pivot = next(j for j in spec["joints"] if j["name"] == "pivot")
    assert pivot["axis"] == "Z" and pivot["joint_type"] == "revolute"
    assert pivot["anchor"] == pytest.approx([0.0, 0.0, 0.002], abs=1e-4)
    # closed is -30 (arms aligned), open is the class's 60 from closed
    assert (pivot["lower_limit"], pivot["upper_limit"]) == pytest.approx((-30.0, 30.0), abs=0.5)
    fixed = {j["child_prim"].rsplit("/", 1)[-1] for j in spec["joints"] if j["joint_type"] == "fixed"}
    assert fixed == {"Pin"} and spec["fixed_base"] is False


# --- threads -----------------------------------------------------------------

def _bolt_stage(with_nut=True):
    stage = Usd.Stage.CreateInMemory()
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    UsdGeom.Xform.Define(stage, "/World")
    UsdGeom.Xform.Define(stage, "/World/Bolt")
    # M8 x 40: an 8 mm shank, a 13 mm head on top
    _mesh(stage, "/World/Bolt/Screw", [((-0.004, -0.004, -0.04), (0.004, 0.004, 0.0)),
                                       ((-0.0065, -0.0065, 0.0), (0.0065, 0.0065, 0.0055))])
    ring = [((-0.0065, -0.0065, 0), (-0.0042, 0.0065, 1)), ((0.0042, -0.0065, 0), (0.0065, 0.0065, 1)),
            ((-0.0042, -0.0065, 0), (0.0042, -0.0042, 1)), ((-0.0042, 0.0042, 0), (0.0042, 0.0065, 1))]

    def at(z0, z1):
        return [((lo[0], lo[1], z0), (hi[0], hi[1], z1)) for lo, hi in ring]
    _mesh(stage, "/World/Bolt/Washer", at(-0.0016, 0.0))
    if with_nut:
        _mesh(stage, "/World/Bolt/Nut", at(-0.03, -0.0235))
    _mesh(stage, "/World/Bolt/Ground", [((-0.1, -0.1, -0.05), (0.1, 0.1, -0.05))])  # a backdrop plane
    return stage


def test_thread_sizes_the_bolt_and_lets_the_nut_run_from_tip_to_head():
    from thread_draft import propose_thread

    spec, notes = propose_thread(_bolt_stage(), "/World/Bolt", {})
    assert spec["thread"]["nominal_m"] == 0.008 and spec["thread"]["pitch_m"] == 0.00125
    assert spec["thread"]["axis"] == "Z" and spec["thread"]["head_end"] == "+"
    helix = spec["mechanisms"][0]
    assert helix["type"] == "helix" and helix["nut"].endswith("Nut") and helix["bolt"].endswith("Screw")
    # nut 6.5 mm thick at -30..-23.5 mm on a -40..0 shank: bolt moves -16.5 mm (nut at the
    # head) to +10 mm (nut at the tip)
    assert helix["turns"] == pytest.approx([-0.0235 / 0.00125, 0.010 / 0.00125], abs=0.6)
    fixed = {j["child_prim"].rsplit("/", 1)[-1] for j in spec["joints"] if j["joint_type"] == "fixed"}
    assert fixed == {"Washer"}  # the backdrop plane is not a part


def test_a_loose_bolt_gets_no_joint_but_keeps_its_thread():
    from thread_draft import propose_thread

    spec, notes = propose_thread(_bolt_stage(with_nut=False), "/World/Bolt", {})
    assert not spec["mechanisms"] and spec["thread"]["nominal_m"] == 0.008
    assert any("loose fastener" in n for n in notes)


def test_helix_replaces_the_placeholder_with_two_coupled_joints():
    from add_mechanism import add_helix

    stage = _bolt_stage()
    for p in ("/World/Bolt/Nut", "/World/Bolt/Screw"):
        UsdPhysics.RigidBodyAPI.Apply(stage.GetPrimAtPath(p))
    UsdGeom.Scope.Define(stage, "/World/Bolt/Joints")
    placeholder = UsdPhysics.RevoluteJoint.Define(stage, "/World/Bolt/Joints/thread")
    placeholder.CreateBody0Rel().SetTargets(["/World/Bolt/Nut"])
    placeholder.CreateBody1Rel().SetTargets(["/World/Bolt/Screw"])
    made = add_helix(stage, "/World/Bolt", {"nut": "/World/Bolt/Nut", "bolt": "/World/Bolt/Screw", "axis": "Z",
                                            "anchor": [0, 0, -0.027], "pitch_m": 0.00125, "turns": [-18, 8]})
    assert not stage.GetPrimAtPath("/World/Bolt/Joints/thread").IsValid()
    turn = UsdPhysics.RevoluteJoint(stage.GetPrimAtPath(made["turn"]))
    assert (turn.GetLowerLimitAttr().Get(), turn.GetUpperLimitAttr().Get()) == (-18 * 360.0, 8 * 360.0)
    advance = stage.GetPrimAtPath(made["advance"])
    # metres per degree: PhysX takes the gearing in the joints' USD units
    assert advance.GetAttribute("physxMimicJoint:rotX:gearing").Get() == pytest.approx(-0.00125 / 360)
    assert str(advance.GetRelationship("physxMimicJoint:rotX:referenceJoint").GetTargets()[0]) == made["turn"]
    assert stage.GetPrimAtPath(made["carrier"]).HasAPI(UsdPhysics.RigidBodyAPI)
    # self-locking: the thread's running torque damps the turn
    drive = UsdPhysics.DriveAPI.Get(stage.GetPrimAtPath(made["turn"]), "angular")
    assert drive.GetStiffnessAttr().Get() == 0.0 and drive.GetDampingAttr().Get() > 0.0


# --- plungers ----------------------------------------------------------------

def _pipette_stage():
    stage = Usd.Stage.CreateInMemory()
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    UsdGeom.Xform.Define(stage, "/World")
    UsdGeom.Xform.Define(stage, "/World/Pip")
    _mesh(stage, "/World/Pip/Body", [((-0.02, -0.008, 0.0), (0.016, 0.008, 0.1))])          # handle
    _mesh(stage, "/World/Pip/Rest", [((-0.024, -0.003, 0.09), (-0.018, 0.003, 0.098))])     # finger rest, off axis
    _mesh(stage, "/World/Pip/Knob", [((-0.007, -0.007, 0.1), (0.007, 0.007, 0.112))])       # plunger
    _mesh(stage, "/World/Pip/KnobCap", [((-0.005, -0.005, 0.112), (0.005, 0.005, 0.115))])
    _mesh(stage, "/World/Pip/Ejector", [((-0.004, -0.006, -0.06), (0.024, 0.006, 0.095))])  # sleeve + button
    _mesh(stage, "/World/Pip/Tip", [((-0.003, -0.003, -0.11), (0.003, 0.003, -0.075))])     # disposable tip
    return stage


def test_pipette_plunger_ejector_and_tip_are_found():
    from pipette_draft import propose_pipette

    spec, notes = propose_pipette(_pipette_stage(), "/World/Pip", {})
    name = lambda p: p.rsplit("/", 1)[-1]  # noqa: E731
    two_stop = next(m for m in spec["mechanisms"] if m["type"] == "two_stop")
    assert name(two_stop["body"]) == "Body" and two_stop["press"] == -1.0 and two_stop["axis"] == "Z"
    assert {name(two_stop["plunger"])} | {name(j["child_prim"]) for j in spec["joints"]
                                           if j["name"].startswith("plunger_")} == {"Knob", "KnobCap"}
    ejector = next(j for j in spec["joints"] if j["name"] == "tip_ejector")
    assert name(ejector["child_prim"]) == "Ejector" and ejector["upper_limit"] == 0.0
    # the sleeve ends 15 mm above the tip: it travels there and 4 mm further
    assert ejector["lower_limit"] == pytest.approx(-0.019, abs=1e-4)
    fit = next(m for m in spec["mechanisms"] if m["type"] == "press_fit")
    assert name(fit["part"]) == "Tip" and fit["released_by"]["joint"] == "tip_ejector"
    fixed = {name(j["child_prim"]) for j in spec["joints"] if j["joint_type"] == "fixed"}
    assert "Rest" in fixed and "Tip" not in {name(j["child_prim"]) for j in spec["joints"]}


def test_two_stop_springs_are_preloaded_in_series_through_a_carrier_as_heavy_as_the_plunger():
    from add_mechanism import add_two_stop

    stage = _pipette_stage()
    for p in ("/World/Pip/Body", "/World/Pip/Knob"):
        UsdPhysics.RigidBodyAPI.Apply(stage.GetPrimAtPath(p))
    UsdPhysics.MassAPI.Apply(stage.GetPrimAtPath("/World/Pip/Knob")).CreateMassAttr().Set(0.004)
    made = add_two_stop(stage, "/World/Pip", {
        "body": "/World/Pip/Body", "plunger": "/World/Pip/Knob", "axis": "Z", "press": -1, "anchor": [0, 0, 0.1],
        "first_stop_m": 0.012, "blowout_m": 0.004, "first_force_n": 10.0, "blowout_force_n": 25.0})
    assert stage.GetPrimAtPath(made["carrier"]).GetAttribute("physics:mass").Get() == pytest.approx(0.004)
    for key, travel, force, preload in (("stroke", 0.012, 10.0, 2.0), ("blowout", 0.004, 25.0, 12.0)):
        j = stage.GetPrimAtPath(made[key])
        assert (j.GetAttribute("physics:lowerLimit").Get(), j.GetAttribute("physics:upperLimit").Get()) == \
            pytest.approx((-travel, 0.0))
        d = UsdPhysics.DriveAPI.Get(j, "linear")
        k, target = d.GetStiffnessAttr().Get(), d.GetTargetPositionAttr().Get()
        # at rest the spring pushes back with the preload; at full travel with the full force
        assert k * target == pytest.approx(preload, rel=1e-3)
        assert k * (target + travel) == pytest.approx(force, rel=1e-3)


def test_press_fit_is_a_breakable_joint_outside_the_articulation_released_by_rule():
    from add_mechanism import PHYSX_BREAK_FORCE_SCALE, add_press_fit

    stage = _pipette_stage()
    UsdPhysics.RigidBodyAPI.Apply(stage.GetPrimAtPath("/World/Pip/Body"))
    made = add_press_fit(stage, "/World/Pip", {"holder": "/World/Pip/Body", "part": "/World/Pip/Tip",
                                               "anchor": [0, 0, -0.075], "break_force_n": 15.0,
                                               "released_by": {"joint": "tip_ejector", "travel_m": 0.016}})
    j = stage.GetPrimAtPath(made["joint"])
    assert j.GetAttribute("physics:excludeFromArticulation").Get() is True
    assert j.GetAttribute("physics:breakForce").Get() == pytest.approx(15.0 * PHYSX_BREAK_FORCE_SCALE)
    assert j.GetCustomDataByKey("simReady:breakForceN") == 15.0
    assert dict(j.GetCustomDataByKey("simReady:releasedBy")) == {"joint": "tip_ejector", "travel_m": 0.016}
    tip = stage.GetPrimAtPath("/World/Pip/Tip")
    assert tip.HasAPI(UsdPhysics.RigidBodyAPI)
    assert tip.GetAttribute("physics:approximation").Get() == "convexHull"


# --- watches -----------------------------------------------------------------

def _rotated_bar(stage, path, length, width, z, angle_deg, tail=0.15):
    """A hand: a bar from the centre out to `length` (with a short tail) at an angle."""
    import math

    m = _mesh(stage, path, [((-tail * length, -width / 2, z), (length, width / 2, z + 0.0004))])
    c, s = math.cos(math.radians(angle_deg)), math.sin(math.radians(angle_deg))
    m.GetPointsAttr().Set([Gf.Vec3f(c * p[0] - s * p[1], s * p[0] + c * p[1], p[2]) for p in m.GetPointsAttr().Get()])
    return m


def _watch_stage():
    stage = Usd.Stage.CreateInMemory()
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    UsdGeom.Xform.Define(stage, "/World")
    UsdGeom.Xform.Define(stage, "/World/W")
    _mesh(stage, "/World/W/Case", [((-0.02, -0.02, -0.006), (0.02, 0.02, 0.004))])
    _mesh(stage, "/World/W/Back", [((-0.022, -0.022, -0.008), (0.022, 0.022, -0.0065))])   # a bigger disc behind
    _mesh(stage, "/World/W/Dial", [((-0.016, -0.016, 0.004), (0.016, 0.016, 0.0045))])
    _mesh(stage, "/World/W/Glass", [((-0.017, -0.017, 0.007), (0.017, 0.017, 0.0075))])   # a disc in front
    _rotated_bar(stage, "/World/W/Minute", 0.014, 0.0012, 0.005, 45.0)                     # diagonal
    _rotated_bar(stage, "/World/W/MinuteGold", 0.014, 0.0012, 0.005, 45.0)                  # same hand, 2nd material
    _rotated_bar(stage, "/World/W/Hour", 0.009, 0.0015, 0.0055, 200.0)
    _mesh(stage, "/World/W/Logo", [((-0.006, -0.001, 0.0046), (0.006, 0.001, 0.0048))])    # centred: not a hand
    _mesh(stage, "/World/W/Crown", [((0.02, -0.002, -0.003), (0.024, 0.002, 0.001))])
    return stage


def test_watch_finds_dial_crown_and_hands_and_gears_the_hour_hand():
    from watch_draft import propose_watch

    spec, notes = propose_watch(_watch_stage(), "/World/W", {})
    name = lambda p: p.rsplit("/", 1)[-1]  # noqa: E731
    moving = {j["name"]: j for j in spec["joints"] if j["joint_type"] == "revolute"}
    assert name(spec["_analysis"]["dial"]) == "Dial" and name(spec["_analysis"]["case"]) == "Case"
    assert name(moving["crown"]["child_prim"]) == "Crown" and moving["crown"]["axis"] == "X"
    assert name(moving["hand_0"]["child_prim"]).startswith("Minute") and name(moving["hand_1"]["child_prim"]) == "Hour"
    assert moving["hand_0"]["axis"] == "Z" and moving["hand_0"]["anchor"][:2] == pytest.approx([0, 0], abs=1e-4)
    assert spec["mechanisms"] == [{"type": "couple", "follower": "hand_1", "leader": "hand_0",
                                   "gearing": round(-1 / 12, 6)}]
    fixed = {name(j["child_prim"]): name(j["parent_prim"]) for j in spec["joints"] if j["joint_type"] == "fixed"}
    assert fixed["Logo"] == "Case" and fixed["Glass"] == "Case"
    twin = {"Minute", "MinuteGold"} - {name(moving["hand_0"]["child_prim"])}
    assert name(next(iter(twin))) in fixed and fixed[next(iter(twin))].startswith("Minute")


# --- rules across assets ------------------------------------------------------

def _jointed_asset(path, camel, joint_type, joint_name):
    stage = Usd.Stage.CreateNew(str(path))
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    UsdGeom.Xform.Define(stage, "/World")
    stage.SetDefaultPrim(stage.GetPrimAtPath("/World"))
    UsdGeom.Xform.Define(stage, f"/World/{camel}")
    _mesh(stage, f"/World/{camel}/Base", [((0, 0, 0), (0.1, 0.1, 0.1))])
    _mesh(stage, f"/World/{camel}/Moving", [((0, 0, 0.1), (0.1, 0.1, 0.2))])
    j = (UsdPhysics.RevoluteJoint if joint_type == "revolute" else UsdPhysics.PrismaticJoint).Define(
        stage, f"/World/{camel}/Joints/{joint_name}")
    j.CreateBody0Rel().SetTargets([f"/World/{camel}/Base"])
    j.CreateBody1Rel().SetTargets([f"/World/{camel}/Moving"])
    stage.GetRootLayer().Save()


def test_a_rule_gates_one_assets_joint_on_anothers_in_a_composed_scene(tmp_path, monkeypatch):
    import ingest_asset
    from compose_scene import compose

    monkeypatch.setattr(ingest_asset, "QUEUE_DIR", tmp_path)
    for aid, camel, kind, name in (("lift_door", "LiftDoor", "revolute", "door_hinge"),
                                   ("call_panel", "CallPanel", "prismatic", "button_00")):
        _jointed_asset(tmp_path / f"{aid}.usda", camel, kind, name)
        (tmp_path / f"{aid}.json").write_text(json.dumps({"asset_id": aid, "file": str(tmp_path / f"{aid}.usda")}))
    made = compose(str(tmp_path / "scene.usda"), [("lift_door", ()), ("call_panel", (0.9, 0.0, 0.6))],
                   [("lift_door", "door_hinge", "call_panel", "button_00", 0.004)])
    scene = Usd.Stage.Open(made["scene"])
    hinge = scene.GetPrimAtPath(made["rules"][0]["gated"])
    gate = dict(hinge.GetCustomDataByKey("simReady:gate"))
    assert gate == {"mechanism": "rule", "actuator_joint": "/World/CallPanel/CallPanel/Joints/button_00",
                    "engage": 0.004, "action": "open"}
    # the referenced joints still point at their own asset's bodies
    button = scene.GetPrimAtPath(gate["actuator_joint"])
    assert str(UsdPhysics.Joint(button).GetBody1Rel().GetTargets()[0]) == "/World/CallPanel/CallPanel/Moving"
    assert scene.GetPrimAtPath("/World/CallPanel").GetAttribute("xformOp:translate").Get() == Gf.Vec3d(0.9, 0, 0.6)


# --- turntables ---------------------------------------------------------------

def _turntable_stage():
    stage = Usd.Stage.CreateInMemory()
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    UsdGeom.Xform.Define(stage, "/World")
    UsdGeom.Xform.Define(stage, "/World/TT")
    _mesh(stage, "/World/TT/Plinth", [((-0.22, -0.18, 0.0), (0.22, 0.18, 0.08))])
    # a round platter: an octagonal prism
    import math
    m = UsdGeom.Mesh.Define(stage, "/World/TT/Platter")
    ring = [(-0.04 + 0.15 * math.cos(math.radians(a)), 0.15 * math.sin(math.radians(a))) for a in range(0, 360, 45)]
    pts = [Gf.Vec3f(x, y, z) for z in (0.08, 0.1) for x, y in ring]
    m.CreatePointsAttr(pts)
    m.CreateFaceVertexCountsAttr([8, 8] + [4] * 8)
    m.CreateFaceVertexIndicesAttr(list(range(8))[::-1] + list(range(8, 16))
                                  + [i for k in range(8) for i in (k, (k + 1) % 8, 8 + (k + 1) % 8, 8 + k)])
    # the tonearm parked on its rest beside the platter, its pivot housing a third of the way along
    _mesh(stage, "/World/TT/Arm", [((0.16, -0.12, 0.1), (0.17, 0.12, 0.11))])
    _mesh(stage, "/World/TT/Pivot", [((0.15, 0.04, 0.08), (0.18, 0.07, 0.115))])
    _mesh(stage, "/World/TT/Weight", [((0.155, 0.11, 0.095), (0.175, 0.13, 0.115))])
    # the dust cover standing open at the back, hinged at its lowest edge
    _mesh(stage, "/World/TT/Cover", [((-0.21, 0.17, 0.08), (0.21, 0.19, 0.42))])
    return stage


def test_turntable_platter_spins_tonearm_reaches_the_record_and_lid_closes_onto_the_plinth():
    import math

    from turntable_draft import propose_turntable

    spec, notes = propose_turntable(_turntable_stage(), "/World/TT", {})
    name = lambda p: p.rsplit("/", 1)[-1]  # noqa: E731
    j = {x["name"]: x for x in spec["joints"]}
    assert name(j["platter"]["child_prim"]) == "Platter" and j["platter"]["axis"] == "Z"
    assert j["platter"]["anchor"][:2] == pytest.approx([-0.04, 0.0], abs=2e-3)
    arm = j["tonearm"]
    assert name(arm["child_prim"]) == "Arm" and arm["axis"] == "Z"
    # pivot at the housing, not the arm's end
    assert arm["anchor"][:2] == pytest.approx([0.165, 0.055], abs=2e-3)
    swing = arm["lower_limit"] if abs(arm["lower_limit"]) > abs(arm["upper_limit"]) else arm["upper_limit"]
    # swinging the stylus end (y = -0.12) by the limit lands it near the lead-out groove
    a = math.radians(swing)
    sx, sy = 0.165 + (0.165 - 0.165) * math.cos(a) - (-0.12 - 0.055) * math.sin(a), 0.055 + (-0.12 - 0.055) * math.cos(a)
    assert math.hypot(sx + 0.04, sy) == pytest.approx(0.35 * 0.15, abs=0.01)
    fixed = {name(x["child_prim"]): name(x["parent_prim"]) for x in spec["joints"] if x["joint_type"] == "fixed"}
    assert fixed["Weight"] == "Arm" and fixed["Pivot"] == "Plinth"
    lid = j["lid"]
    assert name(lid["child_prim"]) == "Cover" and lid["axis"] == "X"
    assert max(abs(lid["lower_limit"]), abs(lid["upper_limit"])) == pytest.approx(90.0, abs=1.0)


# --- spring clips ---------------------------------------------------------------

def test_clothes_peg_halves_hinge_at_the_spring_and_are_held_shut_by_its_preload():
    import math

    from clip_draft import propose_clip

    stage = Usd.Stage.CreateInMemory()
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    UsdGeom.Xform.Define(stage, "/World")
    UsdGeom.Xform.Define(stage, "/World/Peg")
    # jaws (+Y) shut against each other; the handles (-Y) 6 mm apart, so
    # squeezing them opens the jaws until they meet, ~10 deg - short of the
    # class's 25 (opening further would put them through each other)
    _mesh(stage, "/World/Peg/Left", [((-0.008, 0.0, 0.0), (0.0, 0.036, 0.007)),
                                     ((-0.008, -0.036, 0.0), (-0.003, 0.0, 0.007))])
    _mesh(stage, "/World/Peg/Right", [((0.0, 0.0, 0.0), (0.008, 0.036, 0.007)),
                                      ((0.003, -0.036, 0.0), (0.008, 0.0, 0.007))])
    _mesh(stage, "/World/Peg/Spring", [((-0.006, -0.012, -0.001), (0.006, 0.008, 0.008))])
    spec, notes = propose_clip(stage, "/World/Peg", {"open_deg": 25, "preload_torque_nm": 0.12, "full_torque_nm": 0.35})
    hinge = spec["joints"][0]
    assert hinge["axis"] == "Z" and hinge["anchor"][:2] == pytest.approx([0.0, -0.002], abs=1e-4)
    k, target = hinge["stiffness"], hinge["target"]
    lo, hi = hinge["lower_limit"], hinge["upper_limit"]
    open_end = lo if abs(lo) > abs(hi) else hi
    assert 7.0 <= abs(open_end) <= 12.0 and 0.0 in (lo, hi)
    # at rest (closed, 0) the spring presses shut with the preload; fully open with the full torque
    assert k * abs(target) == pytest.approx(0.12, rel=1e-2)
    assert k * abs(target - open_end) == pytest.approx(0.35, rel=1e-2)
    # opening swings the child's +Y end away from the parent, never into it
    child = "Right" if hinge["child_prim"].endswith("Right") else "Left"
    side = 1 if child == "Right" else -1
    a = math.radians(open_end)
    x_tip = 0.004 * math.cos(a) - (0.036 + 0.002) * math.sin(a)
    assert x_tip * side > 0.004
    fixed = {j["child_prim"].rsplit("/", 1)[-1] for j in spec["joints"] if j["joint_type"] == "fixed"}
    assert fixed == {"Spring"}


# --- power drills -------------------------------------------------------------------

def test_drill_chuck_is_the_front_round_part_and_the_trigger_pulls_toward_the_handle():
    from drill_draft import propose_drill

    stage = Usd.Stage.CreateInMemory()
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    UsdGeom.Xform.Define(stage, "/World")
    UsdGeom.Xform.Define(stage, "/World/D")
    _mesh(stage, "/World/D/Body", [((-0.05, -0.03, 0.14), (0.06, 0.03, 0.19)),     # barrel / motor
                                   ((0.0, -0.02, 0.04), (0.04, 0.02, 0.14))])      # handle
    _mesh(stage, "/World/D/Collar", [((-0.085, -0.02, 0.145), (-0.05, 0.02, 0.185))])  # clutch: wide, bulky
    _mesh(stage, "/World/D/Chuck", [((-0.12, -0.016, 0.149), (-0.085, 0.016, 0.181))])
    _mesh(stage, "/World/D/Bit", [((-0.16, -0.003, 0.162), (-0.12, 0.003, 0.168))])
    _mesh(stage, "/World/D/Trigger", [((-0.02, -0.006, 0.105), (0.0, 0.006, 0.13))])
    _mesh(stage, "/World/D/Battery", [((-0.03, -0.04, 0.0), (0.07, 0.04, 0.045))])
    spec, notes = propose_drill(stage, "/World/D", {"trigger_travel_m": 0.01})
    name = lambda p: p.rsplit("/", 1)[-1]  # noqa: E731
    j = {x["name"]: x for x in spec["joints"]}
    assert name(j["chuck"]["child_prim"]) == "Chuck" and j["chuck"]["axis"] == "X"
    assert name(j["clutch"]["child_prim"]) == "Collar"
    fixed = {name(x["child_prim"]): name(x["parent_prim"]) for x in spec["joints"] if x["joint_type"] == "fixed"}
    assert fixed["Bit"] == "Chuck"
    trig = j["trigger"]
    # the front is -X, so pulling the trigger moves it +X, toward the handle; travel capped at 8 mm (0.4 x 20 mm)
    assert (trig["lower_limit"], trig["upper_limit"]) == pytest.approx((0.0, 0.008))
    assert [name(m["part"]) for m in spec["mechanisms"] if m["type"] == "press_fit"] == ["Battery"]


def test_knobs_turn_and_buttons_press_where_the_class_has_knobs():
    from button_draft import propose_buttons

    stage = Usd.Stage.CreateInMemory()
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    UsdGeom.Xform.Define(stage, "/World")
    UsdGeom.Xform.Define(stage, "/World/Deck")
    _mesh(stage, "/World/Deck/Body", [((-0.2, -0.12, 0.0), (0.2, 0.12, 0.1))])
    _mesh(stage, "/World/Deck/Knob", [((0.0, -0.132, 0.024), (0.014, -0.12, 0.038))])     # round, 12 mm proud
    _mesh(stage, "/World/Deck/Play", [((-0.015, -0.1245, 0.06), (0.008, -0.12, 0.07))])  # flat and wide
    with_knobs, _ = propose_buttons(stage, "/World/Deck", {"knobs": True, "knob_range_deg": 150})
    roles = {j["child_prim"].rsplit("/", 1)[-1]: j for j in with_knobs["joints"] if j.get("_role")}
    assert roles["Knob"]["joint_type"] == "revolute" and roles["Knob"]["axis"] == "Y"
    assert (roles["Knob"]["lower_limit"], roles["Knob"]["upper_limit"]) == (-150.0, 150.0)
    assert roles["Play"]["joint_type"] == "prismatic"
    without, _ = propose_buttons(stage, "/World/Deck", {})
    assert all(j["joint_type"] != "revolute" for j in without["joints"])   # a remote's tall button stays a button


def test_a_door_sold_without_a_frame_hangs_on_the_edge_away_from_its_handle():
    from door_draft import propose_door

    stage = Usd.Stage.CreateInMemory()
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    UsdGeom.Xform.Define(stage, "/World")
    UsdGeom.Xform.Define(stage, ROOT)
    _mesh(stage, f"{ROOT}/Leaf", [((-0.45, -0.02, 0.0), (0.45, 0.02, 2.0))])
    _mesh(stage, f"{ROOT}/Handle", [((-0.42, -0.08, 0.95), (-0.3, -0.02, 1.05))])   # near the -X edge
    spec, notes = propose_door(stage, ROOT)
    hinge = next(j for j in spec["joints"] if j["name"] == "door_hinge")
    assert hinge["child_prim"].endswith("Leaf") and hinge["parent_prim"].endswith("HingeMount")
    assert hinge["anchor"][0] == pytest.approx(0.45, abs=1e-3)    # the +X edge, away from the handle
    assert spec["fixed_base"] is True and any("frameless" in n for n in notes)
    mount = stage.GetPrimAtPath(hinge["parent_prim"])
    lo = min(p[0] for p in UsdGeom.Mesh(mount).GetPointsAttr().Get())
    assert lo > 0.45                                                # just outside the hinge edge


# --- cabinets and syringes -------------------------------------------------------------

def test_cabinet_drawers_slide_out_of_their_face_and_doors_hinge_away_from_the_handle():
    from cabinet_draft import propose_cabinet

    stage = Usd.Stage.CreateInMemory()
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    UsdGeom.Xform.Define(stage, "/World")
    UsdGeom.Xform.Define(stage, "/World/C")
    _mesh(stage, "/World/C/Carcass", [((-0.3, -0.25, 0.0), (0.3, 0.25, 0.9))])
    # two drawers on the -Y face: wide, thin fronts with boxes behind and handles in front
    for i, (z0, z1) in enumerate(((0.75, 0.88), (0.6, 0.73))):
        _mesh(stage, f"/World/C/Front{i}", [((-0.28, -0.27, z0), (0.28, -0.25, z1))])
        _mesh(stage, f"/World/C/Box{i}", [((-0.26, -0.25, z0 + 0.01), (0.26, 0.2, z1 - 0.01))])
        _mesh(stage, f"/World/C/Pull{i}", [((-0.05, -0.29, z0 + 0.05), (0.05, -0.27, z0 + 0.07))])
    # a tall door below, its knob on the +X side
    _mesh(stage, "/World/C/Door", [((-0.15, -0.27, 0.05), (0.15, -0.25, 0.55))])
    _mesh(stage, "/World/C/Knob", [((0.1, -0.3, 0.3), (0.13, -0.27, 0.33))])
    spec, notes = propose_cabinet(stage, "/World/C", {"door_swing_deg": 100})
    j = {x["name"]: x for x in spec["joints"]}
    assert j["drawer_00"]["axis"] == "Y" and j["drawer_00"]["lower_limit"] < 0 == j["drawer_00"]["upper_limit"]
    # out by 80% of the box's depth (0.45 m)
    assert j["drawer_00"]["lower_limit"] == pytest.approx(-0.36, abs=1e-3)
    riders = {x["child_prim"].rsplit("/", 1)[-1]: x["parent_prim"].rsplit("/", 1)[-1]
              for x in spec["joints"] if x["joint_type"] == "fixed"}
    assert riders["Box0"] == "Front0" and riders["Pull0"] == "Front0" and riders["Pull1"] == "Front1"
    door = j["door_00"]
    # hinged on the -X edge, away from the knob; the knob edge swings out toward -Y
    assert door["axis"] == "Z" and door["anchor"][0] == pytest.approx(-0.15)
    assert riders["Knob"] == "Door"
    import math
    a = math.radians(door["lower_limit"] if door["lower_limit"] else door["upper_limit"])
    knob_edge_y = -0.27 + 0.3 * math.sin(a)     # the free edge, 0.3 m from the hinge along +X, after the swing
    assert knob_edge_y < -0.27


def test_syringe_plunger_slides_into_the_barrel_on_friction():
    from pipette_draft import propose_pipette

    stage = Usd.Stage.CreateInMemory()
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    UsdGeom.Xform.Define(stage, "/World")
    UsdGeom.Xform.Define(stage, "/World/S")
    _mesh(stage, "/World/S/Barrel", [((-0.006, -0.006, 0.0), (0.006, 0.006, 0.08))])
    _mesh(stage, "/World/S/Rod", [((-0.004, -0.004, 0.05), (0.004, 0.004, 0.13))])     # half in, half out
    _mesh(stage, "/World/S/Needle", [((-0.0005, -0.0005, -0.03), (0.0005, 0.0005, 0.0))])
    spec, notes = propose_pipette(stage, "/World/S", {"single_stage": True})
    p = next(x for x in spec["joints"] if x["name"] == "plunger")
    assert p["child_prim"].endswith("Rod") and p["axis"] == "Z" and p["stiffness"] == 0.0
    # in until the seal (z 0.05) nears the barrel's tip end (z 0): 95% of 50 mm; out until it
    # nears the barrel's back (z 0.08): 90% of 30 mm
    assert (p["lower_limit"], p["upper_limit"]) == pytest.approx((-0.0475, 0.027), abs=1e-4)
    assert not spec["mechanisms"]
    fixed = {x["child_prim"].rsplit("/", 1)[-1]: x["name"] for x in spec["joints"] if x["joint_type"] == "fixed"}
    assert fixed["Needle"] == "needle"


def test_a_syringe_standing_tip_up_pushes_its_plunger_upward():
    from pipette_draft import propose_pipette

    stage = Usd.Stage.CreateInMemory()
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    UsdGeom.Xform.Define(stage, "/World")
    UsdGeom.Xform.Define(stage, "/World/S")
    _mesh(stage, "/World/S/Barrel", [((-0.014, -0.025, 0.02), (0.014, 0.025, 0.141))])   # flange makes it widest
    _mesh(stage, "/World/S/Plunger", [((-0.014, -0.014, 0.0), (0.014, 0.014, 0.128))])  # out the bottom
    spec, notes = propose_pipette(stage, "/World/S", {"single_stage": True})
    p = spec["joints"][0]
    assert p["child_prim"].endswith("Plunger") and p["axis"] == "Z"
    # push: seal 0.128 up to the tip 0.141 (95% of 13 mm); pull: down toward the back 0.02 (90% of 108 mm)
    assert (p["lower_limit"], p["upper_limit"]) == pytest.approx((-0.0972, 0.01235), abs=1e-4)


def test_eyeglass_temples_fold_inward_about_the_front_corners():
    import math

    from temples_draft import propose_temples

    stage = Usd.Stage.CreateInMemory()
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    UsdGeom.Xform.Define(stage, "/World")
    UsdGeom.Xform.Define(stage, "/World/G")
    _mesh(stage, "/World/G/Front", [((0.068, -0.075, 0.03), (0.076, 0.075, 0.07))])
    _mesh(stage, "/World/G/Left", [((-0.077, -0.076, 0.045), (0.068, -0.073, 0.068))])
    _mesh(stage, "/World/G/Right", [((-0.077, 0.073, 0.045), (0.068, 0.076, 0.068))])
    _mesh(stage, "/World/G/LeftTip", [((-0.07, -0.0758, 0.05), (-0.04, -0.0732, 0.06))])
    spec, notes = propose_temples(stage, "/World/G", {"fold_deg": 95})
    j = {x["name"]: x for x in spec["joints"]}
    for t in ("temple_0", "temple_1"):
        hinge = j[t]
        assert hinge["axis"] == "Z" and hinge["anchor"][0] == pytest.approx(0.068, abs=1e-3)
        fold = hinge["lower_limit"] or hinge["upper_limit"]
        y0 = hinge["anchor"][1]
        a = math.radians(fold)
        far_y = y0 + (-0.145) * math.sin(a)         # the far end, 0.145 m back along -X, after folding
        assert abs(far_y) < abs(y0)                 # swung toward the middle
    riders = {x["child_prim"].rsplit("/", 1)[-1]: x["parent_prim"].rsplit("/", 1)[-1]
              for x in spec["joints"] if x["joint_type"] == "fixed"}
    assert riders["LeftTip"] == "Left"


# --- multirotor propellers ------------------------------------------------------

def test_drone_rotors_spin_over_their_motors_and_the_motors_stay_on_the_frame():
    from rotor_draft import propose_rotors

    stage = Usd.Stage.CreateInMemory()
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    UsdGeom.Xform.Define(stage, "/World")
    UsdGeom.Xform.Define(stage, "/World/Q")
    _mesh(stage, "/World/Q/Frame", [((-0.04, -0.04, 0.0), (0.04, 0.04, 0.03))])
    stations = [(0.09, 0.09), (-0.09, 0.09), (-0.09, -0.09), (0.09, -0.09)]
    for i, (x, y) in enumerate(stations):
        _mesh(stage, f"/World/Q/Arm{i}", [((min(0, x), y - 0.004, 0.01), (max(0, x), y + 0.004, 0.016))])
        _mesh(stage, f"/World/Q/Motor{i}", [((x - 0.015, y - 0.015, 0.0), (x + 0.015, y + 0.015, 0.03))])
        _mesh(stage, f"/World/Q/Bell{i}", [((x - 0.012, y - 0.012, 0.03), (x + 0.012, y + 0.012, 0.036))])
        _mesh(stage, f"/World/Q/Prop{i}", [((x - 0.06, y - 0.006, 0.036), (x + 0.06, y + 0.006, 0.039))])
    _mesh(stage, "/World/Q/Spinner0", [((0.085, 0.085, 0.039), (0.095, 0.095, 0.045))])
    spec, notes = propose_rotors(stage, "/World/Q", {"rpm": 5000})
    name = lambda p: p.rsplit("/", 1)[-1]  # noqa: E731
    rotors = [j for j in spec["joints"] if j["joint_type"] == "revolute"]
    assert len(rotors) == 4 and all(j["axis"] == "Z" for j in rotors)
    assert sorted(name(j["child_prim"]) for j in rotors) == ["Prop0", "Prop1", "Prop2", "Prop3"]
    for j in rotors:
        x, y = stations[int(name(j["child_prim"])[-1])]
        assert j["anchor"][:2] == pytest.approx([x, y], abs=2e-3)
    parent = {name(j["child_prim"]): name(j["parent_prim"]) for j in spec["joints"]}
    assert parent["Spinner0"] == "Prop0"                       # the cap turns with its prop
    assert all(parent[f"Motor{i}"] == "Frame" for i in range(4))
    assert len(parent) == 4 * 4 + 1                             # every part but the frame is jointed


# --- keyboards: keys fused into one mesh, keys sitting in a deck ----------------

def _keyboard_stage(fused: bool):
    """A body with a raised back edge (taller than the keys) and 3 x 4 keys
    sitting on the lower deck, each with a legend on top."""
    stage = Usd.Stage.CreateInMemory()
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    UsdGeom.Xform.Define(stage, "/World")
    UsdGeom.Xform.Define(stage, "/World/K")
    _mesh(stage, "/World/K/Body", [((-0.12, -0.05, 0.0), (0.12, 0.05, 0.01)),       # the deck
                                   ((-0.12, 0.05, 0.0), (0.12, 0.06, 0.025))])      # raised back edge
    keys = []
    for r in range(3):
        for c in range(4):
            x, y = -0.06 + 0.04 * c, -0.03 + 0.03 * r
            keys.append(((x - 0.009, y - 0.009, 0.008), (x + 0.009, y + 0.009, 0.016)))
            keys.append(((x - 0.003, y - 0.003, 0.016), (x + 0.003, y + 0.003, 0.0165)))  # legend
    if fused:
        _mesh(stage, "/World/K/Keys", keys)
    else:
        for i in range(0, len(keys), 2):
            _mesh(stage, f"/World/K/Key{i // 2:02d}", [keys[i]])
            _mesh(stage, f"/World/K/Legend{i // 2:02d}", [keys[i + 1]])
    return stage


def test_key_split_makes_one_part_a_key_with_its_legend():
    from segment_mesh import split_keys

    stage = _keyboard_stage(fused=True)
    parts = split_keys(stage, "/World/K/Keys")
    assert len(parts) == 12
    assert not stage.GetPrimAtPath("/World/K/Keys").IsActive()
    for p in parts:                                   # cap + legend: 2 boxes, 12 faces
        assert len(UsdGeom.Mesh(stage.GetPrimAtPath(p)).GetFaceVertexCountsAttr().Get()) == 12


def test_keys_below_a_raised_back_edge_press_into_the_deck_they_sit_on():
    from button_draft import propose_buttons

    spec, notes = propose_buttons(_keyboard_stage(fused=False), "/World/K", {"max_travel_m": 0.003})
    buttons = [j for j in spec["joints"] if j.get("_role") == "button"]
    assert len(buttons) == 12 and all(j["axis"] == "Z" for j in buttons)
    riders = {j["child_prim"].rsplit("/", 1)[-1]: j["parent_prim"].rsplit("/", 1)[-1]
              for j in spec["joints"] if j["joint_type"] == "fixed"}
    assert riders["Legend05"] == "Key05"


def test_joints_past_an_articulations_link_limit_become_maximal(tmp_path, monkeypatch):
    import asset_review_hub as hub

    monkeypatch.setattr(hub, "ARTICULATION_MAX_JOINTS", 60)   # the switch is off by default

    stage = Usd.Stage.CreateNew(str(tmp_path / "kb.usda"))
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    UsdGeom.Xform.Define(stage, "/World")
    UsdGeom.Xform.Define(stage, "/World/Kb")
    _mesh(stage, "/World/Kb/Body", [((-0.3, -0.1, 0.0), (0.3, 0.1, 0.01))])
    joints = []
    for i in range(70):
        x = -0.28 + 0.008 * i
        _mesh(stage, f"/World/Kb/Key{i:02d}", [((x, -0.003, 0.01), (x + 0.006, 0.003, 0.014))])
        joints.append({"name": f"key_{i:02d}", "joint_type": "prismatic", "parent_prim": "/World/Kb/Body",
                       "child_prim": f"/World/Kb/Key{i:02d}", "axis": "Z", "lower_limit": -0.003,
                       "upper_limit": 0.0})
    _mesh(stage, "/World/Kb/Legend", [((0.27, -0.001, 0.014), (0.273, 0.001, 0.0145))])
    joints.append({"name": "trim_on_key_69", "joint_type": "fixed", "parent_prim": "/World/Kb/Key69",
                   "child_prim": "/World/Kb/Legend"})
    stage.GetRootLayer().Save()
    monkeypatch.setattr(hub, "_re_ingest", lambda entry: entry.setdefault("report", {"verdict": "ok"}))
    entry = {"asset_id": "kb", "file": str(tmp_path / "kb.usda")}
    spec = {"prim_path": "/World/Kb", "fixed_base": False, "approximation": "convexHull", "joints": joints}
    hub.apply_articulation(entry, json.dumps(spec))
    stage = Usd.Stage.Open(str(tmp_path / "kb.usda"))
    out = {p.GetName() for p in stage.Traverse() if p.IsA(UsdPhysics.Joint)
           and p.GetAttribute("physics:excludeFromArticulation").Get()}
    assert out == {f"key_{i:02d}" for i in range(hub.ARTICULATION_MAX_JOINTS, 70)} | {"trim_on_key_69"}


def test_pliers_without_a_pin_pivot_where_the_arms_cross_not_mid_handle():
    from pivot_draft import propose_pivot

    stage = Usd.Stage.CreateInMemory()
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    UsdGeom.Xform.Define(stage, "/World")
    UsdGeom.Xform.Define(stage, "/World/P")
    # two arms crossing at the origin: handles 0.15 m out at +-20 deg off -X,
    # jaws 0.04 m the other way; both run the tool's length, so the middle of
    # their bounding boxes' overlap is mid-handle (x ~ -0.05)
    _rotated_bar(stage, "/World/P/ArmA", 0.15, 0.012, 0.0, 160.0, tail=0.04 / 0.15)
    _rotated_bar(stage, "/World/P/ArmB", 0.15, 0.012, 0.0004, 200.0, tail=0.04 / 0.15)
    spec, notes = propose_pivot(stage, "/World/P", {"open_deg": 30})
    pivot = next(j for j in spec["joints"] if j["name"] == "pivot")
    assert pivot["axis"] == "Z"
    assert pivot["anchor"][:2] == pytest.approx([0.0, 0.0], abs=0.01)


# --- where a turning part meets its mate (PhysX never collides joined links) ---

def _blades(stacked: bool):
    """Two bars 4 mm wide and 3 mm thick reaching from 30 mm to 100 mm out
    from a pin at the origin, 20 deg apart (clear of each other at rest): a
    layer apart (scissor blades) or in one layer (pliers' jaws). In one layer
    they meet when 2*asin(0.002/0.03) ~ 7.6 deg apart, after ~12 deg."""
    stage = Usd.Stage.CreateInMemory()
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    UsdGeom.Xform.Define(stage, "/World")
    UsdGeom.Xform.Define(stage, "/World/S")
    _mesh(stage, "/World/S/A", [((0.03, -0.002, 0.0), (0.1, 0.002, 0.003))])
    b = _mesh(stage, "/World/S/B", [((0.03, -0.002, 0.003 if stacked else 0.0), (0.1, 0.002, 0.006 if stacked else 0.003))])
    import math
    c, s = math.cos(math.radians(20)), math.sin(math.radians(20))
    b.GetPointsAttr().Set([Gf.Vec3f(c * p[0] - s * p[1], s * p[0] + c * p[1], p[2]) for p in b.GetPointsAttr().Get()])
    return stage


def test_a_turning_part_meets_its_mate_in_one_layer_and_slides_past_it_a_layer_apart():
    from swing_contact import swing_until_contact

    # B lies 20 deg counter-clockwise of A; turning it clockwise (-) closes on A
    one_layer = swing_until_contact(_blades(False), ["/World/S/A"], ["/World/S/B"], (0, 0, 0.0015), 2, -1.0, 0.11)
    assert one_layer is not None and 9.0 <= one_layer <= 15.0
    assert swing_until_contact(_blades(False), ["/World/S/A"], ["/World/S/B"], (0, 0, 0.0015), 2, 1.0, 0.11,
                               max_deg=90) is None
    stacked = swing_until_contact(_blades(True), ["/World/S/A"], ["/World/S/B"], (0, 0, 0.003), 2, -1.0, 0.11)
    assert stacked is None


# --- behaviors: what an object does when worked ------------------------------------

def test_a_drill_motor_runs_by_trigger_switch_and_battery():
    from behaviors import motor

    b = {"output": "chuck", "throttle": "trigger", "throttle_full": -0.01, "direction": "direction",
         "direction_forward": 0.003, "max_rpm": 1500.0, "forward_sign": -1.0}
    run = lambda trig, sw, powered=True: motor(b, {"trigger": trig, "direction": sw}, powered)["chuck"]  # noqa: E731
    assert run(-0.01, 0.003) == pytest.approx(-9000.0)          # full squeeze, forward: clockwise from behind
    assert run(-0.0025, 0.003) == pytest.approx(-2250.0)        # speed follows the trigger
    assert run(-0.01, -0.003) == pytest.approx(9000.0)          # reverse
    assert run(-0.01, 0.0) == 0.0                               # centred: locked
    assert run(-0.01, 0.003, powered=False) == 0.0              # battery off
    assert run(0.002, 0.003) == 0.0                             # pushed the wrong way: nothing


def test_a_wheeled_base_turns_its_sides_apart_to_turn():
    from behaviors import wheeled_base

    b = {"drive_left": ["wl"], "drive_right": ["wr"], "radius_m": 0.3, "track_m": 0.6,
         "forward_sign": {"wl": 1.0, "wr": -1.0}}
    straight = wheeled_base(b, 0.3, 0.0)
    assert straight["wl"] == pytest.approx(math.degrees(1.0)) and straight["wr"] == pytest.approx(-math.degrees(1.0))
    turn = wheeled_base(b, 0.0, 1.0)                            # left: left side back, right side forward
    assert turn["wl"] == pytest.approx(-math.degrees(1.0)) and turn["wr"] == pytest.approx(-math.degrees(1.0))


def test_what_an_object_should_do_comes_from_its_class_and_what_the_vlm_saw(monkeypatch, tmp_path):
    import behaviors

    priors = tmp_path / "priors.json"
    priors.write_text(json.dumps({"classes": {"power_drill": {"behaviors": ["motor"]}, "box": {}}}))
    monkeypatch.setattr(behaviors, "PRIORS_PATH", priors)
    drill = {"class_hint": "power_drill", "articulation_draft": json.dumps({
        "joints": [{"name": "chuck"}, {"name": "trigger"}],
        "behaviors": [{"type": "motor", "output": "chuck", "throttle": "trigger"}]})}
    assert behaviors.check(drill)["motor"]["ok"]
    no_trigger = dict(drill, articulation_draft=json.dumps({"joints": [{"name": "chuck"}]}))
    r = behaviors.check(no_trigger)["motor"]
    assert not r["ok"] and not r["present"]
    seen = {"class_hint": "box", "vlm": {"functions": [{"does": "opens once the key turns", "kind": "gate"}]}}
    r = behaviors.check(seen)
    assert set(r) == {"gate"} and r["gate"]["why"].startswith("seen") and not r["gate"]["ok"]


def test_articulate_asset_warns_when_a_limit_runs_past_where_the_parts_meet(capsys):
    """Through the plain tool (and MCP): a hand-written limit that closes two
    same-layer bars 18 deg when they meet after ~12 is flagged, not applied
    silently; the same limit on bars a layer apart is not."""
    for stacked, flagged in ((False, True), (True, False)):
        stage = _blades(stacked)
        spec = {"prim_path": "/World/S", "fixed_base": False, "joints": [
            {"name": "pivot", "joint_type": "revolute", "parent_prim": "/World/S/A", "child_prim": "/World/S/B",
             "axis": "Z", "lower_limit": -18.0, "upper_limit": 0.0, "anchor": [0.0, 0.0, 0.003]}]}
        capsys.readouterr()
        _articulate(stage, spec)
        result = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
        assert bool(result.get("over_travel")) is flagged, result.get("warnings")
