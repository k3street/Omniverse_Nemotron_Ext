"""Governance data models — decisions, audit entries, and policy configuration."""
from pydantic import BaseModel, Field
from typing import List, Dict, Any, Optional
from datetime import datetime, timezone


class ApprovalDecision(BaseModel):
    """A human operator's yes/no decision on a pending patch plan.

    Attributes:
        request_id: Identifier of the PatchPlan being decided upon.
        decision: ``"approved"`` | ``"rejected"`` | ``"skipped"``.
        approved_action_ids: Subset of action IDs the operator approved.
        rejected_action_ids: Subset of action IDs explicitly rejected.
        remember: If True, record this decision pattern for future auto-approve.
        user_note: Free-text operator comment.
        decided_at: UTC timestamp of the decision.
    """
    request_id: str
    decision: str # "approved" | "rejected" | "skipped"
    approved_action_ids: List[str] = []
    rejected_action_ids: List[str] = []
    remember: bool = False
    user_note: Optional[str] = None
    decided_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class AuditEntry(BaseModel):
    """Immutable record of a single governance event written to the audit JSONL.

    Attributes:
        entry_id: Unique identifier for this log line.
        timestamp: UTC time the event occurred.
        event_type: Short string classifier, e.g. ``"patch_approved"``.
        plan_id: Associated patch plan ID, if any.
        action_id: Associated action ID within a plan, if any.
        risk_level: ``"low"`` | ``"medium"`` | ``"high"`` as assessed by PolicyEngine.
        write_surface: ``"python"`` | ``"settings"`` | ``"usd"`` etc.
        target: The USD path or settings key that was modified.
        user_decision: ``"approved"`` | ``"rejected"`` | ``"auto"`` etc.
        snapshot_id: ID of the pre-action snapshot, if one was taken.
        sources_consulted: RAG source IDs queried for this action.
        confidence: Planner confidence score (0–1).
        error: Error message if the action failed.
        metadata: Arbitrary extra fields for extensibility.
    """
    entry_id: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    event_type: str
    plan_id: Optional[str] = None
    action_id: Optional[str] = None
    risk_level: Optional[str] = None
    write_surface: Optional[str] = None
    target: Optional[str] = None
    user_decision: Optional[str] = None
    snapshot_id: Optional[str] = None
    sources_consulted: List[str] = []
    confidence: Optional[float] = None
    error: Optional[str] = None
    metadata: Dict[str, Any] = {}

class GovernanceConfig(BaseModel):
    """Policy configuration for the governance engine.

    Attributes:
        operational_mode: ``"interactive"`` requires human approval for every
            action; ``"semi_autonomous"`` auto-approves low-risk actions;
            ``"explain_only"`` never executes, only explains.
        network_mode: ``"official_only"`` restricts RAG to ``network_allowlist``
            domains; ``"open"`` permits any source.
        network_allowlist: Trusted domains for RAG document retrieval.
        secret_patterns: Regex list for ``SecretRedactor`` — matched strings
            are replaced by ``[REDACTED_SECRET]``.
        secret_paths: File paths whose contents should always be redacted.
        max_auto_apply_confidence: Actions with planner confidence above this
            threshold may be auto-applied in semi_autonomous mode.
        audit_log_path: Path to the append-only JSONL audit file.
        audit_retention_days: Age at which audit entries may be pruned.
    """
    operational_mode: str = "interactive" # "interactive" | "semi_autonomous" | "explain_only"
    network_mode: str = "official_only"
    network_allowlist: List[str] = ["https://docs.isaacsim.omniverse.nvidia.com"]
    secret_patterns: List[str] = [
        r"(?i)aws_access_key_id",
        r"(?i)sk-[a-zA-Z0-9]{32,}",
        r"(?i)bearer\s+[a-zA-Z0-9\-\._~+/]+="
    ]
    secret_paths: List[str] = []
    max_auto_apply_confidence: float = 0.9
    audit_log_path: str = "workspace/audit.jsonl"
    audit_retention_days: int = 30
