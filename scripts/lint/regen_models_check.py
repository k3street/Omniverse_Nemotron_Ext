"""Phase 17 — Pydantic models regen freshness check.

When `tool_schemas.py` changes, the generated `handlers/_models.py`
must be regenerated to keep the typed-arg contract in sync.

This lint:
  1. Compares git-mtime of `tool_schemas.py` vs `handlers/_models.py`.
  2. If `_models.py` does not yet exist (pre-Phase-10 state), emit a
     SOFT warning and return 0 — we don't block commits until Phase 10
     ships the generator.
  3. After Phase 10: hard-fail when `_models.py` is older than
     `tool_schemas.py`.

Exit codes:
  0 — clean, or pre-Phase-10 soft warning
  1 — `_models.py` is stale and must be regenerated

Usage:
    python scripts/lint/regen_models_check.py

Per spec/IA_FULL_SPEC_2026-05-10.md Phase 17.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Iterable

_REPO_ROOT = Path(__file__).parent.parent.parent
_SCHEMAS = (
    _REPO_ROOT
    / "service"
    / "isaac_assist_service"
    / "chat"
    / "tools"
    / "tool_schemas.py"
)
_MODELS = (
    _REPO_ROOT
    / "service"
    / "isaac_assist_service"
    / "chat"
    / "tools"
    / "handlers"
    / "_models.py"
)


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Hard-fail when _models.py is missing (post-Phase-10 mode).",
    )
    args = parser.parse_args(argv)

    if not _SCHEMAS.exists():
        print(f"ERROR: {_SCHEMAS} does not exist", file=sys.stderr)
        return 2

    if not _MODELS.exists():
        if args.strict:
            print(
                f"ERROR: {_MODELS} does not exist. Run scripts/gen_handler_models.py "
                "to generate it.",
                file=sys.stderr,
            )
            return 1
        # Pre-Phase-10: soft warning
        print(
            f"WARNING: {_MODELS} does not exist yet (pre-Phase-10 state). "
            "After Phase 10 ships, this becomes a hard requirement.",
            file=sys.stderr,
        )
        return 0

    schemas_mtime = _SCHEMAS.stat().st_mtime
    models_mtime = _MODELS.stat().st_mtime

    if models_mtime < schemas_mtime:
        print(
            f"ERROR: {_MODELS} is older than {_SCHEMAS}.\n"
            f"  Run scripts/gen_handler_models.py to regenerate.\n"
            f"  schemas mtime: {schemas_mtime}\n"
            f"  models  mtime: {models_mtime}",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
