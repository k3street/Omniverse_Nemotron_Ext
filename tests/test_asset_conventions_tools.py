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
