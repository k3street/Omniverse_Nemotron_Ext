"""Verify CRM-C2 + CRM-C3 are wired into the production ratify→apply path.

The integration audit (2026-05-14) flagged that ``autopick_compliance_mode``
and ``validate_compliance_override`` had no production callers — they
were testable but unreachable at runtime. This test file pins the wire-
in done in `multimodal/ratify.py:resolve_compliance` and the call from
`chat/tools/multimodal_handlers.py:_handle_apply_layout_spec_to_scene`.
"""
from __future__ import annotations

from typing import Any

import pytest

pytestmark = pytest.mark.l0


# ---------------------------------------------------------------------------
# resolve_compliance unit tests
# ---------------------------------------------------------------------------


def _make_ok_ratify_result(bindings: dict | None = None) -> Any:
    from service.isaac_assist_service.multimodal.ratify import (
        RatifyResult,
    )

    return RatifyResult(
        status="ok",
        bindings=bindings or {},
    )


def _make_layout_spec(
    *,
    has_contact_phase: bool = True,
    compliance_mode: str | None = None,
    compliance_params: dict | None = None,
    objects: list | None = None,
) -> Any:
    from service.isaac_assist_service.multimodal.types import (
        LayoutSpec,
        Intent,
        Counts,
        StructuralFeatures,
        Source,
    )

    intent = Intent(
        pattern_hint="pick_place",
        counts=Counts(),
        structural_features=StructuralFeatures(has_contact_phase=has_contact_phase),
    )
    kwargs: dict = {
        "modality": "text",
        "intent": intent,
        "objects": objects or [],
        "bindings": {},
        "source": Source(modality="text", confidence=1.0),
    }
    if compliance_mode is not None:
        kwargs["compliance_mode"] = compliance_mode
    if compliance_params is not None:
        kwargs["compliance_params"] = compliance_params
    return LayoutSpec(**kwargs)


# ---------------------------------------------------------------------------
# Auto-pick (CRM-C2) wired in
# ---------------------------------------------------------------------------


def test_resolve_compliance_autopick_no_contact_returns_none():
    """has_contact_phase=False → mode=None (rigid baseline)."""
    from service.isaac_assist_service.multimodal.ratify import resolve_compliance

    spec = _make_layout_spec(has_contact_phase=False)
    ratify_result = _make_ok_ratify_result()
    res = resolve_compliance(spec, ratify_result)
    assert res.mode is None
    assert res.source == "auto"
    assert res.hard_violation is False


def test_resolve_compliance_autopick_skipped_when_ratify_failed():
    """ratify status != ok → compliance resolution skipped."""
    from service.isaac_assist_service.multimodal.ratify import (
        resolve_compliance,
        RatifyResult,
    )

    spec = _make_layout_spec(has_contact_phase=True)
    ratify_result = RatifyResult(status="rejected")
    res = resolve_compliance(spec, ratify_result)
    assert res.source == "skipped"
    assert res.mode is None


# ---------------------------------------------------------------------------
# Override (CRM-C3) wired in
# ---------------------------------------------------------------------------


def test_resolve_compliance_override_clean_returns_no_violations():
    """LayoutSpec.compliance_mode set → override path; clean mode → no violations."""
    from service.isaac_assist_service.multimodal.ratify import resolve_compliance

    spec = _make_layout_spec(
        has_contact_phase=True,
        compliance_mode="null",  # null mode is always valid per CRM-C3 rules
    )
    ratify_result = _make_ok_ratify_result()
    res = resolve_compliance(spec, ratify_result)
    assert res.source == "override"
    assert res.mode == "null"


def test_resolve_compliance_override_unknown_mode_flagged_hard():
    """Bogus mode → validator emits ERROR-severity violation; hard_violation=True."""
    from service.isaac_assist_service.multimodal.ratify import resolve_compliance

    spec = _make_layout_spec(
        has_contact_phase=True,
        compliance_mode="bogus_not_a_mode",
    )
    ratify_result = _make_ok_ratify_result()
    res = resolve_compliance(spec, ratify_result)
    assert res.source == "override"
    assert res.mode == "bogus_not_a_mode"
    assert res.hard_violation is True
    assert len(res.violations) >= 1


def test_resolve_compliance_override_admittance_no_ft_sensor_flagged():
    """admittance without F/T sensor → violation per CRM-C3 rule."""
    from service.isaac_assist_service.multimodal.ratify import resolve_compliance

    spec = _make_layout_spec(
        has_contact_phase=True,
        compliance_mode="admittance",
        compliance_params={},  # no ft_sensor_path
    )
    ratify_result = _make_ok_ratify_result()
    res = resolve_compliance(spec, ratify_result)
    assert res.source == "override"
    # The validator should fire admittance_needs_ft rule. Some
    # implementations may return soft severity — either way we should
    # have at least one violation surface.
    assert len(res.violations) >= 1 or res.hard_violation is True


# ---------------------------------------------------------------------------
# End-to-end: _handle_apply_layout_spec_to_scene returns compliance_resolution
# ---------------------------------------------------------------------------


def test_apply_layout_spec_to_scene_wire_in_present_in_source():
    """Static check: the chat handler imports resolve_compliance and
    emits a compliance_resolution payload key on the ok branch.

    A behavioral integration test of _handle_apply_layout_spec_to_scene
    requires bootstrapping a persisted spec via update_layout_spec's
    mutation grammar, which is brittle to refactor. The wire-in
    correctness is more honestly pinned by source inspection — if a
    future refactor breaks the import or the payload key, this test
    fires before the behavioral path can silently lose the
    compliance_resolution surface.
    """
    import inspect
    from service.isaac_assist_service.chat.tools import multimodal_handlers as mh

    # The module must import resolve_compliance.
    assert hasattr(mh, "resolve_compliance"), (
        "multimodal_handlers must import resolve_compliance from ratify"
    )

    # The handler must reference it.
    source = inspect.getsource(mh._handle_apply_layout_spec_to_scene)
    assert "resolve_compliance(" in source, (
        "_handle_apply_layout_spec_to_scene must call resolve_compliance"
    )
    assert "compliance_resolution" in source, (
        "_handle_apply_layout_spec_to_scene must expose the compliance_resolution key"
    )
