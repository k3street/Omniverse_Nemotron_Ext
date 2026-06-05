#!/usr/bin/env python3
"""Phase 18b — Tool taxonomy auditor (L1/L2/L3 action levels).

Counts how many tool schemas in ``ISAAC_SIM_TOOLS`` carry an
``x-action-level`` annotation set to one of ``L1``, ``L2``, ``L3``.
Anything else is reported as ``UNANNOTATED``.

Outputs:
- stdout: summary table (total + per-level counts + UNANNOTATED).
- ``docs/audits/tool_levels_{date}.md``: markdown report with the same
  numbers plus the first 30 UNANNOTATED tool names (truncation note if
  more remain).

Exit code:
- ``--strict`` (default): exit 1 if any tool is UNANNOTATED, else 0.
- ``--warn``: exit 0 with a stderr warning when UNANNOTATED > 0
  (useful before the 416-entry annotation lands).

Per IA_FULL_SPEC_2026-05-10.md Phase 18b. The bulk annotation of
``tool_schemas.py`` is deferred to a serial pass with full regression
coverage; today this auditor wires up the framework + CI gate that
will turn green once those annotations ship.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping

# Make the project importable when run from repo root.
_REPO_ROOT = Path(__file__).parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


VALID_LEVELS: frozenset[str] = frozenset({"L1", "L2", "L3"})
UNANNOTATED: str = "UNANNOTATED"
REPORT_DIR = _REPO_ROOT / "docs" / "audits"
UNANNOTATED_LIST_LIMIT = 30


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ToolLevel:
    """Classification result for a single tool schema."""

    name: str
    level: str  # one of L1/L2/L3/UNANNOTATED


def _extract_level(schema: Mapping[str, object]) -> str | None:
    """Return the ``x-action-level`` value from a tool schema.

    Tool schemas use the OpenAI function-calling shape::

        {"type": "function", "function": {"name": ..., ...}}

    The spec example places ``x-action-level`` parallel to ``name``,
    i.e., inside the inner ``function`` dict. We also accept the
    annotation at the top level (parallel to ``type``/``function``)
    to be forgiving of whichever placement the bulk-annotation pass
    settles on.

    Returns the raw string value or ``None`` if absent.
    """
    # 1) Preferred: inside the "function" sub-dict (parallel to "name").
    fn = schema.get("function") if isinstance(schema, Mapping) else None
    if isinstance(fn, Mapping):
        value = fn.get("x-action-level")
        if isinstance(value, str):
            return value
    # 2) Fallback: at the top level of the schema (parallel to "function").
    value = schema.get("x-action-level") if isinstance(schema, Mapping) else None
    if isinstance(value, str):
        return value
    return None


def _extract_name(schema: Mapping[str, object]) -> str:
    """Return the tool name from a schema, or '<unknown>' if missing."""
    fn = schema.get("function") if isinstance(schema, Mapping) else None
    if isinstance(fn, Mapping):
        name = fn.get("name")
        if isinstance(name, str) and name:
            return name
    # Fallback: top-level name (some test fixtures use this shape).
    name = schema.get("name") if isinstance(schema, Mapping) else None
    if isinstance(name, str) and name:
        return name
    return "<unknown>"


def classify(schema: Mapping[str, object]) -> ToolLevel:
    """Map a tool schema to a ToolLevel (L1/L2/L3/UNANNOTATED)."""
    name = _extract_name(schema)
    raw = _extract_level(schema)
    if raw in VALID_LEVELS:
        return ToolLevel(name=name, level=raw)
    return ToolLevel(name=name, level=UNANNOTATED)


def classify_all(schemas: Iterable[Mapping[str, object]]) -> list[ToolLevel]:
    """Classify every schema in an iterable. Order-preserving."""
    return [classify(s) for s in schemas]


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def _counts(classifications: list[ToolLevel]) -> Counter:
    return Counter(c.level for c in classifications)


def _summary_lines(classifications: list[ToolLevel]) -> list[str]:
    counts = _counts(classifications)
    total = len(classifications)
    lines: list[str] = [
        f"Total tools: {total}",
        f"  L1:          {counts.get('L1', 0)}",
        f"  L2:          {counts.get('L2', 0)}",
        f"  L3:          {counts.get('L3', 0)}",
        f"  UNANNOTATED: {counts.get(UNANNOTATED, 0)}",
    ]
    return lines


def write_markdown_report(
    classifications: list[ToolLevel],
    out_path: Path,
) -> None:
    """Write the audit markdown to ``out_path``. Creates parent dirs."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    counts = _counts(classifications)
    total = len(classifications)
    unannotated = [c.name for c in classifications if c.level == UNANNOTATED]
    lines: list[str] = []
    lines.append(f"# Tool action-level audit — {_dt.date.today().isoformat()}")
    lines.append("")
    lines.append(
        "Per Phase 18b of `specs/IA_FULL_SPEC_2026-05-10.md`. Counts the "
        "`x-action-level` annotation on every tool in "
        "`service.isaac_assist_service.chat.tools.tool_schemas.ISAAC_SIM_TOOLS`."
    )
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append("| Level | Count |")
    lines.append("|-------|-------|")
    lines.append(f"| L1 | {counts.get('L1', 0)} |")
    lines.append(f"| L2 | {counts.get('L2', 0)} |")
    lines.append(f"| L3 | {counts.get('L3', 0)} |")
    lines.append(f"| UNANNOTATED | {counts.get(UNANNOTATED, 0)} |")
    lines.append(f"| **Total** | **{total}** |")
    lines.append("")
    if unannotated:
        lines.append("## Unannotated tools")
        lines.append("")
        shown = unannotated[:UNANNOTATED_LIST_LIMIT]
        for name in shown:
            lines.append(f"- `{name}`")
        if len(unannotated) > UNANNOTATED_LIST_LIMIT:
            remaining = len(unannotated) - UNANNOTATED_LIST_LIMIT
            lines.append("")
            lines.append(
                f"_…and {remaining} more (truncated to first "
                f"{UNANNOTATED_LIST_LIMIT})._"
            )
        lines.append("")
    else:
        lines.append("## Unannotated tools")
        lines.append("")
        lines.append("_None — every tool carries a valid `x-action-level`._")
        lines.append("")
    out_path.write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Audit x-action-level annotations on ISAAC_SIM_TOOLS. "
            "Exit 1 if any tool is unannotated (--strict, default)."
        )
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--strict",
        action="store_true",
        help="(default) Exit 1 if any tool is unannotated.",
    )
    mode.add_argument(
        "--warn",
        action="store_true",
        help=(
            "Exit 0 even when tools are unannotated; print a warning to "
            "stderr. Use this before the bulk annotation pass lands."
        ),
    )
    parser.add_argument(
        "--report-dir",
        type=Path,
        default=REPORT_DIR,
        help=(
            "Directory for the markdown report "
            f"(default: {REPORT_DIR.relative_to(_REPO_ROOT)})."
        ),
    )
    return parser


def _load_tools() -> list[Mapping[str, object]]:
    """Import ISAAC_SIM_TOOLS lazily so test fixtures can stub it."""
    from service.isaac_assist_service.chat.tools.tool_schemas import (
        ISAAC_SIM_TOOLS,
    )

    return list(ISAAC_SIM_TOOLS)


def run_audit(
    tools: list[Mapping[str, object]] | None = None,
    *,
    report_dir: Path | None = None,
    warn_mode: bool = False,
) -> int:
    """Programmatic entry point — used by tests + ``main()``."""
    if tools is None:
        tools = _load_tools()
    classifications = classify_all(tools)
    summary = _summary_lines(classifications)
    for line in summary:
        print(line)
    report_path = (report_dir or REPORT_DIR) / (
        f"tool_levels_{_dt.date.today().isoformat()}.md"
    )
    write_markdown_report(classifications, report_path)
    try:
        display_path: str = str(report_path.relative_to(_REPO_ROOT))
    except ValueError:
        # Report directory was outside the repo (e.g. tmp_path in tests).
        display_path = str(report_path)
    print(f"Report: {display_path}")
    unannotated = sum(1 for c in classifications if c.level == UNANNOTATED)
    if unannotated == 0:
        return 0
    if warn_mode:
        print(
            f"WARNING: {unannotated} tool(s) lack `x-action-level`. "
            "Run without --warn (or with --strict) for a CI-gate exit code.",
            file=sys.stderr,
        )
        return 0
    print(
        f"FAIL: {unannotated} tool(s) lack `x-action-level`. "
        "See report for the list.",
        file=sys.stderr,
    )
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    warn_mode = bool(args.warn)
    return run_audit(report_dir=args.report_dir, warn_mode=warn_mode)


if __name__ == "__main__":
    sys.exit(main())
