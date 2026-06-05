"""TI.1 — Dependency graph exporter.

Emits a JSON description of the module import-graph for
`service/isaac_assist_service/`. Each node is a module; each edge is
an import relationship.

Output structure:
{
  "nodes": [{"id": "service.isaac_assist_service.chat.orchestrator", ...}],
  "edges": [{"from": "...", "to": "...", "kind": "absolute|relative"}],
  "stats": {"module_count", "edge_count", "cycles": [...]},
  "fan_out": {"<module>": <count>},   # how many other modules it imports
  "fan_in": {"<module>": <count>}     # how many other modules import it
}

Use this for "is architecture sound?" judgement: high fan-in modules
are coupling hot-spots; cycles indicate refactor pressure.
"""
from __future__ import annotations

import ast
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Set, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SERVICE_ROOT = REPO_ROOT / "service" / "isaac_assist_service"
PKG_PREFIX = "service.isaac_assist_service"


def iter_py(root: Path) -> List[Path]:
    out = []
    for p in root.rglob("*.py"):
        if "__pycache__" in p.parts:
            continue
        out.append(p)
    return sorted(out)


def module_name_for(path: Path) -> str:
    rel = path.relative_to(REPO_ROOT)
    parts = list(rel.parts)
    if parts[-1] == "__init__.py":
        parts = parts[:-1]
    else:
        parts[-1] = parts[-1][:-3]  # strip .py
    return ".".join(parts)


def extract_imports(tree: ast.AST, this_mod: str) -> List[Tuple[str, str]]:
    """Return [(imported_mod, kind)] for this module.

    Only top-level imports count for cycle detection. Imports inside
    functions are explicit cycle-breakers (lazy imports) and must not be
    treated as load-time edges.
    """
    out = []
    # Walk only top-level statements + their direct (non-function) children.
    SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)

    def walk_module(n: ast.AST):
        for child in ast.iter_child_nodes(n):
            if isinstance(child, SCOPES):
                continue  # imports inside functions/classes are lazy
            if isinstance(child, ast.Import):
                for alias in child.names:
                    if alias.name.startswith(PKG_PREFIX):
                        out.append((alias.name, "absolute"))
            elif isinstance(child, ast.ImportFrom):
                if child.level > 0:
                    parts = this_mod.split(".")
                    base = parts[: len(parts) - child.level]
                    if child.module:
                        base.append(child.module)
                    resolved = ".".join(base)
                    if resolved.startswith(PKG_PREFIX):
                        out.append((resolved, "relative"))
                elif child.module and child.module.startswith(PKG_PREFIX):
                    out.append((child.module, "absolute"))
            else:
                walk_module(child)  # walk non-scope blocks (If, Try, With)

    walk_module(tree)
    return out


def find_cycles(graph: Dict[str, Set[str]]) -> List[List[str]]:
    """DFS-based cycle detection on the import graph."""
    cycles = []
    color: Dict[str, int] = {}  # 0 = white, 1 = gray, 2 = black
    stack: List[str] = []

    def visit(u: str):
        if color.get(u, 0) == 2:
            return
        if color.get(u, 0) == 1:
            # back-edge — cycle found
            idx = stack.index(u)
            cycles.append(stack[idx:] + [u])
            return
        color[u] = 1
        stack.append(u)
        for v in graph.get(u, set()):
            visit(v)
        stack.pop()
        color[u] = 2

    for node in graph:
        visit(node)
    return cycles[:20]  # cap for sanity


def main():
    nodes: Set[str] = set()
    edges: List[Dict] = []
    graph: Dict[str, Set[str]] = defaultdict(set)
    fan_in: Dict[str, int] = defaultdict(int)
    fan_out: Dict[str, int] = defaultdict(int)

    for path in iter_py(SERVICE_ROOT):
        mod = module_name_for(path)
        nodes.add(mod)
        try:
            tree = ast.parse(path.read_text())
        except (SyntaxError, UnicodeDecodeError):
            continue
        for imported, kind in extract_imports(tree, mod):
            edges.append({"from": mod, "to": imported, "kind": kind})
            graph[mod].add(imported)
            fan_out[mod] += 1
            fan_in[imported] += 1
            nodes.add(imported)

    cycles = find_cycles(graph)

    out = {
        "nodes": sorted(nodes),
        "edges": edges,
        "stats": {
            "module_count": len(nodes),
            "edge_count": len(edges),
            "cycle_count": len(cycles),
            "cycle_samples": cycles[:5],
        },
        "fan_in_top_10": dict(
            sorted(fan_in.items(), key=lambda kv: kv[1], reverse=True)[:10]
        ),
        "fan_out_top_10": dict(
            sorted(fan_out.items(), key=lambda kv: kv[1], reverse=True)[:10]
        ),
    }
    print(json.dumps(out, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
