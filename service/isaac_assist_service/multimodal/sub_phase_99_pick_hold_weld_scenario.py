"""Phase 99 — Pick-hold-weld scenario end-to-end (SPEC/SCENARIO layer).

Pure Python state machine + scoring rubric for the pick-hold-weld
multi-arm scenario.  This module does NOT require a running Kit instance
or GR00T.  The opus-runtime execution layer wraps this module.

Per specs/IA_FULL_SPEC_2026-05-10.md Phase 99.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional, Tuple

# ---------------------------------------------------------------------------
# Phase metadata
# ---------------------------------------------------------------------------

PHASE_ID = "99"
PHASE_TITLE = "Pick-hold-weld scenario end-to-end"
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
        "spec_ref": "specs/IA_FULL_SPEC_2026-05-10.md Phase 99",
    }


# ---------------------------------------------------------------------------
# Type aliases / Literals
# ---------------------------------------------------------------------------

ScenarioPhase = Literal[
    "init",
    "approach",
    "pick",
    "lift_and_hold",
    "weld_seam",
    "release",
    "complete",
    "failed",
]

RobotRole = Literal[
    "picker_arm",
    "holder_arm",
    "welder_arm",
]


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class RobotAssignment:
    """Associates a robot role with a specific robot in the scene."""

    role: RobotRole
    robot_id: str
    base_pose: Tuple[float, float, float] = (0.0, 0.0, 0.0)


@dataclass
class WeldSeamSpec:
    """Specification for the weld seam trajectory."""

    start_xyz: Tuple[float, float, float]
    end_xyz: Tuple[float, float, float]
    speed_mm_per_s: float = 5.0
    weave_amplitude_mm: float = 1.5
    weave_frequency_hz: float = 2.0


@dataclass
class ScenarioConfig:
    """Top-level configuration for a pick-hold-weld scenario."""

    name: str = "pick_hold_weld_default"
    assignments: List[RobotAssignment] = field(default_factory=list)
    seam: Optional[WeldSeamSpec] = None
    max_duration_s: float = 60.0
    success_force_window_N: Tuple[float, float] = (5.0, 50.0)


@dataclass
class ScenarioState:
    """Runtime state of an active pick-hold-weld scenario."""

    phase: ScenarioPhase = "init"
    phase_started_at: float = 0.0
    elapsed_s: float = 0.0
    hold_force_N: float = 0.0
    seam_progress_pct: float = 0.0
    errors: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Default scenario instance
# ---------------------------------------------------------------------------

DEFAULT_SCENARIO = ScenarioConfig(
    name="pick_hold_weld_default",
    assignments=[
        RobotAssignment(
            role="picker_arm",
            robot_id="franka_01",
            base_pose=(0.0, -0.5, 0.0),
        ),
        RobotAssignment(
            role="holder_arm",
            robot_id="ur10_01",
            base_pose=(0.0, 0.5, 0.0),
        ),
        RobotAssignment(
            role="welder_arm",
            robot_id="ur10_02",
            base_pose=(1.0, 0.0, 0.0),
        ),
    ],
    seam=WeldSeamSpec(
        start_xyz=(0.3, 0.0, 0.5),
        end_xyz=(0.6, 0.0, 0.5),
        speed_mm_per_s=5.0,
        weave_amplitude_mm=1.5,
        weave_frequency_hz=2.0,
    ),
    max_duration_s=60.0,
    success_force_window_N=(5.0, 50.0),
)


# ---------------------------------------------------------------------------
# State machine
# ---------------------------------------------------------------------------

# Happy-path ordered phases (excluding "failed")
_HAPPY_PATH: List[ScenarioPhase] = [
    "init",
    "approach",
    "pick",
    "lift_and_hold",
    "weld_seam",
    "release",
    "complete",
]


class PickHoldWeldStateMachine:
    """State machine that drives a pick-hold-weld scenario through its phases."""

    # Legal forward transitions (any phase may also go to "failed")
    LEGAL_TRANSITIONS: Dict[ScenarioPhase, set] = {
        "init": {"approach", "failed"},
        "approach": {"pick", "failed"},
        "pick": {"lift_and_hold", "failed"},
        "lift_and_hold": {"weld_seam", "failed"},
        "weld_seam": {"release", "failed"},
        "release": {"complete", "failed"},
        "complete": set(),
        "failed": set(),
    }

    def __init__(self, config: ScenarioConfig) -> None:
        self.config = config
        self.state = ScenarioState()

    # ------------------------------------------------------------------
    def advance(self) -> ScenarioPhase:
        """Transition to the next phase on the happy path and return it."""
        current = self.state.phase
        idx = _HAPPY_PATH.index(current)
        if idx >= len(_HAPPY_PATH) - 1:
            raise ValueError(
                f"No next happy-path phase after '{current}'"
            )
        next_phase: ScenarioPhase = _HAPPY_PATH[idx + 1]
        self.transition(next_phase)
        return self.state.phase

    # ------------------------------------------------------------------
    def transition(self, target: ScenarioPhase) -> None:
        """Validate and apply a phase transition.

        Raises ValueError if the transition is not legal.
        """
        current = self.state.phase
        allowed = self.LEGAL_TRANSITIONS.get(current, set())
        if target not in allowed:
            raise ValueError(
                f"Illegal transition: '{current}' -> '{target}'. "
                f"Allowed: {sorted(allowed)}"
            )
        self.state.phase = target

    # ------------------------------------------------------------------
    def fail(self, reason: str) -> None:
        """Transition to 'failed' and record the reason in state.errors."""
        self.state.errors.append(reason)
        # Terminal phases cannot be re-failed — avoid double-transition
        if self.state.phase not in ("complete", "failed"):
            self.state.phase = "failed"

    # ------------------------------------------------------------------
    def update_seam_progress(self, pct: float) -> None:
        """Update seam completion percentage (0–100)."""
        self.state.seam_progress_pct = float(pct)

    # ------------------------------------------------------------------
    def update_hold_force(self, force_N: float) -> None:
        """Update hold force.

        During weld_seam phase, logs an error if the force is outside
        the configured success_force_window_N.
        """
        self.state.hold_force_N = float(force_N)
        if self.state.phase == "weld_seam":
            lo, hi = self.config.success_force_window_N
            if force_N < lo or force_N > hi:
                self.state.errors.append(
                    f"hold_force {force_N:.2f} N outside window "
                    f"[{lo}, {hi}] during weld_seam"
                )

    # ------------------------------------------------------------------
    def is_complete(self) -> bool:
        """Return True only when the scenario has reached 'complete'."""
        return self.state.phase == "complete"

    # ------------------------------------------------------------------
    def is_failed(self) -> bool:
        """Return True when the scenario is in the 'failed' terminal state."""
        return self.state.phase == "failed"


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


def score_scenario(
    state: ScenarioState,
    config: ScenarioConfig,
) -> Dict[str, Any]:
    """Compute a deterministic score for a finished (or aborted) scenario.

    100 points possible, broken into four independent criteria:

    - 30 pts  reaching 'complete' phase
    - 30 pts  seam_progress == 100 %
    - 20 pts  hold_force within window throughout (no force-window errors)
    - 20 pts  elapsed_s <= max_duration_s

    Returns:
        {
            "score": float,
            "max_score": float,
            "success": bool,
            "breakdown": {
                "phase_complete": float,    (0 or 30)
                "seam_complete": float,     (0 or 30)
                "force_in_window": float,   (0 or 20)
                "within_time": float,       (0 or 20)
            }
        }
    """
    # --- criterion 1: reached complete
    phase_complete = 30.0 if state.phase == "complete" else 0.0

    # --- criterion 2: seam at 100 %
    seam_complete = 30.0 if state.seam_progress_pct >= 100.0 else 0.0

    # --- criterion 3: no force-window violations (proxy: no force errors in errors list)
    force_errors = [
        e for e in state.errors
        if "outside window" in e and "weld_seam" in e
    ]
    force_in_window = 20.0 if not force_errors else 0.0

    # --- criterion 4: within time budget
    within_time = 20.0 if state.elapsed_s <= config.max_duration_s else 0.0

    total = phase_complete + seam_complete + force_in_window + within_time
    max_score = 100.0
    success = total >= max_score

    return {
        "score": total,
        "max_score": max_score,
        "success": success,
        "breakdown": {
            "phase_complete": phase_complete,
            "seam_complete": seam_complete,
            "force_in_window": force_in_window,
            "within_time": within_time,
        },
    }
