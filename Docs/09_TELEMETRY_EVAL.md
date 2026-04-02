# 09 — Telemetry Pipeline + Evaluation Framework

## Purpose

Track measurable outcomes so the assistant demonstrably improves over time. Emit structured telemetry for diagnosis latency, patch success, rollback rate, and user override rate. Provide evaluation dashboards and regression tracking.

## Runtime

Background service

## Phase

4 (Weeks 12–14)

## Dependencies

- All prior modules (consumes events from each)

---

## Functional Requirements

### FR-09.1 Core Metrics

Track and aggregate the following metrics:

| Metric | Definition | Source |
|--------|-----------|--------|
| **Time to diagnose** | Duration from analysis request to findings presented | Stage analyzer |
| **Time to repair** | Duration from findings presented to fix applied + validated | Patch planner |
| **Time to resolution** | End-to-end: issue reported → validated fix (or escalation) | All modules |
| **First-pass fix rate** | % of plans that resolve all target findings on first apply | Patch planner + validation |
| **Rollback rate** | % of applied plans that are rolled back | Snapshot manager |
| **User override rate** | % of proposed actions rejected or modified by user | Approval engine |
| **Regression rate** | % of applied plans that introduce new findings | Patch planner validation |
| **Escalation rate** | % of issues that could not be fixed locally | Chat UX |
| **Knowledge reuse rate** | % of plans that leverage prior successful fixes | Knowledge base |
| **Retrieval hit rate** | % of diagnoses where relevant sources were found | Source registry |

### FR-09.2 Issue Taxonomy

Classify every issue into a structured taxonomy:

```
Level 1: Domain
  - scene_construction
  - simulation_setup
  - asset_import
  - sensor_configuration
  - extension_conflict
  - python_script
  - isaac_lab_task
  - ros_integration

Level 2: Category (examples)
  - scene_construction.missing_reference
  - simulation_setup.physics_misconfiguration
  - asset_import.unsupported_format
  - isaac_lab_task.observation_space_mismatch

Level 3: Specific issue (auto-generated from findings)
  - simulation_setup.physics_misconfiguration.wrong_solver_type
```

Track metrics per taxonomy level to identify which issue domains are best/worst served.

### FR-09.3 Event Emission

Emit structured events for every significant system action:

```python
@dataclass
class TelemetryEvent:
    event_id: str
    event_type: str              # See event types below
    timestamp: datetime
    session_id: str
    
    # Context
    fingerprint_summary: Dict[str, str]
    issue_taxonomy: Optional[str]
    
    # Timing
    duration_ms: Optional[int]
    
    # Outcome
    outcome: Optional[str]
    
    # Details
    metadata: Dict[str, Any]
```

Event types:
- `analysis.started`, `analysis.completed`
- `retrieval.query`, `retrieval.results`
- `plan.generated`, `plan.approved`, `plan.rejected`
- `plan.applied`, `plan.validated`
- `rollback.performed`
- `escalation.triggered`
- `knowledge.captured`, `knowledge.reused`
- `session.started`, `session.ended`

### FR-09.4 Aggregation and Dashboards

Compute aggregated metrics over configurable time windows:
- Last 7 days, 30 days, 90 days, all time
- Per issue taxonomy level
- Per environment fingerprint (version combination)
- Per operational mode

Expose aggregated data via API for dashboard rendering in the extension UI.

### FR-09.5 Regression Tracking

Detect when the assistant's performance degrades:
- Compare current-window metrics to baseline (first 30 days or manual baseline).
- Alert (in-product notification) if:
  - First-pass fix rate drops by more than 10 percentage points
  - Rollback rate exceeds 15%
  - User override rate exceeds 40%
- Store regression alerts in the audit log.

### FR-09.6 Privacy-Safe Aggregation

- No PII in telemetry events.
- No raw stage content, file contents, or user messages in telemetry.
- Store only structural metadata: prim types, schema names, issue classes, timing, outcomes.
- All telemetry is local-only by default. No external transmission unless explicitly configured.
- Provide a telemetry opt-out setting.

### FR-09.7 Session Tracking

Track per-session metrics:
- Session duration
- Issues diagnosed
- Fixes applied
- Fixes rolled back
- Escalations
- Knowledge objects created

---

## Data Models

### TelemetryAggregation

```python
@dataclass
class MetricsSummary:
    window: str                      # "7d" | "30d" | "90d" | "all"
    period_start: datetime
    period_end: datetime
    
    total_sessions: int
    total_analyses: int
    total_plans_generated: int
    total_plans_applied: int
    total_rollbacks: int
    total_escalations: int
    
    avg_time_to_diagnose_ms: float
    avg_time_to_repair_ms: float
    avg_time_to_resolution_ms: float
    
    first_pass_fix_rate: float       # 0.0–1.0
    rollback_rate: float
    user_override_rate: float
    regression_rate: float
    escalation_rate: float
    knowledge_reuse_rate: float
    retrieval_hit_rate: float
    
    by_taxonomy: Dict[str, TaxonomyMetrics]
    by_version: Dict[str, VersionMetrics]

@dataclass
class TaxonomyMetrics:
    taxonomy: str
    issue_count: int
    fix_rate: float
    avg_time_to_resolution_ms: float
    top_fixes: List[str]             # Most common successful fix summaries

@dataclass
class VersionMetrics:
    version_key: str                 # e.g., "isaacsim_6.0.0_isaaclab_3.0.0"
    issue_count: int
    fix_rate: float
    unique_issue_classes: int
```

---

## API Contract

### Service Endpoints

```
POST /api/v1/telemetry/event
  Request: TelemetryEvent
  Response: { "recorded": bool }

POST /api/v1/telemetry/events/batch
  Request: { "events": [TelemetryEvent] }
  Response: { "recorded": int }

GET /api/v1/telemetry/summary
  Query params: window (7d|30d|90d|all), taxonomy_prefix, version_key
  Response: MetricsSummary

GET /api/v1/telemetry/timeline
  Query params: metric (e.g., "first_pass_fix_rate"), granularity (day|week), date_from, date_to
  Response: { "points": [{ "date": str, "value": float }] }

GET /api/v1/telemetry/regressions
  Response: {
    "alerts": [{
      "metric": str,
      "current_value": float,
      "baseline_value": float,
      "delta": float,
      "severity": str,
      "detected_at": datetime
    }]
  }

GET /api/v1/telemetry/sessions
  Query params: date_from, date_to, limit
  Response: { "sessions": [SessionSummary] }

GET /api/v1/telemetry/config
  Response: { "enabled": bool, "retention_days": int }

PUT /api/v1/telemetry/config
  Request: { "enabled": bool, "retention_days": int }
  Response: { "updated": bool }
```

---

## File Structure

```
service/
└── isaac_assist_service/
    └── telemetry/
        ├── __init__.py
        ├── emitter.py               # Event emission (called by other modules)
        ├── storage.py               # SQLite event storage
        ├── aggregator.py            # Metric computation and windowing
        ├── regression.py            # Regression detection
        ├── taxonomy.py              # Issue taxonomy classification
        ├── privacy.py               # PII stripping and validation
        ├── session.py               # Session tracking
        └── routes.py
```

---

## Implementation Notes

- **Storage:** SQLite database (`~/.isaac_assist/telemetry.db`) with tables for raw events and pre-computed aggregations.
- **Event emission:** Other modules call `telemetry.emitter.emit(event)` directly. The emitter is fire-and-forget — it should never block the calling module. Use a queue if needed.
- **Aggregation:** Pre-compute summaries on a schedule (every 15 minutes or on query if stale). Cache summaries with TTL.
- **Regression detection:** Run after each aggregation cycle. Compare the current 7-day window to the 30-day baseline. Use simple threshold-based detection for MVP; statistical tests can be added later.
- **Taxonomy auto-classification:** Map validation rule IDs to taxonomy categories. Use the rule's `pack` as Level 1, the rule's `rule_id` prefix as Level 2, and the specific rule as Level 3.
- **Retention:** Prune raw events older than `retention_days` (default 90). Keep aggregated summaries indefinitely.
- **Extension UI:** Add a simple "Performance" tab in the Isaac Assist pane showing key metrics as sparklines or simple bar charts. Full dashboard is a post-MVP enhancement.

---

## Acceptance Criteria

- [ ] All 10 core metrics are tracked and computable.
- [ ] Events are emitted for all listed event types.
- [ ] Summary API returns correct aggregations for 7d, 30d, 90d, all-time windows.
- [ ] Regression detection alerts when fix rate drops by >10pp.
- [ ] No PII or raw content in telemetry events.
- [ ] Telemetry can be disabled entirely via config.
- [ ] Session summaries accurately reflect per-session activity.
- [ ] Issue taxonomy classifies findings into at least two levels.
