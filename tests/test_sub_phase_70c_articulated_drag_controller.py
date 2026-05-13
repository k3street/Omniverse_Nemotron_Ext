"""Tests for Phase 70c — Articulated DRAG controller.

Gate: PD controller converges on target; safety limiter caps force.
"""
from __future__ import annotations

import math
import pytest

pytestmark = pytest.mark.l0


# ---------------------------------------------------------------------------
# Imports
# ---------------------------------------------------------------------------

from service.isaac_assist_service.multimodal.sub_phase_70c_articulated_drag_controller import (
    ControlCommand,
    ControlState,
    DRAGController,
    DRAGControllerConfig,
    get_phase_metadata,
)


# ---------------------------------------------------------------------------
# 1. Metadata
# ---------------------------------------------------------------------------

def test_phase_70c_articulated_metadata():
    md = get_phase_metadata()
    assert md["phase"] == "70c"
    assert md["status"] == "landed"
    assert "DRAG" in md["title"] or "drag" in md["title"].lower()


# ---------------------------------------------------------------------------
# 2. Default config values match spec
# ---------------------------------------------------------------------------

def test_default_config_values():
    cfg = DRAGControllerConfig()
    assert cfg.kp == 50.0
    assert cfg.kd == 8.0
    assert cfg.max_force_N == 200.0
    assert cfg.max_velocity_mps == 0.5
    assert cfg.ramp_time_s == 0.5
    assert cfg.deadband_m == 0.005


# ---------------------------------------------------------------------------
# 3. step: error > deadband → nonzero force
# ---------------------------------------------------------------------------

def test_step_large_error_returns_nonzero_force():
    ctrl = DRAGController()
    state = ControlState(position_m=0.0, velocity_mps=0.0, target_m=1.0, elapsed_s=1.0)
    cmd = ctrl.step(state, dt=0.01)
    assert not cmd.in_deadband
    assert cmd.force_N != 0.0


# ---------------------------------------------------------------------------
# 4. step: |error| < deadband → force=0 and in_deadband=True
# ---------------------------------------------------------------------------

def test_step_within_deadband_returns_zero_force():
    ctrl = DRAGController()
    # Place position exactly at target within deadband (0.002 m < 0.005 m default)
    state = ControlState(position_m=1.0, velocity_mps=0.0, target_m=1.002, elapsed_s=1.0)
    cmd = ctrl.step(state, dt=0.01)
    assert cmd.in_deadband is True
    assert cmd.force_N == 0.0


# ---------------------------------------------------------------------------
# 5. step: huge error → force clipped to max_force_N
# ---------------------------------------------------------------------------

def test_step_clips_force_to_max():
    cfg = DRAGControllerConfig(kp=5000.0, kd=0.0, max_force_N=200.0, ramp_time_s=0.0)
    ctrl = DRAGController(cfg)
    state = ControlState(position_m=0.0, velocity_mps=0.0, target_m=100.0, elapsed_s=1.0)
    cmd = ctrl.step(state, dt=0.01)
    assert cmd.clipped is True
    assert abs(cmd.force_N) <= cfg.max_force_N


# ---------------------------------------------------------------------------
# 6. Ramp: at elapsed=0 force ≈ 0; at elapsed > ramp_time full magnitude
# ---------------------------------------------------------------------------

def test_ramp_starts_near_zero():
    cfg = DRAGControllerConfig(kp=100.0, kd=0.0, ramp_time_s=1.0, max_force_N=1e6)
    ctrl = DRAGController(cfg)
    # elapsed=0 → ramp_factor should be 0 → force ≈ 0
    state = ControlState(position_m=0.0, velocity_mps=0.0, target_m=1.0, elapsed_s=0.0)
    cmd = ctrl.step(state, dt=0.01)
    assert cmd.ramp_factor == pytest.approx(0.0, abs=1e-9)
    assert cmd.force_N == pytest.approx(0.0, abs=1e-6)


def test_ramp_reaches_full_after_ramp_time():
    cfg = DRAGControllerConfig(kp=100.0, kd=0.0, ramp_time_s=0.5, max_force_N=1e6)
    ctrl = DRAGController(cfg)
    # elapsed > ramp_time → ramp_factor = 1.0
    state = ControlState(position_m=0.0, velocity_mps=0.0, target_m=1.0, elapsed_s=1.0)
    cmd = ctrl.step(state, dt=0.01)
    assert cmd.ramp_factor == pytest.approx(1.0, abs=1e-9)
    # Full PD force should equal kp * error (no damping, no clip)
    expected_force = cfg.kp * 1.0
    assert cmd.force_N == pytest.approx(expected_force, rel=1e-6)


# ---------------------------------------------------------------------------
# 7. simulate: converges — final position within 5 % of target
# ---------------------------------------------------------------------------

def test_simulate_converges_to_target():
    ctrl = DRAGController()
    initial = ControlState(position_m=0.0, velocity_mps=0.0, target_m=0.0, elapsed_s=0.0)
    traj = ctrl.simulate(initial, target=1.0, dt=0.01, steps=500, mass_kg=5.0)
    final_pos = traj[-1].position_m
    # Within 5 % of target (1.0 m)
    assert abs(final_pos - 1.0) < 0.05, f"Did not converge: final_pos={final_pos:.4f}"


# ---------------------------------------------------------------------------
# 8. simulate: velocity never exceeds max_velocity_mps
# ---------------------------------------------------------------------------

def test_simulate_respects_max_velocity():
    cfg = DRAGControllerConfig(kp=500.0, kd=0.0, max_velocity_mps=0.5, ramp_time_s=0.0)
    ctrl = DRAGController(cfg)
    initial = ControlState(position_m=0.0, velocity_mps=0.0, target_m=5.0, elapsed_s=0.0)
    traj = ctrl.simulate(initial, target=5.0, dt=0.01, steps=300, mass_kg=1.0)
    max_vel = max(abs(s.velocity_mps) for s in traj)
    assert max_vel <= cfg.max_velocity_mps + 1e-9, f"Velocity exceeded limit: {max_vel:.4f}"


# ---------------------------------------------------------------------------
# 9. time_to_target: returns finite time for reachable target
# ---------------------------------------------------------------------------

def test_time_to_target_reachable():
    ctrl = DRAGController()
    initial = ControlState(position_m=0.0, velocity_mps=0.0, target_m=0.0, elapsed_s=0.0)
    t = ctrl.time_to_target(initial, target=0.5, tolerance_m=0.01, dt=0.01, max_steps=5000, mass_kg=5.0)
    assert t is not None, "Expected convergence but got None"
    assert t > 0.0


# ---------------------------------------------------------------------------
# 10. time_to_target: returns None when convergence is impossible
# ---------------------------------------------------------------------------

def test_time_to_target_returns_none_when_not_converging():
    # Very low gain + very few steps ensures we never reach a distant target.
    cfg = DRAGControllerConfig(kp=0.001, kd=0.0, max_force_N=0.01, ramp_time_s=0.0)
    ctrl = DRAGController(cfg)
    initial = ControlState(position_m=0.0, velocity_mps=0.0, target_m=0.0, elapsed_s=0.0)
    result = ctrl.time_to_target(
        initial,
        target=10.0,      # Far target
        tolerance_m=0.01,
        dt=0.01,
        max_steps=50,     # Tiny budget — definitely won't converge
        mass_kg=5.0,
    )
    assert result is None, f"Expected None but got {result}"


# ---------------------------------------------------------------------------
# 11. ControlCommand dataclass fields are present and typed correctly
# ---------------------------------------------------------------------------

def test_control_command_fields():
    cmd = ControlCommand(force_N=10.0, ramp_factor=0.5, clipped=False, in_deadband=False)
    assert isinstance(cmd.force_N, float)
    assert isinstance(cmd.ramp_factor, float)
    assert isinstance(cmd.clipped, bool)
    assert isinstance(cmd.in_deadband, bool)


# ---------------------------------------------------------------------------
# 12. simulate returns steps+1 states (including initial)
# ---------------------------------------------------------------------------

def test_simulate_returns_correct_length():
    ctrl = DRAGController()
    initial = ControlState(position_m=0.0)
    steps = 100
    traj = ctrl.simulate(initial, target=1.0, dt=0.01, steps=steps)
    assert len(traj) == steps + 1


# ---------------------------------------------------------------------------
# 13. Negative-error (overshoot) case: force is in the opposite direction
# ---------------------------------------------------------------------------

def test_step_negative_error_gives_negative_force():
    cfg = DRAGControllerConfig(kp=50.0, kd=0.0, ramp_time_s=0.0, max_force_N=1e6)
    ctrl = DRAGController(cfg)
    # Position PAST target → error is negative
    state = ControlState(position_m=2.0, velocity_mps=0.0, target_m=1.0, elapsed_s=1.0)
    cmd = ctrl.step(state, dt=0.01)
    assert cmd.force_N < 0.0, f"Expected negative corrective force, got {cmd.force_N}"
