"""CRM-C3 — compliance override validator.

Spec: ``docs/specs/2026-05-11-contact-rich-manipulation-spec.md`` §4.2.

This module implements ``validate_compliance_override`` — the hard-
incompatibility gate for explicit ``compliance_mode`` overrides supplied
by template authors or end-users.  The mainline path (auto-pick) lives
in :mod:`service.isaac_assist_service.chat.tools.role_retriever`; this
module only fires when the user has explicitly bypassed auto-pick.

Design notes
------------

* Uses the Phase 11b framework (``ConstraintViolation`` /
  ``ValidationResult``) from
  :mod:`service.isaac_assist_service.types.violations`.  This module does
  NOT define a new result shape — every violation flows through the
  shared primitives so that downstream consumers (chat panel, governance
  policy gates) treat compliance violations identically to assembly /
  blueprint / route violations.
* Rules are table-driven (``HARD_INCOMPATIBILITIES`` — a tuple of
  ``_HardIncompatRule`` dataclass instances).  Adding a new rule is one
  table row; the engine evaluator (``validate_compliance_override``) is
  rule-agnostic.
* Mode and robot-class strings are matched **case-sensitive** against
  the canonical enums (``COMPLIANCE_MODE_ENUM`` /
  ``role_template_index.py``).  This matches the auto-pick convention
  (see :mod:`role_retriever` comment block: "we want mangled class
  names to fall through to the unknown-robot branch with a safe default
  rather than silently coerce").
* The 20-rule table covers four families:
    1. **Embodiment** — mode requires a specific robot class
       (impedance variants need torque-mode robots; the Franka-tuned
       variant is Franka-only).
    2. **Sensor** — mode requires an attached F/T sensor (admittance,
       FDCC, some impedance variants).
    3. **Input-shape** — empty robot_class, unknown mode, mode == None.
    4. **Real-deployment** — additional checks when the layout carries
       the ``real_robot_deployment`` structural tag.

Round-trip
----------

The returned ``ValidationResult`` is a Pydantic ``BaseModel`` from
:mod:`service.isaac_assist_service.types.violations` — it serialises via
``model_dump_json()`` and deserialises via ``model_validate_json()``;
all fields (``constraint_id``, ``category``, ``severity``, ``message``,
``affected_paths``, ``diagnostics``, ``fix_hint``) survive the trip.
This is asserted in the L0 test suite (see
``tests/test_compliance_autopick.py::TestOverride::test_round_trip``).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, FrozenSet, Mapping, Optional

from service.isaac_assist_service.multimodal.types import (
    COMPLIANCE_MODE_ENUM,
)
from service.isaac_assist_service.types.uncertainty import GradedScale
from service.isaac_assist_service.types.violations import (
    ConstraintViolation,
    ValidationResult,
)


# ---------------------------------------------------------------------------
# Robot-class capability sets
# ---------------------------------------------------------------------------

TORQUE_MODE_ROBOTS: FrozenSet[str] = frozenset({
    "franka_panda",
})
"""Robot classes that expose a torque-mode ros2_control surface.

Franka is the only embodiment in the IA library today with a real
joint-effort interface (via FCI).  Add new torque-mode robots here as
the library expands — the rule predicates close over this set.
"""

POSITION_MODE_ONLY_ROBOTS: FrozenSet[str] = frozenset({
    "ur10e", "ur5e", "ur3e", "kinova_gen3",
})
"""Robot classes that are position-mode only at the ros2_control surface.

These robots CANNOT run torque-mode controllers (cartesian_impedance,
franka_cartesian_impedance) regardless of upstream simulation tricks —
the rule rejects with an admittance suggestion.
"""


# ---------------------------------------------------------------------------
# Mode aliases
# ---------------------------------------------------------------------------

# Modes that require a torque-mode robot (effort interface).
_IMPEDANCE_TORQUE_MODES: FrozenSet[str] = frozenset({
    "cartesian_impedance",
    "franka_cartesian_impedance",
})

# Modes that consume an F/T sensor stream as their primary input.
_FT_SENSOR_REQUIRED_MODES: FrozenSet[str] = frozenset({
    "admittance",
    "cartesian_compliance_fdcc",
})


# ---------------------------------------------------------------------------
# Predicate context — the rule evaluator's input shape
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class _ValidationContext:
    """Frozen snapshot of the inputs evaluated by every rule predicate.

    Attributes:
        mode: The compliance_mode being validated (after light
            normalisation — empty strings are passed through as ``""``
            so the empty-mode rule can fire).
        robot_class: The ``role_bindings["primary_robot"]["class"]``
            string, or the empty string when missing.  Case-sensitive.
        has_ft_sensor: True when an F/T sensor is attached to the
            embodiment.  False for both "definitely no sensor" and
            "we don't know" — rules close on the False branch.
        ft_sensor_path: Optional explicit path to the attached F/T
            sensor (e.g. ``/World/Franka/wrist_ft``).  Some rules
            consult this for richer diagnostics; absent / empty is
            treated as ``has_ft_sensor=False`` for rule purposes if
            the explicit ``has_ft_sensor`` flag was not supplied.
        structural_tags: Layer of free-form tags from the LayoutSpec
            (``real_robot_deployment`` etc).  Defensive — may be
            empty / None.
        explicit_K_schedule: True when the override carries an
            explicit ``compliance_params["K_schedule"]`` or similar
            stiffness-schedule directive.  Variable-impedance rule
            consults this to allow position-mode robots to use
            ``variable_impedance`` as long as the schedule is
            explicit.
    """

    mode: str
    robot_class: str
    has_ft_sensor: bool
    ft_sensor_path: Optional[str] = None
    structural_tags: tuple[str, ...] = ()
    explicit_K_schedule: bool = False


# ---------------------------------------------------------------------------
# Rule shape
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class _HardIncompatRule:
    """One hard-incompatibility rule.

    Attributes:
        rule_id: Dotted constraint id — emitted on the
            :class:`ConstraintViolation` so consumers can switch on it.
        mode_match: Compliance mode this rule applies to, OR the
            special sentinel ``"*"`` which matches every mode.
        predicate: Callable taking a :class:`_ValidationContext` and
            returning ``True`` when the rule FIRES (i.e. violation is
            reported).
        message: Human-readable one-line description emitted on the
            violation.
        fix_hint: Optional one-line fix suggestion shown to the user.
    """

    rule_id: str
    mode_match: str
    predicate: Callable[[_ValidationContext], bool]
    message: str
    fix_hint: Optional[str] = None


# ---------------------------------------------------------------------------
# HARD_INCOMPATIBILITIES table
#
# 20 rules covering:
#   - input shape (4):  unknown-mode, empty-robot, mode-not-str, robot-not-str
#   - embodiment   (8):  impedance-needs-torque, franka-only, UR-bans,
#                        kinova-bans, generic-position-mode bans, etc.
#   - sensor       (5):  admittance/FDCC need F/T, ft-path consistency,
#                        cartesian-impedance combined requirement, etc.
#   - real-deploy  (3):  real_robot_deployment + non-franka + impedance
#
# `null` mode is intentionally NOT in this table — it is the rigid
# passthrough and is unconditionally valid (handled in the public entry
# point before the rule loop fires).
# ---------------------------------------------------------------------------


def _is_unknown_mode(ctx: _ValidationContext) -> bool:
    """Mode is not in the closed enum AND not the unset sentinel.

    None / empty-string → handled by a SEPARATE earlier branch (those
    mean "no override supplied, use auto-pick").  This rule only fires
    when the user passed something non-empty but invalid.
    """
    return bool(ctx.mode) and ctx.mode not in COMPLIANCE_MODE_ENUM


def _empty_robot_class(ctx: _ValidationContext) -> bool:
    """Robot class is empty / whitespace — we cannot validate further.

    Skipped when mode is ``"null"`` (rigid passthrough — no robot
    needed) — but the public entry point shortcircuits that case
    anyway, so this rule rarely sees ``null``.
    """
    return not ctx.robot_class.strip()


def _impedance_needs_torque_mode(ctx: _ValidationContext) -> bool:
    """cartesian_impedance variants require a torque-mode robot."""
    return (
        ctx.mode in _IMPEDANCE_TORQUE_MODES
        and bool(ctx.robot_class)
        and ctx.robot_class not in TORQUE_MODE_ROBOTS
    )


def _franka_specific_mode_non_franka(ctx: _ValidationContext) -> bool:
    """franka_cartesian_impedance is Franka-FCI-specific."""
    return (
        ctx.mode == "franka_cartesian_impedance"
        and bool(ctx.robot_class)
        and ctx.robot_class != "franka_panda"
    )


def _ur_bans_franka_impedance(ctx: _ValidationContext) -> bool:
    """UR robots cannot use the Franka-vendor-tuned controller."""
    return (
        ctx.mode == "franka_cartesian_impedance"
        and ctx.robot_class in {"ur10e", "ur5e", "ur3e"}
    )


def _kinova_bans_franka_impedance(ctx: _ValidationContext) -> bool:
    """Kinova cannot use the Franka-vendor-tuned controller."""
    return (
        ctx.mode == "franka_cartesian_impedance"
        and ctx.robot_class == "kinova_gen3"
    )


def _impedance_on_position_only_robot(ctx: _ValidationContext) -> bool:
    """``cartesian_impedance`` on a position-only robot → suggest admittance.

    This is a more pointed variant of ``_impedance_needs_torque_mode``:
    it fires only for the named position-only family so the fix_hint
    can be specific.  The generic torque-mode rule also fires on these
    robots — both violations appear in the result, with distinct
    rule_ids — that is intentional: consumers can switch on either id.
    """
    return (
        ctx.mode == "cartesian_impedance"
        and ctx.robot_class in POSITION_MODE_ONLY_ROBOTS
    )


def _admittance_needs_ft(ctx: _ValidationContext) -> bool:
    """admittance reads from an F/T sensor; reject if none attached."""
    return ctx.mode == "admittance" and not ctx.has_ft_sensor


def _fdcc_needs_ft(ctx: _ValidationContext) -> bool:
    """cartesian_compliance_fdcc reads from an F/T sensor."""
    return (
        ctx.mode == "cartesian_compliance_fdcc"
        and not ctx.has_ft_sensor
    )


def _cartesian_impedance_combined_requirement(
    ctx: _ValidationContext,
) -> bool:
    """``cartesian_impedance`` (matthias-mayr impl) needs BOTH torque-mode
    AND an F/T sensor for the integral wrench feedback term.

    Some authors miss the F/T requirement because they assume torque
    control alone is enough.  This rule fires only when the torque
    side is satisfied but the F/T side isn't — emitted as a SOFT
    violation (advisory): the controller will still run, but the
    wrench-feedback term will be uninformative.
    """
    return (
        ctx.mode == "cartesian_impedance"
        and ctx.robot_class in TORQUE_MODE_ROBOTS
        and not ctx.has_ft_sensor
    )


def _variable_impedance_needs_torque_or_schedule(
    ctx: _ValidationContext,
) -> bool:
    """variable_impedance requires torque-mode OR an explicit K-schedule.

    The paper-derived implementation works in two modes:
    * On a torque-mode robot, the controller writes joint efforts.
    * On a position-mode robot, K is converted to a virtual stiffness
      that the *trajectory generator* respects — only valid if the
      author supplied an explicit ``K_schedule`` so the conversion is
      defined.
    """
    if ctx.mode != "variable_impedance":
        return False
    if ctx.robot_class in TORQUE_MODE_ROBOTS:
        return False
    return not ctx.explicit_K_schedule


def _real_deploy_non_franka_impedance(ctx: _ValidationContext) -> bool:
    """``real_robot_deployment`` + non-franka + any impedance mode → reject.

    No vendor-tuned real-Franka path exists for UR/Kinova — the user
    should pick admittance for real-deployment of non-Franka robots
    (per spec §4.1 last clause).
    """
    if "real_robot_deployment" not in {
        # Tolerate both bare and namespaced tag forms.
        t.partition(":")[2] if ":" in t else t
        for t in ctx.structural_tags
        if isinstance(t, str)
    }:
        return False
    if ctx.robot_class == "franka_panda":
        return False
    return ctx.mode in _IMPEDANCE_TORQUE_MODES


def _ft_sensor_path_inconsistent(ctx: _ValidationContext) -> bool:
    """``ft_sensor_path`` present but ``has_ft_sensor=False`` → inconsistent.

    A non-empty path implies the caller thinks a sensor is attached;
    surface the disagreement as a soft violation so the chat panel
    can prompt the user to reconcile.
    """
    return bool(ctx.ft_sensor_path) and not ctx.has_ft_sensor


def _robot_class_not_in_known_table(ctx: _ValidationContext) -> bool:
    """Robot class is non-empty but not in the known-robots set.

    Soft violation only — the auto-pick fall-through (admittance for
    unknown) is fine, but the override path benefits from a heads-up
    so the user can confirm.  Fires only when an override mode was
    supplied for an unknown robot.
    """
    if not ctx.robot_class.strip():
        return False
    known = TORQUE_MODE_ROBOTS | POSITION_MODE_ONLY_ROBOTS
    return ctx.robot_class not in known


def _torque_mode_for_position_robot_explicit(
    ctx: _ValidationContext,
) -> bool:
    """Explicit ``cartesian_impedance`` on UR or Kinova — duplicate of
    ``_impedance_on_position_only_robot`` but with a different rule_id so
    the chat panel can distinguish "you picked impedance" (this rule)
    from "your robot is position-mode" (the generic rule).
    """
    return (
        ctx.mode == "cartesian_impedance"
        and ctx.robot_class in POSITION_MODE_ONLY_ROBOTS
    )


def _admittance_requires_external_ft_for_position_robot(
    ctx: _ValidationContext,
) -> bool:
    """Position-mode robots NEED an externally-mounted F/T sensor for
    admittance.  Fires when the user picked admittance on a known
    position-mode robot but ``has_ft_sensor=False``.

    Different rule_id than the generic ``_admittance_needs_ft`` so the
    fix_hint can specifically name the external-mount requirement.
    """
    return (
        ctx.mode == "admittance"
        and ctx.robot_class in POSITION_MODE_ONLY_ROBOTS
        and not ctx.has_ft_sensor
    )


def _admittance_torque_mode_redundant(ctx: _ValidationContext) -> bool:
    """``admittance`` + torque-mode robot — soft advisory.

    Not strictly incompatible — admittance works on torque-mode robots
    too — but ``cartesian_impedance`` is usually a better fit (lower
    latency, lower comput cost).  Soft violation only.
    """
    return (
        ctx.mode == "admittance"
        and ctx.robot_class in TORQUE_MODE_ROBOTS
        and ctx.has_ft_sensor
    )


def _fdcc_torque_mode_redundant(ctx: _ValidationContext) -> bool:
    """``cartesian_compliance_fdcc`` + torque-mode — soft advisory.

    FDCC is position-mode-targeted; on torque-mode hardware,
    ``cartesian_impedance`` skips an unnecessary conversion step.
    Soft violation only.
    """
    return (
        ctx.mode == "cartesian_compliance_fdcc"
        and ctx.robot_class in TORQUE_MODE_ROBOTS
        and ctx.has_ft_sensor
    )


def _real_deploy_admittance_no_ft(ctx: _ValidationContext) -> bool:
    """``real_robot_deployment`` + admittance + no F/T → hard reject.

    Sim runs can sometimes fake admittance with simulated wrench
    estimation, but real-robot deployment without a physical sensor
    cannot.  Hard reject — admittance on the real robot will not
    function safely.
    """
    has_real_tag = "real_robot_deployment" in {
        t.partition(":")[2] if ":" in t else t
        for t in ctx.structural_tags
        if isinstance(t, str)
    }
    return (
        has_real_tag
        and ctx.mode == "admittance"
        and not ctx.has_ft_sensor
    )


HARD_INCOMPATIBILITIES: tuple[_HardIncompatRule, ...] = (
    # ---- Input-shape rules (4) -------------------------------------------
    _HardIncompatRule(
        rule_id="compliance.unknown_mode",
        mode_match="*",
        predicate=_is_unknown_mode,
        message=(
            "unknown compliance_mode; valid: "
            f"{sorted(COMPLIANCE_MODE_ENUM)!r}"
        ),
        fix_hint=(
            "use one of the 6 enum members or omit the field to let "
            "auto-pick choose"
        ),
    ),
    _HardIncompatRule(
        rule_id="compliance.empty_robot_class",
        mode_match="*",
        predicate=_empty_robot_class,
        message=(
            "robot_class is empty; cannot validate compliance override "
            "without knowing the embodiment"
        ),
        fix_hint=(
            "bind a primary_robot before setting compliance_mode, or "
            "leave compliance_mode unset for auto-pick"
        ),
    ),
    # ---- Embodiment rules (8) --------------------------------------------
    _HardIncompatRule(
        rule_id="compliance.impedance_needs_torque_mode",
        mode_match="cartesian_impedance",
        predicate=_impedance_needs_torque_mode,
        message=(
            "cartesian_impedance requires a torque-mode robot "
            "(e.g. Franka FCI)"
        ),
        fix_hint="switch to admittance for position-mode robots",
    ),
    _HardIncompatRule(
        rule_id="compliance.franka_impedance_needs_torque_mode",
        mode_match="franka_cartesian_impedance",
        predicate=_impedance_needs_torque_mode,
        message=(
            "franka_cartesian_impedance requires a torque-mode robot "
            "(Franka FCI)"
        ),
        fix_hint="switch to admittance for non-Franka robots",
    ),
    _HardIncompatRule(
        rule_id="compliance.franka_specific_mode_non_franka",
        mode_match="franka_cartesian_impedance",
        predicate=_franka_specific_mode_non_franka,
        message=(
            "franka_cartesian_impedance is Franka-FCI-specific; "
            "robot_class must be franka_panda"
        ),
        fix_hint="use admittance instead, or change robot to franka_panda",
    ),
    _HardIncompatRule(
        rule_id="compliance.ur_bans_franka_impedance",
        mode_match="franka_cartesian_impedance",
        predicate=_ur_bans_franka_impedance,
        message=(
            "UR robots cannot use franka_cartesian_impedance — "
            "Franka-vendor-only"
        ),
        fix_hint="use admittance for UR robots",
    ),
    _HardIncompatRule(
        rule_id="compliance.kinova_bans_franka_impedance",
        mode_match="franka_cartesian_impedance",
        predicate=_kinova_bans_franka_impedance,
        message=(
            "Kinova cannot use franka_cartesian_impedance — "
            "Franka-vendor-only"
        ),
        fix_hint="use admittance for Kinova",
    ),
    _HardIncompatRule(
        rule_id="compliance.impedance_on_position_only_robot",
        mode_match="cartesian_impedance",
        predicate=_impedance_on_position_only_robot,
        message=(
            "cartesian_impedance not supported on position-only robot; "
            "no joint-effort interface"
        ),
        fix_hint="use admittance for this robot",
    ),
    _HardIncompatRule(
        rule_id="compliance.torque_mode_for_position_robot_explicit",
        mode_match="cartesian_impedance",
        predicate=_torque_mode_for_position_robot_explicit,
        message=(
            "explicit cartesian_impedance override rejected for "
            "position-mode-only robot"
        ),
        fix_hint=(
            "remove the override and let auto-pick select admittance "
            "for this robot"
        ),
    ),
    _HardIncompatRule(
        rule_id="compliance.variable_impedance_needs_torque_or_schedule",
        mode_match="variable_impedance",
        predicate=_variable_impedance_needs_torque_or_schedule,
        message=(
            "variable_impedance on a position-mode robot requires an "
            "explicit K_schedule in compliance_params"
        ),
        fix_hint=(
            "add compliance_params.K_schedule, OR switch to a torque-"
            "mode robot, OR use admittance"
        ),
    ),
    # ---- Sensor rules (5) ------------------------------------------------
    _HardIncompatRule(
        rule_id="compliance.admittance_needs_ft",
        mode_match="admittance",
        predicate=_admittance_needs_ft,
        message=(
            "admittance requires an F/T sensor; attach one via "
            "attach_ft_sensor before applying compliance"
        ),
        fix_hint="call attach_ft_sensor on the robot's wrist link",
    ),
    _HardIncompatRule(
        rule_id="compliance.fdcc_needs_ft",
        mode_match="cartesian_compliance_fdcc",
        predicate=_fdcc_needs_ft,
        message=(
            "cartesian_compliance_fdcc requires an F/T sensor for "
            "the wrench-tracking term"
        ),
        fix_hint="call attach_ft_sensor on the robot's wrist link",
    ),
    _HardIncompatRule(
        rule_id="compliance.admittance_requires_external_ft_for_position_robot",
        mode_match="admittance",
        predicate=_admittance_requires_external_ft_for_position_robot,
        message=(
            "admittance on a position-mode robot needs an externally-"
            "mounted F/T sensor"
        ),
        fix_hint=(
            "mount a wrist F/T sensor (e.g. Robotiq FT-300, ATI Mini45) "
            "and attach via attach_ft_sensor"
        ),
    ),
    _HardIncompatRule(
        rule_id="compliance.cartesian_impedance_combined_requirement",
        mode_match="cartesian_impedance",
        predicate=_cartesian_impedance_combined_requirement,
        message=(
            "cartesian_impedance benefits from an F/T sensor for the "
            "wrench-feedback term; without one the integral channel "
            "is uninformative"
        ),
        fix_hint=(
            "add an F/T sensor for full impedance benefit, or accept "
            "the joint-effort-only mode"
        ),
    ),
    _HardIncompatRule(
        rule_id="compliance.ft_sensor_path_inconsistent",
        mode_match="*",
        predicate=_ft_sensor_path_inconsistent,
        message=(
            "ft_sensor_path is set but has_ft_sensor=False — "
            "inconsistent"
        ),
        fix_hint=(
            "set has_ft_sensor=True, or clear ft_sensor_path"
        ),
    ),
    # ---- Real-deployment rules (3) ---------------------------------------
    _HardIncompatRule(
        rule_id="compliance.real_deploy_non_franka_impedance",
        mode_match="*",
        predicate=_real_deploy_non_franka_impedance,
        message=(
            "real_robot_deployment + non-Franka + impedance mode → "
            "no vendor-tuned path; use admittance for real-robot "
            "deployment on non-Franka embodiments"
        ),
        fix_hint=(
            "switch compliance_mode to admittance for real deployment "
            "on this robot"
        ),
    ),
    _HardIncompatRule(
        rule_id="compliance.real_deploy_admittance_no_ft",
        mode_match="admittance",
        predicate=_real_deploy_admittance_no_ft,
        message=(
            "real_robot_deployment + admittance + no F/T sensor → "
            "sim-only path; real deployment will not safely produce "
            "compliant motion without a physical sensor"
        ),
        fix_hint=(
            "attach a wrist F/T sensor before real-robot deployment"
        ),
    ),
    # ---- Soft-advisory rules (3) -----------------------------------------
    _HardIncompatRule(
        rule_id="compliance.robot_class_not_in_known_table",
        mode_match="*",
        predicate=_robot_class_not_in_known_table,
        message=(
            "robot_class is not in the known torque-mode / position-"
            "mode tables; override may not be valid"
        ),
        fix_hint=(
            "verify the robot class string matches "
            "role_template_index.py; common typos: 'ur10' vs 'ur10e'"
        ),
    ),
    _HardIncompatRule(
        rule_id="compliance.admittance_torque_mode_redundant",
        mode_match="admittance",
        predicate=_admittance_torque_mode_redundant,
        message=(
            "admittance on a torque-mode robot works but "
            "cartesian_impedance has lower latency"
        ),
        fix_hint=(
            "consider cartesian_impedance for torque-mode embodiments"
        ),
    ),
    _HardIncompatRule(
        rule_id="compliance.fdcc_torque_mode_redundant",
        mode_match="cartesian_compliance_fdcc",
        predicate=_fdcc_torque_mode_redundant,
        message=(
            "cartesian_compliance_fdcc on a torque-mode robot adds a "
            "conversion step; cartesian_impedance is more direct"
        ),
        fix_hint=(
            "consider cartesian_impedance for torque-mode embodiments"
        ),
    ),
)


# ---------------------------------------------------------------------------
# Rule classification — which rules are HARD vs SOFT.
#
# Most embodiment / sensor-required rules are HARD (they block the
# operation).  A few advisory rules (the *_redundant rules,
# ft_sensor_path inconsistency, unknown robot class) are SOFT — they
# surface as warnings without flipping ``valid``.
#
# Decisions per spec §4.2:
#   "~20 hard-incompat rules" + the framework's separate soft severity.
# The `_SOFT_RULE_IDS` set intentionally contains only the explicit
# advisories.  Everything else is HARD by default.
# ---------------------------------------------------------------------------

_SOFT_RULE_IDS: FrozenSet[str] = frozenset({
    "compliance.cartesian_impedance_combined_requirement",
    "compliance.ft_sensor_path_inconsistent",
    "compliance.robot_class_not_in_known_table",
    "compliance.admittance_torque_mode_redundant",
    "compliance.fdcc_torque_mode_redundant",
})

# Severity assignments — most violations are ERROR; advisory ones are
# WARNING; an internal/unrecoverable violation would be CRITICAL but no
# rule currently emits that.
_SOFT_SEVERITY: GradedScale = GradedScale.WARNING
_HARD_SEVERITY: GradedScale = GradedScale.ERROR


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def _normalise_structural_tags(tags: Any) -> tuple[str, ...]:
    """Coerce ``tags`` into a tuple of strings.  Tolerates None / non-iterable."""
    if tags is None:
        return ()
    try:
        return tuple(str(t) for t in tags if isinstance(t, str))
    except TypeError:
        # Non-iterable input — return empty tuple.
        return ()


def validate_compliance_override(
    mode: Optional[str],
    robot_class: str,
    has_ft_sensor: bool = False,
    *,
    ft_sensor_path: Optional[str] = None,
    structural_tags: Optional[list[str]] = None,
    explicit_K_schedule: bool = False,
    **_extra: Any,
) -> ValidationResult:
    """Validate an explicit ``compliance_mode`` override.

    Per spec §4.2, an override is supplied either by a template author
    (``compliance_mode`` field on a CP-NEW template) or by an end-user
    flow that bypasses auto-pick.  This function checks the override
    against ~20 hard-incompatibility rules drawn from the impedance /
    admittance / FDCC / torque-mode interaction matrix.

    Args:
        mode: The compliance_mode override string.  ``None`` or
            empty-string is treated as "no override supplied" — the
            function returns an empty (valid) result without firing
            any rules.  ``"null"`` is the explicit rigid-passthrough
            mode and is unconditionally valid (handled before the rule
            loop).  Any other value is checked against the closed
            enum.
        robot_class: The primary-robot class string (e.g.
            ``"franka_panda"``, ``"ur10e"``).  Empty / whitespace
            triggers the ``empty_robot_class`` rule unless the mode is
            ``None`` / ``""`` / ``"null"`` (none of which need a robot).
        has_ft_sensor: True when an F/T sensor is attached to the
            embodiment.  Default False — most callers will know.
        ft_sensor_path: Optional explicit path to the attached F/T
            sensor.  Inconsistency (path set but ``has_ft_sensor=False``)
            surfaces as a soft violation.
        structural_tags: Optional list of structural tags from the
            LayoutSpec.  Defensively accepts None.  The
            ``real_robot_deployment`` tag (bare or namespaced
            ``user:`` / ``isaac:`` form) gates the real-deployment
            rules.
        explicit_K_schedule: True when the override carries an
            explicit K_schedule in compliance_params.  Variable-
            impedance rule consults this.
        **_extra: Forward-compatible keyword sink — additional ctx
            fields can be added without breaking existing callers.

    Returns:
        A :class:`ValidationResult` with zero or more violations.  The
        result is ``valid=True`` iff no HARD violations fired (soft
        advisories never flip the verdict, per Phase 11b contract).

    Examples:
        >>> r = validate_compliance_override("admittance", "ur10e",
        ...                                  has_ft_sensor=True)
        >>> r.valid
        True

        >>> r = validate_compliance_override("cartesian_impedance",
        ...                                  "ur10e", has_ft_sensor=True)
        >>> r.valid
        False
        >>> r.n_hard > 0
        True

        >>> r = validate_compliance_override(None, "ur10e")
        >>> r.valid
        True
        >>> len(r.violations)
        0
    """
    # ---- Fast paths -------------------------------------------------------
    # mode is None or empty-string → no override supplied; auto-pick will
    # handle the choice.  Return an empty valid result.
    if mode is None or (isinstance(mode, str) and not mode.strip()):
        return ValidationResult.from_violations([])

    # Validate type defensively — non-string mode is invalid input.
    if not isinstance(mode, str):
        return ValidationResult.from_violations([
            ConstraintViolation(
                constraint_id="compliance.mode_not_string",
                category="hard",
                severity=_HARD_SEVERITY,
                message=(
                    f"compliance_mode must be a string or None, got "
                    f"{type(mode).__name__}"
                ),
                fix_hint="pass a string from COMPLIANCE_MODE_ENUM, or None",
            )
        ])

    # robot_class type check — empty STRING is handled by the rule loop,
    # but non-string types fail loudly here.
    if not isinstance(robot_class, str):
        return ValidationResult.from_violations([
            ConstraintViolation(
                constraint_id="compliance.robot_class_not_string",
                category="hard",
                severity=_HARD_SEVERITY,
                message=(
                    f"robot_class must be a string, got "
                    f"{type(robot_class).__name__}"
                ),
                fix_hint="pass the canonical class string (e.g. 'ur10e')",
            )
        ])

    # `null` mode is unconditionally valid — rigid passthrough.  No
    # robot / F/T requirements.
    if mode == "null":
        return ValidationResult.from_violations([])

    # ---- Build ctx -------------------------------------------------------
    ctx = _ValidationContext(
        mode=mode,
        robot_class=robot_class,
        has_ft_sensor=bool(has_ft_sensor),
        ft_sensor_path=ft_sensor_path or None,
        structural_tags=_normalise_structural_tags(structural_tags),
        explicit_K_schedule=bool(explicit_K_schedule),
    )

    # ---- Run rules -------------------------------------------------------
    violations: list[ConstraintViolation] = []
    for rule in HARD_INCOMPATIBILITIES:
        # Filter by mode_match — `"*"` matches every mode.
        if rule.mode_match != "*" and rule.mode_match != ctx.mode:
            continue
        try:
            fires = rule.predicate(ctx)
        except (AttributeError, KeyError, TypeError, ValueError) as exc:
            # Predicate raised on a degenerate ctx shape — emit a soft
            # diagnostic so the rule failure is visible but the
            # validator still produces a result.  Never swallow silently.
            violations.append(
                ConstraintViolation(
                    constraint_id=f"{rule.rule_id}.predicate_error",
                    category="soft",
                    severity=GradedScale.NOTICE,
                    message=(
                        f"rule {rule.rule_id} predicate raised "
                        f"{type(exc).__name__}: {exc}"
                    ),
                    diagnostics={"rule_id": rule.rule_id},
                )
            )
            continue

        if not fires:
            continue

        category = (
            "soft" if rule.rule_id in _SOFT_RULE_IDS else "hard"
        )
        severity = (
            _SOFT_SEVERITY if category == "soft" else _HARD_SEVERITY
        )
        violations.append(
            ConstraintViolation(
                constraint_id=rule.rule_id,
                category=category,
                severity=severity,
                message=rule.message,
                diagnostics={
                    "mode": ctx.mode,
                    "robot_class": ctx.robot_class,
                    "has_ft_sensor": ctx.has_ft_sensor,
                },
                fix_hint=rule.fix_hint,
            )
        )

    return ValidationResult.from_violations(violations)


# ---------------------------------------------------------------------------
# Introspection helpers (for tests + docs)
# ---------------------------------------------------------------------------

def list_rule_ids() -> tuple[str, ...]:
    """Return the rule_ids in the order they appear in the table."""
    return tuple(r.rule_id for r in HARD_INCOMPATIBILITIES)


def count_rules() -> int:
    """Return the number of incompatibility rules currently registered."""
    return len(HARD_INCOMPATIBILITIES)


def rules_by_mode(mode: str) -> tuple[_HardIncompatRule, ...]:
    """Return the rules that apply to a given mode (including ``"*"`` rules)."""
    return tuple(
        r
        for r in HARD_INCOMPATIBILITIES
        if r.mode_match in ("*", mode)
    )


__all__ = [
    "HARD_INCOMPATIBILITIES",
    "TORQUE_MODE_ROBOTS",
    "POSITION_MODE_ONLY_ROBOTS",
    "count_rules",
    "list_rule_ids",
    "rules_by_mode",
    "validate_compliance_override",
]
