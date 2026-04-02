# 07 — Approval Engine + Dry-Run + Governance

## Purpose

Enforce policy controls over all mutating operations. No shell command, package change, network request, or destructive write should execute without explicit user approval. Manage action approvals, secrets redaction, audit trails, and operational mode enforcement.

## Runtime

Extension (approval UI), Background service (policy evaluation, audit log)

## Phase

3 (Weeks 9–11)

## Dependencies

- Extension shell (01) for approval dialog UI
- Snapshot manager (03) for pre-mutation snapshots
- Patch planner (06) for plan approval workflow
- Environment fingerprint (02) for mode enforcement

---

## Functional Requirements

### FR-07.1 Action Classification

Classify every action by risk level:

| Risk Level | Examples | Approval Requirement |
|------------|----------|---------------------|
| **Read-only** | Stage inspection, retrieval query, diff preview | No approval needed |
| **Low-risk write** | Set a USD property value, toggle a setting | Single-click approve |
| **Medium-risk write** | Add/remove prims, modify Python files, change physics parameters | Approve with diff review |
| **High-risk write** | Shell commands, package installs, delete files, modify extension configs | Approve with explicit acknowledgment + diff review |
| **Blocked** | Network requests to unknown hosts, writes outside workspace, credential access | Denied with explanation |

### FR-07.2 Approval Dialog

Display a modal or inline approval card for each write action (or batch):

Contents:
- Action summary (what will change)
- Risk level badge
- Diff preview (USD, file, or settings)
- Provenance summary (which sources informed this)
- Confidence indicator
- Snapshot confirmation ("A snapshot will be created before this change")
- Approve / Reject / Approve All (for batch) / Skip buttons
- "I understand the risk" checkbox (for medium/high-risk)

### FR-07.3 Operational Modes

| Mode | Behavior |
|------|----------|
| **Interactive (default)** | Every write requires approval. |
| **Semi-autonomous** | Low-risk writes auto-approved; medium/high require approval. |
| **Explain-only** | All writes blocked; only explain and propose modes available. |

Mode is set per-session and resets to Interactive on extension reload.

### FR-07.4 Network Policy

Control what the assistant can access over the network:

- **Offline:** No network requests. Retrieval uses local index only.
- **Official-sources-only (default):** Only NVIDIA domains, GitHub (for registered repos), and configured internal endpoints.
- **Open:** Any URL, with logging.

Network allowlist is configurable in extension settings.

### FR-07.5 Secret Scoping

- Detect and redact secrets (API keys, tokens, passwords) in:
  - Logs and audit trail
  - Snapshot settings dumps
  - Chat history sent to LLM
  - Repro bundles
- Use pattern matching (regex for common secret formats) plus explicit secret-path declarations.
- Never send detected secrets to the LLM provider.

### FR-07.6 Audit Trail

Record every significant action in an append-only local log:

```
{
  "timestamp": "2026-03-15T10:23:45Z",
  "event_type": "action_approved",
  "action_id": "a1b2c3",
  "plan_id": "p4d5e6",
  "risk_level": "medium",
  "write_surface": "usd",
  "target": "/World/Robot/base_link",
  "user_decision": "approved",
  "snapshot_id": "s7f8g9",
  "sources_consulted": ["chunk_abc", "chunk_def"],
  "confidence": 0.85
}
```

Events to log:
- Diagnosis inputs (what was analyzed)
- Sources retrieved (which chunks, from which sources)
- Patches proposed (plan summary)
- Patches applied (which actions, which approved/rejected)
- User approvals and rejections
- Rollbacks performed
- Mode changes
- Errors and failures

### FR-07.7 Per-Action Approval Memory

For repeated operations (e.g., "always allow setting physics time step"):
- Allow "Remember this decision" checkbox on approval dialogs.
- Store remembered approvals per action pattern (not per specific value).
- Provide a settings page to review and revoke remembered approvals.
- Remembered approvals expire after session end (never persist across restarts, for safety).

---

## Data Models

### ApprovalRequest

```python
@dataclass
class ApprovalRequest:
    request_id: str
    plan_id: str
    action_ids: List[str]
    risk_level: str              # "low" | "medium" | "high"
    summary: str
    diff_preview: Optional[str]
    provenance_summary: str
    confidence: float
    requires_acknowledgment: bool
    snapshot_will_be_created: bool
    timestamp: datetime

@dataclass
class ApprovalDecision:
    request_id: str
    decision: str                # "approved" | "rejected" | "skipped"
    approved_action_ids: List[str]
    rejected_action_ids: List[str]
    remember: bool
    user_note: Optional[str]
    decided_at: datetime
```

### AuditEntry

```python
@dataclass
class AuditEntry:
    entry_id: str
    timestamp: datetime
    event_type: str
    plan_id: Optional[str]
    action_id: Optional[str]
    risk_level: Optional[str]
    write_surface: Optional[str]
    target: Optional[str]
    user_decision: Optional[str]
    snapshot_id: Optional[str]
    sources_consulted: List[str]
    confidence: Optional[float]
    error: Optional[str]
    metadata: Dict[str, Any]
```

### GovernanceConfig

```python
@dataclass
class GovernanceConfig:
    operational_mode: str        # "interactive" | "semi_autonomous" | "explain_only"
    network_mode: str            # "offline" | "official_only" | "open"
    network_allowlist: List[str]
    secret_patterns: List[str]   # Regex patterns for secret detection
    secret_paths: List[str]      # Settings paths known to contain secrets
    max_auto_apply_confidence: float  # For semi-autonomous mode (default 0.9)
    audit_log_path: str
    audit_retention_days: int
```

---

## API Contract

### Service Endpoints

```
POST /api/v1/governance/evaluate
  Request: {
    "actions": [PatchAction],
    "operational_mode": str,
    "network_mode": str
  }
  Response: {
    "approved_auto": [str],       # action_ids auto-approved (semi-autonomous)
    "require_approval": [str],    # action_ids needing user approval
    "blocked": [{ "action_id": str, "reason": str }],
    "risk_levels": { action_id: str }
  }

POST /api/v1/governance/audit
  Request: AuditEntry
  Response: { "logged": bool }

GET /api/v1/governance/audit
  Query params: event_type, date_from, date_to, plan_id, limit
  Response: { "entries": [AuditEntry], "total": int }

GET /api/v1/governance/config
  Response: GovernanceConfig

PUT /api/v1/governance/config
  Request: Partial<GovernanceConfig>
  Response: GovernanceConfig

POST /api/v1/governance/redact
  Request: { "text": str }
  Response: { "redacted_text": str, "secrets_found": int }
```

---

## File Structure

```
service/
└── isaac_assist_service/
    └── governance/
        ├── __init__.py
        ├── policy_engine.py        # Action classification + mode enforcement
        ├── secret_redactor.py      # Secret detection and redaction
        ├── audit_log.py            # Append-only audit trail
        ├── network_policy.py       # Network allowlist enforcement
        └── routes.py

exts/
└── omni.isaac.assist/
    └── omni/isaac/assist/
        └── ui/
            └── approval_dialog.py  # (already listed in 01, detailed here)
```

---

## Implementation Notes

- **Approval dialog:** Use `omni.ui.Window` with modal behavior. Show the diff in a scrollable code view with syntax highlighting (green/red for additions/removals).
- **Audit log:** Use an append-only JSONL file (`~/.isaac_assist/audit.jsonl`). Rotate by size (10MB) or age (30 days).
- **Secret redaction:** Ship a default set of regex patterns (AWS keys, GitHub tokens, generic API keys, passwords in URLs). Allow users to add custom patterns.
- **Network enforcement:** The background service's HTTP client should route all external requests through a policy-checked proxy function that validates the domain against the allowlist.
- **Semi-autonomous mode:** Only auto-approve actions that are: (a) low-risk, (b) above the confidence threshold, (c) validated against the compatibility matrix, and (d) not blocked by network policy.
- **LLM secret safety:** Before sending any context to the LLM provider, run it through the secret redactor. This includes chat history, stage summaries, file contents, and error messages.

---

## Acceptance Criteria

- [ ] Every mutating action is classified by risk level before execution.
- [ ] Medium and high-risk actions show an approval dialog with diff preview.
- [ ] Blocked actions are denied with a clear explanation.
- [ ] Offline mode prevents all network requests.
- [ ] Official-only mode allows only allowlisted domains.
- [ ] Secrets are redacted from audit logs and LLM context.
- [ ] Audit trail records all significant events with correct metadata.
- [ ] Semi-autonomous mode auto-approves only low-risk, high-confidence actions.
- [ ] Explain-only mode prevents all write operations.
- [ ] Remembered approvals do not persist across sessions.
