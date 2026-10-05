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
    for st in ("ingest", "classify", "file", "behaviors", "verify", "critic"):
        processing.record(e, st)
    processing.record(e, "articulate", tier="pivot")
    assert processing.stale(e) == []
    monkeypatch.setitem(processing.TIERS, "pivot", "2099-01-01")                       # a pliers fix
    st = dict(processing.stale(e))
    assert "rules" in st["articulate"] and st["verify"].startswith("after articulate")
    assert "ingest" not in st
    other = {"asset_id": "y", "applied_fixes": ["articulate_asset: 3 joints"]}
    for s in ("ingest", "classify", "file", "behaviors", "verify", "critic"):
        processing.record(other, s)
    processing.record(other, "articulate", tier="power_drill")
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
