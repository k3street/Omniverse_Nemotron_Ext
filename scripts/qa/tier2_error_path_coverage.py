"""T2.2 — Error-path coverage scanner.

For each handler `_handle_<name>` in `chat/tools/handlers/`, check if
at least one test file under `tests/` mentions that handler AND
contains an assertion looking for `success: False` / `error` / failure.

Heuristic-based: doesn't guarantee the test actually triggers the
error path, but a handler with no error-related test mention is
almost certainly under-tested.

Output: JSON list of handlers + their test status.
"""
from __future__ import annotations

import ast
import json
import sys
from pathlib import Path
from typing import Dict, List

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
HANDLERS_ROOT = REPO_ROOT / "service" / "isaac_assist_service" / "chat" / "tools" / "handlers"
TESTS_ROOT = REPO_ROOT / "tests"


def iter_py(root: Path, prefix: str = "") -> List[Path]:
    out = []
    for p in root.rglob("*.py"):
        if "__pycache__" in p.parts or p.name == "__init__.py":
            continue
        if prefix and not p.name.startswith(prefix):
            continue
        out.append(p)
    return sorted(out)


def find_handlers() -> List[str]:
    handlers = []
    for path in iter_py(HANDLERS_ROOT):
        try:
            tree = ast.parse(path.read_text())
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if (
                isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name.startswith("_handle_")
            ):
                handlers.append(node.name)
    return sorted(set(handlers))


def build_test_index() -> Dict[str, List[str]]:
    """Map each handler name -> [test_file paths that mention it]."""
    index: Dict[str, List[str]] = {}
    test_files = iter_py(TESTS_ROOT, "test_")
    handlers = find_handlers()
    for h in handlers:
        # Look for `_handle_<name>` OR the bare tool name without the prefix
        tool_name = h[len("_handle_"):]
        index[h] = []
        for tf in test_files:
            text = tf.read_text(errors="ignore")
            if h in text or f'"{tool_name}"' in text or f"'{tool_name}'" in text:
                index[h].append(str(tf.relative_to(REPO_ROOT)))
    return index


def has_error_assertion(file_paths: List[str]) -> bool:
    """Does any of these test files contain a failure-path assertion?"""
    patterns = [
        '"success": False',
        "'success': False",
        '"error"',
        "'error'",
        "assert.*error",
        "pytest.raises",
        "with raises",
    ]
    for fp in file_paths:
        text = (REPO_ROOT / fp).read_text(errors="ignore")
        if any(p in text for p in patterns):
            return True
    return False


def main():
    handlers = find_handlers()
    index = build_test_index()

    result = []
    for h in handlers:
        test_files = index[h]
        if not test_files:
            status = "NO_TEST_MENTIONS_HANDLER"
        elif has_error_assertion(test_files):
            status = "HAS_ERROR_PATH_ASSERT"
        else:
            status = "TEST_MENTIONS_BUT_NO_ERROR_ASSERT"
        result.append({
            "handler": h,
            "status": status,
            "test_file_count": len(test_files),
        })

    summary = {
        "total_handlers": len(handlers),
        "no_test_mentions": sum(1 for r in result if r["status"] == "NO_TEST_MENTIONS_HANDLER"),
        "no_error_assert": sum(1 for r in result if r["status"] == "TEST_MENTIONS_BUT_NO_ERROR_ASSERT"),
        "has_error_assert": sum(1 for r in result if r["status"] == "HAS_ERROR_PATH_ASSERT"),
    }
    summary["coverage_pct"] = round(
        summary["has_error_assert"] / max(1, summary["total_handlers"]) * 100, 1
    )
    print(json.dumps({"summary": summary, "details": result}, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
