"""Tests for Phase 79b — IsaacLab 2.3 G1 locomanipulation env + WBC integration."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.l0


# ---------------------------------------------------------------------------
# Import helpers
# ---------------------------------------------------------------------------

def _import():
    from service.isaac_assist_service.multimodal.sub_phase_79b_isaaclab_g1_locomanip import (
        DEFAULT_CURRICULUM,
        G1ActionSpec,
        G1CurriculumScheduler,
        G1EnvConfigValidator,
        G1HardwareSpec,
        G1ObservationSpec,
        IsaacLabG1EnvConfig,
        LocomanipCurriculumStage,
        get_phase_metadata,
        make_default_g1_env_config,
    )
    return (
        DEFAULT_CURRICULUM,
        G1ActionSpec,
        G1CurriculumScheduler,
        G1EnvConfigValidator,
        G1HardwareSpec,
        G1ObservationSpec,
        IsaacLabG1EnvConfig,
        LocomanipCurriculumStage,
        get_phase_metadata,
        make_default_g1_env_config,
    )


# ---------------------------------------------------------------------------
# 1. Metadata
# ---------------------------------------------------------------------------

class TestMetadata:
    def test_phase_id_and_status(self):
        (
            _,_,_,_,_,_,_,_,
            get_phase_metadata,
            _,
        ) = _import()
        md = get_phase_metadata()
        assert md["phase"] == "79b"
        assert md["status"] == "landed"
        assert "spec_ref" in md

    def test_phase_title_non_empty(self):
        (
            _,_,_,_,_,_,_,_,
            get_phase_metadata,
            _,
        ) = _import()
        md = get_phase_metadata()
        assert len(md["title"]) > 0


# ---------------------------------------------------------------------------
# 2. G1HardwareSpec defaults
# ---------------------------------------------------------------------------

class TestG1HardwareSpec:
    def test_defaults_reasonable(self):
        (
            _,_,_,_,
            G1HardwareSpec,
            _,_,_,_,_,
        ) = _import()
        hw = G1HardwareSpec()
        assert hw.joint_count == 23
        assert hw.arm_dof_per_side == 5
        assert hw.leg_dof_per_side == 6
        assert hw.torso_dof == 1
        assert hw.weight_kg == pytest.approx(35.0)
        assert hw.height_m == pytest.approx(1.32)
        assert hw.has_dexterous_hands is False

    def test_total_arm_dof(self):
        (
            _,_,_,_,
            G1HardwareSpec,
            _,_,_,_,_,
        ) = _import()
        hw = G1HardwareSpec()
        assert hw.total_arm_dof() == hw.arm_dof_per_side * 2

    def test_total_leg_dof(self):
        (
            _,_,_,_,
            G1HardwareSpec,
            _,_,_,_,_,
        ) = _import()
        hw = G1HardwareSpec()
        assert hw.total_leg_dof() == hw.leg_dof_per_side * 2


# ---------------------------------------------------------------------------
# 3. G1ObservationSpec — observation_dim computed correctly
# ---------------------------------------------------------------------------

class TestG1ObservationSpec:
    def test_observation_dim_computed(self):
        (
            _,_,_,_,_,
            G1ObservationSpec,
            _,_,_,_,
        ) = _import()
        obs = G1ObservationSpec(proprioceptive_dim=52, exteroceptive_dim=187)
        assert obs.observation_dim == 52 + 187 + 3  # command_dim defaults to 3

    def test_observation_dim_custom_command(self):
        (
            _,_,_,_,_,
            G1ObservationSpec,
            _,_,_,_,
        ) = _import()
        obs = G1ObservationSpec(proprioceptive_dim=10, exteroceptive_dim=20, command_dim=5)
        assert obs.observation_dim == 35

    def test_observation_dim_zero_exteroceptive(self):
        (
            _,_,_,_,_,
            G1ObservationSpec,
            _,_,_,_,
        ) = _import()
        obs = G1ObservationSpec(proprioceptive_dim=50, exteroceptive_dim=0, command_dim=3)
        assert obs.observation_dim == 53


# ---------------------------------------------------------------------------
# 4. G1ActionSpec — action_dim computed correctly
# ---------------------------------------------------------------------------

class TestG1ActionSpec:
    def test_action_dim_defaults(self):
        (
            _,
            G1ActionSpec,
            _,_,_,_,_,_,_,_,
        ) = _import()
        act = G1ActionSpec()
        assert act.joint_target_dim == 23
        assert act.gripper_dim == 2
        assert act.action_dim == 25

    def test_action_dim_custom(self):
        (
            _,
            G1ActionSpec,
            _,_,_,_,_,_,_,_,
        ) = _import()
        act = G1ActionSpec(joint_target_dim=10, gripper_dim=4)
        assert act.action_dim == 14


# ---------------------------------------------------------------------------
# 5. DEFAULT_CURRICULUM size and ordering
# ---------------------------------------------------------------------------

class TestDefaultCurriculum:
    def test_at_least_5_stages(self):
        (
            DEFAULT_CURRICULUM,
            _,_,_,_,_,_,_,_,_,
        ) = _import()
        assert len(DEFAULT_CURRICULUM) >= 5

    def test_stage_ids_are_0_to_n_minus_1(self):
        (
            DEFAULT_CURRICULUM,
            _,_,_,_,_,_,_,_,_,
        ) = _import()
        ids = [s.stage_id for s in DEFAULT_CURRICULUM]
        assert ids == list(range(len(DEFAULT_CURRICULUM)))

    def test_first_stage_is_balance_only(self):
        (
            DEFAULT_CURRICULUM,
            _,_,_,_,_,_,_,_,_,
        ) = _import()
        assert DEFAULT_CURRICULUM[0].name == "balance_only"

    def test_balance_only_has_gravity_scale_range(self):
        (
            DEFAULT_CURRICULUM,
            _,_,_,_,_,_,_,_,_,
        ) = _import()
        s = DEFAULT_CURRICULUM[0]
        assert "gravity_scale" in s.ranges

    def test_loco_manip_basic_has_target_height(self):
        (
            DEFAULT_CURRICULUM,
            _,_,_,_,_,_,_,_,_,
        ) = _import()
        stage = next(s for s in DEFAULT_CURRICULUM if s.name == "loco_manip_basic")
        assert "target_height_m" in stage.ranges
        lo, hi = stage.ranges["target_height_m"]
        assert lo < hi


# ---------------------------------------------------------------------------
# 6. G1EnvConfigValidator
# ---------------------------------------------------------------------------

class TestG1EnvConfigValidator:
    def _make_valid_cfg(self):
        (
            DEFAULT_CURRICULUM,
            G1ActionSpec,
            _,
            G1EnvConfigValidator,
            G1HardwareSpec,
            G1ObservationSpec,
            IsaacLabG1EnvConfig,
            _,_,_,
        ) = _import()
        return IsaacLabG1EnvConfig(
            num_envs=4096,
            episode_length_s=20.0,
            sim_dt=0.005,
            decimation=4,
            hardware=G1HardwareSpec(),
            curriculum=list(DEFAULT_CURRICULUM),
        )

    def test_valid_config_returns_empty_list(self):
        (
            _,_,_,
            G1EnvConfigValidator,
            _,_,_,_,_,_,
        ) = _import()
        cfg = self._make_valid_cfg()
        validator = G1EnvConfigValidator()
        issues = validator.validate(cfg)
        assert issues == []

    def test_num_envs_zero_returns_issue(self):
        (
            _,_,_,
            G1EnvConfigValidator,
            _,_,_,_,_,_,
        ) = _import()
        cfg = self._make_valid_cfg()
        cfg.num_envs = 0
        issues = G1EnvConfigValidator().validate(cfg)
        assert any("num_envs" in i for i in issues)

    def test_num_envs_negative_returns_issue(self):
        (
            _,_,_,
            G1EnvConfigValidator,
            _,_,_,_,_,_,
        ) = _import()
        cfg = self._make_valid_cfg()
        cfg.num_envs = -1
        issues = G1EnvConfigValidator().validate(cfg)
        assert any("num_envs" in i for i in issues)

    def test_sim_dt_zero_returns_issue(self):
        (
            _,_,_,
            G1EnvConfigValidator,
            _,_,_,_,_,_,
        ) = _import()
        cfg = self._make_valid_cfg()
        cfg.sim_dt = 0.0
        issues = G1EnvConfigValidator().validate(cfg)
        assert any("sim_dt" in i for i in issues)

    def test_sim_dt_too_large_returns_issue(self):
        (
            _,_,_,
            G1EnvConfigValidator,
            _,_,_,_,_,_,
        ) = _import()
        cfg = self._make_valid_cfg()
        cfg.sim_dt = 0.05  # boundary — must be *less than* 0.05
        issues = G1EnvConfigValidator().validate(cfg)
        assert any("sim_dt" in i for i in issues)

    def test_unordered_curriculum_returns_issue(self):
        (
            DEFAULT_CURRICULUM,
            _,_,
            G1EnvConfigValidator,
            _,_,_,
            LocomanipCurriculumStage,
            _,_,
        ) = _import()
        from service.isaac_assist_service.multimodal.sub_phase_79b_isaaclab_g1_locomanip import (
            IsaacLabG1EnvConfig, G1HardwareSpec,
        )
        bad_stages = [
            LocomanipCurriculumStage(stage_id=1, name="a", min_episodes=100, success_rate_threshold=0.5),
            LocomanipCurriculumStage(stage_id=0, name="b", min_episodes=100, success_rate_threshold=0.5),
        ]
        cfg = IsaacLabG1EnvConfig(
            hardware=G1HardwareSpec(),
            curriculum=bad_stages,
        )
        issues = G1EnvConfigValidator().validate(cfg)
        assert any("stage_id" in i or "curriculum" in i for i in issues)

    def test_episode_length_zero_returns_issue(self):
        (
            _,_,_,
            G1EnvConfigValidator,
            _,_,_,_,_,_,
        ) = _import()
        cfg = self._make_valid_cfg()
        cfg.episode_length_s = 0.0
        issues = G1EnvConfigValidator().validate(cfg)
        assert any("episode_length_s" in i for i in issues)


# ---------------------------------------------------------------------------
# 7. G1CurriculumScheduler
# ---------------------------------------------------------------------------

class TestG1CurriculumScheduler:
    def _make_mini_stages(self):
        from service.isaac_assist_service.multimodal.sub_phase_79b_isaaclab_g1_locomanip import (
            LocomanipCurriculumStage,
        )
        return [
            LocomanipCurriculumStage(
                stage_id=0, name="stage0", min_episodes=100, success_rate_threshold=0.5
            ),
            LocomanipCurriculumStage(
                stage_id=1, name="stage1", min_episodes=200, success_rate_threshold=0.4
            ),
        ]

    def test_initial_stage_idx_is_0(self):
        (
            _,_,
            G1CurriculumScheduler,
            _,_,_,_,_,_,_,
        ) = _import()
        sched = G1CurriculumScheduler(self._make_mini_stages())
        assert sched.current_stage_idx == 0

    def test_advance_false_when_episodes_too_low(self):
        (
            _,_,
            G1CurriculumScheduler,
            _,_,_,_,_,_,_,
        ) = _import()
        sched = G1CurriculumScheduler(self._make_mini_stages())
        advanced = sched.advance(episodes_completed=50, recent_success_rate=0.9)
        assert advanced is False
        assert sched.current_stage_idx == 0

    def test_advance_false_when_success_rate_too_low(self):
        (
            _,_,
            G1CurriculumScheduler,
            _,_,_,_,_,_,_,
        ) = _import()
        sched = G1CurriculumScheduler(self._make_mini_stages())
        advanced = sched.advance(episodes_completed=200, recent_success_rate=0.1)
        assert advanced is False
        assert sched.current_stage_idx == 0

    def test_advance_true_when_both_conditions_met(self):
        (
            _,_,
            G1CurriculumScheduler,
            _,_,_,_,_,_,_,
        ) = _import()
        sched = G1CurriculumScheduler(self._make_mini_stages())
        advanced = sched.advance(episodes_completed=100, recent_success_rate=0.5)
        assert advanced is True
        assert sched.current_stage_idx == 1

    def test_advance_returns_false_when_both_conditions_met_but_already_at_last_stage(self):
        (
            _,_,
            G1CurriculumScheduler,
            _,_,_,_,_,_,_,
        ) = _import()
        stages = self._make_mini_stages()
        sched = G1CurriculumScheduler(stages)
        # Advance past all stages
        sched.advance(episodes_completed=100, recent_success_rate=0.9)
        sched.advance(episodes_completed=200, recent_success_rate=0.9)
        # Now complete — further advance should be False
        result = sched.advance(episodes_completed=999, recent_success_rate=1.0)
        assert result is False

    def test_is_complete_false_initially(self):
        (
            _,_,
            G1CurriculumScheduler,
            _,_,_,_,_,_,_,
        ) = _import()
        sched = G1CurriculumScheduler(self._make_mini_stages())
        assert sched.is_complete() is False

    def test_is_complete_true_after_all_stages_done(self):
        (
            _,_,
            G1CurriculumScheduler,
            _,_,_,_,_,_,_,
        ) = _import()
        sched = G1CurriculumScheduler(self._make_mini_stages())
        sched.advance(episodes_completed=100, recent_success_rate=0.9)
        assert sched.is_complete() is False
        sched.advance(episodes_completed=200, recent_success_rate=0.9)
        assert sched.is_complete() is True

    def test_reset_returns_to_stage_0(self):
        (
            _,_,
            G1CurriculumScheduler,
            _,_,_,_,_,_,_,
        ) = _import()
        sched = G1CurriculumScheduler(self._make_mini_stages())
        sched.advance(episodes_completed=100, recent_success_rate=0.9)
        assert sched.current_stage_idx == 1
        sched.reset()
        assert sched.current_stage_idx == 0

    def test_current_stage_returns_correct_object(self):
        (
            _,_,
            G1CurriculumScheduler,
            _,_,_,_,_,_,_,
        ) = _import()
        stages = self._make_mini_stages()
        sched = G1CurriculumScheduler(stages)
        assert sched.current_stage().name == "stage0"
        sched.advance(episodes_completed=100, recent_success_rate=0.9)
        assert sched.current_stage().name == "stage1"

    def test_default_curriculum_used_when_no_stages_given(self):
        (
            DEFAULT_CURRICULUM,
            _,
            G1CurriculumScheduler,
            _,_,_,_,_,_,_,
        ) = _import()
        sched = G1CurriculumScheduler()
        assert sched.current_stage().name == DEFAULT_CURRICULUM[0].name


# ---------------------------------------------------------------------------
# 8. make_default_g1_env_config
# ---------------------------------------------------------------------------

class TestMakeDefaultG1EnvConfig:
    def test_returns_populated_config(self):
        (
            _,_,_,_,_,_,_,_,_,
            make_default_g1_env_config,
        ) = _import()
        cfg = make_default_g1_env_config()
        assert cfg.env_name == "Isaac-G1-LocoManip-v0"
        assert cfg.observation is not None
        assert cfg.action is not None
        assert len(cfg.curriculum) >= 5

    def test_observation_dim_positive(self):
        (
            _,_,_,_,_,_,_,_,_,
            make_default_g1_env_config,
        ) = _import()
        cfg = make_default_g1_env_config()
        assert cfg.observation.observation_dim > 0

    def test_action_dim_matches_hardware(self):
        (
            _,_,_,_,_,_,_,_,_,
            make_default_g1_env_config,
        ) = _import()
        cfg = make_default_g1_env_config()
        assert cfg.action.joint_target_dim == cfg.hardware.joint_count

    def test_validator_passes_on_default_config(self):
        (
            _,_,_,
            G1EnvConfigValidator,
            _,_,_,_,_,
            make_default_g1_env_config,
        ) = _import()
        cfg = make_default_g1_env_config()
        issues = G1EnvConfigValidator().validate(cfg)
        assert issues == [], f"Unexpected issues: {issues}"

    def test_control_dt_equals_sim_dt_times_decimation(self):
        (
            _,_,_,_,_,_,_,_,_,
            make_default_g1_env_config,
        ) = _import()
        cfg = make_default_g1_env_config()
        assert cfg.control_dt == pytest.approx(cfg.sim_dt * cfg.decimation)
