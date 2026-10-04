"""Asset conventions as tools: draft, apply, check and verify an ingested asset
the way the review hub and the downloads pipeline do.

articulate_asset applies whatever joint list it is given. These tools apply
the conventions on top - the class's drafting tier (pivot, clip, drill,
rotors, buttons, doors, ...), limits where parts meet, mechanisms (latches,
press-fits, caster swivels), behaviors (a drill's motor law, a wheeled base),
the articulation's link limit, and verification in PhysX with the motion
critic - so an agent gets the same asset the hub would.

Each runs scripts/asset_tools.py in a subprocess with an OpenUSD build on the
path: one code path with the hub, and no pxr needed in the service. Long
work (a PhysX animation, a downloads run) starts a background job and
returns its id; get_asset_job polls it.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict

REPO = Path(__file__).resolve().parents[5]
_TOOLS = REPO / "scripts" / "asset_tools.py"


def _env() -> dict:
    env = dict(os.environ)
    usd = env.get("USD_INSTALL", "/home/kimate/Documents/Github/openusd_build")
    env["PYTHONPATH"] = os.pathsep.join(p for p in (f"{usd}/lib/python", str(REPO), env.get("PYTHONPATH", "")) if p)
    env["LD_LIBRARY_PATH"] = os.pathsep.join(p for p in (f"{usd}/lib", env.get("LD_LIBRARY_PATH", "")) if p)
    return env


async def _run(op: str, args: Dict[str, Any], timeout: float = 600.0) -> Dict[str, Any]:
    py = os.environ.get("ASSET_TOOLS_PYTHON", "/usr/bin/python3" if Path("/usr/bin/python3").exists() else sys.executable)
    proc = await asyncio.create_subprocess_exec(
        py, str(_TOOLS), op, json.dumps(args), cwd=str(REPO), env=_env(),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        return {"type": "data", "error": f"{op} timed out after {timeout:g} s"}
    lines = [ln for ln in out.decode(errors="replace").splitlines() if ln.strip().startswith("{")]
    if not lines:
        return {"type": "data", "error": f"{op} returned nothing: {err.decode(errors='replace')[-600:]}"}
    res = json.loads(lines[-1])
    if not res.pop("ok", False):
        return {"type": "data", "error": res.get("error", "failed")}
    return {"type": "data", **res}


def _need(args: Dict, key: str):
    if not args or not args.get(key):
        raise ValueError(f"'{key}' is required")
    return args[key]


async def _handle_draft_asset_articulation(args: Dict) -> Dict:
    try:
        return await _run("draft", {"asset_id": _need(args, "asset_id")})
    except ValueError as ex:
        return {"type": "data", "error": str(ex)}


async def _handle_apply_asset_articulation(args: Dict) -> Dict:
    try:
        a = {"asset_id": _need(args, "asset_id"), "replace": bool(args.get("replace", False))}
    except ValueError as ex:
        return {"type": "data", "error": str(ex)}
    spec = args.get("spec")
    if isinstance(spec, str) and spec.strip():
        try:
            spec = json.loads(spec)
        except ValueError:
            return {"type": "data", "error": "spec is not valid JSON"}
    if spec:
        a["spec"] = spec
    if args.get("reason"):
        a["reason"] = args["reason"]
    return await _run("apply", a)


async def _handle_unarticulate_asset(args: Dict) -> Dict:
    try:
        return await _run("unarticulate", {"asset_id": _need(args, "asset_id"), "reason": (args or {}).get("reason")})
    except ValueError as ex:
        return {"type": "data", "error": str(ex)}


async def _handle_check_asset_behaviors(args: Dict) -> Dict:
    try:
        return await _run("behaviors", {"asset_id": _need(args, "asset_id")})
    except ValueError as ex:
        return {"type": "data", "error": str(ex)}


async def _handle_verify_asset_motion(args: Dict) -> Dict:
    try:
        jargs = [_need(args, "asset_id")]
    except ValueError as ex:
        return {"type": "data", "error": str(ex)}
    if args.get("critic") is False:
        jargs.append("--no-critic")
    if args.get("judge"):
        jargs += ["--judge", str(args["judge"])]
    return await _run("start_job", {"kind": "verify", "args": jargs}, timeout=60)


async def _handle_process_downloads(args: Dict) -> Dict:
    args = args or {}
    flags = []
    for key, flag in (("dry_run", "--dry-run"), ("delete_duplicates", "--delete-duplicates"),
                      ("animate", "--animate"), ("soft", "--soft"), ("refile", "--refile"),
                      ("resume", "--resume")):
        if args.get(key):
            flags.append(flag)
    for key, flag in (("downloads", "--downloads"), ("library", "--library")):
        if args.get(key):
            flags += [flag, str(args[key])]
    return await _run("start_job", {"kind": "downloads", "args": flags}, timeout=60)


async def _handle_get_asset_job(args: Dict) -> Dict:
    try:
        return await _run("job", {"job_id": _need(args, "job_id")}, timeout=60)
    except ValueError as ex:
        return {"type": "data", "error": str(ex)}


def register(data: Dict, codegen: Dict) -> None:
    data["draft_asset_articulation"] = _handle_draft_asset_articulation
    data["apply_asset_articulation"] = _handle_apply_asset_articulation
    data["unarticulate_asset"] = _handle_unarticulate_asset
    data["check_asset_behaviors"] = _handle_check_asset_behaviors
    data["verify_asset_motion"] = _handle_verify_asset_motion
    data["process_downloads"] = _handle_process_downloads
    data["get_asset_job"] = _handle_get_asset_job
