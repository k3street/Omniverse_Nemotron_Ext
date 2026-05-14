"""T2.4 — Telemetry coverage scanner.

For each handler `_handle_<name>` in `chat/tools/handlers/`, check
whether the body or its module imports any of:

  - telemetry.emit / telemetry.record / telemetry.log
  - record_event / emit_event
  - the `@telemetry`-style decorator

If neither the function body nor decorator nor a module-level wrapper
provides telemetry, the handler is "telemetry-blind" — failures and
latency are invisible to ops.

Output: JSON summary + per-handler details.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Dict, List, Set, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
HANDLERS_ROOT = REPO_ROOT / "service" / "isaac_assist_service" / "chat" / "tools" / "handlers"

TELEMETRY_NAMES = {
    "emit", "record", "log_event", "record_event", "emit_event",
    "telemetry_record", "metrics_emit",
}
TELEMETRY_MODULE_HINTS = ("telemetry", "metrics", "observability")


def iter_py(root: Path) -> List[Path]:
    return sorted(
        p for p in root.rglob("*.py")
        if "__pycache__" not in p.parts and p.name != "__init__.py"
    )


def has_telemetry_decorator(func_node) -> bool:
    for dec in func_node.decorator_list:
        src = ast.unparse(dec)
        if any(hint in src.lower() for hint in TELEMETRY_MODULE_HINTS):
            return True
    return False


def has_telemetry_call(func_node) -> bool:
    """Walk function body for telemetry emit/record calls."""
    for node in ast.walk(func_node):
        if isinstance(node, ast.Call):
            # bare function: emit(...), record_event(...)
            if isinstance(node.func, ast.Name) and node.func.id in TELEMETRY_NAMES:
                return True
            # attribute: telemetry.emit(...), metrics.record(...)
            if isinstance(node.func, ast.Attribute):
                if node.func.attr in TELEMETRY_NAMES:
                    return True
                if isinstance(node.func.value, ast.Name) and any(
                    h in node.func.value.id.lower() for h in TELEMETRY_MODULE_HINTS
                ):
                    return True
    return False


def module_imports_telemetry(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            mod = (node.module or "").lower()
            if any(h in mod for h in TELEMETRY_MODULE_HINTS):
                return True
        if isinstance(node, ast.Import):
            for alias in node.names:
                if any(h in alias.name.lower() for h in TELEMETRY_MODULE_HINTS):
                    return True
    return False


def main():
    handlers: List[Dict] = []
    for path in iter_py(HANDLERS_ROOT):
        try:
            tree = ast.parse(path.read_text())
        except SyntaxError:
            continue
        module_has_telemetry_import = module_imports_telemetry(tree)
        rel_path = str(path.relative_to(REPO_ROOT))
        for node in ast.walk(tree):
            if (
                isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name.startswith("_handle_")
            ):
                if has_telemetry_decorator(node):
                    status = "DECORATED"
                elif has_telemetry_call(node):
                    status = "EMITS_INLINE"
                elif module_has_telemetry_import:
                    status = "MODULE_IMPORTS_BUT_HANDLER_DOESNT_USE"
                else:
                    status = "TELEMETRY_BLIND"
                handlers.append({
                    "handler": node.name,
                    "file": rel_path,
                    "status": status,
                })

    summary = {
        "total_handlers": len(handlers),
        "decorated": sum(1 for h in handlers if h["status"] == "DECORATED"),
        "emits_inline": sum(1 for h in handlers if h["status"] == "EMITS_INLINE"),
        "module_only": sum(1 for h in handlers if h["status"] == "MODULE_IMPORTS_BUT_HANDLER_DOESNT_USE"),
        "telemetry_blind": sum(1 for h in handlers if h["status"] == "TELEMETRY_BLIND"),
    }
    summary["coverage_pct"] = round(
        (summary["decorated"] + summary["emits_inline"]) / max(1, summary["total_handlers"]) * 100, 1
    )
    print(json.dumps({"summary": summary, "details": handlers}, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
