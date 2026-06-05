"""Phase 42 — governance gates for high-risk patches.

When `validate_patch` reports a high-severity issue, the orchestrator
auto-creates a workflow checkpoint before executing.

Per specs/IA_FULL_SPEC_2026-05-10.md Phase 42.
"""
from typing import Any, Dict, List


def should_auto_checkpoint(validation_issues: List[Any]) -> bool:
    """Return ``True`` if any validation issue has severity ``ERROR`` or ``CRITICAL``.

    Accepts both object-style issues (with a ``.severity`` attribute) and
    dict-style issues (with a ``"severity"`` key), case-insensitively.

    Args:
        validation_issues (List[Any]): Issues returned by a patch validator.

    Returns:
        bool: ``True`` when an ERROR or CRITICAL issue is present.
    """
    for issue in validation_issues:
        sev = getattr(issue, "severity", None) or (issue.get("severity") if isinstance(issue, dict) else None)
        if sev in ("error", "critical", "ERROR", "CRITICAL"):
            return True
    return False


def gate_decision(code: str, validation_issues: List[Any]) -> Dict[str, Any]:
    """Decide whether to auto-checkpoint before executing a patch.

    Args:
        code (str): The patch code string (reserved for future rule expansion).
        validation_issues (List[Any]): Issues from the patch validator.

    Returns:
        Dict[str, Any]: Keys ``auto_checkpoint`` (bool), ``reason`` (str), and
            ``issue_count`` (int).
    """
    auto_cp = should_auto_checkpoint(validation_issues)
    return {
        "auto_checkpoint": auto_cp,
        "reason": "high_risk_patch" if auto_cp else "low_risk",
        "issue_count": len(validation_issues),
    }
