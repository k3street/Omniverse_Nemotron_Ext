# 08 — Local Knowledge Base + Experiential Memory

## Purpose

Continuously build a local, searchable corpus of troubleshooting knowledge from prior sessions, approved fixes, failed attempts, and team-authored guidance. Enable the assistant to improve over time — repeat issues should be resolved faster, and failed fixes should never be re-proposed.

## Runtime

Background service

## Phase

4 (Weeks 12–14)

## Dependencies

- Source registry (04) — knowledge base is Layer C in the three-layer model
- Patch planner (06) — captures fix outcomes
- Snapshot manager (03) — links to state at time of fix

---

## Functional Requirements

### FR-08.1 Three-Layer Knowledge Model

| Layer | Content | Source | Trust |
|-------|---------|--------|-------|
| **A — Authoritative** | Official docs, release notes, API docs, GitHub READMEs | Source registry indexer | Highest |
| **B — Organization** | Internal playbooks, recurring failure recipes, asset conventions, environment setup notes, approved baselines | Manual authoring + import | High |
| **C — Experiential** | Successful fixes, unsuccessful fixes, snapshots, issue classes, validation outcomes | Automatic from sessions | Variable (based on outcome) |

Module 04 owns Layers A and B. This module owns Layer C and the cross-layer query interface.

### FR-08.2 Knowledge Object Structure

Each experiential knowledge object captures a complete issue→fix→validation tuple:

- **Issue signature:** Issue class, affected prim types, schemas involved, error messages, fingerprint context
- **Fix applied:** Patch plan summary, actions taken, write surfaces used
- **Outcome:** Validation result (resolved/partial/failed/rolled-back), user feedback
- **Context:** Fingerprint summary, stage characteristics, timestamp
- **Version scope:** Which Isaac Sim/Lab versions this knowledge applies to

### FR-08.3 Automatic Knowledge Capture

After every completed fix cycle (apply → validate → user accepts or rejects):
1. Extract an issue signature from the original findings.
2. Record the patch plan and actions applied.
3. Record the validation outcome.
4. Store as a knowledge object with appropriate outcome tagging.

No user action required — capture is automatic.

### FR-08.4 Negative Memory

Track fixes that failed, were rolled back, or were rejected by the user:
- Tag these knowledge objects as `outcome: failed` or `outcome: rejected`.
- When generating new patch plans, query negative memory for matching issue signatures.
- If a proposed fix matches a negative memory entry, either exclude it or show a warning: "A similar fix was previously attempted and failed/rejected."
- Negative memory entries include a `reason` field (if available from user feedback or validation result).

### FR-08.5 Positive Memory and Confidence Boosting

Track fixes that succeeded:
- Tag as `outcome: succeeded`.
- When a matching issue signature appears again, boost the confidence of the associated fix in plan generation.
- Track success count — fixes that have worked multiple times are higher confidence.

### FR-08.6 Knowledge Query Interface

Support queries against the knowledge base:
- **By issue signature:** "Have we seen this type of failure before?"
- **By prim type/schema:** "What common issues affect ArticulationRootAPI?"
- **By error message:** Fuzzy match against recorded error messages.
- **By version scope:** "What issues are known for Isaac Sim 6.0?"
- **Full-text search:** Across all knowledge object fields.

### FR-08.7 Knowledge Object Management

- View all knowledge objects with filters (outcome, date, version, issue class).
- Manually tag, annotate, or promote/demote knowledge objects.
- Delete knowledge objects (with audit trail).
- Export knowledge objects as JSON for sharing across team members.
- Import knowledge objects from JSON (with deduplication).

### FR-08.8 Playbook Authoring (Layer B)

Allow users to create structured playbooks:
- Title, description, version scope
- Trigger conditions (prim types, schemas, error patterns)
- Step-by-step remediation instructions
- Validation criteria
- Auto-apply eligible (boolean)

Playbooks are stored as Layer B knowledge and have higher trust than experiential Layer C entries.

---

## Data Models

### KnowledgeObject

```python
@dataclass
class KnowledgeObject:
    knowledge_id: str
    layer: str                       # "B" | "C"
    object_type: str                 # "fix_record" | "playbook" | "recipe" | "note"
    created_at: datetime
    updated_at: datetime
    
    # Issue signature
    issue_class: str                 # Taxonomy category (e.g., "articulation.broken_chain")
    issue_summary: str
    prim_types_involved: List[str]
    schemas_involved: List[str]
    error_patterns: List[str]        # Regex patterns matching relevant error messages
    finding_rule_ids: List[str]      # Which validation rules triggered
    
    # Fix details
    fix_summary: str
    patch_plan_id: Optional[str]
    actions_summary: List[ActionSummary]
    write_surfaces_used: List[str]
    
    # Outcome
    outcome: str                     # "succeeded" | "partial" | "failed" | "rejected" | "rolled_back"
    validation_result_summary: Optional[str]
    user_feedback: Optional[str]
    success_count: int               # Incremented each time this fix succeeds again
    failure_count: int
    
    # Context
    version_scope: str               # Semver range
    fingerprint_summary: Dict[str, str]
    stage_characteristics: Dict[str, Any]   # prim count, types present, etc.
    
    # Metadata
    tags: List[str]
    auto_apply_eligible: bool
    confidence_modifier: float       # Boost or penalty for plan generation

@dataclass
class ActionSummary:
    write_surface: str
    action_type: str
    target_description: str          # Human-readable target description
    change_description: str          # What was changed
```

### IssueSignature

```python
@dataclass
class IssueSignature:
    issue_class: str
    prim_types: List[str]
    schemas: List[str]
    error_patterns: List[str]
    rule_ids: List[str]
    version_scope: str
```

---

## API Contract

### Service Endpoints

```
POST /api/v1/knowledge/capture
  Request: {
    "findings": [ValidationFinding],
    "patch_plan": PatchPlan,
    "validation_result": PlanValidationResult,
    "user_feedback": str | null,
    "fingerprint_summary": dict
  }
  Response: KnowledgeObject

GET /api/v1/knowledge
  Query params: layer, outcome, issue_class, version_scope, search, limit, offset
  Response: { "objects": [KnowledgeObject], "total": int }

GET /api/v1/knowledge/{knowledge_id}
  Response: KnowledgeObject

PUT /api/v1/knowledge/{knowledge_id}
  Request: Partial<KnowledgeObject>   # For manual annotation
  Response: KnowledgeObject

DELETE /api/v1/knowledge/{knowledge_id}
  Response: { "deleted": bool }

POST /api/v1/knowledge/query
  Request: {
    "issue_signature": IssueSignature,
    "include_negative": bool,         # default true
    "top_k": int,                     # default 5
    "version_scope": str | "auto"
  }
  Response: {
    "matches": [KnowledgeMatch],
    "negative_matches": [KnowledgeMatch]
  }

POST /api/v1/knowledge/export
  Request: { "knowledge_ids": [str] | null }  # null = all
  Response: { "objects": [KnowledgeObject] }   # JSON array

POST /api/v1/knowledge/import
  Request: { "objects": [KnowledgeObject] }
  Response: { "imported": int, "duplicates_skipped": int }

# Playbook management
POST /api/v1/knowledge/playbooks
  Request: Playbook
  Response: KnowledgeObject

GET /api/v1/knowledge/playbooks
  Response: { "playbooks": [KnowledgeObject] }
```

---

## File Structure

```
service/
└── isaac_assist_service/
    └── knowledge/
        ├── __init__.py
        ├── capture.py               # Automatic knowledge object creation
        ├── signature.py             # Issue signature extraction and matching
        ├── query.py                 # Cross-layer query engine
        ├── negative_memory.py       # Failed-fix tracking and exclusion
        ├── confidence.py            # Success-count-based confidence boosting
        ├── playbook.py              # Playbook CRUD
        ├── export_import.py         # JSON export/import with dedup
        ├── storage.py               # SQLite persistence for knowledge objects
        └── routes.py
```

---

## Implementation Notes

- **Storage:** Use SQLite with FTS5 for full-text search over knowledge objects. Store each knowledge object as a JSON blob in a row alongside indexed fields (issue_class, outcome, version_scope, created_at).
- **Issue signature matching:** Use a weighted similarity score combining: exact match on issue_class (0.4), Jaccard similarity on prim_types and schemas (0.3), fuzzy match on error_patterns (0.2), version scope overlap (0.1).
- **Negative memory integration with planner:** The patch planner (module 06) should call `POST /api/v1/knowledge/query` with the current issue signature before generating a plan. If negative matches exist, either exclude those fix patterns or reduce their confidence.
- **Auto-capture timing:** Capture happens when: (a) a plan is applied and validated, (b) a plan is applied and rolled back, (c) a plan is rejected by the user. Do not capture explain-only or propose-only interactions.
- **Deduplication on import:** Match by issue_class + fix_summary + version_scope. If a match exists, merge (increment success/failure counts) rather than duplicate.
- **Playbook format:** Playbooks are stored as knowledge objects with `object_type: "playbook"` and additional structured fields for trigger conditions and step-by-step instructions.

---

## Acceptance Criteria

- [ ] Knowledge objects are automatically captured after fix apply + validate cycles.
- [ ] Negative memory prevents re-proposal of previously failed fixes (or warns).
- [ ] Positive memory boosts confidence of previously successful fixes.
- [ ] Query by issue signature returns relevant matches ranked by similarity.
- [ ] Full-text search works across all knowledge object fields.
- [ ] Export produces valid JSON importable by another instance.
- [ ] Import deduplicates and merges correctly.
- [ ] Playbooks can be authored, stored, and matched against incoming issues.
- [ ] Knowledge objects store correct version scope from the fingerprint.
