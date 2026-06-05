from pydantic import BaseModel, Field
from typing import List, Dict, Any, Optional
from datetime import datetime, timezone

class ApprovalDecision(BaseModel):
    request_id: str
    decision: str # "approved" | "rejected" | "skipped"
    approved_action_ids: List[str] = []
    rejected_action_ids: List[str] = []
    remember: bool = False
    user_note: Optional[str] = None
    decided_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class AuditEntry(BaseModel):
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
