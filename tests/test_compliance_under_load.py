"""CRM-T2 — L1 mocked integration tests for the admittance step law.

Exercises the pure-Python admittance step iterator in isolation:
no live Kit, no RPC, no async infrastructure required.

Physics model (single axis, continuous form):
    M · ẍ = -K · (x - x_desired) - D · ẋ + F_ext

Discretised (Euler, dt):
    a = (-K · (x - x_desired) - D · v + F_ext) / M
    v_new = v + a · dt
    x_new = x + v_new · dt

The force returned by _admittance_step is the net spring-damper force
(positive = toward desired position).  We integrate it as the sole
actuator force on a virtual mass M.

Contract: §9.2 of docs/specs/2026-05-11-contact-rich-manipulation-spec.md
"""
from __future__ import annotations

import math
from typing import List, Tuple

import pytest

pytestmark = pytest.mark.l0


# ---------------------------------------------------------------------------
# Pure-Python admittance step — mirrors compliance.py _admittance_step
# so these tests validate the model without importing the handler module
# (keeping them runnable if the handler is refactored or moved).
# ---------------------------------------------------------------------------


def _admittance_step(
    K: float,
    D: float,
    x_desired: float,
    x_actual: float,
    v_actual: float,
    F_ext: float = 0.0,
) -> float:
    """Single-axis admittance control force.

    F = K · (x_desired - x_actual) - D · v_actual + F_ext
    """
    return K * (x_desired - x_actual) - D * v_actual + F_ext


def _simulate(
    K: float,
    D: float,
    M: float,
    F_ext: float,
    dt: float,
    n_steps: int,
    x0: float = 0.0,
    v0: float = 0.0,
    x_desired: float = 0.0,
) -> Tuple[List[float], List[float]]:
    """Run the admittance loop for *n_steps* steps.

    The external force F_ext acts as a constant disturbance (simulating a
    step input from a mock F/T sensor).  x_desired is held at 0 throughout;
    the robot displaces to equilibrium where K·x_eq = F_ext → x_eq = F_ext/K.

    Returns (positions, velocities) over time.
    """
    positions: List[float] = []
    velocities: List[float] = []
    x: float = x0
    v: float = v0
    for _ in range(n_steps):
        f_ctrl = _admittance_step(K, D, x_desired, x, v, F_ext)
        a = f_ctrl / M
        v = v + a * dt
        x = x + v * dt
        positions.append(x)
        velocities.append(v)
    return positions, velocities


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _steady_state_displacement(F_ext: float, K: float) -> float:
    """Theoretical steady-state displacement under constant external force."""
    return F_ext / K


def _critical_damping(K: float, M: float) -> float:
    """Critical damping coefficient: D_crit = 2 · sqrt(K · M)."""
    return 2.0 * math.sqrt(K * M)


# ---------------------------------------------------------------------------
# Test 1 — step response steady-state displacement
# ---------------------------------------------------------------------------


class TestStepResponseSteadyState:
    """Apply a 10 N step input; verify steady-state displacement ≈ F/K ±5%."""

    def test_step_response_steady_state_displacement(self) -> None:
        """10 N external force, K=500 N/m → x_eq ≈ 0.02 m within 5%.

        Setup:
          F_ext = 10 N (step from mock F/T sensor)
          K = 500 N/m, D = 2*sqrt(K*M) (critical), M = 1 kg
          dt = 0.002 s (500 Hz), n_steps = 500 (≡ 1000 ms >> 200 ms settling)

        Assert final position within 5% of F/K = 0.02 m.
        """
        F_ext: float = 10.0
        K: float = 500.0
        M: float = 1.0
        D: float = _critical_damping(K, M)  # critically damped → no overshoot
        dt: float = 0.002  # 500 Hz
        # 200 ms settling = 100 steps; run 5× longer to ensure convergence
        n_steps: int = 500

        positions, _ = _simulate(K, D, M, F_ext, dt, n_steps)

        x_eq = _steady_state_displacement(F_ext, K)  # 0.02 m
        x_final = positions[-1]
        error = abs(x_final - x_eq) / x_eq

        assert x_eq == pytest.approx(0.02, rel=1e-9), (
            "Theoretical steady-state must be exactly 0.02 m"
        )
        assert error < 0.05, (
            f"Final position {x_final:.6f} m deviates from x_eq={x_eq:.4f} m "
            f"by {error*100:.1f}% (limit 5%)"
        )


# ---------------------------------------------------------------------------
# Test 2 — critical damping: no overshoot
# ---------------------------------------------------------------------------


class TestCriticalDamping:
    """Critically-damped system must not overshoot steady-state by more than 5%."""

    def test_critical_damping_no_overshoot(self) -> None:
        """D = 2*sqrt(K*M) → overshoot ≤ 5% above steady-state.

        A critically-damped second-order system approaches x_eq from below
        without oscillation.  The peak must stay within 5% of x_eq.
        """
        F_ext: float = 10.0
        K: float = 500.0
        M: float = 1.0
        D: float = _critical_damping(K, M)
        dt: float = 0.001
        n_steps: int = 2000  # 2 s — well past settling

        positions, _ = _simulate(K, D, M, F_ext, dt, n_steps)

        x_eq = _steady_state_displacement(F_ext, K)
        overshoot_limit = 1.05 * x_eq

        peak = max(positions)
        assert peak <= overshoot_limit, (
            f"Peak displacement {peak:.6f} m exceeds 105% of x_eq={x_eq:.4f} m "
            f"(peak/x_eq = {peak/x_eq:.3f})"
        )

        # Also verify it actually reaches within 1% of x_eq (not stalled)
        x_final = positions[-1]
        assert abs(x_final - x_eq) / x_eq < 0.01, (
            f"Critically-damped system did not converge: final={x_final:.6f}, "
            f"x_eq={x_eq:.4f}"
        )


# ---------------------------------------------------------------------------
# Test 3 — under-damped: overshoots then settles
# ---------------------------------------------------------------------------


class TestUnderDampedResponse:
    """Under-damped system (D = 0.3·D_crit) must overshoot ≥5% then settle."""

    def test_under_damped_overshoots_then_settles(self) -> None:
        """D = 0.3 * D_crit → initial overshoot ≥5%, settles within 500 ms.

        Under-damped response oscillates around x_eq.  We assert:
          1. Peak exceeds 1.05 · x_eq (genuine overshoot).
          2. System settles to within 5% of x_eq by 500 ms (250 steps at 2 ms).
        """
        F_ext: float = 10.0
        K: float = 500.0
        M: float = 1.0
        D: float = 0.3 * _critical_damping(K, M)  # zeta ≈ 0.15 — under-damped
        dt: float = 0.002  # 500 Hz
        n_settle = 250  # 500 ms
        n_steps: int = max(n_settle + 1, 500)

        positions, _ = _simulate(K, D, M, F_ext, dt, n_steps)

        x_eq = _steady_state_displacement(F_ext, K)

        # Assert overshoot occurred somewhere in the trajectory
        peak = max(positions)
        assert peak > 1.05 * x_eq, (
            f"Expected under-damped overshoot ≥5% above x_eq={x_eq:.4f} m, "
            f"got peak={peak:.6f} m (peak/x_eq={peak/x_eq:.3f})"
        )

        # Assert settled within 5% of x_eq by 500 ms
        x_at_settle = positions[n_settle - 1]
        settle_error = abs(x_at_settle - x_eq) / x_eq
        assert settle_error < 0.05, (
            f"Under-damped system did not settle within 5% of x_eq={x_eq:.4f} m "
            f"by 500 ms: position={x_at_settle:.6f} m, error={settle_error*100:.1f}%"
        )


# ---------------------------------------------------------------------------
# Test 4 — zero force: no motion
# ---------------------------------------------------------------------------


class TestZeroForceNoMotion:
    """With F_ext = 0 N and x_desired = x_actual = 0, EE must remain at origin."""

    def test_zero_force_input_no_motion(self) -> None:
        """0 N step input → EE stays at origin (position = 0 throughout).

        If x_desired = x_actual = 0 and v = 0, the spring and damper forces
        are both zero, so no motion occurs regardless of K, D, M.
        """
        F_ext: float = 0.0
        K: float = 500.0
        M: float = 1.0
        D: float = _critical_damping(K, M)
        dt: float = 0.002
        n_steps: int = 1000

        positions, velocities = _simulate(K, D, M, F_ext, dt, n_steps)

        for i, (x, v) in enumerate(zip(positions, velocities)):
            assert x == pytest.approx(0.0, abs=1e-12), (
                f"Step {i}: expected x=0.0, got x={x}"
            )
            assert v == pytest.approx(0.0, abs=1e-12), (
                f"Step {i}: expected v=0.0, got v={v}"
            )
