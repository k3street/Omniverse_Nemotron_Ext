"""TI.3 — Cyclomatic complexity histogram.

Pure-AST CC computation (no `radon` dependency). For each function in
`service/isaac_assist_service/`:

  CC = 1 + (number of branching nodes)

Branching nodes counted: If, For, While, AsyncFor, ExceptHandler,
With, AsyncWith, BoolOp (each operand), comprehension `if` clauses.

Output: histogram of CC values + list of top-N most-complex functions.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import List, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SERVICE_ROOT = REPO_ROOT / "service" / "isaac_assist_service"


def iter_py(root: Path) -> List[Path]:
    return sorted(
        p for p in root.rglob("*.py")
        if "__pycache__" not in p.parts and p.name != "__init__.py"
    )


def cyclomatic_complexity(func: ast.AST) -> int:
    cc = 1
    for node in ast.walk(func):
        if isinstance(node, (ast.If, ast.For, ast.AsyncFor, ast.While,
                             ast.ExceptHandler, ast.With, ast.AsyncWith,
                             ast.Assert)):
            cc += 1
        elif isinstance(node, ast.BoolOp):
            # each additional `and`/`or` adds 1
            cc += len(node.values) - 1
        elif isinstance(node, ast.comprehension):
            cc += 1
            cc += len(node.ifs)  # each `if` filter in a comp is a branch
        elif isinstance(node, ast.Try):
            cc += len(node.handlers)
        elif isinstance(node, ast.IfExp):  # ternary
            cc += 1
    return cc


def main():
    results: List[Tuple[str, str, int, int]] = []  # (file, name, lineno, cc)

    for path in iter_py(SERVICE_ROOT):
        try:
            tree = ast.parse(path.read_text())
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                cc = cyclomatic_complexity(node)
                results.append(
                    (str(path.relative_to(REPO_ROOT)), node.name, node.lineno, cc)
                )

    # Histogram
    buckets = {"1-5": 0, "6-10": 0, "11-15": 0, "16-25": 0, "26-50": 0, "50+": 0}
    for _, _, _, cc in results:
        if cc <= 5:
            buckets["1-5"] += 1
        elif cc <= 10:
            buckets["6-10"] += 1
        elif cc <= 15:
            buckets["11-15"] += 1
        elif cc <= 25:
            buckets["16-25"] += 1
        elif cc <= 50:
            buckets["26-50"] += 1
        else:
            buckets["50+"] += 1

    # Top 25 most complex
    top = sorted(results, key=lambda r: r[3], reverse=True)[:25]

    out = {
        "function_count": len(results),
        "histogram": buckets,
        "top_25_complex": [
            {"file": f, "func": n, "line": l, "cc": cc}
            for (f, n, l, cc) in top
        ],
        "summary": {
            "median": sorted([r[3] for r in results])[len(results) // 2] if results else 0,
            "max": max((r[3] for r in results), default=0),
            "over_15": sum(1 for r in results if r[3] > 15),
            "over_25": sum(1 for r in results if r[3] > 25),
        },
    }
    print(json.dumps(out, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
