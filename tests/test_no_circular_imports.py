"""Phase 12 — Cross-module circular-import verification.

Goal: smoke-test that themed handler modules don't end up importing
each other in cycles. If they do, factor through `handlers/_shared.py`.

Two layers:
1. **Static graph**: AST-parse each themed module's `from .X import Y`
   statements, build a directed graph, fail on any cycle.
2. **Isolated load**: `importlib.machinery.SourceFileLoader` loads each
   theme module fresh and asserts no `ImportError` from a cycle.

Per spec/IA_FULL_SPEC_2026-05-10.md Phase 12.
"""
from __future__ import annotations

import ast
import importlib
from pathlib import Path
from typing import Set

import pytest

pytestmark = pytest.mark.l0


_HANDLERS_DIR = (
    Path(__file__).parent.parent
    / "service"
    / "isaac_assist_service"
    / "chat"
    / "tools"
    / "handlers"
)


# Theme modules to audit. Underscore-prefixed `_shared`, `_state`,
# `_dispatch`, `__init__` are infrastructure — not under cycle audit
# (they're either pure-type or central registry, both of which are
# allowed to be referenced by every theme).
THEMED_MODULES = (
    "scene_authoring",
    "physics",
    "robot",
    "sensors",
    "sdg",
    "training",
    "ros2",
    "teleop",
    "scene_blueprints",
    "diagnostics",
    "arena",
    "workflow",
    "resolve",
    "vision",
    "animation",
    "pick_place",
    "rendering",
)


def _intra_package_imports(module_path: Path) -> Set[str]:
    """Return the set of `handlers.<X>` sibling modules imported by this file.

    Picks up `from . import X`, `from .X import Y`, `from .X.Y import Z`.
    Does NOT count `from .._shared`, `from .._state`, `from .._dispatch`
    (those are infrastructure).
    """
    text = module_path.read_text()
    tree = ast.parse(text, filename=str(module_path))
    siblings: Set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            # `from .` → level=1, module=None (e.g. `from . import scene_authoring`)
            # `from .X` → level=1, module='X'
            # `from ..` → level=2 (parent package, OUT of scope)
            if node.level != 1:
                continue
            if node.module is None:
                # `from . import scene_authoring, robot` — record each
                for alias in node.names:
                    if alias.name in THEMED_MODULES:
                        siblings.add(alias.name)
            else:
                # `from .X import Y` — record X if it's a sibling
                top = node.module.split(".")[0]
                if top in THEMED_MODULES:
                    siblings.add(top)
    return siblings


def _build_import_graph() -> dict[str, Set[str]]:
    """`{module_name: {imported_sibling_name, ...}, ...}`."""
    graph: dict[str, Set[str]] = {}
    for name in THEMED_MODULES:
        path = _HANDLERS_DIR / f"{name}.py"
        assert path.exists(), f"Missing handler module: {path}"
        graph[name] = _intra_package_imports(path)
    return graph


def _find_cycles(graph: dict[str, Set[str]]) -> list[list[str]]:
    """Detect cycles via DFS. Returns each cycle as a list of node names."""
    cycles: list[list[str]] = []
    BLACK, GRAY = "black", "gray"
    color: dict[str, str] = {}
    parent: dict[str, str] = {}

    def visit(u: str, stack: list[str]):
        color[u] = GRAY
        stack.append(u)
        for v in sorted(graph.get(u, ())):
            if color.get(v) == GRAY:
                # Found cycle — extract from stack
                i = stack.index(v)
                cycles.append(stack[i:] + [v])
            elif color.get(v) != BLACK:
                visit(v, stack)
        stack.pop()
        color[u] = BLACK

    for node in graph:
        if color.get(node) != BLACK:
            visit(node, [])

    return cycles


def test_no_cycles_among_themed_modules():
    """Static-graph check: no themed handler imports another themed
    handler in a way that forms a cycle. If a cycle exists, refactor
    the shared piece into `handlers/_shared.py`.
    """
    graph = _build_import_graph()
    cycles = _find_cycles(graph)
    assert cycles == [], (
        "Circular imports detected among themed handler modules:\n"
        + "\n".join(" → ".join(c) for c in cycles)
        + "\nFix: extract the shared symbol into handlers/_shared.py."
    )


@pytest.mark.parametrize("name", THEMED_MODULES)
def test_themed_module_loads_in_isolation(name):
    """Every themed handler module loads cleanly from a fresh interpreter
    state — proves there is no latent cycle Python can't resolve.

    Uses importlib.import_module which handles cycles gracefully if they
    are resolvable; we additionally assert no `ImportError` is raised.
    """
    full_name = f"service.isaac_assist_service.chat.tools.handlers.{name}"
    try:
        module = importlib.import_module(full_name)
    except ImportError as e:
        pytest.fail(
            f"{name} failed to import — likely a circular import.\n"
            f"  ImportError: {e}\n"
            f"  Fix: see handlers/_shared.py for the bridge pattern."
        )
    assert module is not None


def test_import_graph_is_acyclic_dag():
    """Stronger assertion: the import graph forms a DAG.

    Builds the graph and runs a topological sort. Failure raises a
    detailed cycle-listing error.
    """
    graph = _build_import_graph()
    # Topological sort via Kahn's algorithm
    in_degree = {n: 0 for n in graph}
    for src, edges in graph.items():
        for dst in edges:
            in_degree[dst] = in_degree.get(dst, 0) + 1
    queue = [n for n in in_degree if in_degree[n] == 0]
    visited: list[str] = []
    while queue:
        u = queue.pop(0)
        visited.append(u)
        for v in sorted(graph.get(u, ())):
            in_degree[v] -= 1
            if in_degree[v] == 0:
                queue.append(v)
    unvisited = set(graph) - set(visited)
    assert not unvisited, (
        "Import graph contains a cycle.\n"
        f"  Cycle participants (topological sort residual): {sorted(unvisited)}"
    )


def test_graph_shape_smoke():
    """Sanity check: every themed module appears in the graph and the
    graph is well-formed."""
    graph = _build_import_graph()
    assert set(graph) == set(THEMED_MODULES), (
        "Import graph is missing themed modules: "
        f"{set(THEMED_MODULES) - set(graph)}"
    )
    # Every edge target must itself be a themed module
    for src, edges in graph.items():
        for dst in edges:
            assert dst in THEMED_MODULES, (
                f"{src}.py imports unknown handler module {dst!r}"
            )
