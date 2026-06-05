"""T2.3 — O(n²) stage-loop detector.

Finds nested for-loops where both the outer and inner loop iterate
over what looks like a stage prim collection. Common slow paths:

    for prim in stage.Traverse():
        for sibling in stage.Traverse():
            ...

For a scene with N prims this is N² traversal. With 2000-prim scenes
this is ~4M iterations.

Heuristic: outer + inner for-loop where the iter expression mentions
one of: `Traverse`, `GetPrim`, `prims`, `stage`. False positives
expected — emit candidates for manual judgment.

Output: JSON list of (file, outer_line, inner_line, snippet).
"""
from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import List

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SERVICE_ROOT = REPO_ROOT / "service" / "isaac_assist_service"

STAGE_TOKENS = ("Traverse", "GetPrim", "prims", "stage", "AllPrims")


def iter_py(root: Path) -> List[Path]:
    return sorted(
        p for p in root.rglob("*.py")
        if "__pycache__" not in p.parts and p.name != "__init__.py"
    )


def looks_like_stage_iter(node: ast.expr) -> bool:
    """Heuristic: does this iter-expression mention stage tokens?"""
    src = ast.unparse(node)
    return any(t in src for t in STAGE_TOKENS)


def main():
    hits = []
    for path in iter_py(SERVICE_ROOT):
        try:
            tree = ast.parse(path.read_text())
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.For):
                continue
            if not looks_like_stage_iter(node.iter):
                continue
            # Walk body for inner for-loops
            for inner in ast.walk(node):
                if inner is node or not isinstance(inner, ast.For):
                    continue
                if looks_like_stage_iter(inner.iter):
                    hits.append({
                        "file": str(path.relative_to(REPO_ROOT)),
                        "outer_line": node.lineno,
                        "inner_line": inner.lineno,
                        "outer_iter": ast.unparse(node.iter)[:80],
                        "inner_iter": ast.unparse(inner.iter)[:80],
                    })
    out = {
        "candidate_count": len(hits),
        "candidates": hits[:50],
        "note": "Heuristic — review each candidate manually. False positives expected.",
    }
    print(json.dumps(out, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
