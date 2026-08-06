#!/usr/bin/env python3
"""Ingest an asset file for sim2real verification and queue it for human review.

Runs the same `ingest_asset_report` check codegen the chat tool uses, but
headlessly (needs `pxr` importable — e.g. an OpenUSD build on PYTHONPATH).
Writes the report to workspace/review_queue/<asset_id>.json, where the
asset review hub (scripts/asset_review_hub.py) picks it up.

Usage:
    python scripts/ingest_asset.py /path/to/asset.usdz [--class-hint pan] [--id my_asset]
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import io
import json
import re
import sys
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
QUEUE_DIR = REPO / "workspace" / "review_queue"
sys.path.insert(0, str(REPO))


def run_report(file_path: str, class_hint: str | None) -> dict:
    """Execute the ingest-report generated code locally and return the report."""
    from service.isaac_assist_service.chat.tools import kit_tools
    from service.isaac_assist_service.chat.tools.handlers.physics import (
        _handle_ingest_asset_report,
    )

    captured = {}

    async def grab(code, description="", timeout=600):
        captured["code"] = code
        return {}

    kit_tools.queue_exec_patch = grab
    args = {"file_path": file_path}
    if class_hint:
        args["class_hint"] = class_hint
    asyncio.run(_handle_ingest_asset_report(args))
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        exec(compile(captured["code"], "<ingest>", "exec"), {"__builtins__": __builtins__})
    return json.loads(out.getvalue().strip().splitlines()[-1])


def propose_category(report: dict) -> str:
    """Category proposal from the report — final say belongs to the reviewer.

    An articulable-class asset with separate parts but no joints yet proposes
    'articulated_unverified' (its destination after articulate_asset), never
    'rigid' — the object is not rigid just because nobody authored its joints.
    """
    callouts = report.get("callouts", [])
    if any(c["check"] == "articulation" and "baked" in c["message"] for c in callouts):
        return "rigid_only_baked"
    if report.get("structure", {}).get("joints"):
        return "articulated_unverified"
    if any(c["check"] == "articulation" and "articulate_asset" in c["message"]
           for c in callouts):
        return "articulated_unverified"
    return "rigid_unverified"


def needs_articulation(report: dict) -> bool:
    """True when the class should articulate but no joints are authored yet."""
    return any(c["check"] == "articulation" and "articulate_asset" in c["message"]
               for c in report.get("callouts", []))


_USD_EXTS = {".usd", ".usda", ".usdc", ".usdz"}


def _asset_id_for(file_path: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", Path(file_path).stem.lower()).strip("_")


def queue_file(file_path: str, class_hint: str | None = None,
               asset_id: str | None = None) -> dict:
    """Run the ingest report on one file and write its review-queue entry."""
    file_path = str(Path(file_path).resolve())
    report = run_report(file_path, class_hint)
    asset_id = asset_id or _asset_id_for(file_path)
    entry = {
        "asset_id": asset_id,
        "file": file_path,
        "class_hint": class_hint,
        "source_mtime": Path(file_path).stat().st_mtime,
        "queued": date.today().isoformat(),
        "proposed_category": propose_category(report),
        "status": "pending_review",
        "report": report,
    }
    QUEUE_DIR.mkdir(parents=True, exist_ok=True)
    (QUEUE_DIR / f"{asset_id}.json").write_text(json.dumps(entry, indent=1))
    return entry


def _already_processed(file_path: str) -> str | None:
    """Reason to skip this source file, or None to ingest it."""
    src = str(Path(file_path).resolve())
    asset_id = _asset_id_for(src)
    qf = QUEUE_DIR / f"{asset_id}.json"
    if qf.exists():
        try:
            e = json.loads(qf.read_text())
            same = src in (e.get("file"), e.get("original_file"))
            if not same:
                return f"asset_id collision with {e.get('file')}"
            if e.get("status") in ("approved", "rejected"):
                return f"already reviewed ({e['status']})"
            if e.get("applied_fixes") or e.get("original_file"):
                return "in review (corrective fixes applied)"
            if e.get("source_mtime") in (None, Path(src).stat().st_mtime):
                return f"already queued ({e.get('status', '?')})"
            return None  # source changed on disk — re-ingest
        except (json.JSONDecodeError, OSError):
            return None
    reg_path = REPO / "workspace" / "knowledge" / "sim_ready_assets.json"
    if reg_path.exists():
        try:
            reg = json.loads(reg_path.read_text())
            for a in reg.get("assets", []):
                if src in (a.get("source_file"), a.get("file")):
                    return f"already in registry ({a.get('category')})"
        except (json.JSONDecodeError, OSError):
            pass
    return None


def scan_dir(directory: str, limit: int = 0, max_size_mb: float = 200.0) -> dict:
    """Discover USD assets under a directory and queue new/changed ones.

    Returns {'queued': [...], 'skipped': [(file, reason)], 'errors': [...]}.
    """
    root = Path(directory).expanduser().resolve()
    out = {"queued": [], "skipped": [], "errors": []}
    candidates = sorted(
        p for p in root.rglob("*")
        if p.suffix.lower() in _USD_EXTS and p.is_file()
    )
    for p in candidates:
        if limit and len(out["queued"]) >= limit:
            out["skipped"].append((str(p), f"scan limit {limit} reached"))
            continue
        size_mb = p.stat().st_size / 1e6
        if size_mb > max_size_mb:
            out["skipped"].append((str(p), f"{size_mb:.0f} MB > {max_size_mb:.0f} MB cap"))
            continue
        reason = _already_processed(str(p))
        if reason:
            out["skipped"].append((str(p), reason))
            continue
        try:
            entry = queue_file(str(p))
            out["queued"].append(entry)
        except Exception as e:
            out["errors"].append((str(p), str(e)[:200]))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("file", nargs="?", help="USD/USDZ asset file")
    ap.add_argument("--scan", metavar="DIR",
                    help="recursively queue every new/changed USD asset under DIR")
    ap.add_argument("--limit", type=int, default=0,
                    help="max new assets to queue per scan (0 = no limit)")
    ap.add_argument("--max-size-mb", type=float, default=200.0,
                    help="skip files larger than this (default 200 MB)")
    ap.add_argument("--class-hint", default=None)
    ap.add_argument("--id", default=None, help="asset_id (default: from filename)")
    ns = ap.parse_args()

    try:
        import pxr  # noqa: F401
    except ImportError:
        print("error: pxr not importable — set PYTHONPATH/LD_LIBRARY_PATH to an "
              "OpenUSD build (see launch_review_hub.sh)", file=sys.stderr)
        return 1

    if ns.scan:
        res = scan_dir(ns.scan, limit=ns.limit, max_size_mb=ns.max_size_mb)
        for e in res["queued"]:
            errs = sum(1 for c in e["report"].get("callouts", [])
                       if c["severity"] == "error")
            print(f"queued {e['asset_id']:40s} {e['report'].get('verdict', '')[:40]:42s}"
                  f" ({errs} errors)")
        for f, why in res["errors"]:
            print(f"ERROR  {f}: {why}")
        skipped_counts = {}
        for _, why in res["skipped"]:
            key = why.split("(")[0].strip()
            skipped_counts[key] = skipped_counts.get(key, 0) + 1
        summary = ", ".join(f"{v}x {k}" for k, v in skipped_counts.items())
        print(f"\n{len(res['queued'])} queued, {len(res['skipped'])} skipped"
              + (f" ({summary})" if summary else "")
              + (f", {len(res['errors'])} errors" if res["errors"] else ""))
        print("review at the asset review hub (launch_review_hub.sh)")
        return 0

    if not ns.file:
        ap.error("provide a FILE or --scan DIR")
    file_path = str(Path(ns.file).resolve())
    if not Path(file_path).exists():
        print(f"error: no such file: {file_path}", file=sys.stderr)
        return 1
    entry = queue_file(file_path, ns.class_hint, ns.id)
    report = entry["report"]
    errors = [c for c in report.get("callouts", []) if c["severity"] == "error"]
    print(f"queued {entry['asset_id']}: {report.get('verdict')}"
          f" ({len(errors)} errors, {len(report.get('callouts', []))} callouts)")
    print(f"  -> {QUEUE_DIR / (entry['asset_id'] + '.json')}")
    print("  review at the asset review hub (launch_review_hub.sh)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
