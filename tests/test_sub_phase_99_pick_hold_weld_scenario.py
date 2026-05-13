"""Tests for Phase 99 — Pick-hold-weld scenario state machine + scoring.

All tests are l0 (no external dependencies).
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.l0

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _import():
    from service.isaac_assist_service.multimodal.sub_phase_99_pick_hold_weld_scenario import (
        DEFAULT_SCENARIO,
        PHASE_STATUS,
        PickHoldWeldStateMachine,
        RobotAssignment,
        RobotRole,
        ScenarioConfig,
        ScenarioPhase,
        ScenarioState,
        WeldSeamSpec,
        get_phase_metadata,
        score_scenario,
    )
    return (
        DEFAULT_SCENARIO,
        PHASE_STATUS,
        PickHoldWeldStateMachine,
        RobotAssignment,
        RobotRole,
        ScenarioConfig,
        ScenarioPhase,
        ScenarioState,
        WeldSeamSpec,
        get_phase_metadata,
        score_scenario,
    )


# ---------------------------------------------------------------------------
# Test 1 — metadata
# ---------------------------------------------------------------------------

def test_phase_99_metadata():
    (
        _D, _PS, _SM, _RA, _RR, _SC, _SP, _SS, _WS, get_phase_metadata, _score,
    ) = _import()
    md = get_phase_metadata()
    assert md["phase"] == "99"
    assert md["status"] == "landed"
    assert "99" in md["spec_ref"]


# ---------------------------------------------------------------------------
# Test 2 — PHASE_STATUS is "landed"
# ---------------------------------------------------------------------------

def test_phase_status_is_landed():
    (
        _D, PHASE_STATUS, _SM, _RA, _RR, _SC, _SP, _SS, _WS, _meta, _score,
    ) = _import()
    assert PHASE_STATUS == "landed"


# ---------------------------------------------------------------------------
# Test 3 — DEFAULT_SCENARIO has exactly 3 RobotAssignment entries, one per role
# ---------------------------------------------------------------------------

def test_default_scenario_has_three_assignments():
    (
        DEFAULT_SCENARIO, _PS, _SM, _RA, _RR, _SC, _SP, _SS, _WS, _meta, _score,
    ) = _import()
    assignments = DEFAULT_SCENARIO.assignments
    assert len(assignments) == 3
    roles = {a.role for a in assignments}
    assert roles == {"picker_arm", "holder_arm", "welder_arm"}


# ---------------------------------------------------------------------------
# Test 4 — LEGAL_TRANSITIONS includes the full happy path
# ---------------------------------------------------------------------------

def test_legal_transitions_include_happy_path():
    (
        _D, _PS, PickHoldWeldStateMachine, _RA, _RR, ScenarioConfig,
        _SP, _SS, _WS, _meta, _score,
    ) = _import()
    sm = PickHoldWeldStateMachine(ScenarioConfig())
    lt = sm.LEGAL_TRANSITIONS
    happy_path_edges = [
        ("init", "approach"),
        ("approach", "pick"),
        ("pick", "lift_and_hold"),
        ("lift_and_hold", "weld_seam"),
        ("weld_seam", "release"),
        ("release", "complete"),
    ]
    for src, dst in happy_path_edges:
        assert dst in lt[src], f"Missing edge {src} -> {dst}"


# ---------------------------------------------------------------------------
# Test 5 — state starts at "init"
# ---------------------------------------------------------------------------

def test_initial_state_is_init():
    (
        _D, _PS, PickHoldWeldStateMachine, _RA, _RR, ScenarioConfig,
        _SP, _SS, _WS, _meta, _score,
    ) = _import()
    sm = PickHoldWeldStateMachine(ScenarioConfig())
    assert sm.state.phase == "init"


# ---------------------------------------------------------------------------
# Test 6 — advance() walks full happy path init → complete
# ---------------------------------------------------------------------------

def test_advance_walks_full_happy_path():
    (
        _D, _PS, PickHoldWeldStateMachine, _RA, _RR, ScenarioConfig,
        _SP, _SS, _WS, _meta, _score,
    ) = _import()
    sm = PickHoldWeldStateMachine(ScenarioConfig())
    expected = ["approach", "pick", "lift_and_hold", "weld_seam", "release", "complete"]
    for expected_phase in expected:
        reached = sm.advance()
        assert reached == expected_phase, f"Expected {expected_phase}, got {reached}"
    assert sm.state.phase == "complete"


# ---------------------------------------------------------------------------
# Test 7 — illegal transition raises ValueError
# ---------------------------------------------------------------------------

def test_illegal_transition_raises():
    (
        _D, _PS, PickHoldWeldStateMachine, _RA, _RR, ScenarioConfig,
        _SP, _SS, _WS, _meta, _score,
    ) = _import()
    sm = PickHoldWeldStateMachine(ScenarioConfig())
    assert sm.state.phase == "init"
    with pytest.raises(ValueError, match="Illegal transition"):
        sm.transition("complete")  # skipping all intermediate phases


# ---------------------------------------------------------------------------
# Test 8 — fail() transitions to "failed" and appends the error reason
# ---------------------------------------------------------------------------

def test_fail_transitions_to_failed_and_records_reason():
    (
        _D, _PS, PickHoldWeldStateMachine, _RA, _RR, ScenarioConfig,
        _SP, _SS, _WS, _meta, _score,
    ) = _import()
    sm = PickHoldWeldStateMachine(ScenarioConfig())
    sm.advance()  # init → approach
    sm.fail("robot overload")
    assert sm.state.phase == "failed"
    assert "robot overload" in sm.state.errors


# ---------------------------------------------------------------------------
# Test 9 — update_seam_progress accepts values in 0..100
# ---------------------------------------------------------------------------

def test_update_seam_progress():
    (
        _D, _PS, PickHoldWeldStateMachine, _RA, _RR, ScenarioConfig,
        _SP, _SS, _WS, _meta, _score,
    ) = _import()
    sm = PickHoldWeldStateMachine(ScenarioConfig())
    sm.update_seam_progress(0.0)
    assert sm.state.seam_progress_pct == 0.0
    sm.update_seam_progress(55.5)
    assert sm.state.seam_progress_pct == 55.5
    sm.update_seam_progress(100.0)
    assert sm.state.seam_progress_pct == 100.0


# ---------------------------------------------------------------------------
# Test 10 — update_hold_force outside window during weld_seam appends error
# ---------------------------------------------------------------------------

def test_hold_force_outside_window_appends_error():
    (
        _D, _PS, PickHoldWeldStateMachine, _RA, _RR, ScenarioConfig,
        _SP, _SS, _WS, _meta, _score,
    ) = _import()
    cfg = ScenarioConfig(success_force_window_N=(5.0, 50.0))
    sm = PickHoldWeldStateMachine(cfg)
    # Walk to weld_seam
    for _ in range(4):
        sm.advance()
    assert sm.state.phase == "weld_seam"
    sm.update_hold_force(100.0)  # above window
    assert any("outside window" in e for e in sm.state.errors)


# ---------------------------------------------------------------------------
# Test 11 — update_hold_force inside window during weld_seam does NOT append error
# ---------------------------------------------------------------------------

def test_hold_force_inside_window_no_error():
    (
        _D, _PS, PickHoldWeldStateMachine, _RA, _RR, ScenarioConfig,
        _SP, _SS, _WS, _meta, _score,
    ) = _import()
    cfg = ScenarioConfig(success_force_window_N=(5.0, 50.0))
    sm = PickHoldWeldStateMachine(cfg)
    for _ in range(4):
        sm.advance()
    assert sm.state.phase == "weld_seam"
    sm.update_hold_force(25.0)  # well within [5, 50]
    assert sm.state.errors == []


# ---------------------------------------------------------------------------
# Test 12 — is_complete True only after phase=complete
# ---------------------------------------------------------------------------

def test_is_complete_only_after_complete_phase():
    (
        _D, _PS, PickHoldWeldStateMachine, _RA, _RR, ScenarioConfig,
        _SP, _SS, _WS, _meta, _score,
    ) = _import()
    sm = PickHoldWeldStateMachine(ScenarioConfig())
    assert not sm.is_complete()
    for _ in range(6):
        sm.advance()
    assert sm.is_complete()


# ---------------------------------------------------------------------------
# Test 13 — is_failed True after fail()
# ---------------------------------------------------------------------------

def test_is_failed_after_fail():
    (
        _D, _PS, PickHoldWeldStateMachine, _RA, _RR, ScenarioConfig,
        _SP, _SS, _WS, _meta, _score,
    ) = _import()
    sm = PickHoldWeldStateMachine(ScenarioConfig())
    assert not sm.is_failed()
    sm.fail("test failure")
    assert sm.is_failed()


# ---------------------------------------------------------------------------
# Test 14 — score_scenario: perfect run → score == max_score
# ---------------------------------------------------------------------------

def test_score_perfect_run():
    (
        _D, _PS, _SM, _RA, _RR, ScenarioConfig,
        _SP, ScenarioState, _WS, _meta, score_scenario,
    ) = _import()
    cfg = ScenarioConfig(max_duration_s=60.0, success_force_window_N=(5.0, 50.0))
    state = ScenarioState(
        phase="complete",
        elapsed_s=30.0,  # within 60 s
        seam_progress_pct=100.0,
        hold_force_N=25.0,
        errors=[],  # no force violations
    )
    result = score_scenario(state, cfg)
    assert result["score"] == result["max_score"]
    assert result["success"] is True
    assert result["breakdown"]["phase_complete"] == 30.0
    assert result["breakdown"]["seam_complete"] == 30.0
    assert result["breakdown"]["force_in_window"] == 20.0
    assert result["breakdown"]["within_time"] == 20.0


# ---------------------------------------------------------------------------
# Test 15 — score_scenario: failed mid-way → score < max_score
# ---------------------------------------------------------------------------

def test_score_failed_midway():
    (
        _D, _PS, _SM, _RA, _RR, ScenarioConfig,
        _SP, ScenarioState, _WS, _meta, score_scenario,
    ) = _import()
    cfg = ScenarioConfig(max_duration_s=60.0, success_force_window_N=(5.0, 50.0))
    # Scenario aborted during weld, seam only 40 % done, over time
    state = ScenarioState(
        phase="failed",
        elapsed_s=90.0,  # over budget
        seam_progress_pct=40.0,
        hold_force_N=0.0,
        errors=["hold_force 100.00 N outside window [5.0, 50.0] during weld_seam"],
    )
    result = score_scenario(state, cfg)
    assert result["score"] < result["max_score"]
    assert result["success"] is False
    assert result["breakdown"]["phase_complete"] == 0.0
    assert result["breakdown"]["seam_complete"] == 0.0
    assert result["breakdown"]["force_in_window"] == 0.0
    assert result["breakdown"]["within_time"] == 0.0


# ---------------------------------------------------------------------------
# Test 16 — score_scenario breakdown fields are present
# ---------------------------------------------------------------------------

def test_score_breakdown_keys_present():
    (
        _D, _PS, _SM, _RA, _RR, ScenarioConfig,
        _SP, ScenarioState, _WS, _meta, score_scenario,
    ) = _import()
    result = score_scenario(ScenarioState(), ScenarioConfig())
    assert "score" in result
    assert "max_score" in result
    assert "success" in result
    assert "breakdown" in result
    bd = result["breakdown"]
    for key in ("phase_complete", "seam_complete", "force_in_window", "within_time"):
        assert key in bd, f"Missing breakdown key: {key}"
