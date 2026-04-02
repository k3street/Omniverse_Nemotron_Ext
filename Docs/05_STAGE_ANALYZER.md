# 05 — Stage Analyzer + Validator Packs

## Purpose

Inspect the current USD stage, selected prims, and relevant sublayers to produce structured diagnostics. Run configurable validation packs and generate causal graphs of likely failure points. This is the simulation-domain intelligence layer.

## Runtime

Extension (primary — direct USD/pxr access), Background service (for heavy analysis and causal reasoning)

## Phase

2 (Weeks 6–8)

## Dependencies

- Extension shell (01) for selection/stage events
- Environment fingerprint (02) for context
- Source registry (04) for retrieving relevant docs per prim type

---

## Functional Requirements

### FR-05.1 Stage Inspection

On demand or on stage open, collect:

| Component | Inspection Targets |
|-----------|--------------------|
| **Stage metadata** | Root layer path, default prim, up-axis, meters-per-unit, time codes, sublayer stack |
| **Prim hierarchy** | Full prim tree with types, kinds, purposes, and visibility |
| **Composition** | References, payloads, inherits, variants, sublayer opinions |
| **Schemas** | Applied API schemas per prim (PhysicsRigidBodyAPI, PhysicsArticulationRootAPI, etc.) |
| **Materials** | Material bindings, shader types (MDL, UsdPreviewSurface), texture paths, broken references |
| **Articulations** | Joint types, limits, drives, articulation roots, kinematic chains |
| **Sensors** | Camera prims, RTX sensor configs, contact sensors, IMU, lidar |
| **Physics** | PhysicsScene settings, gravity, solver type, time step, collision groups, filters |
| **OmniGraph** | Action graphs, push graphs, node types, connection validity |
| **Assets** | Referenced asset paths, resolved vs. unresolved, missing files |

### FR-05.2 Incremental Inspection

Full-stage scans are expensive. Support incremental modes:
- **Selection-scoped:** Inspect only selected prims and their immediate dependencies (parents, referenced assets, applied schemas, material bindings).
- **Dirty-layer-scoped:** Inspect only prims in layers modified since last inspection.
- **Full scan:** Complete stage inspection (background, async, with progress reporting).

### FR-05.3 Validator Packs

Configurable, composable validation rule sets. Each pack is a collection of validation rules that can be run independently or together.

**Built-in packs (MVP):**

| Pack | Rules |
|------|-------|
| **Import health** | Missing asset references, unresolved paths, unsupported file formats, duplicate prim names |
| **Schema consistency** | Required schemas missing (e.g., rigid body without collision), conflicting schemas, deprecated schemas |
| **Material/physics mismatch** | Physics prims without collision geometry, visual-only materials on physics objects, missing material bindings |
| **Articulation integrity** | Broken kinematic chains, joints without drives, articulation root missing, invalid joint limits |
| **Sensor completeness** | Cameras without render products, sensors without proper parent hierarchy, missing sensor properties |
| **ROS bridge readiness** | Required action graphs present, topic naming conventions, frame ID consistency, TF tree completeness |
| **Isaac Lab task sanity** | Task class requirements, observation/action space definitions, reward function references, environment config validity |
| **Performance warnings** | Excessive prim count, high-poly meshes without LOD, too many rigid bodies, unnecessary visibility |

### FR-05.4 Validation Rule Interface

Each validation rule follows a standard interface:

```python
class ValidationRule:
    rule_id: str               # e.g., "import.missing_asset"
    pack: str                  # e.g., "import_health"
    severity: str              # "error" | "warning" | "info"
    name: str
    description: str
    
    def check(self, stage: Usd.Stage, context: AnalysisContext) -> List[ValidationFinding]: ...
    def auto_fixable(self) -> bool: ...
    def suggest_fix(self, finding: ValidationFinding) -> Optional[FixSuggestion]: ...
```

### FR-05.5 Causal Graph Generation

Instead of a flat list of warnings, produce a causal graph:
- Root causes (e.g., "missing collision mesh on `/Robot/base_link`")
- Downstream effects (e.g., "physics simulation will skip this body", "contact sensor on `/Robot/gripper` will return no data")
- Severity propagation (a root-cause error should elevate downstream warnings)

### FR-05.6 Selection-Context Retrieval

When the user selects a prim:
1. Run selection-scoped inspection.
2. Query the source registry for documentation relevant to the prim's type and schemas.
3. Query the knowledge base for prior issues/fixes involving similar prim types.
4. Present a combined context card in the UI.

---

## Data Models

### AnalysisContext

```python
@dataclass
class AnalysisContext:
    fingerprint: EnvironmentFingerprint
    stage_path: str
    stage_up_axis: str
    stage_meters_per_unit: float
    selected_prims: List[str]
    analysis_scope: str      # "selection" | "dirty_layers" | "full"
    timestamp: datetime
```

### StageAnalysisResult

```python
@dataclass
class StageAnalysisResult:
    analysis_id: str
    context: AnalysisContext
    
    # Stage summary
    total_prims: int
    prim_type_counts: Dict[str, int]
    sublayer_count: int
    total_references: int
    unresolved_references: List[str]
    
    # Validation results
    findings: List[ValidationFinding]
    findings_by_severity: Dict[str, int]   # error/warning/info counts
    findings_by_pack: Dict[str, int]
    
    # Causal graph
    causal_graph: Optional[CausalGraph]
    
    # Timing
    duration_seconds: float

@dataclass
class ValidationFinding:
    finding_id: str
    rule_id: str
    pack: str
    severity: str            # "error" | "warning" | "info"
    prim_path: Optional[str]
    message: str
    detail: str              # Extended explanation
    evidence: Dict[str, Any] # Prim properties, values, etc. that support the finding
    auto_fixable: bool
    fix_suggestion: Optional[FixSuggestion]
    related_docs: List[str]  # Source chunk IDs from retrieval

@dataclass
class FixSuggestion:
    description: str
    confidence: float        # 0.0–1.0
    changes: List[ProposedChange]

@dataclass
class ProposedChange:
    target_type: str         # "usd_property" | "usd_prim" | "file" | "setting"
    target_path: str
    action: str              # "set" | "add" | "remove" | "create" | "delete"
    property_name: Optional[str]
    old_value: Optional[Any]
    new_value: Optional[Any]

@dataclass
class CausalGraph:
    nodes: List[CausalNode]
    edges: List[CausalEdge]

@dataclass
class CausalNode:
    node_id: str
    finding_id: str
    node_type: str           # "root_cause" | "effect" | "warning"
    label: str
    severity: str

@dataclass
class CausalEdge:
    source_id: str
    target_id: str
    relationship: str        # "causes" | "may_cause" | "blocks"
```

---

## API Contract

### Service Endpoints

```
POST /api/v1/analysis/run
  Request: {
    "scope": str,                 # "selection" | "dirty_layers" | "full"
    "selected_prims": [str],
    "packs": [str] | null,        # null = all enabled packs
    "include_causal_graph": bool,
    "include_fix_suggestions": bool
  }
  Response: StageAnalysisResult

GET /api/v1/analysis/{analysis_id}
  Response: StageAnalysisResult

POST /api/v1/analysis/inspect-prim
  Request: {
    "prim_path": str,
    "include_dependencies": bool,
    "include_docs": bool
  }
  Response: {
    "prim_summary": PrimSummary,
    "findings": [ValidationFinding],
    "related_docs": [RetrievalResult],
    "prior_issues": [KnowledgeObject]
  }

GET /api/v1/analysis/packs
  Response: {
    "packs": [{
      "name": str,
      "description": str,
      "rule_count": int,
      "enabled": bool
    }]
  }

PUT /api/v1/analysis/packs/{pack_name}
  Request: { "enabled": bool }
  Response: { "updated": bool }
```

### Extension-Side Interface

```python
class StageAnalyzerUI:
    def get_prim_summary(self, prim_path: str) -> PrimSummary: ...
    def get_selection_context(self) -> SelectionAnalysis: ...
    # These call the background service:
    async def run_analysis(self, scope: str) -> StageAnalysisResult: ...
    async def inspect_prim(self, prim_path: str) -> PrimInspection: ...
```

Note: The extension-side `get_prim_summary` reads directly from the USD stage via `pxr` APIs for responsiveness. Heavy analysis (causal graph, doc retrieval) is delegated to the background service.

---

## File Structure

```
exts/
└── omni.isaac.assist/
    └── omni/isaac/assist/
        └── analyzers/
            ├── __init__.py
            ├── prim_inspector.py      # Direct pxr prim inspection
            ├── stage_summary.py       # Stage-level summary collection
            └── selection_context.py   # Selection-scoped analysis

service/
└── isaac_assist_service/
    └── analysis/
        ├── __init__.py
        ├── orchestrator.py            # Analysis run coordination
        ├── causal_graph.py            # Causal graph generation
        ├── validators/
        │   ├── __init__.py
        │   ├── base.py                # ValidationRule interface
        │   ├── import_health.py
        │   ├── schema_consistency.py
        │   ├── material_physics.py
        │   ├── articulation.py
        │   ├── sensor_completeness.py
        │   ├── ros_bridge.py
        │   ├── isaac_lab_task.py
        │   └── performance.py
        ├── fix_suggestor.py           # Auto-fix suggestion generation
        └── routes.py
```

---

## Implementation Notes

- **Direct pxr access:** The extension-side prim inspector must use `pxr.Usd`, `pxr.UsdGeom`, `pxr.UsdPhysics`, `pxr.UsdShade`, `pxr.PhysxSchema` directly for fast reads. Do not serialize the entire stage and send it to the service for basic inspection.
- **Service-side analysis:** For full scans and causal graph generation, the extension serializes a stage summary (prim paths, types, key properties) and sends it to the service. The service does not need direct `pxr` access if the summary is rich enough.
- **Validator extensibility:** Use a plugin/registration pattern so new packs can be added without modifying core code. Each validator pack is a Python module that registers its rules on import.
- **Causal graph:** Start simple — build a directed graph where each finding can list `caused_by` references to other findings. Propagate severity upward. Full causal inference can be a post-MVP enhancement.
- **Performance:** For stages with 10,000+ prims, a full scan may take 10–30 seconds. Use async execution with progress callbacks. Cache results and invalidate on stage mutations.
- **Isaac Lab task validation:** This requires understanding task class structure (`ManagerBasedRLEnv` config, observation/action space definitions). Parse the Python task files referenced in the workspace.

---

## Acceptance Criteria

- [ ] Selection-scoped inspection returns prim summary within 500ms for typical prims.
- [ ] Full-stage analysis completes within 30s for a 10,000-prim stage.
- [ ] At least 4 validator packs (import health, schema consistency, articulation integrity, material/physics) produce correct findings on test scenes.
- [ ] Causal graph links root causes to downstream effects.
- [ ] Findings include auto-fix suggestions where applicable.
- [ ] Selection context card in UI shows prim summary, findings, and related docs.
- [ ] Validator packs can be enabled/disabled individually.
