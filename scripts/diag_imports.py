"""Phase 12 — Graphviz-renderable import graph for the handlers package.

Usage:
    python scripts/diag_imports.py            # prints DOT to stdout
    python scripts/diag_imports.py > deps.dot
    dot -Tpng deps.dot -o deps.png

Walks `service/isaac_assist_service/chat/tools/handlers/*.py`, parses
each module's `from . import X` / `from .X import Y` statements, and
emits a DOT graph showing sibling-module dependencies.

Underscore-prefixed infrastructure modules (`_shared`, `_state`,
`_dispatch`, `__init__`) are nodes but are styled differently — they
are *allowed* to be referenced by every theme.

Per spec/IA_FULL_SPEC_2026-05-10.md Phase 12.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path
from typing import Iterator, Tuple

_REPO_ROOT = Path(__file__).parent.parent
_HANDLERS_DIR = (
    _REPO_ROOT
    / "service"
    / "isaac_assist_service"
    / "chat"
    / "tools"
    / "handlers"
)

THEMED_MODULES = (
    "scene_authoring", "physics", "robot", "sensors", "sdg", "training",
    "ros2", "teleop", "scene_blueprints", "diagnostics", "arena",
    "workflow", "resolve", "vision", "animation", "pick_place", "rendering",
)
INFRA_MODULES = ("_shared", "_state", "_dispatch")
ALL_MODULES = THEMED_MODULES + INFRA_MODULES


def _iter_intra_imports(module_path: Path) -> Iterator[Tuple[str, str]]:
    """Yield (source_module_name, target_sibling_name) for each intra-package
    import."""
    text = module_path.read_text()
    try:
        tree = ast.parse(text, filename=str(module_path))
    except SyntaxError:
        return
    src = module_path.stem
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.level != 1:
                continue
            if node.module is None:
                for alias in node.names:
                    if alias.name in ALL_MODULES:
                        yield src, alias.name
            else:
                top = node.module.split(".")[0]
                if top in ALL_MODULES:
                    yield src, top


def build_dot() -> str:
    """Render the handlers-package dependency graph as DOT."""
    edges: list[tuple[str, str]] = []
    for path in sorted(_HANDLERS_DIR.glob("*.py")):
        if path.stem == "__init__":
            continue
        edges.extend(_iter_intra_imports(path))

    lines = [
        "digraph handlers_imports {",
        "    rankdir=LR;",
        '    node [shape=box, fontname="Helvetica"];',
        '    edge [fontname="Helvetica", fontsize=10];',
        "",
        "    // Themed modules",
    ]
    for name in THEMED_MODULES:
        lines.append(f'    "{name}" [style=filled, fillcolor="#cfe2ff"];')
    lines.append("")
    lines.append("    // Infrastructure modules")
    for name in INFRA_MODULES:
        lines.append(
            f'    "{name}" [style=filled, fillcolor="#f8d7da", '
            f'shape=oval];'
        )
    lines.append("")
    lines.append("    // Edges")
    for src, dst in sorted(set(edges)):
        lines.append(f'    "{src}" -> "{dst}";')
    lines.append("}")
    return "\n".join(lines)


def main() -> int:
    print(build_dot())
    return 0


if __name__ == "__main__":
    sys.exit(main())
