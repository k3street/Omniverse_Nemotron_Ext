"""Phase 17 — Forbid new handlers in tool_executor.py.

Post-Phase-9 contract: `tool_executor.py` is reserved for the dispatch
core. New `_handle_X` and `_gen_X` handler definitions must live in
`handlers/<theme>.py`.

This lint walks `tool_executor.py` and fails if it sees:
  * A `def _handle_X(...)` or `def _gen_X(...)` top-level definition,
    EXCEPT for the documented straggler `_handle_fix_error` (will be
    migrated in a follow-on Phase 13/14 wave).
  * A top-level `DATA_HANDLERS["X"] = ...` or
    `CODE_GEN_HANDLERS["X"] = ...` assignment, EXCEPT for the same
    `fix_error` straggler.

Exit codes:
  0 — clean
  1 — at least one violation (with line-numbered listing)

Usage:
    python scripts/lint/no_handler_in_dispatch.py
    python scripts/lint/no_handler_in_dispatch.py service/.../tool_executor.py

Per spec/IA_FULL_SPEC_2026-05-10.md Phase 17.
"""
from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path
from typing import Iterable

# The single documented straggler that's allowed to stay in
# tool_executor.py for now. Phase 13/14 cleanup will move it.
ALLOWED_HANDLER_NAMES = frozenset({"_handle_fix_error"})
ALLOWED_DISPATCH_TOOLS = frozenset({"fix_error"})

_REPO_ROOT = Path(__file__).parent.parent.parent
_DEFAULT_TARGET = (
    _REPO_ROOT
    / "service"
    / "isaac_assist_service"
    / "chat"
    / "tools"
    / "tool_executor.py"
)


def _is_handler_def(node: ast.AST) -> bool:
    """True for top-level `def _handle_X(...)` or `def _gen_X(...)`."""
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return False
    name = node.name
    return name.startswith("_handle_") or name.startswith("_gen_")


def _is_dispatch_assign(node: ast.AST) -> tuple[bool, str | None]:
    """True for `DATA_HANDLERS["X"] = ...` or `CODE_GEN_HANDLERS["X"] = ...`.

    Returns (is_dispatch, tool_name) so the caller can check the
    straggler allowlist.
    """
    if not isinstance(node, ast.Assign):
        return False, None
    if len(node.targets) != 1:
        return False, None
    target = node.targets[0]
    if not isinstance(target, ast.Subscript):
        return False, None
    if not isinstance(target.value, ast.Name):
        return False, None
    if target.value.id not in ("DATA_HANDLERS", "CODE_GEN_HANDLERS"):
        return False, None
    # Extract subscript key
    slc = target.slice
    if isinstance(slc, ast.Constant) and isinstance(slc.value, str):
        return True, slc.value
    return True, None


def scan(path: Path) -> list[tuple[int, str]]:
    """Return list of (line_number, message) violations."""
    text = path.read_text()
    try:
        tree = ast.parse(text, filename=str(path))
    except SyntaxError as e:
        return [(e.lineno or 0, f"SyntaxError: {e.msg}")]

    violations: list[tuple[int, str]] = []
    for node in tree.body:
        if _is_handler_def(node):
            if node.name in ALLOWED_HANDLER_NAMES:
                continue
            violations.append((
                node.lineno,
                f"forbidden handler def {node.name!r} — move to handlers/<theme>.py",
            ))
        is_dispatch, tool_name = _is_dispatch_assign(node)
        if is_dispatch:
            if tool_name in ALLOWED_DISPATCH_TOOLS:
                continue
            label = tool_name or "<unknown>"
            violations.append((
                node.lineno,
                f"forbidden dispatch assignment for tool {label!r} — "
                f"register in handlers/<theme>.py:register()",
            ))
    return violations


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "paths",
        nargs="*",
        type=Path,
        default=[_DEFAULT_TARGET],
        help="Files to scan (default: tool_executor.py)",
    )
    args = parser.parse_args(argv)

    total_violations = 0
    for path in args.paths:
        if not path.exists():
            print(f"ERROR: {path} does not exist", file=sys.stderr)
            return 2
        violations = scan(path)
        if violations:
            for lineno, msg in violations:
                print(f"{path}:{lineno}: {msg}")
            total_violations += len(violations)

    if total_violations:
        print(
            f"\nFound {total_violations} violation(s). "
            "Move handlers/dispatch into handlers/<theme>.py per Phase 9 contract.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
