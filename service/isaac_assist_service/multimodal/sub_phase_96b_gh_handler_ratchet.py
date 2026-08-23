"""Phase 96b — handler-count regression ratchet."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict

PHASE_ID = "96b"
PHASE_TITLE = "handler regression ratchet"
PHASE_STATUS = "landed"

DEFAULT_HANDLERS_ROOT = (
    Path(__file__).resolve().parents[1] / "chat" / "tools" / "handlers"
)


@dataclass(frozen=True)
class HandlerRatchetResult:
    current_count: int
    minimum_count: int
    passed: bool
    delta: int


def get_phase_metadata() -> Dict[str, Any]:
    return {
        "phase": PHASE_ID,
        "title": PHASE_TITLE,
        "status": PHASE_STATUS,
        "spec_ref": "specs/IA_FULL_SPEC_2026-05-10.md Phase 96b",
    }


def count_handlers(root: Path = DEFAULT_HANDLERS_ROOT) -> int:
    """Count concrete `_handle_*` functions beneath the handler package."""
    count = 0
    for path in sorted(root.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError):
            continue
        count += sum(
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name.startswith("_handle_")
            for node in ast.walk(tree)
        )
    return count


def check_handler_ratchet(
    minimum_count: int,
    root: Path = DEFAULT_HANDLERS_ROOT,
) -> HandlerRatchetResult:
    """Pass when the current handler count has not fallen below a baseline."""
    if minimum_count < 0:
        raise ValueError("minimum_count must be non-negative")
    current = count_handlers(root)
    return HandlerRatchetResult(
        current_count=current,
        minimum_count=minimum_count,
        passed=current >= minimum_count,
        delta=current - minimum_count,
    )
