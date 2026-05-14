"""Phase 70c — Articulated DRAG controller (SPEC/CONTROLLER layer).

Pure-Python PD control law + force/torque ramp + safety limiter for
drag-along-articulated-joint tasks (drawer-open, door-hinge, slider).

Live Isaac Sim integration stays scaffold; this module is the math
kernel that the handler in handlers/articulated_pull.py will call.

Per specs/IA_FULL_SPEC_2026-05-10.md Phase 70c.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


PHASE_ID = "70c"
PHASE_TITLE = "Articulated DRAG controller"
PHASE_STATUS = "landed"


def get_phase_metadata() -> Dict[str, Any]:
    """Return phase identification and status for this phase.

    Returns:
        Dict[str, Any]: Keys ``phase``, ``title``, ``status``, and ``spec_ref``.
    """
    return {
        "phase": PHASE_ID,
        "title": PHASE_TITLE,
        "status": PHASE_STATUS,
        "spec_ref": "specs/IA_FULL_SPEC_2026-05-10.md Phase 70c",
        "files": [
            "service/isaac_assist_service/multimodal/sub_phase_70c_articulated_drag_controller.py",
            "tests/test_sub_phase_70c_articulated_drag_controller.py",
        ],
    }


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass
class DRAGControllerConfig:
    """Tuning knobs for the PD drag controller.

    Attributes:
        kp: Proportional gain (N/m).
        kd: Derivative (damping) gain (N·s/m).
        max_force_N: Hard force ceiling applied every step.
        max_velocity_mps: Velocity clamp applied during simulation.
        ramp_time_s: Time over which the control output ramps from 0 to full.
            Prevents impulse spikes when the controller first activates.
        deadband_m: Position error smaller than this is treated as zero;
            avoids chatter near the target.
    """
    kp: float = 50.0
    kd: float = 8.0
    max_force_N: float = 200.0
    max_velocity_mps: float = 0.5
    ramp_time_s: float = 0.5
    deadband_m: float = 0.005


# ---------------------------------------------------------------------------
# State / command value objects
# ---------------------------------------------------------------------------

@dataclass
class ControlState:
    """Snapshot of the controlled axis at one instant.

    Attributes:
        position_m: Current joint position along the drag axis (metres).
        velocity_mps: Current joint velocity (metres per second).
        target_m: Desired joint position (metres).
        elapsed_s: Time elapsed since the controller was activated.
            Used by the ramp logic; the caller must advance this.
    """
    position_m: float
    velocity_mps: float = 0.0
    target_m: float = 0.0
    elapsed_s: float = 0.0


@dataclass
class ControlCommand:
    """Output of one controller step.

    Attributes:
        force_N: Force to apply along the joint axis (positive = towards target).
        ramp_factor: Fraction in [0, 1] used to scale the raw PD output.
        clipped: True when the raw PD force was larger than max_force_N.
        in_deadband: True when |error| < deadband; force_N is 0 in this case.
    """
    force_N: float
    ramp_factor: float
    clipped: bool
    in_deadband: bool


# ---------------------------------------------------------------------------
# Controller
# ---------------------------------------------------------------------------

class DRAGController:
    """PD drag controller with force ramp and safety limiter.

    The controller implements a standard PD law:

        pd_force = kp * (target - position) - kd * velocity

    with two modifications:
    * A linear ramp over ``config.ramp_time_s`` suppresses the initial impulse.
    * The output is hard-clipped to ``±config.max_force_N``.

    The controller is stateless between ``step()`` calls — the caller
    owns the :class:`ControlState` and advances ``elapsed_s``.
    """

    def __init__(self, config: Optional[DRAGControllerConfig] = None) -> None:
        """Initialise the DRAG controller with a config; defaults to ``DRAGControllerConfig()``."""
        self.config = config if config is not None else DRAGControllerConfig()

    # ------------------------------------------------------------------
    # Core step
    # ------------------------------------------------------------------

    def step(self, state: ControlState, dt: float) -> ControlCommand:
        """Compute one control step.

        Args:
            state: Current axis state. ``state.elapsed_s`` must be the
                cumulative time since activation (the caller advances it).
            dt: Time-step duration in seconds (used only to validate; the
                actual integration is the caller's responsibility when not
                using :meth:`simulate`).

        Returns:
            A :class:`ControlCommand` with the force to apply.
        """
        cfg = self.config
        error = state.target_m - state.position_m

        # Deadband — zero output near target to prevent chatter
        if abs(error) < cfg.deadband_m:
            return ControlCommand(
                force_N=0.0,
                ramp_factor=min(1.0, state.elapsed_s / cfg.ramp_time_s) if cfg.ramp_time_s > 0.0 else 1.0,
                clipped=False,
                in_deadband=True,
            )

        # PD law
        pd_force = cfg.kp * error - cfg.kd * state.velocity_mps

        # Ramp factor — linearly grows from 0 to 1 over ramp_time_s
        if cfg.ramp_time_s > 0.0:
            ramp = min(1.0, state.elapsed_s / cfg.ramp_time_s)
        else:
            ramp = 1.0

        force = pd_force * ramp

        # Safety clamp
        clipped = abs(force) > cfg.max_force_N
        force = max(-cfg.max_force_N, min(cfg.max_force_N, force))

        return ControlCommand(
            force_N=force,
            ramp_factor=ramp,
            clipped=clipped,
            in_deadband=False,
        )

    # ------------------------------------------------------------------
    # Simulation helpers
    # ------------------------------------------------------------------

    def simulate(
        self,
        initial: ControlState,
        target: float,
        dt: float = 0.01,
        steps: int = 200,
        mass_kg: float = 5.0,
    ) -> List[ControlState]:
        """Integrate the closed-loop dynamics from *initial* for *steps* ticks.

        Uses a simple Euler integrator:
            a = F / mass_kg
            v += a * dt          (then clipped to ±max_velocity_mps)
            p += v * dt

        Args:
            initial: Starting state.  ``target_m`` is ignored; ``target``
                argument is used instead.
            target: Desired final position in metres.
            dt: Integration step size in seconds.
            steps: Number of integration steps.
            mass_kg: Mass of the driven body in kg.

        Returns:
            List of :class:`ControlState` snapshots (including the initial
            state at index 0), length ``steps + 1``.
        """
        cfg = self.config
        trajectory: List[ControlState] = []

        pos = initial.position_m
        vel = initial.velocity_mps
        elapsed = initial.elapsed_s

        for _ in range(steps):
            state = ControlState(
                position_m=pos,
                velocity_mps=vel,
                target_m=target,
                elapsed_s=elapsed,
            )
            trajectory.append(state)

            cmd = self.step(state, dt)

            # Euler integration
            acc = cmd.force_N / mass_kg
            vel = vel + acc * dt
            # Velocity clamp
            vel = max(-cfg.max_velocity_mps, min(cfg.max_velocity_mps, vel))
            pos = pos + vel * dt
            elapsed += dt

        # Append final state
        trajectory.append(
            ControlState(
                position_m=pos,
                velocity_mps=vel,
                target_m=target,
                elapsed_s=elapsed,
            )
        )
        return trajectory

    def time_to_target(
        self,
        initial: ControlState,
        target: float,
        tolerance_m: float = 0.01,
        dt: float = 0.01,
        max_steps: int = 5000,
        mass_kg: float = 5.0,
    ) -> Optional[float]:
        """Return the simulated time (in seconds) to reach *target* within *tolerance_m*.

        Uses the same Euler integrator as :meth:`simulate`.

        Args:
            initial: Starting state.
            target: Desired position in metres.
            tolerance_m: Convergence criterion (|position - target| < tolerance_m).
            dt: Integration step size.
            max_steps: Give up after this many steps and return ``None``.
            mass_kg: Mass of the driven body in kg.

        Returns:
            Elapsed time in seconds when convergence is first achieved, or
            ``None`` if the target was not reached within *max_steps*.
        """
        cfg = self.config
        pos = initial.position_m
        vel = initial.velocity_mps
        elapsed = initial.elapsed_s

        for _ in range(max_steps):
            if abs(pos - target) < tolerance_m:
                return elapsed

            state = ControlState(
                position_m=pos,
                velocity_mps=vel,
                target_m=target,
                elapsed_s=elapsed,
            )
            cmd = self.step(state, dt)

            acc = cmd.force_N / mass_kg
            vel = vel + acc * dt
            vel = max(-cfg.max_velocity_mps, min(cfg.max_velocity_mps, vel))
            pos = pos + vel * dt
            elapsed += dt

        return None
