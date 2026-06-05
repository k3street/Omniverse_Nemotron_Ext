#!/usr/bin/env python3
"""Phase 1 — Tool audit.

Establish a single ground-truth inventory of every tool name, every
handler, and every dispatch entry — with explicit status per name.

Outputs:
- `docs/forensics/tool_audit_{date}.md` — human-readable inventory
- (stdout) — summary counts + exit code

Status taxonomy per schema name:
- `real`             — name is in DATA_HANDLERS or CODE_GEN_HANDLERS with a callable value
- `none_explicit`    — name is in DATA_HANDLERS or CODE_GEN_HANDLERS with value None (intentional no-op)
- `composite`        — name is in BOTH DATA_HANDLERS and CODE_GEN_HANDLERS (the only legitimate case is `setup_pick_place_with_vision`)
- `ghost`            — name has a schema but no handler registration anywhere
- `dead_handler`     — handler registered but no schema (reported as a separate list)

The audit imports the live modules rather than AST-parsing, so any
runtime registration (`register_multimodal_handlers`,
`register_diagnose_handlers`, `register_bridge_handlers`, etc.) is
captured automatically.

Per IA_FULL_SPEC_2026-05-10.md Phase 1.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

# Make the project importable when run from repo root.
_REPO_ROOT = Path(__file__).parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


# ---------------------------------------------------------------------------
# Data shapes


@dataclass
class ToolEntry:
    name: str
    schema_present: bool
    in_data_handlers: bool
    data_handler_callable: bool  # True iff in DATA_HANDLERS and value is not None
    in_code_gen_handlers: bool
    code_gen_handler_callable: bool
    status: str = ""  # populated by classify()


@dataclass
class AuditReport:
    tools: list[ToolEntry] = field(default_factory=list)
    dead_handlers: list[str] = field(default_factory=list)
    # Names whose status is `composite` (in both dispatch dicts).
    composites: list[str] = field(default_factory=list)
    recovered_state_blocks: list[tuple[int, int]] = field(default_factory=list)
    # The 35,507 → current line count of tool_executor.py
    monolith_line_count: int = 0
    allowlist_used: list[str] = field(default_factory=list)

    def counts(self) -> Counter:
        return Counter(t.status for t in self.tools)


# Special-case tools handled inline by the LLM / orchestrator (not by a
# tool_executor handler). Source: tests/test_tool_schemas.py:_SPECIAL_CASE_TOOLS.
# Treated as "real" by this audit because they're intentionally outside the
# dispatch dicts.
_INLINE_HANDLED = frozenset(
    [
        "run_usd_script",
        "generate_scene_blueprint",
        "create_isaaclab_env",
        "launch_training",
        "vision_detect_objects",
        "vision_bounding_boxes",
        "vision_plan_trajectory",
        "vision_analyze_scene",
        "export_scene_package",
        "get_physics_errors",
        "check_collisions",
        "fix_error",
    ]
)

# The only legitimate composite — registered in both dicts deliberately.
_ALLOWED_COMPOSITES = frozenset(["setup_pick_place_with_vision"])


# ---------------------------------------------------------------------------
# Classification


def classify(
    name: str,
    in_data: bool,
    data_callable: bool,
    in_code: bool,
    code_callable: bool,
    allowlist_none: frozenset[str],
) -> str:
    """Five-way classifier for a schema name."""
    if name in _INLINE_HANDLED:
        return "real"  # handled outside dispatch dicts by design

    if in_data and in_code:
        if name in _ALLOWED_COMPOSITES:
            return "composite"
        return "composite_unexpected"  # surfaces in report

    if in_data:
        if data_callable:
            return "real"
        # Value is None
        return "none_explicit" if name in allowlist_none else "ghost_none"

    if in_code:
        if code_callable:
            return "real"
        return "none_explicit" if name in allowlist_none else "ghost_none"

    # In neither dispatch dict
    return "ghost"


# ---------------------------------------------------------------------------
# Live module inspection


def collect_inventory(allowlist_none: frozenset[str]) -> AuditReport:
    """Import the live modules and collect inventory."""
    from service.isaac_assist_service.chat.tools.tool_schemas import (
        ISAAC_SIM_TOOLS,
    )
    from service.isaac_assist_service.chat.tools.tool_executor import (
        CODE_GEN_HANDLERS,
        DATA_HANDLERS,
    )

    schema_names = [t["function"]["name"] for t in ISAAC_SIM_TOOLS]

    report = AuditReport(allowlist_used=sorted(allowlist_none))

    for name in schema_names:
        in_data = name in DATA_HANDLERS
        data_callable = in_data and DATA_HANDLERS[name] is not None
        in_code = name in CODE_GEN_HANDLERS
        code_callable = in_code and CODE_GEN_HANDLERS[name] is not None

        status = classify(
            name,
            in_data,
            data_callable,
            in_code,
            code_callable,
            allowlist_none,
        )
        report.tools.append(
            ToolEntry(
                name=name,
                schema_present=True,
                in_data_handlers=in_data,
                data_handler_callable=data_callable,
                in_code_gen_handlers=in_code,
                code_gen_handler_callable=code_callable,
                status=status,
            )
        )
        if status.startswith("composite"):
            report.composites.append(name)

    # Dead handlers: in a dispatch dict but no schema
    schema_set = set(schema_names)
    for handler_name in sorted(set(DATA_HANDLERS) | set(CODE_GEN_HANDLERS)):
        if handler_name not in schema_set:
            report.dead_handlers.append(handler_name)

    # Recovered-state forensic block (tool_executor.py:33-1572 per spec)
    te_path = (
        _REPO_ROOT
        / "service"
        / "isaac_assist_service"
        / "chat"
        / "tools"
        / "tool_executor.py"
    )
    if te_path.exists():
        text = te_path.read_text()
        report.monolith_line_count = text.count("\n") + 1
        start = None
        for i, line in enumerate(text.splitlines(), start=1):
            if "Recovered state" in line and start is None:
                start = i
            elif "End recovered state" in line and start is not None:
                report.recovered_state_blocks.append((start, i))
                start = None

    return report


# ---------------------------------------------------------------------------
# Report rendering


def render_markdown(report: AuditReport) -> str:
    counts = report.counts()
    by_status: dict[str, list[ToolEntry]] = {}
    for t in report.tools:
        by_status.setdefault(t.status, []).append(t)

    lines: list[str] = []
    lines.append(f"# Tool Audit — {_dt.date.today().isoformat()}")
    lines.append("")
    lines.append(
        f"**`tool_schemas.py` ISAAC_SIM_TOOLS:** {len(report.tools)} tools"
    )
    lines.append(
        f"**`tool_executor.py` monolith size:** {report.monolith_line_count} lines"
    )
    if report.recovered_state_blocks:
        for start, end in report.recovered_state_blocks:
            lines.append(
                f"**Recovered-state forensic block:** lines {start}-{end} "
                f"({end - start + 1} lines — treat as read-only until Phase 13)"
            )
    lines.append(f"**Dead handlers (registered but no schema):** {len(report.dead_handlers)}")
    lines.append("")
    lines.append("**Status counts:**")
    for status in sorted(counts):
        lines.append(f"- `{status}`: {counts[status]}")
    lines.append("")
    if report.composites:
        lines.append(f"**Composite registrations (in both DATA + CODE_GEN):** {len(report.composites)}")
        for name in report.composites:
            allowed = name in _ALLOWED_COMPOSITES
            note = "✓ allowlisted" if allowed else "⚠ unexpected"
            lines.append(f"- `{name}` ({note})")
        lines.append("")

    # Status-by-status detail tables
    detail_order = (
        "ghost",
        "ghost_none",
        "composite_unexpected",
        "none_explicit",
        "composite",
        "real",
    )
    for status in detail_order:
        rows = by_status.get(status, [])
        if not rows:
            continue
        lines.append(f"## `{status}` ({len(rows)})")
        lines.append("")
        if status == "real":
            # Just count, don't enumerate 400+ rows
            lines.append("(table omitted for brevity — these are healthy entries)")
            lines.append("")
            continue
        lines.append("| name | in DATA | DATA callable | in CODE_GEN | CODE_GEN callable |")
        lines.append("|---|---|---|---|---|")
        for t in sorted(rows, key=lambda x: x.name):
            lines.append(
                f"| `{t.name}` | {t.in_data_handlers} | {t.data_handler_callable} "
                f"| {t.in_code_gen_handlers} | {t.code_gen_handler_callable} |"
            )
        lines.append("")

    if report.dead_handlers:
        lines.append(f"## Dead handlers ({len(report.dead_handlers)})")
        lines.append("")
        lines.append(
            "Handlers registered in DATA_HANDLERS or CODE_GEN_HANDLERS but "
            "with no matching entry in `ISAAC_SIM_TOOLS`. These tools cannot "
            "be called by the LLM (no schema to advertise). Either add a "
            "schema or remove the registration."
        )
        lines.append("")
        for name in sorted(report.dead_handlers):
            lines.append(f"- `{name}`")
        lines.append("")

    if report.allowlist_used:
        lines.append("## Allowlist (`tests/fixtures/no_handler_tools.json`)")
        lines.append("")
        lines.append(
            "Schema names whose handler value is intentionally `None` "
            "(handled inline by the LLM, special-cased in the orchestrator, "
            "or stubbed pending integration work)."
        )
        lines.append("")
        for name in report.allowlist_used:
            lines.append(f"- `{name}`")
        lines.append("")

    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Fixture loading


def _default_fixture_path() -> Path:
    return _REPO_ROOT / "tests" / "fixtures" / "no_handler_tools.json"


def load_allowlist(path: Path | None = None) -> frozenset[str]:
    p = path or _default_fixture_path()
    if not p.exists():
        return frozenset()
    data = json.loads(p.read_text())
    return frozenset(data.get("none_handlers", []))


# ---------------------------------------------------------------------------
# CLI


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        default=None,
        help="Output path (default: docs/forensics/tool_audit_{today}.md)",
    )
    parser.add_argument(
        "--fixture",
        default=None,
        help="Path to no_handler_tools.json allowlist (default: tests/fixtures/no_handler_tools.json)",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    allowlist = load_allowlist(Path(args.fixture)) if args.fixture else load_allowlist()
    report = collect_inventory(allowlist)

    out_path = (
        Path(args.out)
        if args.out
        else _REPO_ROOT
        / "docs"
        / "forensics"
        / f"tool_audit_{_dt.date.today().isoformat()}.md"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(render_markdown(report))

    counts = report.counts()
    print(
        f"Wrote {out_path} — {len(report.tools)} schemas, "
        f"{len(report.dead_handlers)} dead handlers, "
        f"counts={dict(counts)}"
    )
    # Exit code: 0 if no ghosts; 1 if there are ghosts or unexpected composites.
    bad = (
        counts.get("ghost", 0)
        + counts.get("ghost_none", 0)
        + counts.get("composite_unexpected", 0)
    )
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
