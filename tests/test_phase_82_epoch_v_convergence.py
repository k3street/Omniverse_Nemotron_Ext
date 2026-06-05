"""Phase 82 — Epoch V capability surface convergence test.

Gate: pytest — all 7 steps pass, convergence_ok is True.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.l0


# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------

def test_phase_82_metadata():
    from service.isaac_assist_service.multimodal.epoch_v_convergence import get_phase_metadata
    md = get_phase_metadata()
    assert md["phase"] == 82
    assert md["status"] == "landed"
    assert "title" in md
    assert "spec_ref" in md


# ---------------------------------------------------------------------------
# Main convergence run
# ---------------------------------------------------------------------------

def test_run_epoch_v_convergence_succeeds():
    """All 7 Epoch V steps must pass and convergence_ok must be True."""
    from service.isaac_assist_service.multimodal.epoch_v_convergence import run_epoch_v_convergence

    result = run_epoch_v_convergence()

    assert isinstance(result, dict), "run_epoch_v_convergence must return a dict"
    assert result.get("convergence_ok") is True, (
        f"convergence_ok is False. Failed steps: {result.get('failed_steps')}\n"
        f"Steps detail:\n"
        + "\n".join(
            f"  Step {s.get('step')}: {s.get('name')} — passed={s.get('passed')} "
            f"reason={s.get('reason')}"
            for s in result.get("steps", [])
        )
    )

    steps = result.get("steps", [])
    assert len(steps) == 7, f"expected 7 steps, got {len(steps)}"
    for step in steps:
        assert step.get("passed") is True, (
            f"Step {step.get('step')} '{step.get('name')}' failed: {step.get('reason')}"
        )


# ---------------------------------------------------------------------------
# Diagnostic completeness
# ---------------------------------------------------------------------------

def test_each_step_returns_diagnostic_info():
    """Every step dict must contain 'passed' (bool) and 'reason' (str) even on success."""
    from service.isaac_assist_service.multimodal.epoch_v_convergence import run_epoch_v_convergence

    result = run_epoch_v_convergence()
    steps = result.get("steps", [])

    assert len(steps) == 7, f"expected 7 steps, got {len(steps)}"
    for step in steps:
        assert "passed" in step, f"step missing 'passed' key: {step}"
        assert isinstance(step["passed"], bool), (
            f"step 'passed' must be bool, got {type(step['passed'])}: {step}"
        )
        assert "reason" in step, f"step missing 'reason' key: {step}"
        assert isinstance(step["reason"], str), (
            f"step 'reason' must be str, got {type(step['reason'])}: {step}"
        )
        assert len(step["reason"]) > 0, f"step 'reason' must be non-empty: {step}"
