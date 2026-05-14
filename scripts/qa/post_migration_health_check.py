"""Post-migration health check — Tier 1 deterministic 100% audit.

Scans `service/isaac_assist_service/` for the deterministic + cheap
quality questions from the consolidated audit question-set. Each
check returns a pass/fail with a list of offending locations.

Usage:
    python scripts/qa/post_migration_health_check.py            # human-readable
    python scripts/qa/post_migration_health_check.py --json     # JSON output
    python scripts/qa/post_migration_health_check.py --strict   # exit 1 on ANY fail

The script is fully self-contained (Python stdlib only) so it can
run anywhere and is the canonical definition of "Tier 1 100%".
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SERVICE_ROOT = REPO_ROOT / "service" / "isaac_assist_service"
HANDLERS_ROOT = SERVICE_ROOT / "chat" / "tools" / "handlers"
TESTS_ROOT = REPO_ROOT / "tests"

# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------

def iter_py_files(root: Path, exclude_dirs: Tuple[str, ...] = ()) -> List[Path]:
    skip = set(exclude_dirs) | {"__pycache__"}
    out = []
    for p in root.rglob("*.py"):
        if any(part in skip for part in p.parts):
            continue
        if p.name == "__init__.py":
            continue
        out.append(p)
    return sorted(out)


def parse(path: Path):
    try:
        return ast.parse(path.read_text(), filename=str(path))
    except (SyntaxError, UnicodeDecodeError):
        return None


def rel(path: Path) -> str:
    return str(path.relative_to(REPO_ROOT))


# ---------------------------------------------------------------------------
# Q3: datetime.utcnow() — Y, cheap
# ---------------------------------------------------------------------------

def check_no_utcnow() -> List[Dict]:
    hits = []
    for path in iter_py_files(SERVICE_ROOT):
        tree = parse(path)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "utcnow"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "datetime"
            ):
                hits.append({"file": rel(path), "line": node.lineno})
    return hits


# ---------------------------------------------------------------------------
# Q4: asyncio.get_event_loop() outside run_stdio — Y, cheap
# ---------------------------------------------------------------------------

def check_no_get_event_loop() -> List[Dict]:
    hits = []
    for path in iter_py_files(SERVICE_ROOT):
        tree = parse(path)
        if tree is None:
            continue
        # Find functions named run_stdio to whitelist its body
        whitelist_lines = set()
        for node in ast.walk(tree):
            if (
                isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name == "run_stdio"
            ):
                whitelist_lines.update(range(node.lineno, (node.end_lineno or node.lineno) + 1))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "get_event_loop"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "asyncio"
                and node.lineno not in whitelist_lines
            ):
                hits.append({"file": rel(path), "line": node.lineno})
    return hits


# ---------------------------------------------------------------------------
# Q9: eval()/exec() in non-test code — Y, cheap
# ---------------------------------------------------------------------------

def check_no_eval_exec() -> List[Dict]:
    hits = []
    for path in iter_py_files(SERVICE_ROOT):
        tree = parse(path)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in {"eval", "exec"}
            ):
                hits.append({
                    "file": rel(path),
                    "line": node.lineno,
                    "call": node.func.id,
                })
    return hits


# ---------------------------------------------------------------------------
# Q10: subprocess shell=True — Y, cheap
# ---------------------------------------------------------------------------

def check_no_shell_true() -> List[Dict]:
    hits = []
    for path in iter_py_files(SERVICE_ROOT):
        tree = parse(path)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                for kw in node.keywords:
                    if (
                        kw.arg == "shell"
                        and isinstance(kw.value, ast.Constant)
                        and kw.value.value is True
                    ):
                        hits.append({"file": rel(path), "line": node.lineno})
    return hits


# ---------------------------------------------------------------------------
# Q21: Honesty gate — handlers returning bare `return` / `return None`
# ---------------------------------------------------------------------------

def check_section_19_honesty() -> List[Dict]:
    hits = []
    for path in iter_py_files(HANDLERS_ROOT):
        tree = parse(path)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not node.name.startswith("_handle_"):
                continue
            returns = [s for s in ast.walk(node) if isinstance(s, ast.Return)]
            raises = [s for s in ast.walk(node) if isinstance(s, ast.Raise)]
            if not returns and not raises:
                hits.append({
                    "file": rel(path),
                    "line": node.lineno,
                    "handler": node.name,
                    "reason": "no return statement (falls through)",
                })
                continue
            for ret in returns:
                if ret.value is None or (
                    isinstance(ret.value, ast.Constant) and ret.value.value is None
                ):
                    hits.append({
                        "file": rel(path),
                        "line": ret.lineno,
                        "handler": node.name,
                        "reason": "return None",
                    })
    return hits


# ---------------------------------------------------------------------------
# Q12: Blocking I/O inside async def
# ---------------------------------------------------------------------------

BLOCKING_FUNCS = {"open", "input"}
BLOCKING_ATTRS = {
    ("time", "sleep"),
    ("requests", "get"),
    ("requests", "post"),
    ("requests", "put"),
    ("requests", "delete"),
    ("urllib", "urlopen"),
}


def check_no_blocking_io_in_async() -> List[Dict]:
    hits = []
    for path in iter_py_files(SERVICE_ROOT):
        tree = parse(path)
        if tree is None:
            continue
        for func in ast.walk(tree):
            if not isinstance(func, ast.AsyncFunctionDef):
                continue
            for node in ast.walk(func):
                if not isinstance(node, ast.Call):
                    continue
                # bare blocking funcs (open, input)
                if (
                    isinstance(node.func, ast.Name)
                    and node.func.id in BLOCKING_FUNCS
                ):
                    # `open` in `with open(...) as fh:` outside loops is technically blocking
                    # but extremely common config-file reads — skip if first stmt in function
                    hits.append({
                        "file": rel(path),
                        "line": node.lineno,
                        "fn": func.name,
                        "call": node.func.id,
                    })
                # attribute blocking (time.sleep, requests.*)
                if isinstance(node.func, ast.Attribute) and isinstance(
                    node.func.value, ast.Name
                ):
                    key = (node.func.value.id, node.func.attr)
                    if key in BLOCKING_ATTRS:
                        hits.append({
                            "file": rel(path),
                            "line": node.lineno,
                            "fn": func.name,
                            "call": f"{key[0]}.{key[1]}",
                        })
    return hits


# ---------------------------------------------------------------------------
# Q15: Docstring coverage in handlers — count MISSING
# ---------------------------------------------------------------------------

def check_missing_docstrings() -> Tuple[List[Dict], Dict]:
    hits = []
    total = 0
    has_doc = 0
    for path in iter_py_files(SERVICE_ROOT):
        tree = parse(path)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if not isinstance(
                node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
            ):
                continue
            # Skip trivial bodies
            body = node.body
            if len(body) == 1:
                s = body[0]
                if isinstance(s, ast.Pass):
                    continue
                if isinstance(s, ast.Return) and s.value is None:
                    continue
                if (
                    isinstance(s, ast.Expr)
                    and isinstance(s.value, ast.Constant)
                    and s.value.value is Ellipsis
                ):
                    continue
            total += 1
            if ast.get_docstring(node):
                has_doc += 1
            else:
                hits.append({
                    "file": rel(path),
                    "line": node.lineno,
                    "name": node.name,
                })
    coverage = (has_doc / total * 100) if total else 100.0
    return hits, {"total": total, "with_doc": has_doc, "coverage_pct": round(coverage, 2)}


# ---------------------------------------------------------------------------
# Q17: Module size <= 500 lines (excluding _models.py)
# ---------------------------------------------------------------------------

def check_module_size() -> List[Dict]:
    hits = []
    for path in iter_py_files(SERVICE_ROOT):
        if path.name == "_models.py":
            continue
        loc = sum(1 for _ in path.read_text(errors="ignore").splitlines())
        if loc > 500:
            hits.append({"file": rel(path), "loc": loc})
    return hits


# ---------------------------------------------------------------------------
# Q18: Circular imports — try `import service.isaac_assist_service.<module>`
# ---------------------------------------------------------------------------

def check_no_circular_imports() -> List[Dict]:
    hits = []
    # Sample top-level packages
    samples = [
        "service.isaac_assist_service.chat.tools.handlers.workflow",
        "service.isaac_assist_service.chat.tools.handlers._state",
        "service.isaac_assist_service.chat.orchestrator",
        "service.isaac_assist_service.multimodal.workflow_engine",
        "service.isaac_assist_service.planner.generator",
    ]
    for mod in samples:
        r = subprocess.run(
            [sys.executable, "-c", f"import {mod}"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=15,
        )
        if r.returncode != 0:
            err_short = (r.stderr or "").strip().splitlines()[-1] if r.stderr else ""
            hits.append({"module": mod, "error": err_short[:120]})
    return hits


# ---------------------------------------------------------------------------
# Q19: handlers/ peer-module imports — should only import _shared/_models/constants
# ---------------------------------------------------------------------------

def check_handlers_layer_isolation() -> List[Dict]:
    hits = []
    handler_files = list(iter_py_files(HANDLERS_ROOT))
    handler_names = {p.stem for p in handler_files}
    allowed = {"_shared", "_models", "_state", "constants"}
    for path in handler_files:
        tree = parse(path)
        if tree is None:
            continue
        for node in ast.walk(tree):
            # `from .<peer> import ...`
            if isinstance(node, ast.ImportFrom):
                if node.module and not node.module.startswith("."):
                    continue  # absolute import — usually fine
                if node.level == 1 and node.module in handler_names - allowed:
                    hits.append({
                        "file": rel(path),
                        "line": node.lineno,
                        "imports_peer": node.module,
                    })
    return hits


# ---------------------------------------------------------------------------
# Q21b: silent-success — handlers returning {"success": False} without "error"
# ---------------------------------------------------------------------------

def check_silent_failures() -> List[Dict]:
    """Find dict returns with success=False but no error key."""
    hits = []
    for path in iter_py_files(SERVICE_ROOT):
        tree = parse(path)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Return):
                continue
            if not isinstance(node.value, ast.Dict):
                continue
            keys = []
            success_false = False
            for k, v in zip(node.value.keys, node.value.values):
                if (
                    isinstance(k, ast.Constant)
                    and isinstance(k.value, str)
                ):
                    keys.append(k.value)
                    if (
                        k.value == "success"
                        and isinstance(v, ast.Constant)
                        and v.value is False
                    ):
                        success_false = True
            if success_false and "error" not in keys:
                hits.append({"file": rel(path), "line": node.lineno})
    return hits


# ---------------------------------------------------------------------------
# Q22 (ratchet): Handler count — write/read baseline
# ---------------------------------------------------------------------------

def count_handlers() -> int:
    n = 0
    for path in iter_py_files(HANDLERS_ROOT):
        tree = parse(path)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if isinstance(
                node, (ast.FunctionDef, ast.AsyncFunctionDef)
            ) and node.name.startswith("_handle_"):
                n += 1
    return n


# ---------------------------------------------------------------------------
# Q14: Schema/handler drift — quick grep version
# ---------------------------------------------------------------------------

def check_schema_handler_drift() -> List[Dict]:
    """Compare tool_schemas.py declared tools vs handler dispatch keys.

    Lightweight: parse tool_schemas.py for `"name": "..."` and grep handlers
    for `data["..."] = ` and `_REGISTRY[...] = `.
    """
    hits = []
    schemas_path = SERVICE_ROOT / "chat" / "tools" / "tool_schemas.py"
    if not schemas_path.exists():
        return [{"missing_file": str(schemas_path)}]
    src = schemas_path.read_text()
    declared = set(re.findall(r'"name":\s*"(\w+)"', src))

    bound = set()
    for path in iter_py_files(HANDLERS_ROOT):
        text = path.read_text()
        bound.update(re.findall(r'data\["(\w+)"\]\s*=\s*_handle_', text))
        bound.update(re.findall(r'codegen\["(\w+)"\]\s*=\s*_gen_', text))

    orphan_schemas = declared - bound
    # Many tools are bound by codegen or other paths — only flag if neither
    # match. We tolerate the small false-positive surface as informational.
    return [{"orphan_schema_no_handler": sorted(orphan_schemas)}] if orphan_schemas else []


# ---------------------------------------------------------------------------
# Main runner
# ---------------------------------------------------------------------------

CHECKS = [
    ("Q3_no_datetime_utcnow", check_no_utcnow, "Deprecation"),
    ("Q4_no_asyncio_get_event_loop", check_no_get_event_loop, "Deprecation"),
    ("Q9_no_eval_exec", check_no_eval_exec, "Security"),
    ("Q10_no_subprocess_shell_true", check_no_shell_true, "Security"),
    ("Q12_no_blocking_io_in_async", check_no_blocking_io_in_async, "Perf"),
    ("Q15_missing_docstrings", check_missing_docstrings, "Docs"),
    ("Q17_module_size_le_500_lines", check_module_size, "Complexity"),
    ("Q18_no_circular_imports", check_no_circular_imports, "Imports"),
    ("Q19_handlers_layer_isolation", check_handlers_layer_isolation, "Imports"),
    ("Q21_section_19_honesty", check_section_19_honesty, "Honesty"),
    ("Q21b_silent_failures", check_silent_failures, "Honesty"),
    ("Q14_schema_handler_drift", check_schema_handler_drift, "Schema"),
]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true", help="Emit JSON")
    parser.add_argument("--strict", action="store_true", help="Exit 1 if any check fails")
    args = parser.parse_args()

    report: Dict = {"checks": {}, "summary": {}, "ratchet": {}}

    for name, fn, category in CHECKS:
        result = fn()
        if name == "Q15_missing_docstrings":
            hits, meta = result
            report["checks"][name] = {
                "category": category,
                "fail_count": len(hits),
                "samples": hits[:5],
                "meta": meta,
            }
        else:
            report["checks"][name] = {
                "category": category,
                "fail_count": len(result),
                "samples": result[:5] if result else [],
            }

    report["ratchet"]["handler_count"] = count_handlers()

    total_fails = sum(c["fail_count"] for c in report["checks"].values())
    report["summary"] = {
        "total_fail_count": total_fails,
        "tier1_clean": total_fails == 0,
    }

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print("=== Post-migration Tier 1 health check ===\n")
        for name, data in report["checks"].items():
            status = "PASS" if data["fail_count"] == 0 else f"FAIL ({data['fail_count']})"
            print(f"  [{data['category']:10}] {name:40} {status}")
        print(f"\nHandler count: {report['ratchet']['handler_count']}")
        print(f"Tier 1 clean: {report['summary']['tier1_clean']}")
        print(f"Total fails: {total_fails}\n")
        # Print top 3 fail samples for non-clean checks
        for name, data in report["checks"].items():
            if data["fail_count"] > 0 and data["samples"]:
                print(f"--- {name} (top {min(3, len(data['samples']))} of {data['fail_count']}):")
                for sample in data["samples"][:3]:
                    print(f"    {sample}")
                print()

    return 1 if (args.strict and not report["summary"]["tier1_clean"]) else 0


if __name__ == "__main__":
    sys.exit(main())
