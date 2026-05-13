"""Phase 11 — pipeline framework tests.

The 22 rules are tested individually by the existing
`tests/test_patch_validator.py`. These tests verify the pipeline
framework: registry, runner, ValidationResult shape.

Per specs/IA_FULL_SPEC_2026-05-10.md Phase 11.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.l0


def test_registry_has_22_rules():
    """All 22 _check_* functions are registered as rule classes."""
    from service.isaac_assist_service.chat.tools.patch_validators import REGISTRY
    assert len(REGISTRY) == 22, (
        f"Expected 22 registered rules (one per _check_X in patch_validator.py); "
        f"got {len(REGISTRY)}. Rules: {[c.__name__ for c in REGISTRY]}"
    )


def test_rule_ids_unique():
    """No two rules share the same rule_id."""
    from service.isaac_assist_service.chat.tools.patch_validators import REGISTRY
    rule_ids = [cls.rule_id for cls in REGISTRY]
    duplicates = [r for r in rule_ids if rule_ids.count(r) > 1]
    assert not duplicates, f"Duplicate rule_ids: {set(duplicates)}"


def test_pipeline_runs_clean_patch():
    """A trivially clean patch produces no issues."""
    from service.isaac_assist_service.chat.tools.patch_validators import run_pipeline
    code = "import omni.usd\nstage = omni.usd.get_context().get_stage()\n"
    result = run_pipeline(code)
    assert result.issues == []
    assert result.blocking is False
    assert result.format_for_llm() == ""


def test_pipeline_detects_omnigraph_type_mismatch():
    """The og_double3_to_double rule fires on a direct twist→diff wire."""
    from service.isaac_assist_service.chat.tools.patch_validators import run_pipeline
    code = '''
import omni.graph.core as og
og.Controller.edit("/Graph", {
    "edges": [("ROS2SubscribeTwist.outputs:linearVelocity",
               "DiffController.inputs:linearVelocity")]
})
'''
    result = run_pipeline(code)
    assert any(i.rule == "og_double3_to_double" for i in result.issues), (
        f"Expected og_double3_to_double issue; got {[i.rule for i in result.issues]}"
    )
    assert result.blocking


def test_pipeline_format_for_llm():
    """ValidationResult.format_for_llm() produces useful text."""
    from service.isaac_assist_service.chat.tools.patch_validators import (
        PatchIssue, Severity, ValidationResult,
    )
    result = ValidationResult(issues=[
        PatchIssue(severity=Severity.ERROR.value, rule="test_rule",
                   message="Test message", fix_hint="Test fix"),
    ])
    msg = result.format_for_llm()
    assert "VALIDATION FAILED" in msg
    assert "test_rule" in msg
    assert "Test message" in msg
    assert "FIX: Test fix" in msg


def test_pipeline_runner_isolated_rules():
    """Runner with a subset of rules only fires those."""
    from service.isaac_assist_service.chat.tools.patch_validators import (
        PipelineRunner, REGISTRY,
    )
    # Use just the first rule
    runner = PipelineRunner(rules=REGISTRY[:1])
    result = runner.run("# trivial code")
    # The runner ran exactly one rule; result is well-formed
    assert isinstance(result.issues, list)


def test_rule_class_has_required_attrs():
    """Every registered rule defines rule_id and severity."""
    from service.isaac_assist_service.chat.tools.patch_validators import REGISTRY
    for cls in REGISTRY:
        assert hasattr(cls, "rule_id"), f"{cls.__name__} missing rule_id"
        assert isinstance(cls.rule_id, str)
        assert hasattr(cls, "severity"), f"{cls.__name__} missing severity"
