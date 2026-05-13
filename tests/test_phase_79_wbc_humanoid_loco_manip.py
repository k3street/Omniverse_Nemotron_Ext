"""Phase 79 SPEC/REWARD-DECOMP — pytest gate.

Gate: reward decomposes correctly, weights sum sensibly, terminations
trigger at boundaries.
"""
from __future__ import annotations

import math
import pytest

pytestmark = pytest.mark.l0

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

from service.isaac_assist_service.multimodal.wbc_humanoid_loco_manip import (
    DEFAULT_TARGET_HEIGHT_M,
    PHASE_STATUS,
    Vec3,
    WBCObservation,
    WBCRewardComponents,
    WBCRewardCalculator,
    WBCRewardWeights,
    WBCTerminationFlags,
    distance,
    get_phase_metadata,
)


def _obs(
    ee: Vec3 = (0.0, 0.0, DEFAULT_TARGET_HEIGHT_M),
    target: Vec3 = (0.0, 0.0, DEFAULT_TARGET_HEIGHT_M),
    base_pos: Vec3 = (0.0, 0.0, 1.0),
    base_vel: Vec3 = (0.0, 0.0, 0.0),
    base_ori: tuple = (0.0, 0.0, 0.0, 1.0),
    joint_pos: list | None = None,
    joint_vel: list | None = None,
    foot_left: bool = True,
    foot_right: bool = True,
    energy: float = 0.0,
    margins: list | None = None,
) -> WBCObservation:
    return WBCObservation(
        base_position=base_pos,
        base_velocity=base_vel,
        base_orientation_xyzw=base_ori,
        joint_positions=joint_pos if joint_pos is not None else [0.0] * 6,
        joint_velocities=joint_vel if joint_vel is not None else [0.0] * 6,
        end_effector_position=ee,
        target_position=target,
        foot_contact_left=foot_left,
        foot_contact_right=foot_right,
        energy_used_J=energy,
        joint_limit_margins=margins if margins is not None else [0.5] * 6,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestMetadata:
    def test_metadata_phase_number(self):
        md = get_phase_metadata()
        assert md["phase"] == 79

    def test_metadata_status_landed(self):
        md = get_phase_metadata()
        assert md["status"] == "landed"

    def test_metadata_has_layer(self):
        md = get_phase_metadata()
        assert md["layer"] == "SPEC/REWARD-DECOMP"


class TestDistance:
    def test_distance_345_triangle(self):
        a: Vec3 = (0.0, 0.0, 0.0)
        b: Vec3 = (3.0, 4.0, 0.0)
        assert math.isclose(distance(a, b), 5.0)

    def test_distance_same_point(self):
        p: Vec3 = (1.0, 2.0, 3.0)
        assert distance(p, p) == 0.0

    def test_distance_3d(self):
        # sqrt(1+4+9) = sqrt(14)
        a: Vec3 = (0.0, 0.0, 0.0)
        b: Vec3 = (1.0, 2.0, 3.0)
        assert math.isclose(distance(a, b), math.sqrt(14.0))


class TestDefaultWeightsSanity:
    def test_positive_weights_sum_positive(self):
        w = WBCRewardWeights()
        positive_sum = (
            w.goal_reach + w.base_stability + w.end_effector_tracking
            + w.foot_contact + w.balance + w.manipulation_smoothness
        )
        assert positive_sum > 0.0

    def test_energy_and_joint_limit_weights_negative(self):
        w = WBCRewardWeights()
        assert w.energy_penalty < 0.0
        assert w.joint_limit_penalty < 0.0


class TestComputeComponents:
    def test_ee_at_target_goal_reach_zero(self):
        calc = WBCRewardCalculator()
        obs = _obs(ee=(1.0, 2.0, 3.0), target=(1.0, 2.0, 3.0))
        c = calc.compute_components(obs)
        assert math.isclose(c.goal_reach, 0.0)

    def test_ee_at_target_tracking_zero(self):
        calc = WBCRewardCalculator()
        obs = _obs(ee=(1.0, 2.0, 3.0), target=(1.0, 2.0, 3.0))
        c = calc.compute_components(obs)
        assert math.isclose(c.end_effector_tracking, 0.0)

    def test_ee_1m_from_target_goal_reach_minus_one(self):
        calc = WBCRewardCalculator()
        obs = _obs(ee=(1.0, 0.0, 0.0), target=(0.0, 0.0, 0.0))
        c = calc.compute_components(obs)
        assert math.isclose(c.goal_reach, -1.0)

    def test_both_feet_on_foot_contact_plus_one(self):
        calc = WBCRewardCalculator()
        obs = _obs(foot_left=True, foot_right=True)
        c = calc.compute_components(obs)
        assert math.isclose(c.foot_contact, 1.0)

    def test_one_foot_on_foot_contact_zero(self):
        calc = WBCRewardCalculator()
        obs = _obs(foot_left=True, foot_right=False)
        c = calc.compute_components(obs)
        assert math.isclose(c.foot_contact, 0.0)

    def test_no_feet_foot_contact_minus_one(self):
        calc = WBCRewardCalculator()
        obs = _obs(foot_left=False, foot_right=False)
        c = calc.compute_components(obs)
        assert math.isclose(c.foot_contact, -1.0)

    def test_joint_at_limit_penalty(self):
        # One joint with margin 0.01 (< 0.05 threshold) → penalty = -1
        calc = WBCRewardCalculator()
        obs = _obs(margins=[0.01, 0.5, 0.5])
        c = calc.compute_components(obs)
        assert math.isclose(c.joint_limit_penalty, -1.0)

    def test_joint_limit_penalty_counts_all_close_joints(self):
        calc = WBCRewardCalculator()
        obs = _obs(margins=[0.01, 0.02, 0.5])  # 2 close joints
        c = calc.compute_components(obs)
        assert math.isclose(c.joint_limit_penalty, -2.0)

    def test_base_stability_stationary(self):
        calc = WBCRewardCalculator()
        obs = _obs(base_vel=(0.0, 0.0, 0.0))
        c = calc.compute_components(obs)
        assert math.isclose(c.base_stability, 0.0)

    def test_balance_zero_roll(self):
        calc = WBCRewardCalculator()
        obs = _obs(base_ori=(0.0, 0.0, 0.0, 1.0))
        c = calc.compute_components(obs)
        assert math.isclose(c.balance, 0.0)


class TestComputeTotal:
    def test_returns_float(self):
        calc = WBCRewardCalculator()
        obs = _obs()
        result = calc.compute_total(obs)
        assert isinstance(result, float)

    def test_weighted_differently_changes_result(self):
        default_calc = WBCRewardCalculator()
        heavy_ee_calc = WBCRewardCalculator(
            weights=WBCRewardWeights(end_effector_tracking=10.0)
        )
        obs = _obs(ee=(2.0, 0.0, 0.0), target=(0.0, 0.0, 0.0))
        assert default_calc.compute_total(obs) != heavy_ee_calc.compute_total(obs)


class TestComputeTermination:
    def test_fallen_when_base_below_fall_height(self):
        calc = WBCRewardCalculator(fall_height_m=0.4)
        obs = _obs(base_pos=(0.0, 0.0, 0.3))
        flags = calc.compute_termination(obs, step=0)
        assert flags.fallen is True
        assert flags.terminated is True

    def test_not_fallen_when_base_above_fall_height(self):
        calc = WBCRewardCalculator(fall_height_m=0.4)
        obs = _obs(base_pos=(0.0, 0.0, 1.0))
        flags = calc.compute_termination(obs, step=0)
        assert flags.fallen is False

    def test_target_reached_when_ee_within_tolerance(self):
        calc = WBCRewardCalculator(target_tolerance_m=0.05)
        obs = _obs(ee=(0.03, 0.0, 0.0), target=(0.0, 0.0, 0.0))
        flags = calc.compute_termination(obs, step=0)
        assert flags.target_reached is True
        assert flags.terminated is True

    def test_target_not_reached_when_far(self):
        calc = WBCRewardCalculator(target_tolerance_m=0.05)
        obs = _obs(ee=(1.0, 0.0, 0.0), target=(0.0, 0.0, 0.0))
        flags = calc.compute_termination(obs, step=0)
        assert flags.target_reached is False

    def test_max_episode_steps_triggers_at_boundary(self):
        calc = WBCRewardCalculator()
        obs = _obs()
        flags = calc.compute_termination(obs, step=1000, max_steps=1000)
        assert flags.max_episode_steps is True
        assert flags.terminated is True

    def test_max_episode_steps_not_triggered_before_boundary(self):
        calc = WBCRewardCalculator()
        obs = _obs()
        flags = calc.compute_termination(obs, step=999, max_steps=1000)
        assert flags.max_episode_steps is False

    def test_energy_budget_exceeded(self):
        calc = WBCRewardCalculator()
        obs = _obs(energy=5001.0)
        flags = calc.compute_termination(obs, step=0, energy_budget=5000.0)
        assert flags.energy_budget_exceeded is True
        assert flags.terminated is True

    def test_energy_within_budget(self):
        calc = WBCRewardCalculator()
        obs = _obs(energy=4999.0)
        flags = calc.compute_termination(obs, step=0, energy_budget=5000.0)
        assert flags.energy_budget_exceeded is False

    def test_clean_state_not_terminated(self):
        calc = WBCRewardCalculator()
        obs = _obs(
            ee=(2.0, 0.0, 1.0),    # not at target
            target=(0.0, 0.0, 1.0),
            base_pos=(0.0, 0.0, 1.0),  # upright
            energy=0.0,
        )
        flags = calc.compute_termination(obs, step=0, max_steps=1000, energy_budget=5000.0)
        assert flags.terminated is False


class TestSummary:
    def test_summary_returns_dict(self):
        calc = WBCRewardCalculator()
        obs = _obs()
        result = calc.summary(obs, step=5)
        assert isinstance(result, dict)

    def test_summary_has_expected_keys(self):
        calc = WBCRewardCalculator()
        obs = _obs()
        result = calc.summary(obs, step=5)
        assert "total_reward" in result
        assert "components" in result
        assert "termination" in result
        assert "metrics" in result

    def test_summary_step_recorded(self):
        calc = WBCRewardCalculator()
        obs = _obs()
        result = calc.summary(obs, step=42)
        assert result["step"] == 42

    def test_summary_termination_combines_correctly(self):
        calc = WBCRewardCalculator(fall_height_m=0.4)
        obs = _obs(base_pos=(0.0, 0.0, 0.1))
        result = calc.summary(obs, step=0)
        assert result["termination"]["fallen"] is True
        assert result["termination"]["terminated"] is True
