"""
LayoutSpec validation — boundary check applied at modality emission and at
backend ingest.

Validation rules per spec §3.7:

1. `intent.pattern_hint` must be in the closed enum (Pydantic enforces this
   at type-construction time).
2. Every `structural_tags[]` element must match the format-regex (Pydantic
   enforces) AND appear in the active registry, OR live in the `user:`
   namespace as observability-only.
3. Cross-feature consistency:
   - `has_color_routing == True` ⇒ `routing_axis == "color"`
   - `has_bounded_footprint == True` ⇒ `footprint_xy_max_m is not None`
   - `has_orientation_requirement == True` ⇒ `upright_dot_threshold is not None`
   - `has_human_in_workspace == True` ⇒ `human_safety_distance_m is not None`
4. `counts.robots == 0 AND pattern_hint != "navigate"` — soft warning, not a
   hard failure.
5. `version` must be supported by current reader (else triggers schema
   migration; not handled here).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import List, Optional

from .types import COMPLIANCE_MODE_ENUM, LayoutSpec
from .vocabulary import StructuralTagRegistry, load_default_registry

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Validation result types
# ---------------------------------------------------------------------------

@dataclass
class ValidationIssue:
    """One validation finding. `severity == 'error'` blocks; `'warning'` doesn't."""
    severity: str  # "error" | "warning"
    code: str
    message: str
    field_path: Optional[str] = None


@dataclass
class ValidationResult:
    """Output of validate_layout_spec(). `valid` is True when zero errors."""
    valid: bool
    issues: List[ValidationIssue] = field(default_factory=list)

    @property
    def errors(self) -> List[ValidationIssue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def warnings(self) -> List[ValidationIssue]:
        return [i for i in self.issues if i.severity == "warning"]


class LayoutSpecValidationError(Exception):
    """Raised when LayoutSpec validation finds at least one error and the
    caller used validate_layout_spec(..., raise_on_error=True)."""
    def __init__(self, result: ValidationResult):
        self.result = result
        msg = "; ".join(f"[{i.code}] {i.message}" for i in result.errors)
        super().__init__(f"LayoutSpec invalid: {msg}")


# ---------------------------------------------------------------------------
# Validation entry point
# ---------------------------------------------------------------------------

def validate_layout_spec(
    spec: LayoutSpec,
    registry: Optional[StructuralTagRegistry] = None,
    raise_on_error: bool = False,
) -> ValidationResult:
    """Validate a LayoutSpec against schema rules + registry.

    Args:
        spec: The LayoutSpec to validate. Must already pass Pydantic
            type-construction (this function checks cross-field rules and
            registry membership, not basic typing).
        registry: Structural-tags registry for membership check. If None,
            loads the default registry (creating it on first run).
        raise_on_error: If True, raises LayoutSpecValidationError when any
            error issues are found.

    Returns:
        ValidationResult with `.valid`, `.errors`, `.warnings`.
    """
    if registry is None:
        registry = load_default_registry()

    issues: List[ValidationIssue] = []

    # --- Rule 2: structural_tags registry membership -----------------------
    for i, tag in enumerate(spec.intent.structural_tags):
        namespace = tag.split(":", 1)[0] if ":" in tag else None
        if namespace == "user":
            # Pass-through: user-namespace tags are observability-only and
            # don't need registry membership. Format already validated by
            # Pydantic via STRUCTURAL_TAG_FORMAT.
            continue
        if not registry.is_registered(tag):
            issues.append(ValidationIssue(
                severity="error",
                code="tag.not_registered",
                message=(
                    f"structural_tag {tag!r} is not in the registry. "
                    f"Add it to workspace/vocabulary/structural_tags.registry.json "
                    f"(append-only) before use, or move it to the 'user:' namespace "
                    f"as observability-only."
                ),
                field_path=f"intent.structural_tags[{i}]",
            ))
            continue
        entry = registry.get(tag)
        if entry and entry.status == "deprecated":
            # Deprecated tags must continue to validate (old data) but emit
            # a warning. Retrieval may downgrade quality.
            replacement = (
                f" (replaced by {entry.replaced_by!r})"
                if entry.replaced_by else ""
            )
            issues.append(ValidationIssue(
                severity="warning",
                code="tag.deprecated",
                message=(
                    f"structural_tag {tag!r} is deprecated"
                    f"{replacement}; deprecated in {entry.deprecated_in_version}."
                ),
                field_path=f"intent.structural_tags[{i}]",
            ))

    # --- Rule 3: cross-feature consistency ---------------------------------
    f = spec.intent.structural_features

    if f.has_color_routing and f.routing_axis != "color":
        issues.append(ValidationIssue(
            severity="error",
            code="features.color_routing_axis_mismatch",
            message=(
                "has_color_routing=True requires routing_axis='color'; "
                f"got {f.routing_axis!r}"
            ),
            field_path="intent.structural_features.routing_axis",
        ))

    if f.has_bounded_footprint and f.footprint_xy_max_m is None:
        issues.append(ValidationIssue(
            severity="error",
            code="features.bounded_footprint_missing_value",
            message=(
                "has_bounded_footprint=True requires footprint_xy_max_m; "
                "got None"
            ),
            field_path="intent.structural_features.footprint_xy_max_m",
        ))

    if f.has_orientation_requirement and f.upright_dot_threshold is None:
        issues.append(ValidationIssue(
            severity="error",
            code="features.orientation_missing_threshold",
            message=(
                "has_orientation_requirement=True requires "
                "upright_dot_threshold; got None"
            ),
            field_path="intent.structural_features.upright_dot_threshold",
        ))

    if f.has_human_in_workspace and f.human_safety_distance_m is None:
        issues.append(ValidationIssue(
            severity="error",
            code="features.human_missing_safety_distance",
            message=(
                "has_human_in_workspace=True requires "
                "human_safety_distance_m; got None"
            ),
            field_path="intent.structural_features.human_safety_distance_m",
        ))

    # --- Rule 4: zero-robots-on-non-navigate is suspect (warning) ----------
    if spec.intent.counts.robots == 0 and spec.intent.pattern_hint != "navigate":
        issues.append(ValidationIssue(
            severity="warning",
            code="counts.zero_robots_unexpected",
            message=(
                f"counts.robots=0 with pattern_hint={spec.intent.pattern_hint!r} "
                "is suspect; most non-navigate patterns require ≥1 robot"
            ),
            field_path="intent.counts.robots",
        ))

    # --- Rule: object-name uniqueness (when objects present) --------------
    if spec.objects:
        names_seen: dict[str, int] = {}
        ids_seen: dict[str, int] = {}
        for i, obj in enumerate(spec.objects):
            if obj.name in names_seen:
                issues.append(ValidationIssue(
                    severity="error",
                    code="objects.duplicate_name",
                    message=(
                        f"object name {obj.name!r} appears at indices "
                        f"{names_seen[obj.name]} and {i}; "
                        "names map to USD prim paths and must be unique"
                    ),
                    field_path=f"objects[{i}].name",
                ))
            else:
                names_seen[obj.name] = i

            if obj.id in ids_seen:
                issues.append(ValidationIssue(
                    severity="error",
                    code="objects.duplicate_id",
                    message=(
                        f"object id {obj.id!r} appears at indices "
                        f"{ids_seen[obj.id]} and {i}"
                    ),
                    field_path=f"objects[{i}].id",
                ))
            else:
                ids_seen[obj.id] = i

    # --- CRM-C1: compliance field validation --------------------------------
    if spec.compliance_mode is not None:
        if spec.compliance_mode not in COMPLIANCE_MODE_ENUM:
            issues.append(ValidationIssue(
                severity="error",
                code="compliance.unknown_mode",
                message=(
                    f"compliance_mode {spec.compliance_mode!r} is not in the "
                    f"allowed enum {sorted(COMPLIANCE_MODE_ENUM)!r}; "
                    "set to None for auto-pick or choose a valid mode."
                ),
                field_path="compliance_mode",
            ))

    if not isinstance(spec.compliance_params, dict):
        issues.append(ValidationIssue(
            severity="error",
            code="compliance.params_not_dict",
            message=(
                f"compliance_params must be a dict; got "
                f"{type(spec.compliance_params).__name__!r}"
            ),
            field_path="compliance_params",
        ))

    # compliance_handoff_at range is enforced by Pydantic (ge=0.0, le=1.0)
    # at construction time; no secondary check needed here.

    # --- Rule: bindings reference valid object ids -------------------------
    if spec.bindings and spec.objects:
        valid_ids = {obj.id for obj in spec.objects}
        for role_name, binding in spec.bindings.items():
            if binding.object_id not in valid_ids:
                issues.append(ValidationIssue(
                    severity="error",
                    code="bindings.unknown_object_id",
                    message=(
                        f"binding role={role_name!r} references "
                        f"object_id={binding.object_id!r} which is not in "
                        "spec.objects"
                    ),
                    field_path=f"bindings.{role_name}.object_id",
                ))

    valid = not any(i.severity == "error" for i in issues)
    result = ValidationResult(valid=valid, issues=issues)

    if not valid and raise_on_error:
        raise LayoutSpecValidationError(result)

    return result
