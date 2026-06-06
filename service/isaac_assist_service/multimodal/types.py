"""
LayoutSpec intermediate representation — typed schemas.

The IR every modality produces and the canonical pipeline consumes. Three-layer
vocabulary regime per spec §3:

- L0 `pattern_hint`: closed enum, version-bumped, success-criterion-discriminated
- L1 `structural_features`: typed booleans + numerics; additive only
- L2 `structural_tags`: namespaced strings; format-regex on shape, never content;
  membership validated against registry (see `vocabulary.py`)

The regime exists to eliminate regex-family fragility from the build path.
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional, Tuple

from pydantic import BaseModel, Field, field_validator


# ---------------------------------------------------------------------------
# CRM-C1 — compliance mode closed enum
# ---------------------------------------------------------------------------

COMPLIANCE_MODE_ENUM = frozenset({
    "admittance",
    "cartesian_compliance_fdcc",
    "cartesian_impedance",
    "variable_impedance",
    "franka_cartesian_impedance",
    "null",
})
"""
Closed set of legal compliance_mode values (excluding Python None which means
"unset / auto-pick at planning time").  Validated in validate.py.
"""


# ---------------------------------------------------------------------------
# L0 — closed enums
# ---------------------------------------------------------------------------

PatternHint = Literal[
    "pick_place",   # success: workpiece in destination, at rest
    "sort",         # success: workpiece in correct-class destination
    "reorient",     # success: workpiece in destination AND oriented
    "navigate",     # success: mobile platform at goal pose
    "insert",       # success: peg/part seated in hole (force-threshold or assembly-constraint active)
    "train",        # success: training loop running / dataset exported / gap metric produced
    "other",        # long-tail novel pattern; structural_tags provide retrieval discrimination
]
"""
Closed enum. Discriminates by simulation success-criterion shape, NOT by
surface task description. CP-04 (constraint) is `pick_place` — the bounded
footprint is a structural feature, not a distinct pattern.

Round 12 additions (2026-05-16):
  `insert`  — promoted from novel_pattern; ≥3 templates share force/compliance
              insertion success criterion.
  `train`   — promoted from novel_pattern; ≥3 templates share training-loop /
              dataset-export / gap-measurement success criterion.
  `other`   — explicit catch-all for ≤2-template clusters.  Intentionally NOT
              named `custom` — `other` signals "unclassified, pending promotion"
              whereas `custom` implies permanent exception.  When ≥3 `other`
              templates share a clear success-criterion shape, promote to a new
              named value (minor version bump + extractor update required).
"""


Modality = Literal[
    "text",        # text prompt — produces only intent, no objects/bindings
    "sketch",      # uploaded sketch image — VLM-parsed
    "drag_drop",   # canvas SPA — explicit user placement
    "photo",       # photo of real environment — VLM-parsed
    "voice",       # speech-to-text → text path
    "viewport",    # extracted from current 3D scene state
]


BindingSource = Literal[
    "user_explicit",    # drag-drop right-click → "set as primary_robot"
    "modality_emitted", # modality producer emitted role_hint
    "disambiguator",    # ratifier ran auto-binding via template-declared rule
    "user_correction",  # rebind_role tool call
]

SpatialRelationKind = Literal[
    "on_top_of",
    "inside",
    "contains",
    "supports",
    "attached_to",
    "mounted_to",
    "beside",
    "near",
    "left_of",
    "right_of",
    "front_of",
    "behind",
    "stacked_above",
]

LightingPreset = Literal[
    "studio",
    "warehouse_dim",
    "warehouse_bright",
    "backlit",
    "dome_overcast",
    "low_angle",
]

CameraPreset = Literal[
    "overhead",
    "robot_view",
    "side_view",
    "wide_context",
]

ActorPreset = Literal[
    "human_observer",
    "forklift_nearby",
    "mobile_robot_crossing",
]

CircumstancePreset = Literal[
    "nominal",
    "occluded_target",
    "distractor_objects",
    "moved_target",
    "tight_clearance",
]


# ---------------------------------------------------------------------------
# L0 — counts
# ---------------------------------------------------------------------------

class Counts(BaseModel):
    """
    Integer instance counts per entity class. Additive: new entity classes
    require minor schema bump.
    """
    robots: int = Field(default=0, ge=0)
    conveyors: int = Field(default=0, ge=0)
    bins: int = Field(default=0, ge=0)
    cubes: int = Field(default=0, ge=0)
    sensors: int = Field(default=0, ge=0)
    humans: int = Field(default=0, ge=0)


# ---------------------------------------------------------------------------
# L1 — structural features
# ---------------------------------------------------------------------------

DestinationKind = Literal["single_bin", "n_bins_routed", "shelf", "fixture"]
RoutingAxis = Literal["color", "size", "shape", "label"]


class StructuralFeatures(BaseModel):
    """
    Typed structural facts. Booleans default false; numerics default None;
    new fields are additive (old data reads as default).

    Removing or retyping a field is a major schema bump.
    """
    # Cardinality of structural elements (distinct from counts —
    # counts are entity-class instances; n_* are role-positions).
    n_robot_stations: int = Field(default=1, ge=1)
    n_handoffs: int = Field(default=0, ge=0)
    n_destinations: int = Field(default=1, ge=1)

    # Destination shape
    destination_kind: DestinationKind = "single_bin"
    routing_axis: Optional[RoutingAxis] = None

    # Capability flags
    uses_conveyor_transport: bool = False
    uses_navigation: bool = False
    has_color_routing: bool = False
    has_orientation_requirement: bool = False
    has_bounded_footprint: bool = False
    has_passive_intermediate_station: bool = False
    has_active_intermediate_station: bool = False
    has_human_in_workspace: bool = False
    has_floor_transitions: bool = False

    # Numeric facts (None when N/A)
    footprint_xy_max_m: Optional[Tuple[float, float]] = None
    upright_dot_threshold: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    human_safety_distance_m: Optional[float] = Field(default=None, ge=0.0)


# ---------------------------------------------------------------------------
# L2 — structural tags (namespaced, registry-validated)
# ---------------------------------------------------------------------------

# Format regex: well-formedness only. Content validation (registry membership)
# happens in validate.py against the structural_tags registry. This regex
# enforces SHAPE, never tag CONTENT — never used as a substring/keyword
# classifier downstream.
STRUCTURAL_TAG_FORMAT = re.compile(
    r"^(isaac|cad|user):[a-z0-9_]+(\.[a-z0-9_]+)*$"
)

StructuralTag = str
"""
Namespaced tag string of form `<namespace>:<segment>(.<segment>)*` where
namespace ∈ {`isaac`, `cad`, `user`}.

- `isaac:` and `cad:` tags must appear in the registry to validate.
- `user:` tags pass through but are observability-only — retrieval and
  hard-instantiate ignore them. They exist so LLM-emitted ad-hoc tags never
  pollute the canonical-relevant tag space.

Examples:
    isaac:transport.conveyor
    isaac:robot.fixed_base.arm
    isaac:topology.linear_pipeline
    isaac:invariant.cube_upright
    isaac:routing.semantic_label.color
    cad:imported.fusion360
    user:annotation.priority_first
"""


# ---------------------------------------------------------------------------
# Intent — three-layer vocabulary composed
# ---------------------------------------------------------------------------

class Intent(BaseModel):
    """
    The structured representation of user intent. Drives retrieval (via
    structural-filter-first protocol per spec §8.1) without ever going through
    natural-language synthesis.
    """
    pattern_hint: PatternHint
    counts: Counts = Field(default_factory=Counts)
    structural_features: StructuralFeatures = Field(default_factory=StructuralFeatures)
    structural_tags: List[StructuralTag] = Field(default_factory=list)

    @field_validator("structural_tags")
    @classmethod
    def _validate_tag_format(cls, v: List[str]) -> List[str]:
        """Enforce tag-format regex only. Registry membership is checked in
        validate.py during full-spec validation."""
        for tag in v:
            if not STRUCTURAL_TAG_FORMAT.match(tag):
                raise ValueError(
                    f"structural_tag {tag!r} does not match format "
                    f"{STRUCTURAL_TAG_FORMAT.pattern!r}"
                )
        return v


# ---------------------------------------------------------------------------
# Source — provenance metadata
# ---------------------------------------------------------------------------

class Source(BaseModel):
    """
    Provenance for the LayoutSpec — which modality produced it, with what
    confidence, when, and optionally the original input for re-derive.
    Never read by retrieval, instantiation, or verification — observability
    and downstream-routing only.
    """
    modality: Modality
    confidence: float = Field(ge=0.0, le=1.0)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    raw_input: Optional[Any] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# TypedObject — present when modality has positional information
# ---------------------------------------------------------------------------

class Position(BaseModel):
    """2-D position in drawing-space units (metres)."""
    x: float
    y: float


class Size(BaseModel):
    """2-D bounding-box dimensions; both ``w`` and ``h`` must be positive."""
    w: float = Field(gt=0)
    h: float = Field(gt=0)


class TypedObject(BaseModel):
    """
    A placed object in the layout. Present when the modality knows positions
    (drag-drop, sketch, photo, viewport-edit). Absent for text-prompt /
    voice modalities, which produce only `intent`.
    """
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    object_class: str = Field(alias="class")
    name: str
    position: Position
    rotation: float = Field(default=0.0, ge=0.0, lt=360.0)
    size: Size
    color: Optional[str] = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")
    notes: str = Field(default="", max_length=4096)
    notes_sensitive: bool = False
    metadata: Dict[str, Any] = Field(default_factory=dict)
    role_hint: Optional[str] = None
    locked: bool = False
    layer: str = "default"

    model_config = {"populate_by_name": True}

    @field_validator("name")
    @classmethod
    def _validate_name(cls, v: str) -> str:
        """Name must be a valid USD prim path fragment.

        The exporter produces `/World/{name}` as the prim path; therefore name
        must be USD-identifier-safe.
        """
        if not re.match(r"^[a-zA-Z][a-zA-Z0-9_]*$", v):
            raise ValueError(
                f"object name {v!r} must match ^[a-zA-Z][a-zA-Z0-9_]*$ "
                "(USD prim path fragment)"
            )
        return v


# ---------------------------------------------------------------------------
# RoleBinding — modality binds objects to template roles
# ---------------------------------------------------------------------------

class RoleBinding(BaseModel):
    """
    Binds a template-declared role to a LayoutSpec object. Source flags how the
    binding was established; conflict resolution per spec §5.3 uses the source
    + timestamp + confidence.
    """
    object_id: str
    source: BindingSource
    confidence: float = Field(ge=0.0, le=1.0)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# ---------------------------------------------------------------------------
# SpatialRelation — semantic placement constraints between objects
# ---------------------------------------------------------------------------

class SpatialRelation(BaseModel):
    """
    A directed semantic relation between two LayoutSpec objects.

    Example:
        subject_id="fruit_1", relation="inside", object_id="bowl_1"
        subject_id="bowl_1", relation="on_top_of", object_id="table_1"

    The floor-plan canvas owns editable XY placement. Relations add the missing
    3D semantics needed by the instantiator: support surfaces, container
    nesting, and approximate Z offsets.
    """
    subject_id: str
    relation: SpatialRelationKind
    object_id: str
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    source: str = "user_explicit"
    metadata: Dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# ScenarioVariants — controlled multi-scene generation knobs
# ---------------------------------------------------------------------------

class PerturbationSpec(BaseModel):
    """Randomization bounds for scene variant generation.

    These are intentionally declarative.  Isaac Sim/Cosmos execution layers can
    consume the same contract locally, through Isaac Automator, or on Brev/DGX.
    """
    enabled: bool = True
    pose_jitter_m: float = Field(default=0.03, ge=0.0, le=1.0)
    rotation_jitter_deg: float = Field(default=5.0, ge=0.0, le=180.0)
    material_randomization: bool = True
    sensor_noise: bool = False


class ValidationSpec(BaseModel):
    """Checks that generated variants should satisfy before being accepted."""
    require_relations: bool = True
    require_visibility: bool = True
    require_physics: bool = True


class ScenarioVariants(BaseModel):
    """Controls for generating multiple Isaac/Cosmos scene variants.

    The floor-plan canvas owns the base semantic layout.  This model describes
    how an agent or tool runner may fan that base layout out into a campaign of
    lighting, camera, actor, circumstance, and perturbation variants.
    """
    enabled: bool = False
    variant_count: int = Field(default=1, ge=1, le=500)
    seed: int = Field(default=1, ge=0)
    lighting: List[LightingPreset] = Field(default_factory=lambda: ["studio"])
    cameras: List[CameraPreset] = Field(default_factory=lambda: ["overhead"])
    actors: List[ActorPreset] = Field(default_factory=list)
    circumstances: List[CircumstancePreset] = Field(default_factory=lambda: ["nominal"])
    perturbations: PerturbationSpec = Field(default_factory=PerturbationSpec)
    validation: ValidationSpec = Field(default_factory=ValidationSpec)


# ---------------------------------------------------------------------------
# LayoutSpec — top-level
# ---------------------------------------------------------------------------

LAYOUT_SPEC_VERSION = "1.0"


class LayoutSpec(BaseModel):
    """
    The intermediate representation produced by every modality and consumed
    by the canonical pipeline. Top-level structure per spec §3.1.

    `objects`, `constraints`, and `bindings` are optional — text-prompt and
    voice modalities produce only `intent`; canvas and sketch/photo modalities
    produce all three.
    """
    version: Literal["1.0"] = LAYOUT_SPEC_VERSION

    intent: Intent
    objects: Optional[List[TypedObject]] = None
    constraints: Optional[List[Dict[str, Any]]] = None  # full schema in v1.1
    relations: Optional[List[SpatialRelation]] = None
    scenario_variants: ScenarioVariants = Field(default_factory=ScenarioVariants)
    bindings: Optional[Dict[str, RoleBinding]] = None  # role_name -> binding

    parameters: Dict[str, Any] = Field(default_factory=dict)
    """T2 substitution targets — values that vary the same template's behavior
    without changing which template matches. Not read by retrieval."""

    # CRM-C1 — compliance template fields (Phase 20-managed) ----------------
    compliance_mode: Optional[str] = None
    """Compliance variant to activate when the template is instantiated.
    Must be a member of COMPLIANCE_MODE_ENUM or None (auto-pick at planning
    time via autopick_compliance_mode).  Validated in validate.py."""

    compliance_params: Dict[str, Any] = Field(default_factory=dict)
    """Free-form controller parameters passed verbatim to the compliance
    handler (e.g. stiffness K, damping D, mass M).  No nested validation —
    handler is responsible for interpreting its own schema."""

    compliance_handoff_at: float = Field(default=0.5, ge=0.0, le=1.0)
    """Fraction in [0, 1] of the planned trajectory at which rigid execution
    hands off to the compliance controller.  0.0 = immediately compliant;
    1.0 = rigid throughout (no handoff).  Validated in validate.py."""

    source: Source

    revision: int = Field(default=0, ge=0)
    """Monotonically increasing per session. Compare-and-swap protocol uses
    parent_revision in patch requests; mismatch → 409 Conflict."""
