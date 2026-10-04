#!/usr/bin/env python3
"""The asset conventions as one command per operation, JSON in and out.

What the review hub and process_downloads do to an asset - draft its joints
by its class's tier, apply them with every safeguard, say what it should do
and whether it can, verify it in PhysX - is exposed to agents as MCP tools
(service/.../handlers/asset_conventions.py). Those run this script in a
subprocess with an OpenUSD build on the path, so the tools and the hub run
one code path and the service needs no pxr of its own.

    asset_tools.py draft        '{"asset_id": "drill_1"}'
    asset_tools.py apply        '{"asset_id": "drill_1", "spec": {...}?, "replace": false}'
    asset_tools.py unarticulate '{"asset_id": "drill_1", "reason": "..."}'
    asset_tools.py behaviors    '{"asset_id": "drill_1"}'
    asset_tools.py start_job    '{"kind": "verify"|"downloads", "args": [...]}'
    asset_tools.py job          '{"job_id": "..."}'

The last line of output is the JSON result: {"ok": true, ...} or
{"ok": false, "error": "..."}.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))
QUEUE = REPO / "workspace" / "review_queue"
JOBS = REPO / "workspace" / "asset_jobs"


def _entry(asset_id: str) -> dict:
    f = QUEUE / f"{asset_id}.json"
    if not f.exists():
        raise FileNotFoundError(f"no review-queue entry {asset_id!r} (ingest it first: process_downloads or "
                                "ingest_asset_report)")
    return json.loads(f.read_text())


def _articulated(e: dict) -> bool:
    fx = [f for f in e.get("applied_fixes", []) if f.startswith(("articulate_asset", "unarticulated"))]
    return bool(fx) and fx[-1].startswith("articulate_asset")


def draft(a: dict) -> dict:
    from asset_review_hub import draft_articulation
    from behaviors import check

    e = _entry(a["asset_id"])
    note = draft_articulation(e)
    e = _entry(a["asset_id"])
    spec = json.loads(e.get("articulation_draft") or "{}")
    return {"note": note, "spec": spec, "behaviors": check(e), "already_articulated": _articulated(e)}


def apply(a: dict) -> dict:
    from asset_review_hub import apply_articulation, unarticulate
    from behaviors import check

    e = _entry(a["asset_id"])
    if _articulated(e):
        if not a.get("replace"):
            raise RuntimeError("already articulated: pass replace=true to undo it and apply this spec")
        unarticulate(e, a.get("reason") or "replaced through apply_asset_articulation")
        e = _entry(a["asset_id"])
    spec = a.get("spec") or json.loads(e.get("articulation_draft") or "null")
    if not spec:
        raise RuntimeError("no spec given and no draft on the entry: draft_asset_articulation first")
    note = apply_articulation(e, json.dumps(spec))
    e = _entry(a["asset_id"])
    return {"note": note, "file": e["file"], "behaviors": check(e)}


def unarticulate_(a: dict) -> dict:
    from asset_review_hub import unarticulate

    e = _entry(a["asset_id"])
    if not _articulated(e):
        return {"note": "not articulated: nothing to undo"}
    return {"note": unarticulate(e, a.get("reason") or "undone through unarticulate_asset")}


def behaviors_(a: dict) -> dict:
    from behaviors import KIND_ROLES, check, expected

    e = _entry(a["asset_id"])
    return {"expected": expected(e), "check": check(e), "kinds": {k: list(v) for k, v in KIND_ROLES.items()},
            "functions_seen": (e.get("vlm") or {}).get("functions")}


def start_job(a: dict) -> dict:
    kind = a["kind"]
    if kind not in ("verify", "downloads"):
        raise ValueError(f"unknown job kind {kind!r}")
    if kind == "verify":
        _entry(a["args"][0])
    job_id = f"{kind}_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
    JOBS.mkdir(parents=True, exist_ok=True)
    subprocess.Popen([sys.executable, str(REPO / "scripts" / "asset_job.py"), job_id, kind, *map(str, a.get("args", []))],
                     cwd=REPO, start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     env=dict(os.environ))
    return {"job_id": job_id, "job_file": str(JOBS / f"{job_id}.json"),
            "note": "running in the background (an Isaac animation takes a minute or more per asset, queued on the "
                    "machine's single Isaac slot): poll get_asset_job"}


def job(a: dict) -> dict:
    f = JOBS / f"{a['job_id']}.json"
    if not f.exists():
        return {"status": "starting", "note": "the job has not written its file yet"}
    j = json.loads(f.read_text())
    if j.get("status") == "running" and j.get("pid"):
        try:
            os.kill(int(j["pid"]), 0)
        except OSError:
            j["status"], j["error"] = "failed", "the job process is gone without finishing"
    if j.get("status") != "done" and j.get("log") and Path(j["log"]).exists():
        j["log_tail"] = Path(j["log"]).read_text(errors="replace")[-1500:]
    return j


OPS = {"draft": draft, "apply": apply, "unarticulate": unarticulate_, "behaviors": behaviors_,
       "start_job": start_job, "job": job}


def main() -> int:
    op, args = sys.argv[1], json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            res = OPS[op](args)
        res = {"ok": True, **res}
    except Exception as ex:  # noqa: BLE001 - reported to the caller
        res = {"ok": False, "error": f"{type(ex).__name__}: {str(ex)[:600]}"}
    print(json.dumps(res, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
