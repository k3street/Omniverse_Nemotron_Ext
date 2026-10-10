"""The asset-convention tools (MCP / chat): argument handling and the job file
round trip. The conventions themselves are tested in test_door_mechanism.py."""
import asyncio
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from service.isaac_assist_service.chat.tools.handlers import asset_conventions as ac  # noqa: E402

pytestmark = pytest.mark.l0


def run(coro):
    return asyncio.run(coro)


def test_the_tools_are_registered():
    data, codegen = {}, {}
    ac.register(data, codegen)
    assert set(data) == {"draft_asset_articulation", "apply_asset_articulation", "unarticulate_asset",
                         "check_asset_behaviors", "verify_asset_motion", "process_downloads", "get_asset_job",
                         "reprocess_assets"}
    from service.isaac_assist_service.chat.tools.tool_schemas import ISAAC_SIM_TOOLS
    names = {t["function"]["name"] for t in ISAAC_SIM_TOOLS}
    assert set(data) <= names


def test_a_missing_asset_id_is_an_error_not_a_crash():
    for h in (ac._handle_draft_asset_articulation, ac._handle_check_asset_behaviors,
              ac._handle_verify_asset_motion, ac._handle_get_asset_job):
        assert "required" in run(h({}))["error"]


def test_an_unknown_asset_says_to_ingest_it_first():
    r = run(ac._handle_check_asset_behaviors({"asset_id": "no_such_asset_zz"}))
    assert "ingest it first" in r["error"]


def test_a_finished_job_is_read_back(tmp_path, monkeypatch):
    jobs = REPO / "workspace" / "asset_jobs"
    jobs.mkdir(parents=True, exist_ok=True)
    f = jobs / "test_job_zz.json"
    f.write_text(json.dumps({"job_id": "test_job_zz", "status": "done", "result": {"joints_reached": "1/1"}}))
    try:
        r = run(ac._handle_get_asset_job({"job_id": "test_job_zz"}))
        assert r["status"] == "done" and r["result"]["joints_reached"] == "1/1"
    finally:
        f.unlink()


def test_a_fix_makes_its_stage_and_the_stages_after_it_stale(monkeypatch):
    sys.path.insert(0, str(REPO / "scripts"))
    import processing

    monkeypatch.setattr(processing, "_prior", lambda e: {"articulable": True, "behaviors": ["motor"]})
    e = {"asset_id": "x", "applied_fixes": ["articulate_asset: 3 joints"]}
    assert [s for s, _ in processing.stale(e)][:3] == ["ingest", "classify", "file"]   # before the ledger
    for st in ("ingest", "classify", "file", "materials", "behaviors", "verify", "critic", "approve"):
        processing.record(e, st)
    processing.record(e, "articulate", tier="pivot")
    processing.record(e, "verify")                       # after articulate, as a run does
    processing.record(e, "critic")
    processing.record(e, "approve")
    assert processing.stale(e) == []
    monkeypatch.setitem(processing.TIERS, "pivot", "2099-01-01")                       # a pliers fix
    st = dict(processing.stale(e))
    assert "rules" in st["articulate"] and st["verify"].startswith("after articulate")
    assert "ingest" not in st
    other = {"asset_id": "y", "applied_fixes": ["articulate_asset: 3 joints"]}
    for s in ("ingest", "classify", "file", "materials", "behaviors"):
        processing.record(other, s)
    processing.record(other, "articulate", tier="power_drill")
    for s in ("verify", "critic", "approve"):
        processing.record(other, s)
    assert processing.stale(other) == []                                                 # drills untouched
    # an unselected stale stage does not cascade (no VLM bill by surprise)
    monkeypatch.setitem(processing.RULES, "classify", "2099-01-01")
    assert processing.stale(other, ["verify", "critic"]) == []


def test_reprocess_defaults_to_a_dry_run():
    import asyncio as _a
    calls = {}

    async def fake(op, args, timeout=0):
        calls.update(op=op, args=args)
        return {"type": "data", "job_id": "j"}

    ac_run = ac._run
    try:
        ac._run = fake
        _a.run(ac._handle_reprocess_assets({"classes": ["pliers"]}))
    finally:
        ac._run = ac_run
    assert calls["args"]["kind"] == "reprocess" and "--dry-run" in calls["args"]["args"]
    assert calls["args"]["args"][-2:] == ["--classes", "pliers"]


def test_a_stage_that_ran_before_an_earlier_stage_reran_is_stale(monkeypatch):
    sys.path.insert(0, str(REPO / "scripts"))
    import processing

    monkeypatch.setattr(processing, "_prior", lambda e: {"articulable": True})
    e = {"asset_id": "x", "applied_fixes": ["articulate_asset: 2 joints"]}
    for st in processing.ORDER:
        processing.record(e, st, tier="pivot" if st == "articulate" else None)
    e["processing"]["verify"]["at"] = "2000-01-01T00:00:00"     # verified before the re-articulation
    e["processing"]["critic"]["at"] = "2000-01-01T00:00:00"
    st = dict(processing.stale(e, ["articulate", "verify", "critic"]))
    assert "articulate" not in st and st["verify"].startswith("older than")


def test_a_part_is_numbered_on_its_own_pixels_not_on_what_hides_it():
    np = pytest.importorskip("numpy")
    sys.path.insert(0, str(REPO / "scripts"))
    import part_survey

    img = np.zeros((60, 80, 3), np.uint8)
    green = np.array([44, 160, 44], float) / 255     # part 3's colour, lit and washed toward white
    img[10:50, 5:35] = np.round((0.7 * green + 0.3) * 255)
    red = np.array([214, 39, 40], float) / 255       # part 4
    img[10:50, 45:75] = np.round((0.9 * red + 0.05) * 255)
    spots = part_survey.visible_spots(img, [{"id": 3}, {"id": 4}, {"id": 13}])
    assert 5 <= spots[3][0] < 35 and 45 <= spots[4][0] < 75
    assert 13 not in spots and 13 in spots["_chroma"]    # light green: not in view, so no number


def test_an_internal_motor_is_not_an_expected_behavior():
    sys.path.insert(0, str(REPO / "scripts"))
    import behaviors

    e = {"class_hint": "no_such_class", "vlm": {"functions": [
        {"does": "brews", "moving_part": "internal pump (no external motion)", "kind": "motor"}]}}
    assert behaviors.expected(e) == {}


def test_set_bodies_above_articulated_links_are_taken_off():
    pytest.importorskip("pxr")
    from pxr import Usd, UsdGeom, UsdPhysics

    sys.path.insert(0, str(REPO / "scripts"))
    from asset_review_hub import _strip_set_bodies

    st = Usd.Stage.CreateInMemory()
    for p in ("/W/A", "/W/A/shell", "/W/A/lid", "/W/B", "/W/B/m"):
        UsdGeom.Xform.Define(st, p)
    for p in ("/W/A", "/W/B"):
        UsdPhysics.RigidBodyAPI.Apply(st.GetPrimAtPath(p))
    UsdPhysics.ArticulationRootAPI.Apply(st.GetPrimAtPath("/W/A"))
    j = UsdPhysics.FixedJoint.Define(st, "/W/Joints/object0_A")
    j.CreateBody0Rel().SetTargets(["/W/A"])
    spec = {"prim_path": "/W", "joints": [{"parent_prim": "/W/A/shell", "child_prim": "/W/A/lid"}]}
    assert _strip_set_bodies(st, spec) == 1
    assert not st.GetPrimAtPath("/W/A").HasAPI(UsdPhysics.RigidBodyAPI)
    assert not st.GetPrimAtPath("/W/A").HasAPI(UsdPhysics.ArticulationRootAPI)
    assert not st.GetPrimAtPath("/W/Joints/object0_A")
    assert st.GetPrimAtPath("/W/B").HasAPI(UsdPhysics.RigidBodyAPI)     # nothing articulated under it


def test_a_mirroring_transform_is_baked_into_points_with_the_world_unchanged():
    pytest.importorskip("pxr")
    from pxr import Gf, Usd, UsdGeom

    sys.path.insert(0, str(REPO / "scripts"))
    from unmirror import unmirror

    st = Usd.Stage.CreateInMemory()
    UsdGeom.Xform.Define(st, "/W")
    m = UsdGeom.Xform.Define(st, "/W/Mirror")
    m.AddTransformOp().Set(Gf.Matrix4d(1.0).SetScale(Gf.Vec3d(1, 1, -1)) * Gf.Matrix4d(1.0).SetTranslate((0.3, 0, 0)))
    c = UsdGeom.Xform.Define(st, "/W/Mirror/C")
    c.AddRotateXYZOp().Set(Gf.Vec3f(30, 10, 0))
    c.AddScaleOp().Set(Gf.Vec3f(2, 1, 1))
    mesh = UsdGeom.Mesh.Define(st, "/W/Mirror/C/M")
    pts = [(0, 0, 0), (1, 0, 0), (0, 1, 0), (0, 0, 1)]
    mesh.CreatePointsAttr(pts)
    mesh.CreateFaceVertexCountsAttr([3, 3])
    mesh.CreateFaceVertexIndicesAttr([0, 1, 2, 0, 2, 3])

    def world():
        w = UsdGeom.XformCache().GetLocalToWorldTransform(mesh.GetPrim())
        return [w.Transform(Gf.Vec3d(p)) for p in mesh.GetPointsAttr().Get()], w

    before, w0 = world()
    assert w0.ExtractRotationMatrix().GetDeterminant() < 0
    assert unmirror(st, "/W") == 1
    after, w1 = world()
    assert w1.ExtractRotationMatrix().GetDeterminant() > 0
    assert all((a - b).GetLength() < 1e-6 for a, b in zip(before, after))
    assert mesh.GetOrientationAttr().Get() == UsdGeom.Tokens.leftHanded
    assert unmirror(st, "/W") == 0                       # idempotent


def test_only_workspace_files_are_ours_to_write():
    sys.path.insert(0, str(REPO / "scripts"))
    from processing import owned

    assert owned(str(REPO / "workspace" / "assets_fixed" / "x_simready.usda"))
    assert not owned("/home/kimate/Desktop/assets/Lightwheel_OpenSource/Manipulation/Kettle.usd")


def test_a_surveyed_moving_part_left_fixed_is_missing():
    sys.path.insert(0, str(REPO / "scripts"))
    from motion_critic import completeness

    survey = {"parts": [
        {"id": 1, "path": "/A/frame", "role": "frame", "motion": "none"},
        {"id": 2, "path": "/A/wheel", "role": "front wheel", "motion": "spin"},
        {"id": 3, "path": "/A/post", "role": "seat post", "motion": "slide"},
        {"id": 4, "path": "/A/pump", "role": "internal pump", "motion": "spin"}]}
    spec = {"joints": [{"name": "slide_03", "joint_type": "prismatic", "parent_prim": "/A/frame",
                        "child_prim": "/A/post"},
                       {"name": "part_000", "joint_type": "fixed", "parent_prim": "/A/frame",
                        "child_prim": "/A/wheel"}], "mechanisms": [], "_analysis": {"notes": []}}
    c = completeness({"part_survey": survey, "articulation_draft": json.dumps(spec)})
    assert not c["ok"] and len(c["missing"]) == 1 and "front wheel" in c["missing"][0]   # the pump is internal


def test_a_complete_rigid_asset_passes_the_measured_gates(tmp_path, monkeypatch):
    pytest.importorskip("pxr")
    from pxr import Gf, Usd, UsdGeom, UsdPhysics, UsdShade

    sys.path.insert(0, str(REPO / "scripts"))
    import auto_approve

    f = tmp_path / "mug_simready.usda"
    st = Usd.Stage.CreateNew(str(f))
    UsdGeom.SetStageUpAxis(st, UsdGeom.Tokens.z)
    UsdGeom.SetStageMetersPerUnit(st, 1.0)
    w = UsdGeom.Xform.Define(st, "/World")
    st.SetDefaultPrim(w.GetPrim())
    root = UsdGeom.Xform.Define(st, "/World/Mug")
    UsdPhysics.RigidBodyAPI.Apply(root.GetPrim())
    UsdPhysics.MassAPI.Apply(root.GetPrim()).CreateMassAttr(0.3)
    cube = UsdGeom.Cube.Define(st, "/World/Mug/Body")
    cube.CreateSizeAttr(0.1)
    UsdPhysics.CollisionAPI.Apply(cube.GetPrim())
    m = UsdShade.Material.Define(st, "/World/Mug/PhysicsMaterials/ceramic")
    UsdPhysics.MaterialAPI.Apply(m.GetPrim())
    UsdShade.MaterialBindingAPI.Apply(cube.GetPrim()).Bind(m, materialPurpose="physics")
    st.GetRootLayer().Save()
    entry = {"asset_id": "mug_t", "file": str(f), "class_hint": "mug"}
    meas = auto_approve.measure(entry)
    assert meas["bodies"] == 1 and not meas["no_physics_material"] and meas["mass_kg"] == pytest.approx(0.3)
    import ingest_asset
    monkeypatch.setattr(ingest_asset, "run_report", lambda *a, **k: {"callouts": []})
    pri = tmp_path / "priors.json"
    pri.write_text(json.dumps({"classes": {"mug": {"max_dim_m": [0.08, 0.2], "mass_kg": [0.2, 0.5]}}}))
    monkeypatch.setattr(auto_approve, "PRIORS", pri)
    failed = [c["check"] for c in auto_approve.gates(entry, meas) if not c["ok"]]
    assert failed == []
    # mirrored or materialless: refused
    meas2 = dict(meas, no_physics_material=["Body"])
    assert "physics_complete" in [c["check"] for c in auto_approve.gates(entry, meas2) if not c["ok"]]


def test_a_shoe_with_a_soft_upper_is_not_a_rigid_body(monkeypatch):
    sys.path.insert(0, str(REPO / "scripts"))
    import processing
    from processing import soft_parts

    monkeypatch.setattr(processing, "_prior", lambda e: {})
    shoe = {"vlm": {"deformable_type": "cloth"}, "part_survey": {"parts": [
        {"id": 1, "path": "/S/sole", "role": "rubber outsole", "material": "rubber_natural", "motion": "none"},
        {"id": 2, "path": "/S/upper", "role": "knit upper", "material": "fabric_cotton", "motion": "none"},
        {"id": 3, "path": "/S/lace", "role": "laces", "material": "fabric_cotton", "motion": "flex"}]}}
    sp = soft_parts(shoe)
    assert sp["kind"] == "cloth" and sp["parts"] == ["knit upper", "laces"] and not sp["whole"]
    meter = soft_parts({"part_survey": {"parts": [
        {"id": 1, "path": "/M/case", "role": "case", "material": "plastic_abs", "motion": "none"},
        {"id": 2, "path": "/M/lead", "role": "test lead cable", "material": "rubber_soft", "motion": "flex"}]}})
    assert meter["kind"] is None and meter["accessories"] == ["test lead cable"]    # a cable does not make it soft
    # a felt pad and a paper decal on a chess set are not soft parts of it
    chess = soft_parts({"report": {"max_dim_m": 0.42}, "part_survey": {"parts": [
        {"id": 1, "path": "/C/board", "role": "board", "material": "wood_oak", "motion": "none", "size_m": [0.42, 0.42, 0.03]},
        {"id": 2, "path": "/C/felt", "role": "felt pad under a piece base", "material": "fabric_cotton", "motion": "none",
         "size_m": [0.005, 0.005, 0.0]},
        {"id": 3, "path": "/C/decal", "role": "printed crown marking", "material": "paper_kraft", "motion": "none",
         "size_m": [0.004, 0.004, 0.0]}]}})
    assert chess["kind"] is None and len(chess["accessories"]) == 2
    # the class knows even when nothing else says so
    monkeypatch.setattr(processing, "_prior", lambda e: {"soft_parts": ["upper"], "soft_kind": "cloth"})
    assert soft_parts({"asset_id": "x"})["parts"] == ["upper"]


def _shoe_stage(tmp_path):
    from pxr import Usd, UsdGeom, UsdPhysics

    st = Usd.Stage.CreateNew(str(tmp_path / "shoe_simready.usda"))
    UsdGeom.SetStageUpAxis(st, UsdGeom.Tokens.z)
    w = UsdGeom.Xform.Define(st, "/World")
    st.SetDefaultPrim(w.GetPrim())
    root = UsdGeom.Xform.Define(st, "/World/Shoe")
    UsdPhysics.RigidBodyAPI.Apply(root.GetPrim())
    UsdPhysics.MassAPI.Apply(root.GetPrim()).CreateMassAttr(0.4)

    def box(path, lo, hi, copies=1):
        m = UsdGeom.Mesh.Define(st, path)
        x0, y0, z0 = lo
        x1, y1, z1 = hi
        pts = [(x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0), (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)]
        quads = [0, 1, 2, 3, 4, 5, 6, 7, 0, 1, 5, 4, 2, 3, 7, 6, 1, 2, 6, 5, 0, 3, 7, 4]
        P, I, C = [], [], []
        for k in range(copies):               # several boxes apart: many faces, many islands
            off = len(P)
            P += [(x, y, z + 0.2 * k) for x, y, z in pts]
            I += [i + off for i in quads]
            C += [4] * 6
        m.CreatePointsAttr(P)
        m.CreateFaceVertexCountsAttr(C)
        m.CreateFaceVertexIndicesAttr(I)
        UsdPhysics.CollisionAPI.Apply(m.GetPrim())

    box("/World/Shoe/sole", (0, 0, 0), (0.3, 0.1, 0.02))
    box("/World/Shoe/upper", (0.02, 0.01, 0.02), (0.28, 0.09, 0.12), copies=1)
    # the upper as one 240-face island: subdivide by repeating faces on the same points
    m = UsdGeom.Mesh(st.GetPrimAtPath("/World/Shoe/upper"))
    idx = list(m.GetFaceVertexIndicesAttr().Get())
    m.GetFaceVertexIndicesAttr().Set(idx * 40)
    m.GetFaceVertexCountsAttr().Set([4] * 240)
    box("/World/Shoe/lace", (0.1, 0.04, 0.12), (0.2, 0.06, 0.125))
    st.GetRootLayer().Save()
    return st


def test_a_shoe_becomes_a_sole_with_a_deformable_upper(tmp_path, monkeypatch):
    pytest.importorskip("pxr")
    from pxr import Usd, UsdPhysics

    sys.path.insert(0, str(REPO / "scripts"))
    import processing
    import soft_body_parts as sbp

    st = _shoe_stage(tmp_path)
    monkeypatch.setattr(processing, "_prior", lambda e: {})
    parts = [
        {"id": 1, "path": "/World/Shoe/sole", "role": "rubber outsole", "material": "rubber_natural", "motion": "none"},
        {"id": 2, "path": "/World/Shoe/upper", "role": "knit upper", "material": "fabric_cotton", "motion": "none"},
        {"id": 3, "path": "/World/Shoe/lace", "role": "laces", "material": "fabric_cotton", "motion": "flex"}]
    entry = {"asset_id": "shoe_t", "file": str(tmp_path / "shoe_simready.usda"),
             "part_survey": {"root": "/World/Shoe", "parts": parts}}
    p = sbp.plan(entry, st)
    assert p["base"] == "/World/Shoe/sole" and len(p["carriers"]) == 1
    assert p["carriers"][0]["riders"] == ["/World/Shoe/lace"] and p["carriers"][0]["attach_to"] == "/World/Shoe/sole"
    # a hidden foam block inside is not a deformable of its own: rigid with the base
    hidden = dict(entry, part_survey={"root": "/World/Shoe", "parts": parts[:2] + [
        {"id": 3, "path": "/World/Shoe/lace", "role": "hidden internal footbed block", "material": "foam_polyurethane",
         "motion": "none"}]})
    ph = sbp.plan(hidden, st)
    assert "/World/Shoe/lace" in ph["rigid"] and any("kept rigid" in n for n in ph["notes"])
    # a soft part that is the structure too is refused
    fused = dict(entry, part_survey={"root": "/World/Shoe", "parts": [
        {"id": 1, "path": "/World/Shoe/upper", "role": "upper with moulded sole", "material": "leather", "motion": "none"},
        {"id": 2, "path": "/World/Shoe/lace", "role": "eyelet", "material": "steel_mild", "motion": "none"}]})
    with pytest.raises(RuntimeError, match="not separable"):
        sbp.plan(fused, st)
    monkeypatch.setattr(sbp, "QUEUE", tmp_path)
    (tmp_path / "shoe_t.json").write_text(json.dumps(entry))
    rec = sbp.apply("shoe_t")
    st = Usd.Stage.Open(entry["file"])
    root, sole = st.GetPrimAtPath("/World/Shoe"), st.GetPrimAtPath("/World/Shoe/sole")
    assert not root.HasAPI(UsdPhysics.RigidBodyAPI) and sole.HasAPI(UsdPhysics.RigidBodyAPI)
    assert UsdPhysics.MassAPI(sole).GetMassAttr().Get() == pytest.approx(0.4)
    body = st.GetPrimAtPath(rec["soft"][0]["body"])
    # the PhysX schemas are not registered in a bare OpenUSD build, so
    # GetAppliedSchemas() drops them; the authored metadata holds them
    authored = list(body.GetMetadata("apiSchemas").GetAddedOrExplicitItems())
    for api in ("OmniPhysicsDeformableBodyAPI", "PhysxAutoDeformableBodyAPI", "PhysxSurfaceDeformableBodyAPI"):
        assert api in authored
    assert st.GetPrimAtPath(f"{body.GetPath()}/simMesh") and st.GetPrimAtPath(f"{body.GetPath()}/upper")
    assert st.GetPrimAtPath(f"{body.GetPath()}/lace")                      # the trim rides on the upper
    assert not st.GetPrimAtPath("/World/Shoe/upper").IsActive()
    att = st.GetPrimAtPath(f"{body.GetPath()}_attach")
    assert [str(t) for t in att.GetRelationship("physxAutoDeformableAttachment:attachable1").GetTargets()] == ["/World/Shoe/sole"]
    assert sbp.apply("shoe_t")["soft"][0]["body"] == rec["soft"][0]["body"]   # re-running re-authors, not doubles
    st = Usd.Stage.Open(entry["file"])
    assert len([c for c in st.GetPrimAtPath("/World/Shoe/SoftBodies").GetChildren()]) == 2   # body + attach
    assert sbp.strip(st, "/World/Shoe") == 1
    assert st.GetPrimAtPath("/World/Shoe").HasAPI(UsdPhysics.RigidBodyAPI) and st.GetPrimAtPath("/World/Shoe/upper").IsActive()
    assert not st.GetPrimAtPath("/World/Shoe/SoftBodies")


def test_a_chess_set_needs_its_board_and_pieces_to_be_approved(monkeypatch, tmp_path):
    pytest.importorskip("pxr")
    sys.path.insert(0, str(REPO / "scripts"))
    import auto_approve, ingest_asset, processing, process_downloads as pd

    assert pd.chess_convention.__doc__ and pd.class_of({"class_hint": "mug"}) == "mug"
    monkeypatch.setattr(pd, "entry_of", lambda a: {"asset_id": a, "class_hint": "mug", "file": "/x"})
    assert pd.chess_convention("mug_t") is None                       # not a chess set: nothing to do
    monkeypatch.setattr(ingest_asset, "run_report", lambda *a, **k: {"callouts": []})
    monkeypatch.setattr(processing, "_prior", lambda e: {"max_dim_m": [0.25, 0.7], "mass_kg": [0.6, 4.0]})
    pri = tmp_path / "priors.json"
    pri.write_text(json.dumps({"classes": {"chess_set": {"max_dim_m": [0.25, 0.7], "mass_kg": [0.6, 4.0]}}}))
    monkeypatch.setattr(auto_approve, "PRIORS", pri)
    m = {"up": "Z", "meters_per_unit": 1.0, "dims_m": [0.4, 0.4, 0.08], "bodies": 33, "colliders": 33,
         "no_collider": [], "no_physics_material": [], "mass_kg": 1.2, "bodies_with_mass": 33, "mirrored": [],
         "moving_joints": 0, "physics_materials": ["wood_oak"]}
    e = {"asset_id": "chess_t", "file": "/x", "class_hint": "chess_set"}
    failed = [c["check"] for c in auto_approve.gates(e, m) if not c["ok"]]
    assert "chess_playable" in failed
    e["chess"] = {"ok": True, "pieces": 32, "standard_start": True, "board": {"a1_is_dark": True}}
    assert "chess_playable" not in [c["check"] for c in auto_approve.gates(e, m) if not c["ok"]]


def test_a_set_of_free_bodies_is_not_articulated(monkeypatch):
    sys.path.insert(0, str(REPO / "scripts"))
    import asset_review_hub
    import process_downloads as pd

    e = {"asset_id": "chess_t", "file": "/x", "class_hint": "chess_set", "applied_fixes": [], "report": {},
         "vlm": {"content_kind": "object_set"},
         "part_survey": {"parts": [{"id": 1, "role": "board", "motion": "none"},
                                   {"id": 2, "role": "queen piece", "motion": "detach"}]}}
    monkeypatch.setattr(pd, "entry_of", lambda a: e)
    monkeypatch.setattr(asset_review_hub, "_load_priors_fresh", lambda: {"chess_set": {"articulable": False}})
    assert pd._articulate("chess_t", dry=False).startswith("rigid: a set of free bodies")


# --- rules the corkscrew and the padlocks taught (2026-10-09) ----------------------

sys.path.insert(0, str(REPO / "scripts"))

def test_a_hinge_turns_about_the_pin_that_joins_it():
    """A rivet the survey says joins the wing to the body gives the hinge its
    axis (the pin's length) and its pivot (the pin's centre)."""
    import numpy as np
    from survey_draft import _pin_for, _pin_id

    wing = {"id": 3, "role": "Side wing lever arm (x2)", "path": "/w", "centroid": [0, 0.047, -0.008],
            "size_m": [0.011, 0.087, 0.058], "motion": "hinge"}
    pin = {"id": 4, "role": "Wing hinge pin/rivet (x2) joining wing to body", "path": "/p",
           "centroid": [0, 0.0175, 0.008], "size_m": [0.0126, 0.0058, 0.0058], "motion": "spin"}
    far_pin = {**pin, "id": 5, "path": "/q", "centroid": [0, -0.0175, 0.008]}
    blob = {"id": 6, "role": "hinge pin", "path": "/b", "centroid": [0, 0.02, 0.0], "size_m": [0.01, 0.01, 0.01]}
    geo = {"/w": {"min": [-0.006, 0.004, -0.037], "max": [0.006, 0.091, 0.021]},
           "/p": {"min": [-0.006, 0.0146, 0.005], "max": [0.006, 0.0204, 0.011]},
           "/q": {"min": [-0.006, -0.0204, 0.005], "max": [0.006, -0.0146, 0.011]},
           "/b": {"min": [-0.005, 0.015, -0.005], "max": [0.005, 0.025, 0.005]}}
    sparts = {p["id"]: p for p in (wing, pin, far_pin, blob)}
    assert _pin_id(wing, sparts, geo) == 4                       # the pin touching it, not the far one or a blob
    axis, centre = _pin_for(wing, sparts, geo)
    assert list(axis) == [1.0, 0.0, 0.0] and np.allclose(centre, [0, 0.0175, 0.008])


def test_a_rack_driven_part_and_its_worm_are_told_apart_by_their_words():
    from survey_draft import DRIVEN, LEADER, PIN

    assert DRIVEN.search("Side wing lever arm driven by the rack and pinion")
    assert not DRIVEN.search("Central screw shaft with helical worm")
    assert LEADER.search("Central screw shaft with helical worm") and not LEADER.search("Side wing lever arm")
    assert PIN.search("Wing hinge pin/rivet joining wing to body")


def test_a_pin_hinged_about_and_a_whole_tool_use_are_not_missing_joints():
    """Completeness wanted the rivets as spin joints and a joint for prying a
    cap off with the whole tool; neither is an articulation."""
    from motion_critic import completeness

    spec = {"joints": [{"name": "hinge_03", "joint_type": "revolute", "parent_prim": "/body", "child_prim": "/wing"},
                       {"name": "part_003", "joint_type": "fixed", "parent_prim": "/body", "child_prim": "/pin"}],
            "_analysis": {"notes": ["#3 hinges about pin #4", "#3 Side wing: hinge about/along X"]}}
    entry = {"articulation_draft": json.dumps(spec),
             "part_survey": {"parts": [
                 {"id": 1, "role": "body", "path": "/body", "motion": "none"},
                 {"id": 3, "role": "Side wing lever arm", "path": "/wing", "motion": "hinge"},
                 {"id": 4, "role": "Wing hinge pin/rivet joining wing to body", "path": "/pin", "motion": "spin"}]},
             "vlm": {"functions": [{"kind": "manual", "does": "pries off a crown cap",
                                    "moving_part": "whole tool pivoting on cap edge"}]}}
    c = completeness(entry)
    assert c["ok"] and c["missing"] == []
    entry["part_survey"]["parts"].append({"id": 5, "role": "trigger", "path": "/trig", "motion": "press"})
    assert completeness(entry)["missing"] == ["#5 trigger (press)"]


def test_the_whole_video_grid_frames_the_object_not_the_floor():
    """A corkscrew was a thumbnail in its own panels; the judge said it could
    not see it and inferred faults. The grid crops to the object: colour or
    strong contrast, with room for pale parts; grey shadows do not count."""
    import numpy as np
    from PIL import Image
    from motion_critic import _crop_tile, _object_box

    frame = np.full((720, 1280, 3), 229, dtype=np.uint8)
    frame[300:480, 600:690] = (196, 90, 89)          # the red body
    frame[480:700, 300:650] = 175                     # its grey shadow, long and dark
    box = _object_box([Image.fromarray(frame)])
    assert 400 <= box[0] <= 560 and box[2] <= 800 and box[1] >= 150 and box[3] <= 640   # the body, with room, not the floor
    tile = _crop_tile(Image.fromarray(frame), box, (480, 270))
    assert tile.size == (480, 270)


def test_a_set_of_articulable_copies_is_split_one_asset_per_object():
    """Two padlocks in one file: an object set whose survey finds a hinge on
    each is split; a set of pieces that only sit there is not."""
    pytest.importorskip("pxr")
    from pxr import Usd, UsdGeom
    sys.path.insert(0, str(REPO / "tests"))
    from test_door_mechanism import _mesh
    from split_set import splittable

    stage = Usd.Stage.CreateInMemory()
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    UsdGeom.Xform.Define(stage, "/World")
    UsdGeom.Xform.Define(stage, "/World/Set")
    for name, x in (("Lock", 0.0), ("Lock_1", 0.3)):
        UsdGeom.Xform.Define(stage, f"/World/Set/{name}")
        _mesh(stage, f"/World/Set/{name}/Body", [((x, 0, 0), (x + 0.05, 0.02, 0.06))])
        _mesh(stage, f"/World/Set/{name}/Shackle", [((x + 0.01, 0.005, 0.06), (x + 0.04, 0.015, 0.1))])
    survey = {"root": "/World/Set", "parts": [
        {"id": 1, "role": "padlock body", "path": "/World/Set/Lock/Body", "motion": "none",
         "members": ["/World/Set/Lock/Body", "/World/Set/Lock_1/Body"]},
        {"id": 2, "role": "U-shaped shackle", "path": "/World/Set/Lock/Shackle", "motion": "hinge",
         "members": ["/World/Set/Lock/Shackle", "/World/Set/Lock_1/Shackle"]}]}
    entry = {"vlm": {"content_kind": "object_set"}, "part_survey": survey}
    groups = splittable(entry, stage)
    assert len(groups) == 2 and all(any("Shackle" in p or "Lock" in p for p in g) for g in groups)
    still = {**entry, "part_survey": {**survey, "parts": [dict(p, motion="none") for p in survey["parts"]]}}
    assert splittable(still, stage) == []
    assert splittable({**entry, "vlm": {"content_kind": "single"}}, stage) == []


def test_the_critic_s_corrections_map_onto_the_survey_s_words():
    """Every axis and pivot the judge may name has a drafter's word (or is
    the pin rule's job); nothing it says is dropped on the floor."""
    from motion_critic import _schema
    from process_downloads import AXIS_WORDS, PIVOT_WORDS

    corr = _schema()["properties"]["correction"]["properties"]
    assert set(corr["axis"]["enum"]) - {"none"} == set(AXIS_WORDS)
    assert set(corr["pivot"]["enum"]) - {"none", "other_end"} == set(PIVOT_WORDS)    # other_end is the flip's
