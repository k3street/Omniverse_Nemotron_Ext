"""Phase 63 SPEC/SEQUENCE-RUNTIME — execute_contact_sequence_runtime tests.

Gate: sequence advances step-by-step, contact predicates evaluated,
      abort-on-failure works.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.l0

# ---------------------------------------------------------------------------
# Import helper
# ---------------------------------------------------------------------------


def _import():
    from service.isaac_assist_service.multimodal.execute_contact_sequence_runtime import (
        PHASE_STATUS,
        ContactObservation,
        ContactSequencePlan,
        ContactSequenceRuntime,
        ContactStep,
        ContactStepResult,
        expected_step_types,
        get_phase_metadata,
    )
    return (
        PHASE_STATUS,
        ContactObservation,
        ContactSequencePlan,
        ContactSequenceRuntime,
        ContactStep,
        ContactStepResult,
        expected_step_types,
        get_phase_metadata,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_step(idx: int, prim_a: str = "/World/A", prim_b: str = "/World/B",
               step_type: str = "make_contact",
               predicate: str = "contact_established",
               target_force: float = 1.0) -> "ContactStep":
    (_, _, _, _, ContactStep, _, _, _) = _import()
    return ContactStep(
        step_idx=idx,
        step_type=step_type,  # type: ignore[arg-type]
        prim_a=prim_a,
        prim_b=prim_b,
        target_force_N=target_force,
        success_predicate=predicate,
    )


def _make_plan(n: int = 3, abort_on_failure: bool = True):
    (_, _, ContactSequencePlan, _, _, _, _, _) = _import()
    steps = [_make_step(i) for i in range(n)]
    return ContactSequencePlan(steps=steps, abort_on_failure=abort_on_failure)


def _make_obs(step_idx: int, in_contact: bool = True,
              force: float = 1.0, distance: float = 0.0):
    (_, ContactObservation, _, _, _, _, _, _) = _import()
    return ContactObservation(
        step_idx=step_idx,
        observed_force_N=force,
        observed_torque_Nm=0.0,
        in_contact=in_contact,
        prim_distance_m=distance,
    )


# ---------------------------------------------------------------------------
# 1. Metadata
# ---------------------------------------------------------------------------


def test_phase_63_runtime_metadata():
    (_, _, _, _, _, _, _, get_phase_metadata) = _import()
    md = get_phase_metadata()
    assert md["phase"] == 63
    assert md["status"] == "landed"
    assert "contact" in md["title"].lower() or "sequence" in md["title"].lower()
    assert "spec_ref" in md


# ---------------------------------------------------------------------------
# 2. expected_step_types has ≥7 types
# ---------------------------------------------------------------------------


def test_expected_step_types_minimum_seven():
    (_, _, _, _, _, _, expected_step_types, _) = _import()
    types = expected_step_types()
    assert len(types) >= 7
    for expected in ("approach", "make_contact", "apply_force", "slide",
                     "twist", "release", "verify"):
        assert expected in types, f"'{expected}' missing from expected_step_types()"


# ---------------------------------------------------------------------------
# 3. ContactStep dataclass
# ---------------------------------------------------------------------------


def test_contact_step_dataclass_fields():
    (_, _, _, _, ContactStep, _, _, _) = _import()
    step = ContactStep(
        step_idx=0,
        step_type="approach",
        prim_a="/World/RobotTip",
        prim_b="/World/Part",
        target_force_N=2.5,
        target_torque_Nm=0.1,
        duration_s=0.5,
        success_predicate="distance_min",
        retry_count=2,
    )
    assert step.step_idx == 0
    assert step.step_type == "approach"
    assert step.prim_a == "/World/RobotTip"
    assert step.prim_b == "/World/Part"
    assert step.target_force_N == pytest.approx(2.5)
    assert step.target_torque_Nm == pytest.approx(0.1)
    assert step.duration_s == pytest.approx(0.5)
    assert step.success_predicate == "distance_min"
    assert step.retry_count == 2


# ---------------------------------------------------------------------------
# 4. validate: clean 3-step plan → []
# ---------------------------------------------------------------------------


def test_validate_clean_plan_no_issues():
    plan = _make_plan(3)
    issues = plan.validate()
    assert issues == [], f"Expected no issues but got: {issues}"


# ---------------------------------------------------------------------------
# 5. validate: duplicate step_idx → issue
# ---------------------------------------------------------------------------


def test_validate_duplicate_step_idx():
    (_, _, ContactSequencePlan, _, ContactStep, _, _, _) = _import()
    steps = [
        ContactStep(0, "approach", "/World/A", "/World/B"),
        ContactStep(0, "make_contact", "/World/A", "/World/B"),  # duplicate
        ContactStep(1, "release", "/World/A", "/World/B"),
    ]
    plan = ContactSequencePlan(steps=steps)
    issues = plan.validate()
    assert any("uplicate" in i or "step_idx" in i for i in issues), issues


# ---------------------------------------------------------------------------
# 6. validate: non-contiguous idx → issue
# ---------------------------------------------------------------------------


def test_validate_non_contiguous_step_idx():
    (_, _, ContactSequencePlan, _, ContactStep, _, _, _) = _import()
    steps = [
        ContactStep(0, "approach", "/World/A", "/World/B"),
        ContactStep(2, "make_contact", "/World/A", "/World/B"),  # skips 1
        ContactStep(3, "release", "/World/A", "/World/B"),
    ]
    plan = ContactSequencePlan(steps=steps)
    issues = plan.validate()
    assert any("contiguous" in i or "step_idx" in i for i in issues), issues


# ---------------------------------------------------------------------------
# 7. validate: prim_a == prim_b → issue
# ---------------------------------------------------------------------------


def test_validate_prim_a_equals_prim_b():
    (_, _, ContactSequencePlan, _, ContactStep, _, _, _) = _import()
    steps = [
        ContactStep(0, "approach", "/World/Same", "/World/Same"),
    ]
    plan = ContactSequencePlan(steps=steps)
    issues = plan.validate()
    assert any("prim_a" in i or "differ" in i for i in issues), issues


# ---------------------------------------------------------------------------
# 8. next_step advances through steps
# ---------------------------------------------------------------------------


def test_next_step_advances_through_plan():
    (_, _, _, _, _, ContactStepResult, _, _) = _import()
    plan = _make_plan(3)
    # Initially points to step 0
    s0 = plan.next_step()
    assert s0 is not None
    assert s0.step_idx == 0

    # Mark step 0 complete
    obs0 = _make_obs(0)
    r0 = ContactStepResult(
        step_idx=0, step_type="make_contact", success=True,
        observation=obs0, duration_s=0.0
    )
    plan.mark_complete(0, r0)
    s1 = plan.next_step()
    assert s1 is not None
    assert s1.step_idx == 1

    # Mark step 1 complete
    obs1 = _make_obs(1)
    r1 = ContactStepResult(
        step_idx=1, step_type="make_contact", success=True,
        observation=obs1, duration_s=0.0
    )
    plan.mark_complete(1, r1)
    s2 = plan.next_step()
    assert s2 is not None
    assert s2.step_idx == 2


# ---------------------------------------------------------------------------
# 9. is_complete after all steps marked
# ---------------------------------------------------------------------------


def test_is_complete_after_all_marked():
    (_, _, _, _, _, ContactStepResult, _, _) = _import()
    plan = _make_plan(2)
    assert not plan.is_complete()

    obs = _make_obs(0)
    r0 = ContactStepResult(
        step_idx=0, step_type="make_contact", success=True,
        observation=obs, duration_s=0.0
    )
    plan.mark_complete(0, r0)
    assert not plan.is_complete()

    obs1 = _make_obs(1)
    r1 = ContactStepResult(
        step_idx=1, step_type="make_contact", success=True,
        observation=obs1, duration_s=0.0
    )
    plan.mark_complete(1, r1)
    assert plan.is_complete()


# ---------------------------------------------------------------------------
# 10. evaluate_predicate "contact_established" with in_contact=True → True
# ---------------------------------------------------------------------------


def test_evaluate_predicate_contact_established_true():
    (_, _, _, ContactSequenceRuntime, _, _, _, _) = _import()
    rt = ContactSequenceRuntime(dry_run=True)
    obs = _make_obs(0, in_contact=True, force=1.0)
    assert rt.evaluate_predicate("contact_established", obs, expected_force=1.0) is True


def test_evaluate_predicate_contact_established_false():
    (_, _, _, ContactSequenceRuntime, _, _, _, _) = _import()
    rt = ContactSequenceRuntime(dry_run=True)
    obs = _make_obs(0, in_contact=False, force=0.0)
    assert rt.evaluate_predicate("contact_established", obs, expected_force=1.0) is False


# ---------------------------------------------------------------------------
# 11. evaluate_predicate "force_within_tolerance" within → True
# ---------------------------------------------------------------------------


def test_evaluate_predicate_force_within_tolerance_pass():
    (_, _, _, ContactSequenceRuntime, _, _, _, _) = _import()
    rt = ContactSequenceRuntime(dry_run=True)
    obs = _make_obs(0, force=5.0)
    # |5.0 - 5.2| = 0.2 < 0.5 → True
    assert rt.evaluate_predicate(
        "force_within_tolerance", obs, expected_force=5.2, tolerance_N=0.5
    ) is True


def test_evaluate_predicate_force_within_tolerance_fail():
    (_, _, _, ContactSequenceRuntime, _, _, _, _) = _import()
    rt = ContactSequenceRuntime(dry_run=True)
    obs = _make_obs(0, force=0.0)
    # |0.0 - 10.0| = 10.0 ≥ 0.5 → False
    assert rt.evaluate_predicate(
        "force_within_tolerance", obs, expected_force=10.0, tolerance_N=0.5
    ) is False


# ---------------------------------------------------------------------------
# 12. evaluate_predicate "no_contact" with in_contact=False → True
# ---------------------------------------------------------------------------


def test_evaluate_predicate_no_contact_true():
    (_, _, _, ContactSequenceRuntime, _, _, _, _) = _import()
    rt = ContactSequenceRuntime(dry_run=True)
    obs = _make_obs(0, in_contact=False)
    assert rt.evaluate_predicate("no_contact", obs, expected_force=0.0) is True


def test_evaluate_predicate_no_contact_false_when_in_contact():
    (_, _, _, ContactSequenceRuntime, _, _, _, _) = _import()
    rt = ContactSequenceRuntime(dry_run=True)
    obs = _make_obs(0, in_contact=True)
    assert rt.evaluate_predicate("no_contact", obs, expected_force=0.0) is False


# ---------------------------------------------------------------------------
# 13. execute_step dry-run returns success ContactStepResult
# ---------------------------------------------------------------------------


def test_execute_step_dry_run_success():
    (_, _, _, ContactSequenceRuntime, _, ContactStepResult, _, _) = _import()
    rt = ContactSequenceRuntime(dry_run=True)
    step = _make_step(0, predicate="contact_established", target_force=1.0)
    result = rt.execute_step(step)
    assert isinstance(result, ContactStepResult)
    assert result.success is True
    assert result.step_idx == 0
    assert result.error is None


# ---------------------------------------------------------------------------
# 14. execute_step with failing mock_obs → success=False
# ---------------------------------------------------------------------------


def test_execute_step_failing_mock_obs():
    (_, _, _, ContactSequenceRuntime, _, ContactStepResult, _, _) = _import()
    rt = ContactSequenceRuntime(dry_run=True)
    step = _make_step(0, predicate="contact_established")
    # Provide obs where in_contact=False → predicate fails
    failing_obs = _make_obs(step_idx=0, in_contact=False)
    result = rt.execute_step(step, mock_obs=failing_obs)
    assert isinstance(result, ContactStepResult)
    assert result.success is False
    assert result.error is not None and len(result.error) > 0


# ---------------------------------------------------------------------------
# 15. execute_plan walks all steps when clean
# ---------------------------------------------------------------------------


def test_execute_plan_walks_all_steps():
    (_, _, _, ContactSequenceRuntime, _, _, _, _) = _import()
    rt = ContactSequenceRuntime(dry_run=True)
    plan = _make_plan(4)
    results = rt.execute_plan(plan)
    assert len(results) == 4
    for i, r in enumerate(results):
        assert r.step_idx == i
        assert r.success is True
    assert plan.is_complete()


# ---------------------------------------------------------------------------
# 16. execute_plan stops early on failure when abort_on_failure=True
# ---------------------------------------------------------------------------


def test_execute_plan_aborts_on_failure():
    (_, ContactObservation, ContactSequencePlan, ContactSequenceRuntime,
     ContactStep, _, _, _) = _import()

    rt = ContactSequenceRuntime(dry_run=True)
    plan = _make_plan(4, abort_on_failure=True)

    # Force step 1 to fail via failing observation
    failing_obs = _make_obs(step_idx=1, in_contact=False)
    observations = {1: failing_obs}

    results = rt.execute_plan(plan, observations=observations)

    # Steps 0 and 1 should be executed; 2 and 3 should not
    assert len(results) == 2, f"Expected 2 results (abort), got {len(results)}"
    assert results[0].success is True
    assert results[1].success is False


# ---------------------------------------------------------------------------
# 17. execute_plan completes all when abort_on_failure=False
# ---------------------------------------------------------------------------


def test_execute_plan_continues_when_no_abort():
    (_, _, _, ContactSequenceRuntime, _, _, _, _) = _import()

    rt = ContactSequenceRuntime(dry_run=True)
    plan = _make_plan(4, abort_on_failure=False)

    # Force step 1 to fail
    failing_obs = _make_obs(step_idx=1, in_contact=False)
    observations = {1: failing_obs}

    results = rt.execute_plan(plan, observations=observations)

    # All 4 steps should execute despite step 1 failing
    assert len(results) == 4, f"Expected 4 results (no abort), got {len(results)}"
    assert results[0].success is True
    assert results[1].success is False
    assert results[2].success is True
    assert results[3].success is True
    assert plan.is_complete()


# ---------------------------------------------------------------------------
# 18. PHASE_STATUS constant is "landed"
# ---------------------------------------------------------------------------


def test_phase_status_is_landed():
    (PHASE_STATUS, _, _, _, _, _, _, _) = _import()
    assert PHASE_STATUS == "landed"
