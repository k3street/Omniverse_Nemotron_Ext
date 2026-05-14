#!/usr/bin/env python3
"""Apply @with_telemetry decorator to all _handle_* functions in handler files.

Uses AST to find the correct last-import line (accounting for multi-line
imports), then inserts the import once and prepends @with_telemetry before
each _handle_ function definition that doesn't already have it.

Safe to re-run: idempotent.
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
HANDLERS_ROOT = REPO_ROOT / "service" / "isaac_assist_service" / "chat" / "tools" / "handlers"

IMPORT_STMT = "from service.isaac_assist_service.observability.handler_telemetry import with_telemetry"
IMPORT_LINE = IMPORT_STMT + "\n"

# Matches top-level _handle_ definitions (no leading spaces)
HANDLE_DEF_RE = re.compile(r'^(?:async )?def (_handle_\w+)\(')

SKIP_FILES = {"__init__.py", "_models.py", "_state.py"}


def find_last_import_line(tree: ast.Module) -> int:
    """Return the 1-based end_lineno of the last top-level import statement."""
    last = 0
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            last = node.end_lineno  # type: ignore[attr-defined]
    return last


def process_file(path: Path) -> int:
    """Returns number of decorators applied."""
    if path.name in SKIP_FILES:
        return 0

    text = path.read_text()

    # Quick check: does file have any _handle_ at all?
    if "_handle_" not in text:
        return 0

    try:
        tree = ast.parse(text)
    except SyntaxError as e:
        print(f"  SKIP (syntax error before edit): {path.name}: {e}", file=sys.stderr)
        return 0

    # Already imported?
    already_imported = IMPORT_STMT in text

    # Find last top-level import line (1-based)
    last_import_line = find_last_import_line(tree)  # 0 if no imports

    lines = text.splitlines(keepends=True)
    new_lines = list(lines)
    offset = 0  # track insertions

    decorated_count = 0

    for i, line in enumerate(lines):
        if HANDLE_DEF_RE.match(line):
            # Check previous non-empty line in new_lines at adjusted position
            adjusted_i = i + offset
            j = adjusted_i - 1
            while j >= 0 and new_lines[j].strip() == "":
                j -= 1
            prev = new_lines[j].strip() if j >= 0 else ""
            if prev != "@with_telemetry":
                new_lines.insert(adjusted_i, "@with_telemetry\n")
                offset += 1
                decorated_count += 1

    if decorated_count == 0:
        return 0

    # Insert import after last top-level import block
    if not already_imported:
        insert_at = (last_import_line if last_import_line > 0 else 0) + offset
        # offset already accounts for decorator inserts above, but import
        # inserts happen AFTER decorators are counted, so we must recalculate.
        # Rebuild: find how many decorator inserts happened before last_import_line
        decorators_before = sum(
            1 for i, line in enumerate(lines)
            if HANDLE_DEF_RE.match(line) and i < last_import_line
        )
        insert_at = last_import_line + decorators_before
        new_lines.insert(insert_at, IMPORT_LINE)

    result = "".join(new_lines)

    # Validate: must parse cleanly
    try:
        ast.parse(result)
    except SyntaxError as e:
        print(f"  ERROR: introduced syntax error in {path.name}: {e}", file=sys.stderr)
        return 0

    path.write_text(result)
    return decorated_count


def main():
    total = 0
    for path in sorted(HANDLERS_ROOT.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        count = process_file(path)
        if count:
            print(f"  {path.name}: +{count} decorators")
            total += count
    print(f"\nTotal decorators applied: {total}")


if __name__ == "__main__":
    main()
