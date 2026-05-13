#!/usr/bin/env python3
"""Quarterly audit CLI — Phase 96.

Usage::

    python scripts/quarterly_audit.py                  # print report to stdout
    python scripts/quarterly_audit.py --out report.md  # also write markdown file

Runs the three audit checks from
``service/isaac_assist_service/multimodal/quarterly_audit_tool_executor.py``
and formats the results as a Markdown report.

Exit codes:
  0 — audit ran successfully (warnings do NOT raise non-zero exit).
  1 — unrecoverable error (import failure, missing files, etc.).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Allow running from repo root without installing the package.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from service.isaac_assist_service.multimodal.quarterly_audit_tool_executor import (
    run_full_audit,
)


def _build_markdown(report: dict) -> str:
    """Format the audit report dict as a Markdown string."""
    ts = report.get("timestamp", "unknown")
    # Strip microseconds for readability
    ts_display = ts[:19].replace("T", " ") + " UTC" if len(ts) >= 19 else ts

    lines: list[str] = []
    lines.append(f"# Quarterly Audit {ts_display}")
    lines.append("")

    # ── tool_executor.py size ─────────────────────────────────────────────────
    lines.append("## tool_executor.py size")
    size = report["tool_executor_size"]
    status_icon = "OK" if size["under_500_lines"] else "WARN"
    lines.append(f"- Lines: **{size['lines']}** [{status_icon}]")
    lines.append(f"- Under 500 lines: `{size['under_500_lines']}`")
    if size.get("warnings"):
        lines.append("")
        lines.append("**Warnings:**")
        for w in size["warnings"]:
            lines.append(f"- {w}")
    lines.append("")

    # ── ghost handlers ────────────────────────────────────────────────────────
    lines.append("## Ghost handlers")
    gh = report["ghost_handlers"]
    lines.append(f"- Total tools in schema: **{gh['total_tools']}**")
    lines.append(f"- Registered handlers: **{gh['registered_count']}**")
    ghost_count = len(gh["ghost_handlers"])
    ghost_icon = "OK" if ghost_count == 0 else "WARN"
    lines.append(f"- Ghost handlers: **{ghost_count}** [{ghost_icon}]")
    if gh["ghost_handlers"]:
        lines.append("")
        lines.append("**Unregistered tool names:**")
        for name in gh["ghost_handlers"]:
            lines.append(f"- `{name}`")
    lines.append("")

    # ── Phase completion ──────────────────────────────────────────────────────
    lines.append("## Phase completion")
    comp = report["phase_completion"]
    lines.append(f"- Total phases: **{comp['total']}**")
    lines.append(f"- Landed: **{comp['landed']}** ({comp['landed_pct']}%)")
    lines.append(f"- Scaffold: **{comp['scaffold']}**")
    lines.append("")

    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run quarterly Isaac Assist audit and print/write a Markdown report."
    )
    parser.add_argument(
        "--out",
        metavar="FILE",
        default=None,
        help="Write Markdown report to FILE in addition to stdout.",
    )
    args = parser.parse_args(argv)

    try:
        report = run_full_audit()
    except Exception as exc:
        print(f"ERROR: audit failed — {exc}", file=sys.stderr)
        return 1

    md = _build_markdown(report)
    print(md)

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(md, encoding="utf-8")
        print(f"\nReport written to: {out_path}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
