"""Phase 79b — IsaacLab 2.3 G1 locomanipulation env + WBC integration.

SPEC/ENV-CONFIG layer: env config dataclass, observation/action space spec,
curriculum schedule, and integration test layout (pure Python — no live GPU).

Per specs/IA_FULL_SPEC_2026-05-10.md Phase 79b.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


PHASE_ID = "79b"
PHASE_TITLE = "IsaacLab 2.3 G1 locomanipulation env + WBC integration"
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
        "spec_ref": "specs/IA_FULL_SPEC_2026-05-10.md Phase 79b",
    }


# ---------------------------------------------------------------------------
# Hardware spec
# ---------------------------------------------------------------------------

@dataclass
class G1HardwareSpec:
    """Unitree G1 humanoid hardware specification."""

    joint_count: int = 23
    arm_dof_per_side: int = 5
    leg_dof_per_side: int = 6
    torso_dof: int = 1
    weight_kg: float = 35.0
    height_m: float = 1.32
    has_dexterous_hands: bool = False

    def total_arm_dof(self) -> int:
        """Return the total arm degrees-of-freedom across both sides."""
        return self.arm_dof_per_side * 2

    def total_leg_dof(self) -> int:
        """Return the total leg degrees-of-freedom across both sides."""
        return self.leg_dof_per_side * 2


# ---------------------------------------------------------------------------
# Observation / action space specs
# ---------------------------------------------------------------------------

@dataclass
class G1ObservationSpec:
    """Observation space specification for the G1 locomanipulation env.

    ``observation_dim`` is computed automatically in ``__post_init__``
    as ``proprioceptive_dim + exteroceptive_dim + command_dim``.
    """

    proprioceptive_dim: int
    exteroceptive_dim: int
    command_dim: int = 3
    observation_dim: int = field(init=False)

    def __post_init__(self) -> None:
        """Compute ``observation_dim`` = proprioceptive + exteroceptive + command dims."""
        self.observation_dim = (
            self.proprioceptive_dim + self.exteroceptive_dim + self.command_dim
        )


@dataclass
class G1ActionSpec:
    """Action space specification for the G1 locomanipulation env.

    ``action_dim`` is computed automatically in ``__post_init__``
    as ``joint_target_dim + gripper_dim``.
    """

    joint_target_dim: int = 23
    gripper_dim: int = 2
    action_dim: int = field(init=False)

    def __post_init__(self) -> None:
        """Compute ``action_dim`` = joint_target_dim + gripper_dim."""
        self.action_dim = self.joint_target_dim + self.gripper_dim


# ---------------------------------------------------------------------------
# Curriculum
# ---------------------------------------------------------------------------

@dataclass
class LocomanipCurriculumStage:
    """Single stage in the G1 locomanipulation curriculum.

    Parameters
    ----------
    stage_id:
        Zero-indexed stage number.  Stages must be ordered 0, 1, 2, …
    name:
        Human-readable stage label.
    min_episodes:
        Minimum episodes completed at this stage before advancement is
        considered.
    success_rate_threshold:
        Recent success-rate (0-1) that must be reached before advancing.
    ranges:
        Optional dict of ``param_name -> (min, max)`` defining the
        randomisation ranges active during this stage.
    description:
        Optional longer description.
    """

    stage_id: int
    name: str
    min_episodes: int
    success_rate_threshold: float
    ranges: Dict[str, Tuple[float, float]] = field(default_factory=dict)
    description: str = ""


#: Default curriculum — 5 stages from balance-only through full loco-manip.
DEFAULT_CURRICULUM: List[LocomanipCurriculumStage] = [
    LocomanipCurriculumStage(
        stage_id=0,
        name="balance_only",
        min_episodes=500,
        success_rate_threshold=0.8,
        ranges={"gravity_scale": (1.0, 1.0)},
        description="Robot must maintain upright balance without locomotion commands.",
    ),
    LocomanipCurriculumStage(
        stage_id=1,
        name="walk_flat_ground",
        min_episodes=1000,
        success_rate_threshold=0.7,
        ranges={
            "gravity_scale": (0.9, 1.1),
            "terrain_friction": (0.8, 1.2),
        },
        description="Walk on flat terrain following velocity commands.",
    ),
    LocomanipCurriculumStage(
        stage_id=2,
        name="walk_uneven_terrain",
        min_episodes=2000,
        success_rate_threshold=0.6,
        ranges={
            "gravity_scale": (0.8, 1.2),
            "terrain_friction": (0.5, 1.5),
            "terrain_roughness": (0.0, 0.05),
        },
        description="Walk on randomised uneven terrain.",
    ),
    LocomanipCurriculumStage(
        stage_id=3,
        name="loco_manip_basic",
        min_episodes=3000,
        success_rate_threshold=0.5,
        ranges={
            "target_height_m": (0.8, 1.2),
            "gravity_scale": (0.9, 1.1),
        },
        description="Locomotion combined with basic arm reaching tasks.",
    ),
    LocomanipCurriculumStage(
        stage_id=4,
        name="loco_manip_full",
        min_episodes=5000,
        success_rate_threshold=0.4,
        ranges={
            "target_height_m": (0.6, 1.4),
            "gravity_scale": (0.8, 1.2),
            "terrain_friction": (0.4, 1.6),
            "payload_kg": (0.0, 2.0),
        },
        description="Full loco-manipulation with payload and full terrain randomisation.",
    ),
]


# ---------------------------------------------------------------------------
# Top-level env config
# ---------------------------------------------------------------------------

@dataclass
class IsaacLabG1EnvConfig:
    """Top-level configuration for the Isaac-G1-LocoManip-v0 environment.

    All fields have sensible defaults; call :func:`make_default_g1_env_config`
    for a fully-populated instance.
    """

    env_name: str = "Isaac-G1-LocoManip-v0"
    num_envs: int = 4096
    episode_length_s: float = 20.0
    sim_dt: float = 0.005
    decimation: int = 4
    observation: Optional[G1ObservationSpec] = None
    action: Optional[G1ActionSpec] = None
    hardware: G1HardwareSpec = field(default_factory=G1HardwareSpec)
    curriculum: List[LocomanipCurriculumStage] = field(default_factory=list)

    @property
    def control_dt(self) -> float:
        """Effective control timestep in seconds: ``sim_dt × decimation``."""
        return self.sim_dt * self.decimation


# ---------------------------------------------------------------------------
# Validator
# ---------------------------------------------------------------------------

class G1EnvConfigValidator:
    """Validate an :class:`IsaacLabG1EnvConfig` and collect issues."""

    def validate(self, cfg: IsaacLabG1EnvConfig) -> List[str]:
        """Return a list of issue strings.  Empty list means the config is valid.

        Checks:
        - ``num_envs > 0``
        - ``episode_length_s > 0``
        - ``sim_dt`` in ``(0, 0.05)`` exclusive
        - ``decimation >= 1``
        - ``hardware.joint_count > 0``
        - curriculum stage_ids start at 0 and are consecutive
        """
        issues: List[str] = []

        if cfg.num_envs <= 0:
            issues.append(
                f"num_envs must be > 0, got {cfg.num_envs}"
            )

        if cfg.episode_length_s <= 0:
            issues.append(
                f"episode_length_s must be > 0, got {cfg.episode_length_s}"
            )

        if not (0.0 < cfg.sim_dt < 0.05):
            issues.append(
                f"sim_dt must be in (0, 0.05), got {cfg.sim_dt}"
            )

        if cfg.decimation < 1:
            issues.append(
                f"decimation must be >= 1, got {cfg.decimation}"
            )

        if cfg.hardware.joint_count <= 0:
            issues.append(
                f"hardware.joint_count must be > 0, got {cfg.hardware.joint_count}"
            )

        if cfg.curriculum:
            expected_ids = list(range(len(cfg.curriculum)))
            actual_ids = [s.stage_id for s in cfg.curriculum]
            if actual_ids != expected_ids:
                issues.append(
                    f"curriculum stage_ids must be 0..N-1, got {actual_ids}"
                )

        return issues


# ---------------------------------------------------------------------------
# Curriculum scheduler
# ---------------------------------------------------------------------------

class G1CurriculumScheduler:
    """Advance through :data:`DEFAULT_CURRICULUM` (or a custom list) based on
    episode count and rolling success rate.

    Parameters
    ----------
    stages:
        Ordered list of :class:`LocomanipCurriculumStage`.  Defaults to
        :data:`DEFAULT_CURRICULUM`.
    """

    def __init__(
        self,
        stages: Optional[List[LocomanipCurriculumStage]] = None,
    ) -> None:
        """Initialise the scheduler.

        Args:
            stages (List[LocomanipCurriculumStage], optional): Custom stage list.
                Defaults to :data:`DEFAULT_CURRICULUM`.
        """
        self._stages: List[LocomanipCurriculumStage] = (
            stages if stages is not None else list(DEFAULT_CURRICULUM)
        )
        self.current_stage_idx: int = 0

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def current_stage(self) -> LocomanipCurriculumStage:
        """Return the active :class:`LocomanipCurriculumStage`."""
        return self._stages[self.current_stage_idx]

    def is_complete(self) -> bool:
        """Return ``True`` when all stages have been completed."""
        return self.current_stage_idx >= len(self._stages)

    # ------------------------------------------------------------------
    # State mutations
    # ------------------------------------------------------------------

    def advance(self, episodes_completed: int, recent_success_rate: float) -> bool:
        """Attempt to advance to the next curriculum stage.

        Advancement happens when **both** conditions are satisfied:
        - ``episodes_completed >= current_stage.min_episodes``
        - ``recent_success_rate >= current_stage.success_rate_threshold``

        Returns
        -------
        bool
            ``True`` if the scheduler advanced to the next stage,
            ``False`` otherwise (including when already complete).
        """
        if self.is_complete():
            return False

        stage = self.current_stage()

        if (
            episodes_completed >= stage.min_episodes
            and recent_success_rate >= stage.success_rate_threshold
        ):
            self.current_stage_idx += 1
            return True

        return False

    def reset(self) -> None:
        """Reset the scheduler back to stage 0 (beginning of the curriculum)."""
        self.current_stage_idx = 0


# ---------------------------------------------------------------------------
# Factory helper
# ---------------------------------------------------------------------------

def make_default_g1_env_config() -> IsaacLabG1EnvConfig:
    """Return a fully-populated :class:`IsaacLabG1EnvConfig`.

    Observation space:
    - proprioceptive: 2 * joint_count (pos + vel) + 6 (base lin/ang vel) = 52
    - exteroceptive: 187 (height-scan grid, 11x17)
    - command: 3 (vx, vy, yaw_rate)
    - total: 242

    Action space:
    - joint_target_dim: 23 (matches G1HardwareSpec.joint_count)
    - gripper_dim: 2
    - total: 25
    """
    hw = G1HardwareSpec()

    # Proprioceptive: joint pos + joint vel + base lin vel (3) + base ang vel (3)
    proprioceptive_dim = hw.joint_count * 2 + 6  # 52

    # Exteroceptive: 11 x 17 height scan grid
    exteroceptive_dim = 11 * 17  # 187

    obs = G1ObservationSpec(
        proprioceptive_dim=proprioceptive_dim,
        exteroceptive_dim=exteroceptive_dim,
        command_dim=3,
    )

    act = G1ActionSpec(
        joint_target_dim=hw.joint_count,
        gripper_dim=2,
    )

    return IsaacLabG1EnvConfig(
        env_name="Isaac-G1-LocoManip-v0",
        num_envs=4096,
        episode_length_s=20.0,
        sim_dt=0.005,
        decimation=4,
        observation=obs,
        action=act,
        hardware=hw,
        curriculum=list(DEFAULT_CURRICULUM),
    )
