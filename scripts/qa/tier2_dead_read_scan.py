"""T2.1 — Dead-read scanner.

Finds module-level identifiers that are READ in non-test code but never
WRITTEN anywhere in non-test code. Detects the EUREKA.runs anti-pattern:
a singleton that handlers read but no production path ever writes to.

Output: JSON list of `{name, file_with_def, read_sites, write_sites_in_tests_only}`.
"""
from __future__ import annotations

import ast
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Set

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SERVICE_ROOT = REPO_ROOT / "service" / "isaac_assist_service"
TESTS_ROOT = REPO_ROOT / "tests"


def iter_py_files(root: Path) -> List[Path]:
    return sorted(
        p for p in root.rglob("*.py")
        if "__pycache__" not in p.parts and p.name != "__init__.py"
    )


def parse(path: Path):
    try:
        return ast.parse(path.read_text(), filename=str(path))
    except (SyntaxError, UnicodeDecodeError):
        return None


def collect_module_level_dataclass_names(root: Path) -> Dict[str, Path]:
    """Find module-level assignments like `EUREKA = EurekaState()`.

    These are singletons — the variable is bound at module load time
    to a fresh instance. We track their names + defining file.
    """
    out: Dict[str, Path] = {}
    for path in iter_py_files(root):
        tree = parse(path)
        if tree is None:
            continue
        for node in tree.body:  # module-level only
            if isinstance(node, ast.Assign):
                for tgt in node.targets:
                    if (
                        isinstance(tgt, ast.Name)
                        and isinstance(node.value, ast.Call)
                        and isinstance(node.value.func, ast.Name)
                        and tgt.id.isupper()
                    ):
                        out[tgt.id] = path
    return out


def find_attribute_accesses(root: Path, names: Set[str]) -> Dict[str, Dict]:
    """For each name in `names`, find Attribute accesses like `EUREKA.runs`.

    Returns {qualified_name: {"reads": [(file, line)], "writes": [(file, line)]}}.
    A write is detected if the Attribute is the target of an Assign,
    AugAssign, or its parent is Subscript-assignment (`x[k] = v`).
    """
    data: Dict[str, Dict] = defaultdict(lambda: {"reads": [], "writes": []})
    for path in iter_py_files(root):
        tree = parse(path)
        if tree is None:
            continue
        rel_path = str(path.relative_to(REPO_ROOT))

        # First pass: collect write nodes (Attributes that are targets)
        write_nodes: Set[int] = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Assign, ast.AugAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for t in targets:
                    # `EUREKA.runs = ...` — direct attribute assignment
                    if isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name):
                        if t.value.id in names:
                            qn = f"{t.value.id}.{t.attr}"
                            data[qn]["writes"].append((rel_path, t.lineno))
                            write_nodes.add(id(t))
                    # `EUREKA.runs["..."] = ...` — subscript on attribute
                    if (
                        isinstance(t, ast.Subscript)
                        and isinstance(t.value, ast.Attribute)
                        and isinstance(t.value.value, ast.Name)
                        and t.value.value.id in names
                    ):
                        qn = f"{t.value.value.id}.{t.value.attr}"
                        data[qn]["writes"].append((rel_path, t.lineno))
                        write_nodes.add(id(t.value))

        # Second pass: collect read accesses
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id in names
                and id(node) not in write_nodes
            ):
                qn = f"{node.value.id}.{node.attr}"
                data[qn]["reads"].append((rel_path, node.lineno))

        # Also catch `.method.append`, `.method.update`, etc. — those are
        # mutating method calls that count as writes.
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Attribute)
                and isinstance(node.func.value.value, ast.Name)
                and node.func.value.value.id in names
                and node.func.attr in {"append", "update", "pop", "clear", "setdefault", "__setitem__"}
            ):
                qn = f"{node.func.value.value.id}.{node.func.value.attr}"
                # Promote the most-recent read on this line to a write
                data[qn]["writes"].append((rel_path, node.lineno))

    return data


def main():
    singletons = collect_module_level_dataclass_names(SERVICE_ROOT)
    prod_data = find_attribute_accesses(SERVICE_ROOT, set(singletons.keys()))
    test_data = find_attribute_accesses(TESTS_ROOT, set(singletons.keys()))

    dead_reads = []
    for qn, info in sorted(prod_data.items()):
        reads = info["reads"]
        prod_writes = info["writes"]
        test_writes = test_data.get(qn, {}).get("writes", [])

        if reads and not prod_writes:
            singleton_name = qn.split(".")[0]
            dead_reads.append({
                "qualified_name": qn,
                "defining_file": str(singletons[singleton_name].relative_to(REPO_ROOT)),
                "read_sites_in_prod": [{"file": f, "line": l} for f, l in reads[:10]],
                "read_count_in_prod": len(reads),
                "write_count_in_prod": 0,
                "write_count_in_tests": len(test_writes),
                "category": "DEAD_READ (read in prod, written only in tests)"
                if test_writes else "TRULY_DEAD (no writes anywhere)",
            })

    out = {
        "scan_root": str(SERVICE_ROOT.relative_to(REPO_ROOT)),
        "singletons_scanned": len(singletons),
        "dead_read_count": len(dead_reads),
        "dead_reads": dead_reads,
    }
    print(json.dumps(out, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
