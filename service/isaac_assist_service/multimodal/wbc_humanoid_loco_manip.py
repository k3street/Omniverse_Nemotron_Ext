"""Phase 79 SPEC/REWARD-DECOMP — Whole-body control: humanoid loco-manipulation.

Reward-function decomposition, objective weighting, and termination conditions
for a humanoid whole-body controller.  Pure Python math; no MuJoCo / IsaacLab
runtime dependency.

Per specs/IA_FULL_SPEC_2026-05-10.md Phase 79.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_TARGET_HEIGHT_M: float = 1.0  # typical humanoid hand operating height
PHASE_STATUS: str = "landed"

# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------

Vec3 = Tuple[float, float, float]


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def distance(a: Vec3, b: Vec3) -> float:
    """Euclidean distance between two 3-D points."""
    return math.sqrt(
        (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2
    )


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class WBCRewardComponents:
    """Raw, un-weighted reward signal per objective."""
    goal_reach: float
    base_stability: float
    end_effector_tracking: float
    energy_penalty: float
    joint_limit_penalty: float
    foot_contact: float
    balance: float
    manipulation_smoothness: float


@dataclass
class WBCRewardWeights:
    """Scaling weights applied to each reward component.

    Positive weights encourage, negative weights penalise.
    """
    goal_reach: float = 4.0
    base_stability: float = 2.0
    end_effector_tracking: float = 3.0
    energy_penalty: float = -0.05
    joint_limit_penalty: float = -0.5
    foot_contact: float = 1.0
    balance: float = 1.5
    manipulation_smoothness: float = 0.5


@dataclass
class WBCObservation:
    """Single-timestep observation for the whole-body controller."""
    base_position: Vec3
    base_velocity: Vec3
    base_orientation_xyzw: Tuple[float, float, float, float]
    joint_positions: List[float]
    joint_velocities: List[float]
    end_effector_position: Vec3
    target_position: Vec3
    foot_contact_left: bool = False
    foot_contact_right: bool = False
    energy_used_J: float = 0.0
    joint_limit_margins: List[float] = field(default_factory=list)


@dataclass
class WBCTerminationFlags:
    """Boolean flags indicating why (or whether) an episode ended."""
    fallen: bool
    out_of_workspace: bool
    target_reached: bool
    energy_budget_exceeded: bool
    max_episode_steps: bool
    terminated: bool


# ---------------------------------------------------------------------------
# Core calculator
# ---------------------------------------------------------------------------

class WBCRewardCalculator:
    """Computes decomposed rewards and termination signals for a WBC episode.

    Parameters
    ----------
    weights:
        Per-component scaling weights.  Defaults to ``WBCRewardWeights()``.
    target_tolerance_m:
        End-effector-to-target distance (m) below which the task is
        considered solved.
    fall_height_m:
        Base CoM height (m) below which the robot is considered fallen.
    """

    def __init__(
        self,
        weights: Optional[WBCRewardWeights] = None,
        target_tolerance_m: float = 0.05,
        fall_height_m: float = 0.4,
    ) -> None:
        self.weights = weights if weights is not None else WBCRewardWeights()
        self.target_tolerance_m = target_tolerance_m
        self.fall_height_m = fall_height_m

    # ------------------------------------------------------------------
    # Component decomposition
    # ------------------------------------------------------------------

    def compute_components(self, obs: WBCObservation) -> WBCRewardComponents:
        """Return un-weighted reward components for *obs*.

        All individual signals are designed so that **higher is better**
        (penalties are expressed as negative values).
        """
        # Goal-reach: negative distance; 0 when at target.
        dist_ee_target = distance(obs.end_effector_position, obs.target_position)
        goal_reach = -dist_ee_target

        # Base stability: penalise lateral (XY) speed of the torso.
        vx, vy, _vz = obs.base_velocity
        base_stability = -math.sqrt(vx ** 2 + vy ** 2)

        # End-effector tracking: separate from goal_reach — allows different
        # weighting at the aggregate level.
        end_effector_tracking = -dist_ee_target

        # Energy penalty: each joule consumed is a cost.
        energy_penalty = -obs.energy_used_J

        # Joint-limit penalty: count joints within 5 cm of their limit
        # (margin < 0.05 rad / normalised unit).
        close_to_limit = sum(
            1 for m in obs.joint_limit_margins if m < 0.05
        )
        joint_limit_penalty = float(-close_to_limit)

        # Foot contact: reward = #contacts - 1; both feet on floor gives +1,
        # one foot gives 0, no feet gives -1.
        foot_contact = float(obs.foot_contact_left) + float(obs.foot_contact_right) - 1.0

        # Balance proxy: penalise roll (quaternion x component magnitude).
        balance = -abs(obs.base_orientation_xyzw[0])

        # Manipulation smoothness: penalise mean absolute joint velocity.
        n_jv = len(obs.joint_velocities)
        if n_jv == 0:
            manipulation_smoothness = 0.0
        else:
            manipulation_smoothness = -sum(abs(v) for v in obs.joint_velocities) / n_jv

        return WBCRewardComponents(
            goal_reach=goal_reach,
            base_stability=base_stability,
            end_effector_tracking=end_effector_tracking,
            energy_penalty=energy_penalty,
            joint_limit_penalty=joint_limit_penalty,
            foot_contact=foot_contact,
            balance=balance,
            manipulation_smoothness=manipulation_smoothness,
        )

    # ------------------------------------------------------------------
    # Weighted total
    # ------------------------------------------------------------------

    def compute_total(self, obs: WBCObservation) -> float:
        """Return the scalar weighted-sum reward for *obs*."""
        c = self.compute_components(obs)
        w = self.weights
        return (
            w.goal_reach * c.goal_reach
            + w.base_stability * c.base_stability
            + w.end_effector_tracking * c.end_effector_tracking
            + w.energy_penalty * c.energy_penalty
            + w.joint_limit_penalty * c.joint_limit_penalty
            + w.foot_contact * c.foot_contact
            + w.balance * c.balance
            + w.manipulation_smoothness * c.manipulation_smoothness
        )

    # ------------------------------------------------------------------
    # Termination
    # ------------------------------------------------------------------

    def compute_termination(
        self,
        obs: WBCObservation,
        step: int,
        max_steps: int = 1000,
        energy_budget: float = 5000.0,
    ) -> WBCTerminationFlags:
        """Evaluate termination conditions.

        Parameters
        ----------
        obs:
            Current observation.
        step:
            Current step index (0-based).
        max_steps:
            Episode horizon.
        energy_budget:
            Maximum cumulative energy (J) before forced termination.
        """
        fallen = obs.base_position[2] < self.fall_height_m

        bx, by, _bz = obs.base_position
        out_of_workspace = math.sqrt(bx ** 2 + by ** 2) > 5.0

        target_reached = (
            distance(obs.end_effector_position, obs.target_position)
            < self.target_tolerance_m
        )

        energy_budget_exceeded = obs.energy_used_J > energy_budget

        max_episode_steps = step >= max_steps

        terminated = (
            fallen
            or out_of_workspace
            or target_reached
            or energy_budget_exceeded
            or max_episode_steps
        )

        return WBCTerminationFlags(
            fallen=fallen,
            out_of_workspace=out_of_workspace,
            target_reached=target_reached,
            energy_budget_exceeded=energy_budget_exceeded,
            max_episode_steps=max_episode_steps,
            terminated=terminated,
        )

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------

    def summary(self, obs: WBCObservation, step: int) -> Dict[str, Any]:
        """Return a combined reward + termination + key-metrics dict."""
        components = self.compute_components(obs)
        total = self.compute_total(obs)
        term = self.compute_termination(obs, step)

        return {
            "step": step,
            "total_reward": total,
            "components": {
                "goal_reach": components.goal_reach,
                "base_stability": components.base_stability,
                "end_effector_tracking": components.end_effector_tracking,
                "energy_penalty": components.energy_penalty,
                "joint_limit_penalty": components.joint_limit_penalty,
                "foot_contact": components.foot_contact,
                "balance": components.balance,
                "manipulation_smoothness": components.manipulation_smoothness,
            },
            "termination": {
                "fallen": term.fallen,
                "out_of_workspace": term.out_of_workspace,
                "target_reached": term.target_reached,
                "energy_budget_exceeded": term.energy_budget_exceeded,
                "max_episode_steps": term.max_episode_steps,
                "terminated": term.terminated,
            },
            "metrics": {
                "ee_to_target_m": distance(obs.end_effector_position, obs.target_position),
                "base_height_m": obs.base_position[2],
                "energy_used_J": obs.energy_used_J,
            },
        }


# ---------------------------------------------------------------------------
# Phase metadata
# ---------------------------------------------------------------------------

def get_phase_metadata() -> Dict[str, Any]:
    """Return phase identification and status for this phase.

    Returns:
        Dict[str, Any]: Keys ``phase``, ``title``, ``status``, and ``spec_ref``.
    """
    return {
        "phase": 79,
        "title": "Whole-body control: humanoid loco-manipulation",
        "status": PHASE_STATUS,
        "layer": "SPEC/REWARD-DECOMP",
        "spec_ref": "specs/IA_FULL_SPEC_2026-05-10.md Phase 79",
    }
