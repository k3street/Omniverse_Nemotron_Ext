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
