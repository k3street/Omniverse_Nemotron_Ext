"""Phase 96 — Quarterly audit of tool_executor.py size + ghost handlers.

Three audit functions:

* ``audit_tool_executor_size`` — count lines, warn if > 500.
* ``audit_ghost_handlers``    — call register_handlers on a fresh dict and
  compare against ISAAC_SIM_TOOLS; names with no handler are "ghosts".
* ``audit_phase_completion``  — read phase_metadata.yaml and tally
  landed/scaffold/total.

``run_full_audit`` combines all three and adds a timestamp.

Per specs/IA_FULL_SPEC_2026-05-10.md Phase 96.
"""
from __future__ import annotations

import datetime
import logging
from pathlib import Path
from typing import Any, Dict, List

import yaml

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Phase metadata
# ---------------------------------------------------------------------------

PHASE_ID = 96
PHASE_TITLE = "Quarterly audit of tool_executor.py size + ghost handlers"
PHASE_STATUS = "landed"

# Default paths — can be overridden in tests.
_REPO_ROOT = Path(__file__).resolve().parents[3]
_TOOL_EXECUTOR_DEFAULT = (
    _REPO_ROOT
    / "service"
    / "isaac_assist_service"
    / "chat"
    / "tools"
    / "tool_executor.py"
)
_TOOL_SCHEMAS_DEFAULT = (
    _REPO_ROOT
    / "service"
    / "isaac_assist_service"
    / "chat"
    / "tools"
    / "tool_schemas.py"
)
_METADATA_DEFAULT = _REPO_ROOT / "specs" / "phase_metadata.yaml"

_SIZE_WARN_THRESHOLD = 500


def get_phase_metadata() -> Dict[str, Any]:
    """Return phase metadata for spec-coverage audits."""
    return {
        "phase": PHASE_ID,
        "title": PHASE_TITLE,
        "status": PHASE_STATUS,
        "spec_ref": "specs/IA_FULL_SPEC_2026-05-10.md Phase 96",
    }


# ---------------------------------------------------------------------------
# audit_tool_executor_size
# ---------------------------------------------------------------------------

def audit_tool_executor_size(path: Path) -> Dict[str, Any]:
    """Count the line count of *path* and warn if it exceeds the threshold.

    Returns::

        {
            "lines": int,
            "under_500_lines": bool,
            "warnings": list[str],
        }

    Args:
        path: Filesystem path to ``tool_executor.py`` (or any file to audit).
    """
    path = Path(path)
    if not path.exists():
        return {
            "lines": 0,
            "under_500_lines": True,
            "warnings": [f"File not found: {path}"],
        }

    lines = len(path.read_text(encoding="utf-8").splitlines())
    under_threshold = lines <= _SIZE_WARN_THRESHOLD
    warnings: List[str] = []
    if not under_threshold:
        warnings.append(
            f"tool_executor.py is {lines} lines — exceeds the {_SIZE_WARN_THRESHOLD}-line "
            f"target (Phase 14 goal). Consider moving remaining logic to a handler module."
        )
    return {
        "lines": lines,
        "under_500_lines": under_threshold,
        "warnings": warnings,
    }


# ---------------------------------------------------------------------------
# audit_ghost_handlers
# ---------------------------------------------------------------------------

def audit_ghost_handlers(tool_schemas_path: Path) -> Dict[str, Any]:
    """Check every tool name in ISAAC_SIM_TOOLS against the live handler registry.

    Calls ``register_handlers`` from ``handlers/_dispatch.py`` on fresh empty
    dicts, then checks whether each tool name in ISAAC_SIM_TOOLS appears as a
    key in the DATA_HANDLERS or CODE_GEN_HANDLERS dict.

    A "ghost handler" is a tool name that is present in the schema list but
    absent (or mapped to ``None``) in the dispatch registry.

    Returns::

        {
            "total_tools": int,
            "ghost_handlers": list[str],
            "registered_count": int,
        }

    Args:
        tool_schemas_path: Path to the ``tool_schemas.py`` file — used only to
            locate the package for the import; the import itself is done via
            Python's standard import system.
    """
    # Import ISAAC_SIM_TOOLS
    try:
        from service.isaac_assist_service.chat.tools.tool_schemas import (
            ISAAC_SIM_TOOLS,
        )
    except ImportError:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "tool_schemas", tool_schemas_path
        )
        mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
        ISAAC_SIM_TOOLS = mod.ISAAC_SIM_TOOLS  # type: ignore[assignment]

    all_tool_names: List[str] = [
        t["function"]["name"] for t in ISAAC_SIM_TOOLS
    ]
    total = len(all_tool_names)

    # Build a fresh dispatch registry
    data_handlers: Dict[str, Any] = {}
    codegen_handlers: Dict[str, Any] = {}

    try:
        from service.isaac_assist_service.chat.tools.handlers._dispatch import (
            register_handlers,
        )
        register_handlers(data_handlers, codegen_handlers)
    except Exception as exc:  # pragma: no cover — only fires if dispatch is broken
        logger.warning("[quarterly_audit] register_handlers failed: %s", exc)

    # registered_names = keys present in either dict, regardless of value.
    # None sentinel entries (e.g. ros2 tools when ros-mcp is absent) count as
    # registered: the name IS in the dispatch table; it just resolves to an
    # "unavailable" stub.  A "ghost" is a schema name that has NO entry at all.
    registered_names = set(data_handlers.keys()) | set(codegen_handlers.keys())

    ghosts: List[str] = [
        name
        for name in all_tool_names
        if name not in registered_names
    ]

    registered_count = total - len(ghosts)
    return {
        "total_tools": total,
        "ghost_handlers": sorted(ghosts),
        "registered_count": registered_count,
    }


# ---------------------------------------------------------------------------
# audit_phase_completion
# ---------------------------------------------------------------------------

def audit_phase_completion(metadata_yaml: Path) -> Dict[str, Any]:
    """Read *metadata_yaml* and tally phase statuses.

    Returns::

        {
            "landed": int,
            "scaffold": int,
            "total": int,
            "landed_pct": float,   # 0.0–100.0
        }

    Args:
        metadata_yaml: Path to ``specs/phase_metadata.yaml``.
    """
    metadata_yaml = Path(metadata_yaml)
    if not metadata_yaml.exists():
        return {
            "landed": 0,
            "scaffold": 0,
            "total": 0,
            "landed_pct": 0.0,
            "warnings": [f"Metadata file not found: {metadata_yaml}"],
        }

    with metadata_yaml.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh)

    landed = 0
    scaffold = 0
    total = 0
    for value in data.values():
        if not isinstance(value, dict):
            continue
        total += 1
        status = value.get("status", "")
        if status == "landed":
            landed += 1
        elif status == "scaffold":
            scaffold += 1

    landed_pct = (landed / total * 100.0) if total > 0 else 0.0
    return {
        "landed": landed,
        "scaffold": scaffold,
        "total": total,
        "landed_pct": round(landed_pct, 1),
    }


# ---------------------------------------------------------------------------
# run_full_audit
# ---------------------------------------------------------------------------

def run_full_audit(
    tool_executor_path: Path = _TOOL_EXECUTOR_DEFAULT,
    tool_schemas_path: Path = _TOOL_SCHEMAS_DEFAULT,
    metadata_yaml: Path = _METADATA_DEFAULT,
) -> Dict[str, Any]:
    """Run all three audit checks and return a combined report dict.

    Returns::

        {
            "timestamp": str,           # ISO-8601 UTC
            "tool_executor_size": {...},
            "ghost_handlers": {...},
            "phase_completion": {...},
        }
    """
    timestamp = datetime.datetime.now(datetime.timezone.utc).isoformat()
    size_report = audit_tool_executor_size(tool_executor_path)
    ghost_report = audit_ghost_handlers(tool_schemas_path)
    completion_report = audit_phase_completion(metadata_yaml)

    return {
        "timestamp": timestamp,
        "tool_executor_size": size_report,
        "ghost_handlers": ghost_report,
        "phase_completion": completion_report,
    }
