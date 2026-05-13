"""Phase 83 — Governance: overnight patches policy_engine: contract tests.

Gate criteria:
  * metadata status == "landed"
  * is_overnight: noon → False, 2 AM → True, custom hours respected
  * policy_check overnight + high-risk → veto=True, action=require_human_approval
  * policy_check overnight + low-risk → veto=False, action=log_and_continue
  * policy_check business-hours + high-risk → veto=False, action=execute
  * PolicyAuditLog: record + recent_vetoes returns only veto=True rows
  * validation_issues accepts ConstraintViolation with severity literal "ERROR"
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.l0


# ---------------------------------------------------------------------------
# Helpers / minimal stubs
# ---------------------------------------------------------------------------

class _SimpleIssue:
    """Minimal stub that mimics a ValidationIssue dataclass with .severity."""

    def __init__(self, severity: str) -> None:
        self.severity = severity


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_phase_83_metadata():
    """Metadata reflects landed status."""
    from service.isaac_assist_service.multimodal.governance_overnight_patches import (
        get_phase_metadata,
    )
    md = get_phase_metadata()
    assert md["phase"] == 83
    assert md["status"] == "landed"
    assert "spec_ref" in md


def test_is_overnight_noon_is_false():
    """A noon timestamp is during business hours → is_overnight returns False."""
    from service.isaac_assist_service.multimodal.governance_overnight_patches import (
        OvernightPatchPolicy,
    )
    policy = OvernightPatchPolicy(business_hours_local=(8, 18))
    # 2026-05-13 at 12:00 local — no timezone info, treated as local
    assert policy.is_overnight("2026-05-13T12:00:00") is False


def test_is_overnight_2am_is_true():
    """A 2 AM timestamp is outside business hours → is_overnight returns True."""
    from service.isaac_assist_service.multimodal.governance_overnight_patches import (
        OvernightPatchPolicy,
    )
    policy = OvernightPatchPolicy(business_hours_local=(8, 18))
    assert policy.is_overnight("2026-05-13T02:30:00") is True


def test_is_overnight_custom_hours():
    """Custom business hours are respected."""
    from service.isaac_assist_service.multimodal.governance_overnight_patches import (
        OvernightPatchPolicy,
    )
    # Narrow window: 9–11 only
    policy = OvernightPatchPolicy(business_hours_local=(9, 11))
    assert policy.is_overnight("2026-05-13T10:00:00") is False  # inside
    assert policy.is_overnight("2026-05-13T08:30:00") is True   # before window
    assert policy.is_overnight("2026-05-13T11:00:00") is True   # at end (exclusive)


def test_policy_check_overnight_high_risk_vetoes():
    """Overnight + high-risk issue → veto=True, require_human_approval."""
    from service.isaac_assist_service.multimodal.governance_overnight_patches import (
        OvernightPatchPolicy,
    )
    policy = OvernightPatchPolicy()
    high_risk_issue = _SimpleIssue(severity="ERROR")
    result = policy.policy_check(
        patch_code="# dangerous patch\nos.system('rm -rf /')",
        validation_issues=[high_risk_issue],
        timestamp_iso="2026-05-13T02:00:00",
    )
    assert result["overnight"] is True
    assert result["high_risk"] is True
    assert result["veto"] is True
    assert result["required_action"] == "require_human_approval"
    assert isinstance(result["reason"], str) and len(result["reason"]) > 0


def test_policy_check_overnight_low_risk_no_veto():
    """Overnight + no high-risk issues → veto=False, log_and_continue."""
    from service.isaac_assist_service.multimodal.governance_overnight_patches import (
        OvernightPatchPolicy,
    )
    policy = OvernightPatchPolicy()
    low_risk_issue = _SimpleIssue(severity="warning")
    result = policy.policy_check(
        patch_code="# harmless cleanup",
        validation_issues=[low_risk_issue],
        timestamp_iso="2026-05-13T03:00:00",
    )
    assert result["overnight"] is True
    assert result["high_risk"] is False
    assert result["veto"] is False
    assert result["required_action"] == "log_and_continue"


def test_policy_check_business_hours_high_risk_no_veto():
    """Business hours + high-risk issue → veto=False (day gate), action=execute."""
    from service.isaac_assist_service.multimodal.governance_overnight_patches import (
        OvernightPatchPolicy,
    )
    policy = OvernightPatchPolicy()
    high_risk_issue = _SimpleIssue(severity="CRITICAL")
    result = policy.policy_check(
        patch_code="# high risk but submitted at noon",
        validation_issues=[high_risk_issue],
        timestamp_iso="2026-05-13T14:00:00",
    )
    assert result["overnight"] is False
    assert result["high_risk"] is True
    assert result["veto"] is False
    assert result["required_action"] == "execute"


def test_policy_audit_log_record_and_recent_vetoes(tmp_path: Path):
    """record_decision writes NDJSON; recent_vetoes returns only veto=True rows."""
    import json
    from service.isaac_assist_service.multimodal.governance_overnight_patches import (
        PolicyAuditLog,
    )

    log_path = tmp_path / "audit" / "policy.ndjson"
    audit = PolicyAuditLog(log_path)

    # Record a vetoed decision and two non-vetoed ones
    audit.record_decision({
        "overnight": True,
        "high_risk": True,
        "veto": True,
        "reason": "High-risk overnight patch",
        "required_action": "require_human_approval",
    })
    audit.record_decision({
        "overnight": False,
        "high_risk": False,
        "veto": False,
        "reason": "Normal daytime patch",
        "required_action": "execute",
    })
    audit.record_decision({
        "overnight": True,
        "high_risk": False,
        "veto": False,
        "reason": "Overnight low-risk",
        "required_action": "log_and_continue",
    })

    # NDJSON file must exist and have 3 parseable lines
    assert log_path.exists()
    lines = [l for l in log_path.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(lines) == 3
    for line in lines:
        row = json.loads(line)
        assert "recorded_at" in row
        assert "veto" in row

    # recent_vetoes returns only the veto=True row
    vetoes = audit.recent_vetoes()
    assert len(vetoes) == 1
    assert vetoes[0]["veto"] is True
    assert vetoes[0]["required_action"] == "require_human_approval"


def test_policy_check_constraint_violation_severity_error():
    """ConstraintViolation with severity literal ERROR triggers high_risk=True."""
    from service.isaac_assist_service.multimodal.governance_overnight_patches import (
        OvernightPatchPolicy,
    )
    from service.isaac_assist_service.types.violations import ConstraintViolation
    from service.isaac_assist_service.types.uncertainty import GradedScale

    policy = OvernightPatchPolicy()
    violation = ConstraintViolation(
        constraint_id="patch.unknown_prim_path",
        category="hard",
        severity=GradedScale.ERROR,
        message="Prim path /World/BadPrim does not exist",
    )
    result = policy.policy_check(
        patch_code="stage.GetPrimAtPath('/World/BadPrim').SetActive(False)",
        validation_issues=[violation],
        timestamp_iso="2026-05-13T01:00:00",
    )
    assert result["high_risk"] is True
    assert result["veto"] is True
    assert result["required_action"] == "require_human_approval"


def test_policy_check_no_issues_overnight():
    """Overnight with zero validation issues → high_risk=False, veto=False."""
    from service.isaac_assist_service.multimodal.governance_overnight_patches import (
        OvernightPatchPolicy,
    )
    policy = OvernightPatchPolicy()
    result = policy.policy_check(
        patch_code="# empty patch, no issues",
        validation_issues=[],
        timestamp_iso="2026-05-13T23:59:00",
    )
    assert result["overnight"] is True
    assert result["high_risk"] is False
    assert result["veto"] is False
    assert result["required_action"] == "log_and_continue"
