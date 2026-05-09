"""diagnose_one.py — interactive smoke for diagnose_scene_feasibility.

Run diagnose against a single canonical and pretty-print the report.
Useful for spot-checking: compare diagnose verdict vs known function-gate
result, or to develop+debug new metrics.

Usage:
  python scripts/qa/diagnose_one.py CP-37
  python scripts/qa/diagnose_one.py CP-22 --lang en --no-cache
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from service.isaac_assist_service.chat.canonical_instantiator import (  # noqa: E402
    execute_template_canonical, settle_after_canonical,
)
from service.isaac_assist_service.chat.tools.tool_executor import (  # noqa: E402
    execute_tool_call,
)
from service.isaac_assist_service.chat.tools import kit_tools  # noqa: E402
from service.isaac_assist_service.diagnose.messages import format_for_user  # noqa: E402

# Re-use the args extractor from feasibility_baseline
sys.path.insert(0, str(REPO_ROOT / "scripts/qa"))
from feasibility_baseline import _extract_diagnose_args, _reset_scene  # noqa: E402


def _pretty_report(report: dict, lang: str) -> str:
    lines = [format_for_user(report, lang=lang), ""]
    metrics = report.get("metrics") or {}
    if metrics:
        lines.append("Metrics:")
        for k, v in metrics.items():
            if isinstance(v, float):
                lines.append(f"  {k}: {v:.4f}")
            else:
                lines.append(f"  {k}: {v}")
    violations = report.get("violations") or []
    if violations:
        lines.append("")
        lines.append(f"Violations ({len(violations)}):")
        for v in violations:
            lines.append(f"  [{v.get('severity')}] {v.get('axis')}: {v.get('message')}")
    alts = report.get("alternatives") or []
    if alts:
        lines.append("")
        lines.append(f"Alternatives ({len(alts)}):")
        for a in alts:
            lines.append(f"  {a.get('axis')}: {a.get('suggestion')}")
    lines.append("")
    lines.append(f"verdict: {report.get('verdict')}  "
                 f"seed_used: {report.get('seed_used')}  "
                 f"cache_hit: {report.get('cache_hit')}  "
                 f"elapsed_ms: {report.get('elapsed_ms')}")
    return "\n".join(lines)


async def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("canonical", help="Canonical id, e.g. CP-37")
    p.add_argument("--lang", default="sv", choices=["sv", "en"])
    p.add_argument("--no-cache", action="store_true")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--skip-build", action="store_true",
                   help="Don't rebuild scene; assume current scene matches canonical")
    p.add_argument("--json", action="store_true", help="Emit JSON instead of pretty print")
    args = p.parse_args()

    if not await kit_tools.is_kit_rpc_alive():
        print("[FAIL] Kit RPC not alive at 127.0.0.1:8001", file=sys.stderr)
        return 2

    template_path = REPO_ROOT / f"workspace/templates/{args.canonical}.json"
    if not template_path.exists():
        print(f"[FAIL] template not found: {template_path}", file=sys.stderr)
        return 2
    template = json.loads(template_path.read_text())

    diag_args = _extract_diagnose_args(template)
    if diag_args is None:
        print(f"[FAIL] cannot extract diagnose args from {args.canonical}", file=sys.stderr)
        return 2

    diag_args["seed"] = args.seed
    diag_args["use_cache"] = not args.no_cache
    diag_args["lang"] = args.lang

    if not args.skip_build:
        await _reset_scene()
        build = await execute_template_canonical(template)
        if not build.get("instantiated"):
            print(f"[FAIL] build failed: {(build.get('errors') or [])[:2]}", file=sys.stderr)
            return 1
        try:
            await settle_after_canonical(template)
        except Exception:
            pass

    t0 = time.time()
    res = await execute_tool_call("diagnose_scene_feasibility", diag_args)
    elapsed = time.time() - t0

    if "error" in res:
        print(f"[FAIL] diagnose error: {res['error']}", file=sys.stderr)
        return 1

    out = (res.get("output") or "").strip()
    parsed = res
    if out:
        # diagnose handler is direct-return (no Kit RPC) so res IS the report
        for line in out.splitlines()[::-1]:
            line = line.strip()
            if line.startswith("{"):
                try:
                    parsed = json.loads(line)
                    break
                except Exception:
                    pass

    if args.json:
        print(json.dumps(parsed, indent=2))
    else:
        print(_pretty_report(parsed, args.lang))
        print(f"(total wall-clock: {elapsed*1000:.0f}ms)")

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
