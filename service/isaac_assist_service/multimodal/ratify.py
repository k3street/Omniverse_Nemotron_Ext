"""
Ratify — pure deterministic function that maps a LayoutSpec onto a
template's named roles.

Per spec §5: three-stage protocol propose → ratify → execute. Ratify is the
deterministic gate that takes (template_roles, layout_spec.objects,
layout_spec.bindings) and produces a RatifyResult — either fully bound
({status: "ok"}), bindable-with-user-input ({status: "needs_choice"}), or
rejected ({status: "rejected"} with structured errors).

Design:
- Pure function, unit-testable in isolation, no I/O, no LLM.
- Auto-binding waterfall: cardinality-trivial → disambiguator → modality
  emission order → user-confirmation prompt. Never LLM-mediated.
- Compatible with BOTH role-based templates (Block 1B) AND legacy templates
  (no `roles` field — pre-refactor). Legacy mode trivially returns ok.

This unblocks Block 1A: the ratify wrapper can be inserted into the
hard-instantiate path now, before role-based templates land. Wrapper acts as
a no-op for legacy templates and full ratifier for role-based ones.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional

from .types import LayoutSpec, RoleBinding, TypedObject

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Disambiguator registry
# ---------------------------------------------------------------------------

DisambiguatorFn = "Callable[[List[TypedObject]], List[TypedObject]]"


def _disambig_smaller_x_first(objs: List[TypedObject]) -> List[TypedObject]:
    """Sort objects ascending by world X coordinate.

    Args:
        objs: Candidate TypedObjects.

    Returns:
        Sorted list, smallest X first.
    """
    return sorted(objs, key=lambda o: o.position.x)


def _disambig_larger_x_first(objs: List[TypedObject]) -> List[TypedObject]:
    """Sort objects descending by world X coordinate.

    Args:
        objs: Candidate TypedObjects.

    Returns:
        Sorted list, largest X first.
    """
    return sorted(objs, key=lambda o: o.position.x, reverse=True)


def _disambig_smaller_y_first(objs: List[TypedObject]) -> List[TypedObject]:
    """Sort objects ascending by world Y coordinate.

    Args:
        objs: Candidate TypedObjects.

    Returns:
        Sorted list, smallest Y first.
    """
    return sorted(objs, key=lambda o: o.position.y)


def _disambig_larger_y_first(objs: List[TypedObject]) -> List[TypedObject]:
    """Sort objects descending by world Y coordinate.

    Args:
        objs: Candidate TypedObjects.

    Returns:
        Sorted list, largest Y first.
    """
    return sorted(objs, key=lambda o: o.position.y, reverse=True)


def _disambig_nearest_to_origin(objs: List[TypedObject]) -> List[TypedObject]:
    """Sort objects by ascending 2D distance from the world origin (0, 0).

    Args:
        objs: Candidate TypedObjects.

    Returns:
        Sorted list, nearest first.
    """
    return sorted(
        objs,
        key=lambda o: math.hypot(o.position.x, o.position.y),
    )


def _disambig_farthest_from_origin(objs: List[TypedObject]) -> List[TypedObject]:
    """Sort objects by descending 2D distance from the world origin (0, 0).

    Args:
        objs: Candidate TypedObjects.

    Returns:
        Sorted list, farthest first.
    """
    return sorted(
        objs,
        key=lambda o: math.hypot(o.position.x, o.position.y),
        reverse=True,
    )


def _disambig_first_listed(objs: List[TypedObject]) -> List[TypedObject]:
    """Return objects in their original emission order (no reordering).

    Args:
        objs: Candidate TypedObjects.

    Returns:
        A shallow copy in the same order.
    """
    return list(objs)  # preserve emission order


DISAMBIGUATORS: Dict[str, "DisambiguatorFn"] = {
    "smaller_x_first": _disambig_smaller_x_first,
    "larger_x_first": _disambig_larger_x_first,
    "smaller_y_first": _disambig_smaller_y_first,
    "larger_y_first": _disambig_larger_y_first,
    "nearest_to_origin": _disambig_nearest_to_origin,
    "farthest_from_origin": _disambig_farthest_from_origin,
    "first_listed": _disambig_first_listed,
}


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

RatifyStatus = Literal["ok", "needs_choice", "rejected"]


@dataclass
class BindingDiagnostic:
    """One observed decision the ratifier made — citable in the directive
    + auditable by the post-reply verification guard per spec §5.4."""
    role_name: str
    object_id: Optional[str]
    decision: str
    reason: str


@dataclass
class RatifyError:
    """One ratify failure. `kind` discriminates the recovery path per
    spec §5.4 (wrong_class / constraint_fail / unbindable)."""
    kind: Literal["wrong_class", "constraint_fail", "unbindable"]
    role_name: str
    expected: Optional[List[str]] = None
    got: Optional[str] = None
    constraint: Optional[str] = None
    diagnosis: Optional[str] = None
    candidate_object_ids: Optional[List[str]] = None


@dataclass
class AmbiguityRecord:
    """Role that has multiple plausible candidates and no disambiguator
    fired. UI surfaces this for explicit user choice."""
    role_name: str
    candidate_object_ids: List[str]
    role_constraints: List[str]


@dataclass
class RatifyResult:
    """Output of ratify(). Inspect `status`; on `ok`, `bindings` is the final
    role→object_id map. On `needs_choice`, `partial_bindings` has the
    determined ones and `ambiguous_roles` lists the underspecified."""
    status: RatifyStatus
    bindings: Dict[str, RoleBinding] = field(default_factory=dict)
    partial_bindings: Dict[str, RoleBinding] = field(default_factory=dict)
    ambiguous_roles: List[AmbiguityRecord] = field(default_factory=list)
    errors: List[RatifyError] = field(default_factory=list)
    diagnostics: List[BindingDiagnostic] = field(default_factory=list)


@dataclass
class ComplianceResolution:
    """Result of post-ratify compliance auto-pick / override validation.

    Produced by ``resolve_compliance`` and consumed by the chat handler
    that calls ratify (see chat/tools/multimodal_handlers.py
    ``_handle_apply_layout_spec_to_scene``). Provides the final
    compliance mode to use plus any violations from an explicit override.

    Attributes:
        mode: Final compliance mode to use. ``None`` means rigid baseline.
        source: How ``mode`` was derived — ``"auto"`` (from
            ``autopick_compliance_mode``), ``"override"`` (caller supplied
            it on the LayoutSpec), or ``"skipped"`` (ratify rejected or
            no bindings to dispatch against).
        violations: Phase 11b ``ConstraintViolation`` records emitted by
            ``validate_compliance_override``. Empty when ``source="auto"``
            or override was clean.
        hard_violation: True if any violation has severity ERROR — caller
            should refuse to dispatch the controller.
        diagnostics: Human-readable strings explaining the decision.
    """
    mode: Optional[str]
    source: Literal["auto", "override", "skipped"]
    violations: List[Any] = field(default_factory=list)
    hard_violation: bool = False
    diagnostics: List[str] = field(default_factory=list)


def resolve_compliance(
    layout_spec: LayoutSpec,
    ratify_result: RatifyResult,
) -> ComplianceResolution:
    """Auto-pick or validate the compliance mode for a ratified LayoutSpec.

    Wires together two pieces landed in earlier CRM tasks:

      * Auto-pick (CRM-C2) — when the LayoutSpec has no
        ``compliance_mode`` set, derive one from intent + role bindings
        via ``role_retriever.autopick_compliance_mode``.
      * Override validator (CRM-C3) — when the LayoutSpec explicitly
        sets ``compliance_mode``, run
        ``compliance_validator.validate_compliance_override`` against
        the Phase 11b ``ValidationResult`` framework. Soft violations
        are surfaced as diagnostics; ERROR-severity violations set
        ``hard_violation=True`` so the caller can refuse to dispatch.

    Args:
        layout_spec: The LayoutSpec ratify ran against. Must have its
            three compliance fields (mode/params/handoff_at) populated
            per CRM-C1 schema.
        ratify_result: The result of ``ratify(template, layout_spec)``.
            Used to fish out the primary_robot binding so the auto-pick
            table can match by robot class.

    Returns:
        ComplianceResolution. Always returns; never raises.
    """
    # Skip compliance resolution if ratify didn't succeed — nothing to
    # dispatch against.
    if ratify_result.status != "ok":
        return ComplianceResolution(
            mode=None,
            source="skipped",
            diagnostics=[
                f"ratify status={ratify_result.status!r}; compliance resolution skipped"
            ],
        )

    # Build the role_bindings shape autopick expects: a dict with a
    # "primary_robot" key holding an object that exposes .class (or, in
    # mapping form, just {primary_robot: {"class": "..."}}).
    role_bindings: Dict[str, Any] = {}
    for role_name, binding in (ratify_result.bindings or {}).items():
        obj_id = getattr(binding, "object_id", None)
        if obj_id is None:
            continue
        # Resolve object_id → its object_class
        obj_class: Optional[str] = None
        for obj in (layout_spec.objects or []):
            if getattr(obj, "id", None) == obj_id:
                obj_class = getattr(obj, "object_class", None)
                break
        if obj_class is None:
            continue
        role_bindings[role_name] = {"class": obj_class, "object_id": obj_id}

    # Determine whether the LayoutSpec carries an explicit mode override.
    user_mode = getattr(layout_spec, "compliance_mode", None)
    if user_mode:
        # Override path — validate against Phase 11b framework.
        try:
            from service.isaac_assist_service.chat.tools.compliance_validator import (
                validate_compliance_override,
            )
        except Exception as exc:  # pragma: no cover — only on broken install
            return ComplianceResolution(
                mode=user_mode,
                source="override",
                diagnostics=[f"validator import failed: {exc}"],
            )

        # The validator wants robot_class + has_ft_sensor. Pull from the
        # primary_robot binding; default has_ft_sensor=False (conservative).
        primary = role_bindings.get("primary_robot") or {}
        robot_class = primary.get("class") or ""
        has_ft_sensor = bool(getattr(layout_spec, "compliance_params", {})
                             .get("ft_sensor_path"))
        result = validate_compliance_override(
            mode=user_mode,
            robot_class=robot_class,
            has_ft_sensor=has_ft_sensor,
        )
        # Phase 11b ValidationResult exposes .violations and .errors;
        # accept either to stay decoupled from the framework's exact
        # field names.
        violations = (
            getattr(result, "violations", None)
            or getattr(result, "errors", None)
            or []
        )
        # severity may be the Phase 11b GradedScale enum (with .name)
        # OR a string — handle both.
        def _is_hard(violation: Any) -> bool:
            """Return ``True`` if *violation* has ERROR or CRITICAL severity.

            Args:
                violation: Validation issue with a ``.severity`` attribute or
                    GradedScale IntEnum.

            Returns:
                bool: ``True`` when severity contains ``ERROR`` or ``CRITICAL``.
            """
            sev = getattr(violation, "severity", None)
            if sev is None:
                return False
            sev_name = getattr(sev, "name", None) or str(sev)
            return "ERROR" in sev_name.upper() or "CRITICAL" in sev_name.upper()

        hard = any(_is_hard(v) for v in violations)
        return ComplianceResolution(
            mode=user_mode,
            source="override",
            violations=list(violations),
            hard_violation=hard,
            diagnostics=[
                f"override mode={user_mode!r} robot_class={robot_class!r} "
                f"has_ft_sensor={has_ft_sensor}; "
                f"{len(violations)} violations ({'HARD' if hard else 'soft'})"
            ],
        )

    # Auto-pick path — caller didn't set compliance_mode; derive it.
    try:
        from service.isaac_assist_service.chat.tools.role_retriever import (
            autopick_compliance_mode,
        )
    except Exception as exc:  # pragma: no cover
        return ComplianceResolution(
            mode=None,
            source="auto",
            diagnostics=[f"autopick import failed: {exc}"],
        )

    auto_mode = autopick_compliance_mode(layout_spec, role_bindings)
    return ComplianceResolution(
        mode=auto_mode,
        source="auto",
        diagnostics=[
            f"auto-picked mode={auto_mode!r} from "
            f"intent.has_contact_phase + primary_robot.class"
        ],
    )


# ---------------------------------------------------------------------------
# Template role spec — interpreted from the template's "roles" dict
# ---------------------------------------------------------------------------

@dataclass
class RoleSpec:
    """Internal representation of a single role declaration from a template.

    Templates declare roles as a JSON dict; this class is the parsed view.
    """
    name: str
    constraints: List[str]
    expected_count: int = 1
    required: bool = True
    disambiguator: Optional[str] = None
    min_count: Optional[int] = None
    max_count: Optional[int] = None
    unordered: bool = False
    param_name: Optional[str] = None


def parse_template_roles(template: Dict[str, Any]) -> List[RoleSpec]:
    """Extract the `roles` field from a template dict and produce RoleSpec
    instances. Returns [] for legacy templates without a `roles` field.

    Tolerates the role declaration being either:
    - a dict {role_name: {...}} (preferred)
    - a list [{name: role_name, ...}] (alternate; used if order matters
      explicitly in the template authoring)
    """
    roles_raw = template.get("roles")
    if not roles_raw:
        return []

    out: List[RoleSpec] = []

    if isinstance(roles_raw, dict):
        items = roles_raw.items()
    elif isinstance(roles_raw, list):
        items = ((r["name"], r) for r in roles_raw if "name" in r)
    else:
        raise ValueError(
            f"template.roles must be dict or list of dicts; got {type(roles_raw)}"
        )

    for name, spec in items:
        out.append(RoleSpec(
            name=name,
            constraints=list(spec.get("constraints", [])),
            expected_count=int(spec.get("expected_count", 1)),
            required=bool(spec.get("required", True)),
            disambiguator=spec.get("disambiguator"),
            min_count=spec.get("min"),
            max_count=spec.get("max"),
            unordered=bool(spec.get("unordered", False)),
            param_name=spec.get("param_name"),
        ))

    return out


# ---------------------------------------------------------------------------
# Ratify entry point
# ---------------------------------------------------------------------------

def ratify(
    template: Dict[str, Any],
    layout_spec: LayoutSpec,
) -> RatifyResult:
    """Deterministic role-binding ratifier.

    Args:
        template: The canonical template dict (loaded from JSON). May or may
            not have a `roles` field — legacy templates without `roles` are
            handled trivially.
        layout_spec: The LayoutSpec being ratified. May or may not include
            `objects` and `bindings`. Text-prompt modality omits objects;
            ratify returns ok with no bindings in that case.

    Returns:
        RatifyResult with status ok / needs_choice / rejected.
    """
    role_specs = parse_template_roles(template)

    # Legacy template without roles: trivially ok.
    if not role_specs:
        return RatifyResult(
            status="ok",
            bindings={},
            diagnostics=[BindingDiagnostic(
                role_name="*",
                object_id=None,
                decision="legacy_template_no_roles",
                reason=(
                    "template has no 'roles' field; ratify is a no-op for "
                    "this template until role-based refactor (Block 1B) lands"
                ),
            )],
        )

    # Text-prompt / voice modalities: no objects; canonical pipeline supplies
    # positions via template's authored values; ratify returns ok with
    # diagnostic.
    if not layout_spec.objects:
        return RatifyResult(
            status="ok",
            bindings={},
            diagnostics=[BindingDiagnostic(
                role_name="*",
                object_id=None,
                decision="no_objects_canonical_supplies",
                reason=(
                    "LayoutSpec has no objects; canonical template's authored "
                    "positions become the bindings at exec time via T2 "
                    "parameter substitution"
                ),
            )],
        )

    # Modality-emitted bindings (if any) take precedence — apply first.
    pre_bindings: Dict[str, RoleBinding] = dict(layout_spec.bindings or {})
    obj_by_id: Dict[str, TypedObject] = {o.id: o for o in layout_spec.objects}

    bindings: Dict[str, RoleBinding] = {}
    diagnostics: List[BindingDiagnostic] = []
    errors: List[RatifyError] = []
    ambiguous: List[AmbiguityRecord] = []

    # Track which objects are claimed by which roles to enforce
    # cardinality (e.g., one robot can't be both primary AND secondary).
    claimed_object_ids: set = set()

    for role in role_specs:
        # 1) Honor user-explicit / modality-emitted pre-bindings
        if role.name in pre_bindings:
            pb = pre_bindings[role.name]
            if pb.object_id not in obj_by_id:
                errors.append(RatifyError(
                    kind="unbindable",
                    role_name=role.name,
                    diagnosis=(
                        f"pre-binding references object_id={pb.object_id!r} "
                        "not in spec.objects"
                    ),
                ))
                continue
            obj = obj_by_id[pb.object_id]
            if obj.object_class not in role.constraints:
                errors.append(RatifyError(
                    kind="wrong_class",
                    role_name=role.name,
                    expected=role.constraints,
                    got=obj.object_class,
                ))
                continue
            bindings[role.name] = pb
            claimed_object_ids.add(pb.object_id)
            diagnostics.append(BindingDiagnostic(
                role_name=role.name,
                object_id=pb.object_id,
                decision="pre_binding",
                reason=f"source={pb.source}, confidence={pb.confidence:.2f}",
            ))
            continue

        # 2) Auto-bind: filter unclaimed objects matching role.constraints
        candidates = [
            o for o in layout_spec.objects
            if o.object_class in role.constraints
            and o.id not in claimed_object_ids
        ]

        # Cardinality check: 'required' role with no candidates → unbindable
        if not candidates:
            if role.required:
                errors.append(RatifyError(
                    kind="unbindable",
                    role_name=role.name,
                    expected=role.constraints,
                    candidate_object_ids=[],
                    diagnosis=(
                        f"required role {role.name!r} has no candidate "
                        f"objects of classes {role.constraints} (or all "
                        "matching objects are already claimed by other roles)"
                    ),
                ))
            else:
                diagnostics.append(BindingDiagnostic(
                    role_name=role.name,
                    object_id=None,
                    decision="optional_skipped",
                    reason="no candidates; role is optional",
                ))
            continue

        # 3) expected_count == 1 + exactly one candidate → trivial bind
        if role.expected_count == 1 and len(candidates) == 1:
            obj = candidates[0]
            binding = RoleBinding(
                object_id=obj.id,
                source="disambiguator",
                confidence=1.0,
                timestamp=datetime.now(timezone.utc),
            )
            bindings[role.name] = binding
            claimed_object_ids.add(obj.id)
            diagnostics.append(BindingDiagnostic(
                role_name=role.name,
                object_id=obj.id,
                decision="cardinality_trivial",
                reason=(
                    f"expected_count=1, exactly one candidate of class "
                    f"{obj.object_class!r}"
                ),
            ))
            continue

        # 4) expected_count == 1 + multiple candidates + disambiguator → bind first
        if role.expected_count == 1 and role.disambiguator:
            disambig_fn = DISAMBIGUATORS.get(role.disambiguator)
            if disambig_fn is None:
                errors.append(RatifyError(
                    kind="constraint_fail",
                    role_name=role.name,
                    constraint=f"unknown disambiguator {role.disambiguator!r}",
                    diagnosis=(
                        f"template declared disambiguator "
                        f"{role.disambiguator!r} which is not registered in "
                        f"DISAMBIGUATORS; available: {list(DISAMBIGUATORS)}"
                    ),
                ))
                continue
            ordered = disambig_fn(candidates)
            obj = ordered[0]
            binding = RoleBinding(
                object_id=obj.id,
                source="disambiguator",
                confidence=1.0,
                timestamp=datetime.now(timezone.utc),
            )
            bindings[role.name] = binding
            claimed_object_ids.add(obj.id)
            diagnostics.append(BindingDiagnostic(
                role_name=role.name,
                object_id=obj.id,
                decision="disambiguator",
                reason=(
                    f"applied {role.disambiguator!r} on {len(candidates)} "
                    f"candidates of class {obj.object_class!r}"
                ),
            ))
            continue

        # 5) expected_count == 1 + multiple candidates + NO disambiguator → ambiguous
        if role.expected_count == 1 and len(candidates) > 1:
            ambiguous.append(AmbiguityRecord(
                role_name=role.name,
                candidate_object_ids=[o.id for o in candidates],
                role_constraints=role.constraints,
            ))
            diagnostics.append(BindingDiagnostic(
                role_name=role.name,
                object_id=None,
                decision="ambiguous_no_disambiguator",
                reason=(
                    f"{len(candidates)} candidates, expected_count=1, no "
                    "disambiguator declared; user choice required"
                ),
            ))
            continue

        # 6) Multiple-candidate role (workpieces etc) — bind all in order
        # (or up to max_count). For unordered roles, emission order is fine.
        max_n = role.max_count if role.max_count is not None else len(candidates)
        min_n = role.min_count if role.min_count is not None else 0

        if len(candidates) < min_n:
            if role.required:
                errors.append(RatifyError(
                    kind="unbindable",
                    role_name=role.name,
                    expected=role.constraints,
                    candidate_object_ids=[o.id for o in candidates],
                    diagnosis=(
                        f"role {role.name!r} requires min={min_n} candidates, "
                        f"only {len(candidates)} available"
                    ),
                ))
            continue

        # Bind up to max_n. We model multi-candidate roles as N bindings keyed
        # `role_name[i]` for the bindings dict so each entry has a single
        # object_id (preserving the RoleBinding shape).
        ordered = candidates[:max_n] if role.unordered else candidates[:max_n]
        for i, obj in enumerate(ordered):
            key = f"{role.name}[{i}]" if max_n > 1 or len(candidates) > 1 else role.name
            bindings[key] = RoleBinding(
                object_id=obj.id,
                source="disambiguator",
                confidence=1.0,
                timestamp=datetime.now(timezone.utc),
            )
            claimed_object_ids.add(obj.id)
        diagnostics.append(BindingDiagnostic(
            role_name=role.name,
            object_id=None,
            decision="multi_bound",
            reason=(
                f"bound {len(ordered)} of {len(candidates)} candidates "
                f"(min={min_n}, max={max_n}, unordered={role.unordered})"
            ),
        ))

    # ----------------------------------------------------------------- #
    # Determine final status
    # ----------------------------------------------------------------- #
    if errors:
        return RatifyResult(
            status="rejected",
            errors=errors,
            diagnostics=diagnostics,
            partial_bindings=bindings,
        )
    if ambiguous:
        return RatifyResult(
            status="needs_choice",
            partial_bindings=bindings,
            ambiguous_roles=ambiguous,
            diagnostics=diagnostics,
        )
    return RatifyResult(
        status="ok",
        bindings=bindings,
        diagnostics=diagnostics,
    )
