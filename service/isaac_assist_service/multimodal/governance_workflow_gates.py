"""Phase 42 — governance gates for high-risk patches.

When `validate_patch` reports a high-severity issue, the orchestrator
auto-creates a workflow checkpoint before executing.

Per specs/IA_FULL_SPEC_2026-05-10.md Phase 42.
"""
from typing import Any, Dict, List


def should_auto_checkpoint(validation_issues: List[Any]) -> bool:
    """True if any issue has severity in {ERROR, CRITICAL}."""
    for issue in validation_issues:
        sev = getattr(issue, "severity", None) or (issue.get("severity") if isinstance(issue, dict) else None)
        if sev in ("error", "critical", "ERROR", "CRITICAL"):
            return True
    return False


def gate_decision(code: str, validation_issues: List[Any]) -> Dict[str, Any]:
    auto_cp = should_auto_checkpoint(validation_issues)
    return {
        "auto_checkpoint": auto_cp,
        "reason": "high_risk_patch" if auto_cp else "low_risk",
        "issue_count": len(validation_issues),
    }
