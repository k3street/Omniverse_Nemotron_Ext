#!/usr/bin/env python3
"""Run or dry-run one variant from a materialized scenario campaign.

The campaign materializer writes a ``campaign_plan.json`` with per-variant
``usd_path`` and ``setup_script_path`` fields.  This runner selects one variant,
writes a result artifact, and optionally launches Isaac Sim through the repo's
Isaac Assist launcher.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List


REPO_ROOT = Path(__file__).resolve().parents[1]


def load_manifest(path: Path) -> Dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("variants"), list):
        raise ValueError(f"{path} is not a campaign manifest with a variants list")
    return data


def select_variant(manifest: Dict[str, Any], *, index: int | None, variant_id: str | None) -> Dict[str, Any]:
    variants: List[Dict[str, Any]] = manifest["variants"]
    if not variants:
        raise ValueError("campaign manifest has no variants")
    if variant_id:
        for variant in variants:
            if variant.get("variant_id") == variant_id:
                return variant
        raise ValueError(f"variant_id {variant_id!r} was not found")
    if index is None:
        index = 1
    if index < 1 or index > len(variants):
        raise ValueError(f"variant index {index} outside 1..{len(variants)}")
    return variants[index - 1]


def variant_result_path(variant: Dict[str, Any]) -> Path:
    usd_path = Path(str(variant["usd_path"]))
    return usd_path.with_name(f"{usd_path.stem}_result.json")


def variant_log_path(variant: Dict[str, Any]) -> Path:
    usd_path = Path(str(variant["usd_path"]))
    return usd_path.with_name(f"{usd_path.stem}_launch.log")


def _kit_rpc_health(timeout_s: float = 1.0) -> Dict[str, Any]:
    port = "8001"
    port_file = Path("/tmp/isaac_assist_rpc_port")
    if port_file.exists():
        candidate = port_file.read_text(encoding="utf-8").strip()
        if candidate.isdigit():
            port = candidate
    url = f"http://127.0.0.1:{port}/health"
    try:
        with urllib.request.urlopen(url, timeout=timeout_s) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return {"ok": True, "url": url, "response": payload}
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
        return {"ok": False, "url": url, "error": str(exc)}


def launch_variant(
    variant: Dict[str, Any],
    *,
    dry_run: bool,
    wait: bool,
    startup_grace_s: float = 3.0,
) -> Dict[str, Any]:
    usd_path = Path(str(variant["usd_path"]))
    setup_script = Path(str(variant["setup_script_path"]))
    log_path = variant_log_path(variant)
    result_path = variant_result_path(variant)
    command = ["./launch_canvas_scene.sh", str(usd_path)]
    env = os.environ.copy()
    env["SCENE_SETUP_SCRIPT"] = str(setup_script)

    result: Dict[str, Any] = {
        "variant_id": variant["variant_id"],
        "status": "dry_run" if dry_run else "launching",
        "timestamp": time.time(),
        "usd_path": str(usd_path),
        "setup_script_path": str(setup_script),
        "log_path": str(log_path),
        "command": f"SCENE_SETUP_SCRIPT={setup_script} {' '.join(command)}",
        "preflight": {
            "usd_exists": usd_path.exists(),
            "setup_script_exists": setup_script.exists(),
        },
        "verification": {
            "status": "pending" if not dry_run else "not_run",
            "extension_expected": "omni.isaac.assist",
            "stage_expected": str(usd_path),
            "setup_script_expected": str(setup_script),
            "manual_checks": [
                "Isaac Assist extension loads",
                "Stage opens without USD parser errors",
                "Setup script prints the variant id",
                "Scene objects appear",
                "Spatial relations are visually plausible",
            ],
        },
    }

    if dry_run:
        result_path.write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
        return result

    if not usd_path.exists():
        raise FileNotFoundError(f"USD file does not exist: {usd_path}")
    if not setup_script.exists():
        raise FileNotFoundError(f"setup script does not exist: {setup_script}")

    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_handle = log_path.open("a", encoding="utf-8")
    log_handle.write(f"\n=== Isaac Assist variant launch {time.ctime()} ===\n")
    log_handle.write(result["command"] + "\n")
    log_handle.flush()
    proc = subprocess.Popen(
        command,
        cwd=REPO_ROOT,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        start_new_session=True,
        text=True,
    )
    result["pid"] = proc.pid
    if wait:
        result["returncode"] = proc.wait()
        result["status"] = "completed" if result["returncode"] == 0 else "failed"
        result["verification"]["status"] = "log_review_required"
        log_handle.close()
    else:
        deadline = time.monotonic() + startup_grace_s
        rpc = _kit_rpc_health(timeout_s=0.5)
        returncode = proc.poll()
        while returncode is None and time.monotonic() < deadline:
            time.sleep(1.0)
            rpc = _kit_rpc_health(timeout_s=0.5)
            returncode = proc.poll()
        result["verification"]["kit_rpc"] = rpc
        if returncode is None:
            result["status"] = "launched"
            result["verification"]["status"] = "running" if rpc["ok"] else "launched_unverified"
        else:
            result["returncode"] = returncode
            result["status"] = "failed" if returncode else "exited"
            result["verification"]["status"] = "launcher_exited"
            log_handle.close()
    result_path.write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
    return result


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run one materialized Isaac Assist scenario variant.")
    parser.add_argument("manifest", type=Path, help="Path to campaign_plan.json")
    parser.add_argument("--index", type=int, default=1, help="1-based variant index to run. Defaults to 1.")
    parser.add_argument("--variant-id", help="Exact variant_id to run. Overrides --index.")
    parser.add_argument("--dry-run", action="store_true", help="Write the result artifact without launching Isaac Sim.")
    parser.add_argument("--wait", action="store_true", help="Wait for Isaac Sim launcher to exit.")
    parser.add_argument(
        "--startup-grace-s",
        type=float,
        default=3.0,
        help="Seconds to wait for immediate launcher exit when not using --wait.",
    )
    args = parser.parse_args(argv)

    try:
        manifest = load_manifest(args.manifest)
        variant = select_variant(manifest, index=args.index, variant_id=args.variant_id)
        result = launch_variant(
            variant,
            dry_run=args.dry_run,
            wait=args.wait,
            startup_grace_s=args.startup_grace_s,
        )
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
