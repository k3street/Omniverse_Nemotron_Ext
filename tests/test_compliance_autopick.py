"""L0 tests for CRM-C2 — ``autopick_compliance_mode`` per spec §4.1.

Spec: docs/specs/2026-05-11-contact-rich-manipulation-spec.md §4.1.

Coverage requirement (per spec Section 18.3 CRM-C2 Verify row):
``pytest tests/test_compliance_autopick.py ≥ 12 tests covering all 6
modes + None case``.

The 6 spec-listed modes in ``COMPLIANCE_MODE_ENUM`` are:
``admittance``, ``cartesian_compliance_fdcc``, ``cartesian_impedance``,
``variable_impedance``, ``franka_cartesian_impedance``, ``null``.

CRM-C2 auto-pick returns a STRICT subset of those — only
``admittance`` and ``franka_cartesian_impedance`` are produced by the
auto-pick algorithm itself (plus Python ``None``).  The remaining four
modes (``cartesian_compliance_fdcc``, ``cartesian_impedance``,
``variable_impedance``, ``"null"``) are reachable only via explicit
template-author override — they are validated as legal enum members
here for completeness, and ``test_explicit_override_modes_are_valid``
asserts that they pass the enum check independent of auto-pick.
"""
from __future__ import annotations

from typing import Any, Dict

import pytest

from service.isaac_assist_service.chat.tools.role_retriever import (
    _COMPLIANCE_TABLE,
    _UNKNOWN_ROBOT_MODE,
    autopick_compliance_mode,
)
from service.isaac_assist_service.multimodal.types import (
    COMPLIANCE_MODE_ENUM,
    Counts,
    Intent,
    LayoutSpec,
    Source,
    StructuralFeatures,
)


pytestmark = pytest.mark.l0


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


class _Features:
    """Lightweight stand-in for StructuralFeatures.

    The CRM-C1 schema does NOT yet add ``has_contact_phase`` to the
    Pydantic StructuralFeatures model; CRM-C2 reads it defensively via
    ``getattr`` so it doesn't need the schema field to exist.  Tests
    therefore use this duck-typed stand-in to inject the attribute.
    """

    def __init__(self, **flags: Any) -> None:
        for k, v in flags.items():
            setattr(self, k, v)


class _Intent:
    """Lightweight stand-in for Intent allowing arbitrary structural_tags."""

    def __init__(
        self,
        structural_features: Any = None,
        structural_tags: Any = None,
    ) -> None:
        self.structural_features = structural_features
        self.structural_tags = structural_tags if structural_tags is not None else []


class _Spec:
    """Lightweight stand-in for LayoutSpec.  Only ``intent`` is needed
    by autopick_compliance_mode."""

    def __init__(self, intent: Any = None) -> None:
        self.intent = intent


def _bindings(robot_class: str | None = None, **extra: Any) -> Dict[str, Any]:
    """Build a role_bindings dict shaped like
    ``{"primary_robot": {"class": "<robot_class>", ...}}``.
    """
    out: Dict[str, Any] = {}
    if robot_class is not None:
        out["primary_robot"] = {"class": robot_class}
    out.update(extra)
    return out


def _spec_with_contact(
    has_contact: bool = True,
    structural_tags: list[str] | None = None,
) -> _Spec:
    """Convenience: build a stand-in spec with the contact flag set."""
    return _Spec(
        intent=_Intent(
            structural_features=_Features(has_contact_phase=has_contact),
            structural_tags=structural_tags or [],
        )
    )


# ===========================================================================
# 1. None-case: no contact phase => rigid baseline => return None
# ===========================================================================


class TestNoContactPhase:
    def test_no_contact_phase_returns_none(self):
        spec = _spec_with_contact(has_contact=False)
        bindings = _bindings("franka_panda")
        assert autopick_compliance_mode(spec, bindings) is None

    def test_missing_has_contact_phase_attr_returns_none(self):
        """Spec field absent → defensive read treats as False."""
        # _Features WITHOUT has_contact_phase attribute
        spec = _Spec(intent=_Intent(structural_features=_Features()))
        bindings = _bindings("franka_panda")
        assert autopick_compliance_mode(spec, bindings) is None

    def test_missing_structural_features_returns_none(self):
        spec = _Spec(intent=_Intent(structural_features=None))
        bindings = _bindings("franka_panda")
        assert autopick_compliance_mode(spec, bindings) is None

    def test_missing_intent_returns_none(self):
        spec = _Spec(intent=None)
        bindings = _bindings("franka_panda")
        assert autopick_compliance_mode(spec, bindings) is None

    def test_empty_layout_spec_returns_none(self):
        """Object with no ``intent`` attribute at all."""
        # An arbitrary object with no intent attribute
        bindings = _bindings("franka_panda")
        assert autopick_compliance_mode(object(), bindings) is None


# ===========================================================================
# 2. Franka-specific branches
# ===========================================================================


class TestFrankaBranch:
    def test_franka_sim_default_is_admittance(self):
        """Spec §4.1: franka + no real-robot tag → admittance."""
        spec = _spec_with_contact(has_contact=True, structural_tags=[])
        bindings = _bindings("franka_panda")
        assert autopick_compliance_mode(spec, bindings) == "admittance"

    def test_franka_real_robot_tag_picks_cartesian_impedance(self):
        """Spec §4.1: franka + real_robot_deployment → franka_cartesian_impedance."""
        spec = _spec_with_contact(
            has_contact=True,
            structural_tags=["real_robot_deployment"],
        )
        bindings = _bindings("franka_panda")
        assert (
            autopick_compliance_mode(spec, bindings)
            == "franka_cartesian_impedance"
        )

    def test_franka_real_robot_tag_namespaced_user_form(self):
        """Tag in namespaced ``user:`` form should also match."""
        spec = _spec_with_contact(
            has_contact=True,
            structural_tags=["user:real_robot_deployment"],
        )
        bindings = _bindings("franka_panda")
        assert (
            autopick_compliance_mode(spec, bindings)
            == "franka_cartesian_impedance"
        )

    def test_franka_real_robot_tag_namespaced_isaac_form(self):
        """Tag in ``isaac:`` namespace also matches."""
        spec = _spec_with_contact(
            has_contact=True,
            structural_tags=["isaac:real_robot_deployment"],
        )
        bindings = _bindings("franka_panda")
        assert (
            autopick_compliance_mode(spec, bindings)
            == "franka_cartesian_impedance"
        )


# ===========================================================================
# 3. UR robot family — all map to admittance
# ===========================================================================


class TestURBranch:
    @pytest.mark.parametrize("robot_class", ["ur10e", "ur5e", "ur3e"])
    def test_ur_family_picks_admittance(self, robot_class: str):
        spec = _spec_with_contact(has_contact=True)
        bindings = _bindings(robot_class)
        assert autopick_compliance_mode(spec, bindings) == "admittance"

    @pytest.mark.parametrize("robot_class", ["ur10e", "ur5e", "ur3e"])
    def test_ur_family_ignores_real_robot_tag(self, robot_class: str):
        """UR robots have no vendor-tuned mode — tag is irrelevant."""
        spec = _spec_with_contact(
            has_contact=True,
            structural_tags=["real_robot_deployment"],
        )
        bindings = _bindings(robot_class)
        assert autopick_compliance_mode(spec, bindings) == "admittance"


# ===========================================================================
# 4. Kinova Gen3 — admittance
# ===========================================================================


class TestKinovaBranch:
    def test_kinova_gen3_picks_admittance(self):
        spec = _spec_with_contact(has_contact=True)
        bindings = _bindings("kinova_gen3")
        assert autopick_compliance_mode(spec, bindings) == "admittance"

    def test_kinova_gen3_ignores_real_robot_tag(self):
        spec = _spec_with_contact(
            has_contact=True,
            structural_tags=["real_robot_deployment"],
        )
        bindings = _bindings("kinova_gen3")
        assert autopick_compliance_mode(spec, bindings) == "admittance"


# ===========================================================================
# 5. Unknown / missing robot fallback
# ===========================================================================


class TestUnknownRobotFallback:
    def test_unknown_robot_class_picks_admittance(self):
        """Spec §4.1: unknown robot → safe default."""
        spec = _spec_with_contact(has_contact=True)
        bindings = _bindings("yaskawa_gp25_does_not_exist_yet")
        assert autopick_compliance_mode(spec, bindings) == "admittance"
        assert _UNKNOWN_ROBOT_MODE == "admittance"

    def test_unknown_robot_ignores_real_robot_tag(self):
        """Unknown robot has no vendor-tuned variant — tag ignored."""
        spec = _spec_with_contact(
            has_contact=True,
            structural_tags=["real_robot_deployment"],
        )
        bindings = _bindings("some_future_humanoid")
        # Unknown-robot branch returns admittance regardless of tag.
        assert autopick_compliance_mode(spec, bindings) == "admittance"

    def test_no_primary_robot_picks_admittance(self):
        """Missing primary_robot binding → safe default."""
        spec = _spec_with_contact(has_contact=True)
        bindings: Dict[str, Any] = {}  # no primary_robot
        assert autopick_compliance_mode(spec, bindings) == "admittance"

    def test_none_role_bindings_with_contact_picks_admittance(self):
        spec = _spec_with_contact(has_contact=True)
        assert autopick_compliance_mode(spec, None) == "admittance"

    def test_role_bindings_not_a_mapping_picks_admittance(self):
        """Non-mapping role_bindings → degenerate input → safe default."""
        spec = _spec_with_contact(has_contact=True)
        # Pass a list — defensively treated as "no primary_robot".
        assert autopick_compliance_mode(spec, ["not a dict"]) == "admittance"

    def test_primary_robot_missing_class_field_picks_admittance(self):
        """primary_robot dict without ``class`` → unknown branch."""
        spec = _spec_with_contact(has_contact=True)
        bindings = {"primary_robot": {"path": "/World/X"}}  # no class
        assert autopick_compliance_mode(spec, bindings) == "admittance"

    def test_primary_robot_class_not_string_picks_admittance(self):
        """class field present but non-string → unknown branch."""
        spec = _spec_with_contact(has_contact=True)
        bindings = {"primary_robot": {"class": 12345}}
        assert autopick_compliance_mode(spec, bindings) == "admittance"


# ===========================================================================
# 6. Real-robot tag matcher — bare + namespaced + non-matching
# ===========================================================================


class TestRealRobotTagMatching:
    def test_unrelated_tags_do_not_trigger_real_deployment(self):
        spec = _spec_with_contact(
            has_contact=True,
            structural_tags=[
                "isaac:transport.conveyor",
                "user:annotation.priority_first",
            ],
        )
        bindings = _bindings("franka_panda")
        # No real-robot tag → sim default (admittance).
        assert autopick_compliance_mode(spec, bindings) == "admittance"

    def test_partial_match_does_not_trigger(self):
        """Substring matches must NOT count — only exact tag body."""
        spec = _spec_with_contact(
            has_contact=True,
            # 'real_robot_deployment_v2' is NOT 'real_robot_deployment'
            structural_tags=["user:real_robot_deployment_v2"],
        )
        bindings = _bindings("franka_panda")
        assert autopick_compliance_mode(spec, bindings) == "admittance"

    def test_empty_structural_tags_returns_sim_default(self):
        spec = _spec_with_contact(has_contact=True, structural_tags=[])
        bindings = _bindings("franka_panda")
        assert autopick_compliance_mode(spec, bindings) == "admittance"

    def test_none_structural_tags_returns_sim_default(self):
        """``structural_tags`` attribute missing entirely."""
        intent = _Intent(structural_features=_Features(has_contact_phase=True))
        intent.structural_tags = None  # explicitly set to None
        spec = _Spec(intent=intent)
        bindings = _bindings("franka_panda")
        assert autopick_compliance_mode(spec, bindings) == "admittance"


# ===========================================================================
# 7. Edge cases — real_robot_deployment tag on non-franka robots
# ===========================================================================


class TestRealDeploymentNonFranka:
    @pytest.mark.parametrize("robot_class", ["ur10e", "kinova_gen3"])
    def test_real_robot_tag_with_non_franka_stays_admittance(
        self, robot_class: str
    ):
        """Spec §4.1: only Franka has a vendor-tuned real-robot mode.

        Tag on UR/Kinova MUST NOT escalate to franka_cartesian_impedance
        — the rule's mode_for_real == mode_for_sim for these rows.
        """
        spec = _spec_with_contact(
            has_contact=True,
            structural_tags=["real_robot_deployment"],
        )
        bindings = _bindings(robot_class)
        result = autopick_compliance_mode(spec, bindings)
        assert result == "admittance"
        assert result != "franka_cartesian_impedance"


# ===========================================================================
# 8. Returned values are always valid enum members or None
# ===========================================================================


class TestReturnedValueValidity:
    @pytest.mark.parametrize(
        ("robot_class", "tags", "expected"),
        [
            ("franka_panda", [], "admittance"),
            ("franka_panda", ["real_robot_deployment"], "franka_cartesian_impedance"),
            ("ur10e", [], "admittance"),
            ("ur5e", ["real_robot_deployment"], "admittance"),
            ("ur3e", [], "admittance"),
            ("kinova_gen3", [], "admittance"),
            ("totally_unknown", [], "admittance"),
        ],
    )
    def test_returned_mode_is_in_enum(
        self,
        robot_class: str,
        tags: list[str],
        expected: str,
    ):
        spec = _spec_with_contact(has_contact=True, structural_tags=tags)
        bindings = _bindings(robot_class)
        mode = autopick_compliance_mode(spec, bindings)
        assert mode == expected
        # And the mode must be in the closed enum.
        assert mode in COMPLIANCE_MODE_ENUM

    def test_explicit_override_modes_are_valid_enum_members(self):
        """The 4 spec modes that auto-pick never returns must still be
        legal enum members (reachable via override).  This asserts spec
        §3 ↔ §4.1 alignment so the 6-mode coverage requirement holds.
        """
        override_only_modes = {
            "cartesian_compliance_fdcc",
            "cartesian_impedance",
            "variable_impedance",
            "null",
        }
        # Every override-only mode is a legal enum member.
        assert override_only_modes <= COMPLIANCE_MODE_ENUM
        # And the auto-pick-returnable subset is also in the enum.
        autopick_modes = {"admittance", "franka_cartesian_impedance"}
        assert autopick_modes <= COMPLIANCE_MODE_ENUM
        # Together they cover ≥ 6 enum members (spec calls for "all 6").
        # The full enum has exactly 6 members.
        assert len(COMPLIANCE_MODE_ENUM) == 6
        assert override_only_modes | autopick_modes == COMPLIANCE_MODE_ENUM


# ===========================================================================
# 9. Round-trip with real Pydantic LayoutSpec
# ===========================================================================


class TestRealLayoutSpecRoundTrip:
    """Use the real Pydantic models to confirm the function works against
    the live schema, not just the duck-typed stand-ins."""

    def _real_spec(self) -> LayoutSpec:
        intent = Intent(
            pattern_hint="pick_place",
            counts=Counts(robots=1),
            structural_features=StructuralFeatures(),
            structural_tags=[],
        )
        return LayoutSpec(
            intent=intent,
            source=Source(modality="text", confidence=1.0),
        )

    def test_real_spec_no_contact_returns_none(self):
        spec = self._real_spec()
        # Real Pydantic StructuralFeatures has NO has_contact_phase
        # attribute, so defensive read treats it as False.
        bindings = _bindings("franka_panda")
        assert autopick_compliance_mode(spec, bindings) is None

    def test_real_spec_default_no_robot_returns_none(self):
        spec = self._real_spec()
        # No contact phase + no bindings → None.
        assert autopick_compliance_mode(spec, None) is None


# ===========================================================================
# 10. Auto-pick table extensibility
# ===========================================================================


class TestTableExtensibility:
    def test_table_has_minimum_three_robot_families(self):
        """The auto-pick table covers Franka + UR + Kinova at minimum."""
        all_classes = {
            cls
            for rule in _COMPLIANCE_TABLE
            for cls in rule.robot_classes
        }
        assert "franka_panda" in all_classes
        assert "ur10e" in all_classes
        assert "kinova_gen3" in all_classes

    def test_franka_rule_has_distinct_real_mode(self):
        """Franka MUST be the only row where sim ≠ real mode."""
        franka_rules = [
            r for r in _COMPLIANCE_TABLE if "franka_panda" in r.robot_classes
        ]
        assert len(franka_rules) == 1
        franka = franka_rules[0]
        assert franka.mode_for_sim != franka.mode_for_real
        assert franka.mode_for_real == "franka_cartesian_impedance"

    def test_non_franka_rules_have_matching_sim_and_real(self):
        """All non-Franka rows: sim == real (no vendor-tuned variant)."""
        for rule in _COMPLIANCE_TABLE:
            if "franka_panda" in rule.robot_classes:
                continue
            assert rule.mode_for_sim == rule.mode_for_real, (
                f"Non-Franka rule {rule.robot_classes!r} should have "
                f"matching sim and real modes; got "
                f"sim={rule.mode_for_sim!r} real={rule.mode_for_real!r}"
            )

    def test_all_table_modes_are_enum_members(self):
        """Every mode in the table is a legal compliance_mode value."""
        for rule in _COMPLIANCE_TABLE:
            assert rule.mode_for_sim in COMPLIANCE_MODE_ENUM
            assert rule.mode_for_real in COMPLIANCE_MODE_ENUM


# ===========================================================================
# CRM-C3 — validate_compliance_override
#
# Spec: docs/specs/2026-05-11-contact-rich-manipulation-spec.md §4.2.
#
# Coverage (per spec Section 18.3 CRM-C3 Verify row):
# - ≥ 8 tests in TestOverride
# - ≥ 6 distinct rule classes hit
# - integration via Phase 11b ValidationResult round-trip
# ===========================================================================


from service.isaac_assist_service.chat.tools.compliance_validator import (  # noqa: E402
    HARD_INCOMPATIBILITIES,
    POSITION_MODE_ONLY_ROBOTS,
    TORQUE_MODE_ROBOTS,
    count_rules,
    list_rule_ids,
    rules_by_mode,
    validate_compliance_override,
)
from service.isaac_assist_service.types.violations import (  # noqa: E402
    ConstraintViolation,
    ValidationResult,
)


class TestOverride:
    """L0 tests for ``validate_compliance_override`` per spec §4.2.

    Each test names the rule class it exercises so the
    ``≥ 6 distinct rule classes`` requirement is auditable from the
    rule_id field on the produced violations.
    """

    # ------------------------------------------------------------------
    # 1. Happy path — known-valid combinations should produce zero
    #    HARD violations.
    # ------------------------------------------------------------------

    def test_valid_override_admittance_ur10e_with_ft(self):
        """admittance + ur10e + F/T sensor → valid (no violations)."""
        r = validate_compliance_override(
            "admittance", "ur10e", has_ft_sensor=True
        )
        assert isinstance(r, ValidationResult)
        assert r.valid is True
        assert r.n_hard == 0
        # May produce zero soft violations on this clean combo.
        assert r.n_soft == 0

    def test_valid_override_franka_cartesian_impedance_on_franka(self):
        """franka_cartesian_impedance + franka_panda → valid."""
        r = validate_compliance_override(
            "franka_cartesian_impedance",
            "franka_panda",
            has_ft_sensor=True,
        )
        assert r.valid is True
        assert r.n_hard == 0

    def test_valid_override_variable_impedance_on_franka(self):
        """variable_impedance on torque-mode robot → valid."""
        r = validate_compliance_override(
            "variable_impedance",
            "franka_panda",
            has_ft_sensor=True,
        )
        assert r.valid is True
        assert r.n_hard == 0

    def test_valid_override_variable_impedance_with_explicit_schedule(self):
        """variable_impedance on position-mode robot WITH K_schedule → valid."""
        r = validate_compliance_override(
            "variable_impedance",
            "ur10e",
            has_ft_sensor=True,
            explicit_K_schedule=True,
        )
        assert r.valid is True
        assert r.n_hard == 0

    # ------------------------------------------------------------------
    # 2. Embodiment rule class — impedance variants need torque-mode
    # ------------------------------------------------------------------

    def test_cartesian_impedance_on_ur10e_rejected(self):
        """RULE: impedance_needs_torque_mode + impedance_on_position_only_robot.
        cartesian_impedance + ur10e (position-only) → HARD reject.
        """
        r = validate_compliance_override(
            "cartesian_impedance", "ur10e", has_ft_sensor=True
        )
        assert r.valid is False
        assert r.n_hard >= 1
        rule_ids = {v.constraint_id for v in r.violations}
        # Three distinct rules fire on this combination; assert ≥ 1.
        assert (
            "compliance.impedance_needs_torque_mode" in rule_ids
            or "compliance.impedance_on_position_only_robot" in rule_ids
            or "compliance.torque_mode_for_position_robot_explicit" in rule_ids
        )

    @pytest.mark.parametrize(
        "robot_class", ["ur10e", "ur5e", "ur3e", "kinova_gen3"]
    )
    def test_cartesian_impedance_on_position_robots_rejected(
        self, robot_class: str
    ):
        """RULE: impedance variants rejected on every position-mode-only robot."""
        r = validate_compliance_override(
            "cartesian_impedance", robot_class, has_ft_sensor=True
        )
        assert r.valid is False
        assert r.n_hard >= 1

    def test_franka_cartesian_impedance_on_ur10e_rejected(self):
        """RULE: franka_specific_mode_non_franka + ur_bans_franka_impedance.
        franka_cartesian_impedance + ur10e → HARD reject.
        """
        r = validate_compliance_override(
            "franka_cartesian_impedance", "ur10e", has_ft_sensor=True
        )
        assert r.valid is False
        rule_ids = {v.constraint_id for v in r.violations}
        # Multiple rules can fire — assert at least one of the Franka-
        # specific rejections is present.
        franka_rules = {
            "compliance.franka_specific_mode_non_franka",
            "compliance.ur_bans_franka_impedance",
            "compliance.franka_impedance_needs_torque_mode",
        }
        assert rule_ids & franka_rules

    def test_franka_cartesian_impedance_on_kinova_rejected(self):
        """RULE: kinova_bans_franka_impedance.
        franka_cartesian_impedance + kinova_gen3 → HARD reject.
        """
        r = validate_compliance_override(
            "franka_cartesian_impedance",
            "kinova_gen3",
            has_ft_sensor=True,
        )
        assert r.valid is False
        rule_ids = {v.constraint_id for v in r.violations}
        assert "compliance.kinova_bans_franka_impedance" in rule_ids

    # ------------------------------------------------------------------
    # 3. Sensor rule class — F/T sensor required modes
    # ------------------------------------------------------------------

    def test_admittance_without_ft_rejected(self):
        """RULE: admittance_needs_ft.
        admittance + no F/T → HARD reject.
        """
        r = validate_compliance_override(
            "admittance", "ur10e", has_ft_sensor=False
        )
        assert r.valid is False
        rule_ids = {v.constraint_id for v in r.violations}
        assert "compliance.admittance_needs_ft" in rule_ids

    def test_fdcc_without_ft_rejected(self):
        """RULE: fdcc_needs_ft.
        cartesian_compliance_fdcc + no F/T → HARD reject.
        """
        r = validate_compliance_override(
            "cartesian_compliance_fdcc",
            "ur10e",
            has_ft_sensor=False,
        )
        assert r.valid is False
        rule_ids = {v.constraint_id for v in r.violations}
        assert "compliance.fdcc_needs_ft" in rule_ids

    def test_ft_sensor_path_inconsistent_is_soft(self):
        """RULE: ft_sensor_path_inconsistent.
        ft_sensor_path set but has_ft_sensor=False → SOFT advisory only.
        """
        # Use mode=cartesian_impedance + Franka so the only inconsistency
        # surfacing this rule is the path/flag mismatch.
        r = validate_compliance_override(
            "cartesian_impedance",
            "franka_panda",
            has_ft_sensor=False,
            ft_sensor_path="/World/Franka/wrist_ft",
        )
        rule_ids = {v.constraint_id for v in r.violations}
        assert "compliance.ft_sensor_path_inconsistent" in rule_ids
        path_violation = next(
            v for v in r.violations
            if v.constraint_id == "compliance.ft_sensor_path_inconsistent"
        )
        # ``ft_sensor_path_inconsistent`` is soft.
        assert path_violation.category == "soft"

    # ------------------------------------------------------------------
    # 4. Input-shape rule class
    # ------------------------------------------------------------------

    def test_unknown_mode_rejected(self):
        """RULE: unknown_mode.
        "bogus" → HARD reject.
        """
        r = validate_compliance_override(
            "bogus", "ur10e", has_ft_sensor=True
        )
        assert r.valid is False
        rule_ids = {v.constraint_id for v in r.violations}
        assert "compliance.unknown_mode" in rule_ids
        # Message should mention the valid modes.
        unknown_v = next(
            v for v in r.violations
            if v.constraint_id == "compliance.unknown_mode"
        )
        assert "admittance" in unknown_v.message

    def test_empty_robot_class_rejected(self):
        """RULE: empty_robot_class.
        admittance + "" → HARD reject.
        """
        r = validate_compliance_override(
            "admittance", "", has_ft_sensor=True
        )
        assert r.valid is False
        rule_ids = {v.constraint_id for v in r.violations}
        assert "compliance.empty_robot_class" in rule_ids

    def test_whitespace_only_robot_class_rejected(self):
        """RULE: empty_robot_class (whitespace counts as empty)."""
        r = validate_compliance_override(
            "admittance", "   ", has_ft_sensor=True
        )
        assert r.valid is False

    def test_none_mode_is_noop(self):
        """RULE: shortcircuit.
        mode=None → ValidationResult with zero violations, valid=True.
        """
        r = validate_compliance_override(None, "ur10e")
        assert r.valid is True
        assert len(r.violations) == 0
        assert r.n_hard == 0
        assert r.n_soft == 0

    def test_empty_string_mode_is_noop(self):
        """RULE: shortcircuit.
        mode='' (empty string) → no override → no-op.
        """
        r = validate_compliance_override("", "ur10e")
        assert r.valid is True
        assert len(r.violations) == 0

    def test_null_mode_always_valid(self):
        """RULE: null is unconditional rigid passthrough.

        ``null`` should be valid regardless of robot or F/T state —
        rigid passthrough places NO compliance requirements.
        """
        # null + no robot + no F/T → still valid
        r = validate_compliance_override("null", "any_robot")
        assert r.valid is True
        assert len(r.violations) == 0

        # null + unknown robot + no F/T → still valid
        r = validate_compliance_override("null", "yaskawa_does_not_exist")
        assert r.valid is True

        # null + empty robot → still valid (no robot needed for rigid)
        r = validate_compliance_override("null", "")
        assert r.valid is True

    def test_non_string_mode_rejected(self):
        """RULE: mode_not_string.
        Passing an int / list / dict for mode is type-incompatible.
        """
        r = validate_compliance_override(12345, "ur10e")  # type: ignore[arg-type]
        assert r.valid is False
        rule_ids = {v.constraint_id for v in r.violations}
        assert "compliance.mode_not_string" in rule_ids

    def test_non_string_robot_class_rejected(self):
        """RULE: robot_class_not_string."""
        r = validate_compliance_override("admittance", 12345)  # type: ignore[arg-type]
        assert r.valid is False
        rule_ids = {v.constraint_id for v in r.violations}
        assert "compliance.robot_class_not_string" in rule_ids

    # ------------------------------------------------------------------
    # 5. Real-deployment rule class
    # ------------------------------------------------------------------

    def test_real_deployment_non_franka_impedance_rejected(self):
        """RULE: real_deploy_non_franka_impedance.
        real_robot_deployment tag + UR + impedance → HARD reject.
        """
        r = validate_compliance_override(
            "cartesian_impedance",
            "ur10e",
            has_ft_sensor=True,
            structural_tags=["real_robot_deployment"],
        )
        assert r.valid is False
        rule_ids = {v.constraint_id for v in r.violations}
        assert "compliance.real_deploy_non_franka_impedance" in rule_ids

    def test_real_deployment_namespaced_tag_matches(self):
        """Namespaced ``user:real_robot_deployment`` also triggers the rule."""
        r = validate_compliance_override(
            "cartesian_impedance",
            "ur10e",
            has_ft_sensor=True,
            structural_tags=["user:real_robot_deployment"],
        )
        rule_ids = {v.constraint_id for v in r.violations}
        assert "compliance.real_deploy_non_franka_impedance" in rule_ids

    def test_real_deployment_admittance_no_ft_rejected(self):
        """RULE: real_deploy_admittance_no_ft.
        Real deployment + admittance + no F/T → HARD reject (no fake-FT).
        """
        r = validate_compliance_override(
            "admittance",
            "ur10e",
            has_ft_sensor=False,
            structural_tags=["real_robot_deployment"],
        )
        assert r.valid is False
        rule_ids = {v.constraint_id for v in r.violations}
        assert "compliance.real_deploy_admittance_no_ft" in rule_ids

    # ------------------------------------------------------------------
    # 6. Variable-impedance K_schedule branch
    # ------------------------------------------------------------------

    def test_variable_impedance_position_robot_without_schedule(self):
        """RULE: variable_impedance_needs_torque_or_schedule.
        variable_impedance + position-mode robot WITHOUT K_schedule → reject.
        """
        r = validate_compliance_override(
            "variable_impedance",
            "ur10e",
            has_ft_sensor=True,
            explicit_K_schedule=False,
        )
        assert r.valid is False
        rule_ids = {v.constraint_id for v in r.violations}
        assert (
            "compliance.variable_impedance_needs_torque_or_schedule"
            in rule_ids
        )

    # ------------------------------------------------------------------
    # 7. Phase 11b round-trip — ValidationResult survives JSON
    # ------------------------------------------------------------------

    def test_round_trip_via_json(self):
        """Phase 11b cross-ref: ValidationResult JSON round-trip preserves
        every field including constraint_id, category, severity, message,
        affected_paths, diagnostics, fix_hint.
        """
        r = validate_compliance_override(
            "cartesian_impedance", "ur10e", has_ft_sensor=True
        )
        # Round-trip through JSON.
        as_json = r.model_dump_json()
        r2 = ValidationResult.model_validate_json(as_json)
        assert r2 == r
        assert r2.valid is False
        assert r2.n_hard == r.n_hard
        assert r2.n_soft == r.n_soft
        assert len(r2.violations) == len(r.violations)
        for v1, v2 in zip(r.violations, r2.violations):
            assert isinstance(v2, ConstraintViolation)
            assert v2.constraint_id == v1.constraint_id
            assert v2.category == v1.category
            assert v2.severity == v1.severity
            assert v2.message == v1.message
            assert v2.affected_paths == v1.affected_paths
            assert v2.diagnostics == v1.diagnostics
            assert v2.fix_hint == v1.fix_hint

    def test_round_trip_empty_result_via_json(self):
        """Empty (valid) ValidationResult round-trips intact."""
        r = validate_compliance_override("null", "any_robot")
        r2 = ValidationResult.model_validate_json(r.model_dump_json())
        assert r2 == r
        assert r2.valid is True
        assert len(r2.violations) == 0
        assert r2.n_hard == 0
        assert r2.n_soft == 0

    # ------------------------------------------------------------------
    # 8. Soft advisory rules — should not flip ``valid``
    # ------------------------------------------------------------------

    def test_admittance_on_franka_is_soft_advisory(self):
        """RULE: admittance_torque_mode_redundant (soft).

        admittance works on Franka but cartesian_impedance is lower
        latency.  Should emit a SOFT advisory but stay valid.
        """
        r = validate_compliance_override(
            "admittance", "franka_panda", has_ft_sensor=True
        )
        assert r.valid is True  # soft does not flip valid
        rule_ids = {v.constraint_id for v in r.violations}
        assert "compliance.admittance_torque_mode_redundant" in rule_ids
        soft_v = next(
            v for v in r.violations
            if v.constraint_id == "compliance.admittance_torque_mode_redundant"
        )
        assert soft_v.category == "soft"

    def test_fdcc_on_franka_is_soft_advisory(self):
        """RULE: fdcc_torque_mode_redundant (soft)."""
        r = validate_compliance_override(
            "cartesian_compliance_fdcc",
            "franka_panda",
            has_ft_sensor=True,
        )
        assert r.valid is True
        rule_ids = {v.constraint_id for v in r.violations}
        assert "compliance.fdcc_torque_mode_redundant" in rule_ids

    def test_unknown_robot_is_soft_advisory(self):
        """RULE: robot_class_not_in_known_table (soft).

        Unknown robot class on override path → soft advisory only,
        not a hard reject (auto-pick handles unknown via admittance).
        """
        # Use a valid mode+sensor combo so only the unknown-robot rule
        # surfaces.  null is unconditionally valid so it shortcircuits;
        # use admittance + ft + unknown.
        r = validate_compliance_override(
            "admittance",
            "yaskawa_gp25_does_not_exist_yet",
            has_ft_sensor=True,
        )
        rule_ids = {v.constraint_id for v in r.violations}
        assert "compliance.robot_class_not_in_known_table" in rule_ids
        soft_v = next(
            v for v in r.violations
            if v.constraint_id == "compliance.robot_class_not_in_known_table"
        )
        assert soft_v.category == "soft"
        # Only this soft rule fires → result is still valid.
        assert r.valid is True
        assert r.n_hard == 0

    # ------------------------------------------------------------------
    # 9. Distinct rule classes — meet the ≥ 6 spec requirement
    # ------------------------------------------------------------------

    def test_at_least_six_distinct_rule_classes_covered(self):
        """Spec §18.3 CRM-T1: ``override hard-incompat list covers ≥ 6
        distinct rule classes``.  Bucket the rule_ids by their semantic
        family and assert the suite exercises ≥ 6 families.

        The semantic families are:
        1. impedance-needs-torque-mode
        2. franka-specific-non-franka (3 rules: generic + UR + Kinova)
        3. admittance-needs-ft (2 rules: generic + position-mode external)
        4. fdcc-needs-ft
        5. unknown-mode
        6. empty-robot-class
        7. variable-impedance-needs-torque-or-schedule
        8. real-deploy-non-franka-impedance
        9. real-deploy-admittance-no-ft
        10. ft-sensor-path-inconsistent (soft)
        11. robot-class-not-in-known-table (soft)
        12. admittance/fdcc-torque-mode-redundant (soft advisories)
        """
        # Run a representative case for each family and collect rule ids.
        cases = [
            # 1. impedance-needs-torque
            (("cartesian_impedance", "ur10e", True), {}),
            # 2. franka-specific-non-franka
            (("franka_cartesian_impedance", "kinova_gen3", True), {}),
            # 3. admittance-needs-ft
            (("admittance", "ur10e", False), {}),
            # 4. fdcc-needs-ft
            (("cartesian_compliance_fdcc", "ur10e", False), {}),
            # 5. unknown-mode
            (("bogus_mode_xyz", "ur10e", True), {}),
            # 6. empty-robot-class
            (("admittance", "", True), {}),
            # 7. variable-impedance-needs-schedule
            (("variable_impedance", "ur10e", True),
             {"explicit_K_schedule": False}),
            # 8. real-deploy-non-franka-impedance
            (("cartesian_impedance", "ur10e", True),
             {"structural_tags": ["real_robot_deployment"]}),
            # 9. real-deploy-admittance-no-ft
            (("admittance", "ur10e", False),
             {"structural_tags": ["real_robot_deployment"]}),
        ]
        all_rule_ids: set[str] = set()
        for args, kwargs in cases:
            r = validate_compliance_override(*args, **kwargs)
            all_rule_ids.update(v.constraint_id for v in r.violations)

        # Bucket into families.
        families = {
            "impedance-needs-torque": {
                "compliance.impedance_needs_torque_mode",
                "compliance.franka_impedance_needs_torque_mode",
            },
            "franka-specific-non-franka": {
                "compliance.franka_specific_mode_non_franka",
                "compliance.ur_bans_franka_impedance",
                "compliance.kinova_bans_franka_impedance",
            },
            "admittance-needs-ft": {
                "compliance.admittance_needs_ft",
                "compliance.admittance_requires_external_ft_for_position_robot",
            },
            "fdcc-needs-ft": {"compliance.fdcc_needs_ft"},
            "unknown-mode": {"compliance.unknown_mode"},
            "empty-robot-class": {"compliance.empty_robot_class"},
            "variable-impedance": {
                "compliance.variable_impedance_needs_torque_or_schedule"
            },
            "real-deploy-impedance": {
                "compliance.real_deploy_non_franka_impedance"
            },
            "real-deploy-admittance-no-ft": {
                "compliance.real_deploy_admittance_no_ft"
            },
        }
        covered_families = sum(
            1 for ids in families.values() if ids & all_rule_ids
        )
        # Spec requires ≥ 6 distinct rule families covered.
        assert covered_families >= 6, (
            f"covered {covered_families} families: {all_rule_ids}"
        )

    # ------------------------------------------------------------------
    # 10. Rule table introspection — meta-tests
    # ------------------------------------------------------------------

    def test_at_least_fifteen_rules_registered(self):
        """Spec §4.2 calls for ~20 hard-incompat rules; ≥ 15 is the floor."""
        assert count_rules() >= 15

    def test_rule_ids_are_unique(self):
        """Every rule must have a unique rule_id (so consumers can switch
        on it)."""
        ids = list_rule_ids()
        assert len(ids) == len(set(ids))

    def test_rule_ids_namespace_consistently(self):
        """Every rule_id starts with ``compliance.`` so the constraint
        registry can group them."""
        for rid in list_rule_ids():
            assert rid.startswith("compliance."), rid

    def test_rules_by_mode_filters_correctly(self):
        """rules_by_mode returns mode-specific + ``*``-rules only."""
        adm_rules = rules_by_mode("admittance")
        for r in adm_rules:
            assert r.mode_match in ("admittance", "*")
        # Every wildcard rule appears.
        wild = [r.rule_id for r in HARD_INCOMPATIBILITIES if r.mode_match == "*"]
        adm_ids = [r.rule_id for r in adm_rules]
        for w in wild:
            assert w in adm_ids

    def test_known_robot_sets_disjoint(self):
        """A robot can't simultaneously be torque-mode AND position-only."""
        assert not (TORQUE_MODE_ROBOTS & POSITION_MODE_ONLY_ROBOTS)

    def test_combined_franka_requirement_emits_soft(self):
        """RULE: cartesian_impedance_combined_requirement (soft).

        cartesian_impedance + franka + no F/T → torque side OK, sensor
        side not OK → SOFT advisory (uninformative wrench feedback).
        """
        r = validate_compliance_override(
            "cartesian_impedance",
            "franka_panda",
            has_ft_sensor=False,
        )
        rule_ids = {v.constraint_id for v in r.violations}
        assert (
            "compliance.cartesian_impedance_combined_requirement"
            in rule_ids
        )
        combined_v = next(
            v for v in r.violations
            if v.constraint_id
            == "compliance.cartesian_impedance_combined_requirement"
        )
        assert combined_v.category == "soft"


# ===========================================================================
# CRM-T1 — TestModeConversion
#
# Spec §18.5 CRM-T1: mode conversion has ≥2 tests:
#   1. admittance→impedance: the two handlers accept different arg keys
#      (admittance uses stiffness_xyz, impedance uses Kx).
#   2. impedance→admittance: the impedance config dict exposes null_space_*
#      keys that are absent from an admittance config dict.
# ===========================================================================


class TestModeConversion:
    """L0 tests for admittance ↔ impedance argument-key differences.

    Spec §4.2 and §5.1/5.2: the two controllers share the same conceptual
    role but expose different parameter namespaces.  These tests verify
    that the handlers correctly populate their *own* key set and do not
    cross-contaminate.
    """

    def _get_admittance_handler(self):
        from service.isaac_assist_service.chat.tools.handlers.compliance import (
            _handle_setup_admittance_controller,
        )
        return _handle_setup_admittance_controller

    def _get_impedance_handler(self):
        from service.isaac_assist_service.chat.tools.handlers.compliance import (
            _handle_setup_impedance_controller,
        )
        return _handle_setup_impedance_controller

    # ------------------------------------------------------------------
    # T1 — admittance→impedance: the arg dicts have different keys
    #
    # admittance uses: stiffness_xyz, damping_xyz, mass_xyz,
    #                  stiffness_rot, damping_rot, mass_rot
    # impedance uses:  Kx, Kr, Dx, Dr, null_space_stiffness,
    #                  null_space_damping
    #
    # Passing stiffness_xyz to the impedance handler must NOT appear in
    # the returned config (it is an admittance-only key).  The impedance
    # handler must expose Kx / null_space_* instead.

    @pytest.mark.asyncio
    async def test_admittance_and_impedance_have_distinct_result_keys(self):
        """admittance config exposes stiffness_xyz; impedance config
        exposes Kx + null_space_* — the two key sets are distinct."""
        adm_handler = self._get_admittance_handler()
        imp_handler = self._get_impedance_handler()

        adm_result = await adm_handler({"robot_path": "/World/FrankaConvT1"})
        imp_result = await imp_handler({"robot_path": "/World/FrankaConvT1"})

        assert adm_result["success"] is True
        assert imp_result["success"] is True

        # Admittance-specific keys present in admittance, absent in impedance.
        assert "stiffness_xyz" in adm_result
        assert "stiffness_xyz" not in imp_result

        # Impedance-specific keys present in impedance, absent in admittance.
        assert "Kx" in imp_result
        assert "Kx" not in adm_result

        assert "null_space_stiffness" in imp_result
        assert "null_space_stiffness" not in adm_result

        assert "null_space_damping" in imp_result
        assert "null_space_damping" not in adm_result

    # ------------------------------------------------------------------
    # T2 — impedance→admittance: switching drops null_space_* keys
    #
    # When a template author switches compliance_mode from "impedance" to
    # "admittance", the null_space_stiffness / null_space_damping params
    # simply don't apply (admittance has no null-space policy).
    # Verify that passing null_space_* args to the admittance handler is
    # silently ignored — the keys must NOT appear in the result.

    @pytest.mark.asyncio
    async def test_null_space_params_lost_when_switching_to_admittance(self):
        """Passing null_space_* to setup_admittance_controller is a no-op:
        the keys must be absent from the returned config dict because
        admittance has no null-space policy."""
        adm_handler = self._get_admittance_handler()

        # Pass impedance-style null_space params to the admittance handler.
        result = await adm_handler({
            "robot_path": "/World/FrankaConvT2",
            "null_space_stiffness": 1.5,
            "null_space_damping": 0.8,
        })

        assert result["success"] is True
        # Admittance config must NOT expose these impedance-only keys.
        assert "null_space_stiffness" not in result
        assert "null_space_damping" not in result
