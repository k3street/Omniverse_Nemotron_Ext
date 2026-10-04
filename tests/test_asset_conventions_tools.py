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
                         "check_asset_behaviors", "verify_asset_motion", "process_downloads", "get_asset_job"}
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
