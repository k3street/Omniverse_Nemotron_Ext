"""Tests for service/isaac_assist_service/multimodal/verifier_registry.py.

Block 1B Step 17: verify-pipeline registry-dispatched checks.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.l0

from service.isaac_assist_service.multimodal.types import StructuralFeatures
from service.isaac_assist_service.multimodal.verifier_registry import (
    REGISTRY,
    CheckResult,
    VerifierCheck,
    VerifierRegistry,
    register_form_check,
    register_function_check,
)


def test_default_form_checks_registered():
    """6 default form_gate checks per spec §6.3."""
    ids = REGISTRY.form_check_ids()
    expected = [
        "verify:reach",
        "verify:conveyor_active",
        "verify:controller_installed",
        "verify:cube_source_bridged",
        "verify:footprint_within_bounds",
        "verify:color_routing_consistent",
    ]
    for e in expected:
        assert e in ids, f"missing form check: {e}"


def test_default_function_checks_registered():
    """3 default function_gate checks per spec §6.3."""
    ids = REGISTRY.function_check_ids()
    expected = [
        "simulate:cube_delivered",
        "simulate:upright_at_rest",
        "simulate:human_safety_zone",
    ]
    for e in expected:
        assert e in ids, f"missing function check: {e}"


def test_check_id_must_be_namespaced():
    with pytest.raises(ValueError, match="namespaced"):
        VerifierCheck(
            id="not_namespaced",
            applies_when=lambda f: True,
            run=lambda **k: CheckResult(status="pass"),
        )


def test_dispatch_predicate_skips_inapplicable():
    """When applies_when returns False, check is skipped."""
    reg = VerifierRegistry()
    reg.register_form_check(VerifierCheck(
        id="test:upright",
        applies_when=lambda f: f.has_orientation_requirement,
        run=lambda **k: CheckResult(status="pass"),
    ))
    # Default features: no orientation requirement
    features = StructuralFeatures()
    result = reg.run_form_gate(features)
    assert result.overall == "skipped"
    assert "test:upright" in result.checks_skipped


def test_dispatch_predicate_runs_when_applicable():
    """When applies_when returns True, check runs."""
    reg = VerifierRegistry()
    reg.register_form_check(VerifierCheck(
        id="test:reach",
        applies_when=lambda f: f.n_robot_stations > 0,
        run=lambda **k: CheckResult(status="pass", diagnostics=["ok"]),
    ))
    features = StructuralFeatures(n_robot_stations=1)
    result = reg.run_form_gate(features)
    assert result.overall == "pass"
    assert "test:reach" in result.checks_run
    assert result.per_check["test:reach"].is_pass()


def test_failing_check_propagates_to_gate_result():
    reg = VerifierRegistry()
    reg.register_form_check(VerifierCheck(
        id="test:fail",
        applies_when=lambda f: True,
        run=lambda **k: CheckResult(status="fail", issues=["broke"]),
    ))
    features = StructuralFeatures()
    result = reg.run_form_gate(features)
    assert result.overall == "fail"
    assert result.failed_check_count() == 1


def test_run_exception_becomes_fail_result():
    """A check's run() raising is contained, returning fail with issue text."""
    def bad_run(**kwargs):
        raise ValueError("oops")

    reg = VerifierRegistry()
    reg.register_form_check(VerifierCheck(
        id="test:bad",
        applies_when=lambda f: True,
        run=bad_run,
    ))
    features = StructuralFeatures()
    result = reg.run_form_gate(features)
    assert result.overall == "fail"
    assert any("ValueError" in i for i in result.per_check["test:bad"].issues)


def test_applies_when_exception_skips_check():
    """A predicate raising is contained, marking check skipped."""
    reg = VerifierRegistry()
    reg.register_form_check(VerifierCheck(
        id="test:predicate_err",
        applies_when=lambda f: 1 / 0,  # ZeroDivisionError
        run=lambda **k: CheckResult(status="pass"),
    ))
    features = StructuralFeatures()
    result = reg.run_form_gate(features)
    assert "test:predicate_err" in result.checks_skipped


def test_duplicate_id_registration_raises():
    reg = VerifierRegistry()
    c1 = VerifierCheck(id="x:y", applies_when=lambda f: True, run=lambda **k: CheckResult(status="pass"))
    reg.register_form_check(c1)
    with pytest.raises(ValueError, match="already registered"):
        reg.register_form_check(c1)


def test_reach_dispatches_on_robot_stations():
    """verify:reach in default REGISTRY skips when n_robot_stations is 0."""
    features = StructuralFeatures(n_robot_stations=1)
    result = REGISTRY.run_form_gate(features, args={"reach_diagnostics": {"all_reachable": True}})
    assert "verify:reach" in result.checks_run


def test_conveyor_dispatches_on_uses_conveyor_transport():
    features_off = StructuralFeatures(uses_conveyor_transport=False)
    result_off = REGISTRY.run_form_gate(features_off)
    assert "verify:conveyor_active" in result_off.checks_skipped

    features_on = StructuralFeatures(uses_conveyor_transport=True, n_robot_stations=1)
    result_on = REGISTRY.run_form_gate(features_on)
    assert "verify:conveyor_active" in result_on.checks_run


def test_upright_check_dispatches_on_orientation_requirement():
    features = StructuralFeatures(has_orientation_requirement=True)
    result = REGISTRY.run_function_gate(features, args={"upright_pass": True})
    assert "simulate:upright_at_rest" in result.checks_run


def test_human_safety_dispatches_on_human_in_workspace():
    features = StructuralFeatures(has_human_in_workspace=True)
    result = REGISTRY.run_function_gate(features, args={"safety_violations": []})
    assert "simulate:human_safety_zone" in result.checks_run


def test_args_with_external_failure_propagates():
    """When caller passes failure data, gate fails."""
    features = StructuralFeatures(n_robot_stations=1)
    result = REGISTRY.run_form_gate(
        features,
        args={"reach_diagnostics": {"all_reachable": False, "unreachable": ["cube_5"]}},
    )
    assert result.overall == "fail"
    assert "verify:reach" in [k for k, v in result.per_check.items() if v.is_fail()]


def test_empty_features_gives_partial_skip():
    """Default features have n_robot_stations=1 by default."""
    features = StructuralFeatures()
    result = REGISTRY.run_form_gate(features, args={"reach_diagnostics": {"all_reachable": True}})
    # reach + controller_installed apply; the rest skip
    assert "verify:reach" in result.checks_run
    assert "verify:controller_installed" in result.checks_run
    assert "verify:conveyor_active" in result.checks_skipped
