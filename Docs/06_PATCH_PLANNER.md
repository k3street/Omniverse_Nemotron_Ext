# 06 — Patch Planner + Execution Engine

## Purpose

Generate structured repair plans from diagnostics and retrieval results, produce reviewable diffs, execute approved changes, and validate outcomes. This is the "fixer" — it turns diagnosis into action.

## Runtime

Background service (planning + validation), Extension (diff preview + execution of USD writes)

## Phase

3 (Weeks 9–11)

## Dependencies

- Snapshot manager (03) — snapshot before every mutation
- Source registry (04) — provenance for every recommendation
- Stage analyzer (05) — findings that trigger repair plans

---

## Functional Requirements

### FR-06.1 Patch Plan Generation

Given a set of validation findings and/or a user request, produce a structured patch plan:

- Each plan contains one or more **actions** (atomic changes).
- Each action specifies: target (prim, file, setting), change type, old value, new value, confidence, and source provenance.
- Plans must be **ordered** — some actions depend on others.
- Plans must include **validation steps** — checks to run after the plan is applied.

### FR-06.2 Three Action Modes

Every patch plan supports three modes:

| Mode | Behavior |
|------|----------|
| **Explain** | Show the plan with reasoning and provenance, but make no changes. |
| **Propose (dry run)** | Generate full diffs (USD, file, settings) for review without writing anything. |
| **Apply** | After user approval, execute the changes and run validation. |

### FR-06.3 Write Surfaces

Support four distinct write targets:

1. **USD edits:** Set/add/remove properties, create/delete prims, modify schemas, update material bindings, change physics parameters. Executed via `pxr` APIs in the extension.
2. **Python/code edits:** Modify Python scripts, task definitions, config files. Standard file write with unified diff preview.
3. **Settings edits:** Modify Isaac Sim extension settings, physics solver settings, render settings. Via Omniverse settings API.
4. **Dependency recommendations:** Suggest pip package installs, extension enables/disables, version upgrades. These are *never auto-applied* — always advisory.

### FR-06.4 Provenance View

Every action in a plan links to:
- The validation finding(s) that motivated it.
- The source chunk(s) from retrieval that informed it.
- The confidence level (high/medium/low) with an explanation.
- Whether the fix has been applied successfully before (from knowledge base).

### FR-06.5 Post-Apply Validation

After applying a plan:
1. Re-run the validation rules that produced the original findings.
2. Compare results to the pre-change baseline (from the snapshot).
3. Report: findings resolved, findings unchanged, new findings introduced.
4. If new errors are introduced, flag the plan as "regressive" and recommend rollback.

### FR-06.6 Partial Apply

Allow the user to approve individual actions within a plan:
- Select which actions to apply (checkboxes in diff preview).
- Deselected actions are skipped.
- Dependency warnings if deselecting an action that others depend on.

### FR-06.7 Confidence Gating

Actions below a confidence threshold require explicit acknowledgment:
- High confidence (≥0.8): Can be applied with standard approval.
- Medium confidence (0.5–0.8): Show warning, require explicit "I understand the risk" acknowledgment.
- Low confidence (<0.5): Labeled as "experimental suggestion", require extra confirmation, cannot be batch-applied.

### FR-06.8 Compatibility Gating

Cross-reference every action against the compatibility result from module 02:
- In GA mode: block actions that reference APIs/features not validated for the current stack.
- In experimental mode: allow with warning.

---

## Data Models

### PatchPlan

```python
@dataclass
class PatchPlan:
    plan_id: str
    created_at: datetime
    
    # Context
    trigger: str                     # "finding" | "user_request" | "escalation"
    finding_ids: List[str]           # Validation findings that motivated this plan
    user_request: Optional[str]      # Natural language request if user-initiated
    
    # Plan content
    title: str                       # Human-readable summary
    description: str                 # Detailed explanation
    actions: List[PatchAction]       # Ordered list of changes
    validation_steps: List[ValidationStep]
    
    # Metadata
    overall_confidence: float
    compatibility_status: str        # "validated" | "unvalidated" | "blocked"
    provenance: List[ProvenanceLink]
    
    # Execution state
    status: str                      # "draft" | "proposed" | "approved" | "applied" | "validated" | "rolled_back" | "failed"
    snapshot_id: Optional[str]       # Snapshot created before apply
    validation_result: Optional[PlanValidationResult]

@dataclass
class PatchAction:
    action_id: str
    order: int
    depends_on: List[str]            # action_ids this depends on
    
    # Target
    write_surface: str               # "usd" | "python" | "settings" | "dependency"
    target_path: str                 # Prim path, file path, or setting key
    
    # Change
    action_type: str                 # "set_property" | "add_prim" | "remove_prim" | "add_schema" |
                                     # "edit_file" | "create_file" | "set_setting" | "recommend_package"
    property_name: Optional[str]
    old_value: Optional[Any]
    new_value: Optional[Any]
    file_diff: Optional[str]         # Unified diff for file edits
    
    # Metadata
    confidence: float
    reasoning: str                   # Why this change is recommended
    provenance: List[ProvenanceLink]
    
    # Execution
    approved: bool = False
    applied: bool = False
    apply_error: Optional[str] = None

@dataclass
class ProvenanceLink:
    source_type: str                 # "finding" | "retrieval" | "knowledge_base" | "user"
    source_id: str                   # finding_id, chunk_id, knowledge_object_id, or "user"
    source_name: str
    trust_tier: Optional[int]
    url: Optional[str]

@dataclass
class ValidationStep:
    step_id: str
    description: str
    rule_ids: List[str]              # Validation rules to re-run
    expected_outcome: str            # "finding_resolved" | "no_regression" | "custom"

@dataclass
class PlanValidationResult:
    plan_id: str
    validated_at: datetime
    findings_resolved: List[str]     # finding_ids now resolved
    findings_unchanged: List[str]    # finding_ids still present
    findings_new: List[str]          # New finding_ids introduced
    is_regressive: bool              # True if new errors introduced
    recommendation: str              # "success" | "partial" | "rollback_recommended"
```

---

## API Contract

### Service Endpoints

```
POST /api/v1/plans/generate
  Request: {
    "finding_ids": [str],            # Findings to fix
    "user_request": str | null,      # Natural language request
    "scope": str,                    # "selected_findings" | "all_errors" | "user_request"
    "mode": str                      # "explain" | "propose" | "apply"
  }
  Response: PatchPlan

GET /api/v1/plans/{plan_id}
  Response: PatchPlan

POST /api/v1/plans/{plan_id}/approve
  Request: {
    "approved_action_ids": [str],    # Which actions to approve (null = all)
    "acknowledgments": {
      "low_confidence": bool,
      "unvalidated_stack": bool
    }
  }
  Response: PatchPlan

POST /api/v1/plans/{plan_id}/apply
  Request: {}                        # Must be approved first
  Response: {
    "snapshot_id": str,
    "applied_actions": [str],
    "skipped_actions": [str],
    "errors": [{ "action_id": str, "error": str }]
  }

POST /api/v1/plans/{plan_id}/validate
  Request: {}                        # Run after apply
  Response: PlanValidationResult

GET /api/v1/plans
  Query params: status, trigger, date_from, date_to, limit
  Response: { "plans": [PatchPlan], "total": int }
```

### Extension-Side Execution Interface

```python
class PatchExecutor:
    """Runs in the extension process for direct USD/settings access."""
    
    def apply_usd_action(self, stage: Usd.Stage, action: PatchAction) -> bool: ...
    def apply_settings_action(self, action: PatchAction) -> bool: ...
    def apply_file_action(self, action: PatchAction) -> bool: ...
    def preview_usd_diff(self, stage: Usd.Stage, actions: List[PatchAction]) -> str: ...
```

---

## File Structure

```
service/
└── isaac_assist_service/
    └── planner/
        ├── __init__.py
        ├── generator.py             # Plan generation from findings + retrieval
        ├── action_builder.py        # Individual action construction
        ├── confidence.py            # Confidence scoring
        ├── compatibility_gate.py    # Cross-reference with compatibility matrix
        ├── provenance_linker.py     # Link actions to sources
        ├── validator.py             # Post-apply validation orchestration
        └── routes.py

exts/
└── omni.isaac.assist/
    └── omni/isaac/assist/
        └── executor/
            ├── __init__.py
            ├── usd_executor.py      # Apply USD changes via pxr
            ├── file_executor.py     # Apply file edits
            ├── settings_executor.py # Apply settings changes
            └── diff_preview.py      # Generate diffs for UI display
```

---

## Implementation Notes

- **LLM integration for plan generation:** The planner uses an LLM (via provider interface) to synthesize findings + retrieved sources into a coherent plan. The LLM prompt includes: findings, retrieved doc chunks, fingerprint summary, and the patch action schema. The LLM output is parsed into structured `PatchAction` objects.
- **USD execution safety:** All USD writes go through the extension process (which has the live stage). The background service generates the plan; the extension executes it. Use `omni.kit.commands` for undoable operations where possible.
- **Diff preview for USD:** Generate a "before" snapshot (export affected prims as `.usda` text), apply changes to a temporary sublayer or clone, export "after," and diff the text.
- **File edits:** Use standard `difflib.unified_diff` for preview. Write files atomically (write to temp, rename).
- **Dependency recommendations:** These are display-only — show the user what to install and provide copy-paste commands, but never run `pip install` or modify the environment automatically.
- **Plan versioning:** If the user modifies which actions to approve, create a new version of the plan rather than mutating the original.

---

## Acceptance Criteria

- [ ] Plans are generated from validation findings with correct provenance links.
- [ ] Explain mode shows reasoning without making changes.
- [ ] Propose mode generates accurate diffs for USD, file, and settings changes.
- [ ] Apply mode creates a snapshot before writing and executes only approved actions.
- [ ] Post-apply validation correctly identifies resolved, unchanged, and new findings.
- [ ] Low-confidence actions require explicit acknowledgment.
- [ ] Compatibility gating blocks unvalidated actions in GA mode.
- [ ] Partial apply works — skipping deselected actions and warning on dependency breaks.
