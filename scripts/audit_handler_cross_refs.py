#!/usr/bin/env python3
"""Phase 2b (deliverable 1/3) — Handler cross-reference graph.

AST-walks `tool_executor.py` to find which handlers call which
other handlers and which module-level utilities. Outputs:

- `docs/audits/handler_cross_refs.json` — machine-readable graph
- `docs/audits/handler_cross_refs.md`   — human-readable summary

The summary highlights:
- Utilities used by ≥ N handlers (candidates for `handlers/_shared.py`
  in Phase 2 / refined in Phase 8). N default = 3 per spec text.
- Handlers with high outgoing fan-out (call many other handlers
  directly — these have cross-theme call dependencies and need
  careful theme assignment in Phase 3-7).

Per `specs/IA_FULL_SPEC_2026-05-10.md` Phase 2b.
"""
from __future__ import annotations

import argparse
import ast
import json
import sys
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable

_REPO_ROOT = Path(__file__).parent.parent
_TOOL_EXECUTOR = (
    _REPO_ROOT
    / "service"
    / "isaac_assist_service"
    / "chat"
    / "tools"
    / "tool_executor.py"
)


def _is_handler(name: str) -> bool:
    """Convention: handlers are `_handle_*` or `_gen_*` top-level functions."""
    return name.startswith("_handle_") or name.startswith("_gen_")


@dataclass
class CrossRefReport:
    handlers: list[str] = field(default_factory=list)
    utilities: list[str] = field(default_factory=list)
    edges: list[tuple[str, str]] = field(default_factory=list)
    utility_fan_in: dict[str, int] = field(default_factory=dict)
    handler_fan_out: dict[str, int] = field(default_factory=dict)
    high_fan_in_utilities: list[str] = field(default_factory=list)


def _collect_top_level_callables(tree: ast.Module) -> set[str]:
    """All module-level functions (sync + async)."""
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            names.add(node.name)
    return names


def _outgoing_references(
    func: ast.FunctionDef | ast.AsyncFunctionDef, top_level: set[str]
) -> set[str]:
    """Find module-level callable references inside one function body."""
    refs: set[str] = set()
    for child in ast.walk(func):
        # `Name` covers `_handle_foo(...)` and `_safe_set_translate(...)`.
        if isinstance(child, ast.Name) and child.id in top_level:
            refs.add(child.id)
        # `Attribute` covers `self._handle_foo` — rare for module-level
        # callables but cheap to include.
        if isinstance(child, ast.Attribute) and isinstance(child.attr, str):
            if child.attr in top_level:
                refs.add(child.attr)
    # Don't count self-references (a function calling itself recursively).
    refs.discard(func.name)
    return refs


def audit(path: Path = _TOOL_EXECUTOR, min_fan_in: int = 3) -> CrossRefReport:
    """Build the handler cross-reference graph from `path`."""
    text = path.read_text()
    tree = ast.parse(text, filename=str(path))

    top_level = _collect_top_level_callables(tree)
    handlers_sorted = sorted(n for n in top_level if _is_handler(n))
    utilities_sorted = sorted(n for n in top_level if not _is_handler(n))

    edges: list[tuple[str, str]] = []
    fan_in: Counter[str] = Counter()
    fan_out: Counter[str] = Counter()
    seen_per_caller: dict[str, set[str]] = defaultdict(set)

    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not _is_handler(node.name):
            continue
        for callee in _outgoing_references(node, top_level):
            if callee in seen_per_caller[node.name]:
                continue
            seen_per_caller[node.name].add(callee)
            edges.append((node.name, callee))
            fan_in[callee] += 1
            fan_out[node.name] += 1

    high_fan_in = sorted(
        [u for u, c in fan_in.items() if u in utilities_sorted and c >= min_fan_in],
        key=lambda u: (-fan_in[u], u),
    )

    return CrossRefReport(
        handlers=handlers_sorted,
        utilities=utilities_sorted,
        edges=edges,
        utility_fan_in={u: fan_in[u] for u in utilities_sorted if fan_in[u] > 0},
        handler_fan_out=dict(fan_out),
        high_fan_in_utilities=high_fan_in,
    )


def render_markdown(report: CrossRefReport, min_fan_in: int) -> str:
    lines: list[str] = []
    lines.append("# Handler Cross-Reference Audit")
    lines.append("")
    lines.append(f"**Handlers analysed:** {len(report.handlers)}")
    lines.append(f"**Module-level utilities:** {len(report.utilities)}")
    lines.append(f"**Handler→callee edges (deduped):** {len(report.edges)}")
    lines.append(f"**Min fan-in for `_shared.py` candidacy:** {min_fan_in}")
    lines.append("")

    lines.append("## High-fan-in utilities — `_shared.py` candidates")
    lines.append("")
    lines.append(
        "Module-level utilities called by ≥ "
        f"{min_fan_in} handlers. These belong in "
        "`handlers/_shared.py` (Phase 8 — but identifying them now "
        "lets Phase 3-7 avoid silent collisions between agents)."
    )
    lines.append("")
    if not report.high_fan_in_utilities:
        lines.append("(none)")
    else:
        lines.append("| utility | fan-in |")
        lines.append("|---|---|")
        for util in report.high_fan_in_utilities:
            lines.append(f"| `{util}` | {report.utility_fan_in[util]} |")
    lines.append("")

    lines.append("## Handlers with highest outgoing fan-out")
    lines.append("")
    lines.append(
        "Handlers calling many other module-level functions. High "
        "fan-out increases the cross-theme dependency risk when the "
        "handler moves out of `tool_executor.py` — review the call "
        "list and either move callees together or refactor to inject "
        "the dependency via `handlers/_shared.py`."
    )
    lines.append("")
    top_callers = sorted(
        report.handler_fan_out.items(), key=lambda kv: (-kv[1], kv[0])
    )[:20]
    if not top_callers:
        lines.append("(none)")
    else:
        lines.append("| handler | fan-out |")
        lines.append("|---|---|")
        for name, count in top_callers:
            lines.append(f"| `{name}` | {count} |")
    lines.append("")

    lines.append("## Full edge list — machine-readable")
    lines.append("")
    lines.append(
        f"See `docs/audits/handler_cross_refs.json` for the complete "
        f"edge list ({len(report.edges)} entries)."
    )
    lines.append("")

    return "\n".join(lines) + "\n"


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--path",
        default=str(_TOOL_EXECUTOR),
        help="Path to tool_executor.py (default: project location)",
    )
    parser.add_argument(
        "--min-fan-in",
        type=int,
        default=3,
        help="Min fan-in count for `_shared.py` candidacy (default: 3)",
    )
    parser.add_argument(
        "--md-out",
        default=str(_REPO_ROOT / "docs" / "audits" / "handler_cross_refs.md"),
    )
    parser.add_argument(
        "--json-out",
        default=str(_REPO_ROOT / "docs" / "audits" / "handler_cross_refs.json"),
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    report = audit(Path(args.path), min_fan_in=args.min_fan_in)

    md_out = Path(args.md_out)
    json_out = Path(args.json_out)
    md_out.parent.mkdir(parents=True, exist_ok=True)
    json_out.parent.mkdir(parents=True, exist_ok=True)

    md_out.write_text(render_markdown(report, args.min_fan_in))
    json_out.write_text(json.dumps(asdict(report), indent=2, sort_keys=True))

    print(
        f"Wrote {md_out} + {json_out} — "
        f"handlers={len(report.handlers)}, utilities={len(report.utilities)}, "
        f"edges={len(report.edges)}, "
        f"high-fan-in utilities={len(report.high_fan_in_utilities)}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
