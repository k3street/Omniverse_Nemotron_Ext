"""
Audit DATA_HANDLERS in tool_executor for silent failures.

For each handler in DATA_HANDLERS:
  1. Build sensible default args from tool_schemas.ISAAC_SIM_TOOLS.
  2. Call the handler.
  3. Classify the outcome:
       PASS       — returned dict without an "error" key
       SOFT_FAIL  — returned dict with "error" key (handler swallowed exception)
       HARD_FAIL  — raised an exception
       SKIPPED    — handler is None (handled inline / not installed)

Output: workspace/qa_runs/tool_audit_<date>.jsonl

Each line:
  {
    "tool": "scene_summary",
    "outcome": "PASS|SOFT_FAIL|HARD_FAIL|SKIPPED",
    "args": {...},
    "result_keys": [...],
    "error": "...",
    "duration_ms": 12,
  }

Phase A: catalog only — do not fix anything based on this output.
A handler that fails on a missing prim is expected behavior; the audit
distinguishes that from handlers that silently report success on missing
inputs (the real silent-failure bug class).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

# Force AUTO_APPROVE so queue_exec_patch executes synchronously via Kit RPC's
# /exec_sync. Without this, code-gen handlers post to /exec_patch (approval
# queue) and hang waiting for UI to drain, masquerading as handler timeouts.
os.environ.setdefault("AUTO_APPROVE", "true")

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from service.isaac_assist_service.chat.tools.tool_executor import DATA_HANDLERS  # noqa: E402
from service.isaac_assist_service.chat.tools.tool_schemas import ISAAC_SIM_TOOLS  # noqa: E402


def schema_by_name() -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for entry in ISAAC_SIM_TOOLS:
        fn = entry.get("function") or {}
        name = fn.get("name")
        if name:
            out[name] = fn
    return out


def default_for(prop_name: str, prop_schema: Dict[str, Any]) -> Any:
    """Build a sensible default value for an argument based on schema + name."""
    t = prop_schema.get("type")
    enum = prop_schema.get("enum")
    if enum:
        return enum[0]

    # Name-based heuristics (common arg names → meaningful defaults).
    lname = prop_name.lower()
    if "path" in lname and t == "string":
        if "robot" in lname:
            return "/World/Franka"
        if "camera" in lname:
            return "/World/Camera"
        return "/World/Cube"
    if lname in ("schema", "schema_name"):
        return "Material"
    if lname in ("topic",):
        return "/joint_states"
    if lname in ("query", "description", "name"):
        return "test"
    if lname == "product_name":
        return "Intel RealSense D435"

    if t == "string":
        return ""
    if t in ("number", "integer"):
        return 0
    if t == "boolean":
        return False
    if t == "array":
        items = prop_schema.get("items", {})
        return [default_for("_item", items)] if items else []
    if t == "object":
        return {}
    return None


def build_args(schema: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not schema:
        return {}
    params = schema.get("parameters", {}) or {}
    props = params.get("properties", {}) or {}
    required = params.get("required", []) or []
    args: Dict[str, Any] = {}
    for name in required:
        spec = props.get(name, {})
        args[name] = default_for(name, spec)
    return args


def classify(result: Any, exc: Optional[BaseException]) -> str:
    if exc is not None:
        return "HARD_FAIL"
    if not isinstance(result, dict):
        # Unusual shape — treat as soft fail for inspection.
        return "SOFT_FAIL"
    if result.get("error"):
        return "SOFT_FAIL"
    if result.get("success") is False:
        return "SOFT_FAIL"
    return "PASS"


async def audit_one(name: str, handler: Any, schema: Optional[Dict[str, Any]], timeout_s: float = 30.0) -> Dict[str, Any]:
    if handler is None:
        return {
            "tool": name,
            "outcome": "SKIPPED",
            "reason": "handler is None (inline or missing dependency)",
        }

    args = build_args(schema)
    t0 = time.time()
    result: Any = None
    exc: Optional[BaseException] = None
    try:
        result = await asyncio.wait_for(handler(args), timeout=timeout_s)
    except asyncio.TimeoutError:
        exc = TimeoutError(f"handler exceeded {timeout_s}s")
    except BaseException as e:
        exc = e
    dur_ms = int((time.time() - t0) * 1000)

    outcome = classify(result, exc)
    record: Dict[str, Any] = {
        "tool": name,
        "outcome": outcome,
        "args": args,
        "duration_ms": dur_ms,
    }
    if exc is not None:
        record["error"] = f"{type(exc).__name__}: {exc}"
    elif isinstance(result, dict):
        record["result_keys"] = sorted(result.keys())
        if "error" in result:
            record["error"] = str(result["error"])[:300]
    return record


async def run_all(out_path: Path, only: Optional[list[str]] = None, timeout_s: float = 30.0) -> Dict[str, int]:
    schemas = schema_by_name()
    counts = {"PASS": 0, "SOFT_FAIL": 0, "HARD_FAIL": 0, "SKIPPED": 0}
    names = sorted(DATA_HANDLERS.keys())
    if only:
        names = [n for n in names if n in set(only)]

    print(f"Auditing {len(names)} DATA_HANDLERS → {out_path.name} (timeout={timeout_s}s)")
    with out_path.open("w") as f:
        for name in names:
            handler = DATA_HANDLERS[name]
            schema = schemas.get(name)
            rec = await audit_one(name, handler, schema, timeout_s=timeout_s)
            counts[rec["outcome"]] = counts.get(rec["outcome"], 0) + 1
            f.write(json.dumps(rec) + "\n")
            f.flush()
            print(f"  {rec['outcome']:10s} {name}", flush=True)
    return counts


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--out-dir", default="workspace/qa_runs")
    p.add_argument("--only", nargs="*", help="restrict to specific tool names")
    p.add_argument("--timeout", type=float, default=30.0, help="per-handler timeout seconds")
    args = p.parse_args()

    out_dir = REPO_ROOT / args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    out_path = out_dir / f"tool_audit_{stamp}.jsonl"

    counts = asyncio.run(run_all(out_path, only=args.only, timeout_s=args.timeout))
    print()
    print("Summary:")
    for k in ("PASS", "SOFT_FAIL", "HARD_FAIL", "SKIPPED"):
        print(f"  {k:10s} {counts.get(k, 0)}")
    print(f"Wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
