"""Phase 62b — GR00T N1.7 finetune + Mimic/Dreams blueprint integration.

Eval-harness layer: scenario enumeration, scoring rubric, Mimic/Dreams blueprint
config validation. Actual GR00T inference + Mimic/Dreams execution is
opus-runtime gated; this module is pure Python with no external dependencies.

Per specs/IA_FULL_SPEC_2026-05-10.md Phase 62b.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Literal, Optional

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PHASE_ID = "62b"
PHASE_TITLE = "GR00T N1.7 finetune + Mimic/Dreams blueprint integration"
PHASE_STATUS = "landed"

__all__ = [
    "EvalScenario",
    "EvalRun",
    "MimicBlueprintConfig",
    "DreamsBlueprintConfig",
    "EVAL_SCENARIOS",
    "GrootN17EvalHarness",
    "expected_eval_categories",
    "get_phase_metadata",
    "PHASE_ID",
    "PHASE_TITLE",
    "PHASE_STATUS",
]

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class EvalScenario:
    """Definition of a single evaluation scenario for GR00T N1.7."""

    scenario_id: str
    name: str
    category: Literal["pick_place", "handoff", "navigation", "manipulation", "assembly"]
    difficulty: Literal["L1", "L2", "L3", "L4", "L5"]
    required_skills: List[str]
    success_criteria: Dict[str, float]
    max_episode_steps: int = 200


@dataclass
class EvalRun:
    """Result of evaluating a single scenario."""

    scenario_id: str
    model_version: str
    success: bool
    score: float
    episode_steps: int
    success_criteria_met: Dict[str, bool]
    failure_mode: Optional[str] = None
    run_at: str = ""

    def __post_init__(self) -> None:
        if not self.run_at:
            self.run_at = datetime.now(timezone.utc).isoformat()


@dataclass
class MimicBlueprintConfig:
    """Configuration for a Mimic episode-generation blueprint.

    Mimic synthesises demonstration data from a scene template; actual
    execution requires Mimic runtime (opus-runtime gate).
    """

    blueprint_id: str
    num_episodes: int = 100
    episode_length_s: float = 30.0
    scene_template: str = ""
    controller: Literal["pid", "mpc", "groot"] = "groot"


@dataclass
class DreamsBlueprintConfig:
    """Configuration for a Dreams language-grounded skill-learning blueprint.

    Dreams maps natural-language task descriptions to robot skills; execution
    requires Dreams runtime (opus-runtime gate).
    """

    blueprint_id: str
    language_dataset: str = ""
    dataset_size: int = 1000
    target_skill_count: int = 20
    embedding_dim: int = 768


# ---------------------------------------------------------------------------
# Built-in eval scenarios  (≥12, spanning ≥4 categories)
# ---------------------------------------------------------------------------

EVAL_SCENARIOS: List[EvalScenario] = [
    # --- pick_place ---
    EvalScenario(
        scenario_id="groot_pick_single_object",
        name="Pick Single Object",
        category="pick_place",
        difficulty="L1",
        required_skills=["grasp", "place"],
        success_criteria={"object_placed": 1.0, "no_drop": 1.0},
        max_episode_steps=150,
    ),
    EvalScenario(
        scenario_id="groot_pick_cluttered_bin",
        name="Pick from Cluttered Bin",
        category="pick_place",
        difficulty="L3",
        required_skills=["grasp", "obstacle_avoidance", "place"],
        success_criteria={"object_placed": 1.0, "no_collision": 0.9, "no_drop": 1.0},
        max_episode_steps=250,
    ),
    EvalScenario(
        scenario_id="groot_pick_fragile_glass",
        name="Pick Fragile Glass Object",
        category="pick_place",
        difficulty="L4",
        required_skills=["delicate_grasp", "force_control", "place"],
        success_criteria={"object_placed": 1.0, "no_drop": 1.0, "force_below_threshold": 0.95},
        max_episode_steps=300,
    ),
    # --- handoff ---
    EvalScenario(
        scenario_id="groot_handoff_human_to_robot",
        name="Human-to-Robot Handoff",
        category="handoff",
        difficulty="L3",
        required_skills=["human_detection", "grasp", "cooperative_timing"],
        success_criteria={"object_received": 1.0, "no_collision_with_human": 1.0},
        max_episode_steps=200,
    ),
    EvalScenario(
        scenario_id="groot_handoff_robot_to_robot",
        name="Robot-to-Robot Handoff",
        category="handoff",
        difficulty="L3",
        required_skills=["grasp", "release_on_signal", "coordination"],
        success_criteria={"object_transferred": 1.0, "sync_timing": 0.9},
        max_episode_steps=200,
    ),
    # --- navigation ---
    EvalScenario(
        scenario_id="groot_navigate_corridor",
        name="Navigate Narrow Corridor",
        category="navigation",
        difficulty="L2",
        required_skills=["path_planning", "obstacle_avoidance"],
        success_criteria={"goal_reached": 1.0, "no_collision": 1.0},
        max_episode_steps=400,
    ),
    EvalScenario(
        scenario_id="groot_navigate_dynamic_obstacles",
        name="Navigate with Dynamic Obstacles",
        category="navigation",
        difficulty="L4",
        required_skills=["dynamic_replanning", "obstacle_avoidance", "velocity_control"],
        success_criteria={"goal_reached": 1.0, "no_collision": 1.0, "time_efficiency": 0.8},
        max_episode_steps=600,
    ),
    # --- manipulation ---
    EvalScenario(
        scenario_id="groot_open_drawer",
        name="Open Drawer",
        category="manipulation",
        difficulty="L2",
        required_skills=["grasp_handle", "pull_motion", "force_control"],
        success_criteria={"drawer_open": 1.0, "no_tip": 1.0},
        max_episode_steps=150,
    ),
    EvalScenario(
        scenario_id="groot_pour_cup",
        name="Pour Liquid from Cup",
        category="manipulation",
        difficulty="L3",
        required_skills=["grasp", "tilt_motion", "liquid_sensing"],
        success_criteria={"liquid_transferred": 0.9, "no_spill": 0.85},
        max_episode_steps=200,
    ),
    EvalScenario(
        scenario_id="groot_screw_assembly",
        name="Screw Fastener Assembly",
        category="manipulation",
        difficulty="L4",
        required_skills=["precision_grasp", "rotation_control", "torque_sensing"],
        success_criteria={"fastener_seated": 1.0, "torque_within_spec": 0.95},
        max_episode_steps=300,
    ),
    # --- assembly ---
    EvalScenario(
        scenario_id="groot_snap_fit",
        name="Snap-Fit Component Assembly",
        category="assembly",
        difficulty="L3",
        required_skills=["precision_placement", "force_feedback", "alignment"],
        success_criteria={"snap_engaged": 1.0, "no_damage": 1.0},
        max_episode_steps=200,
    ),
    EvalScenario(
        scenario_id="groot_kit_assembly",
        name="Full Kit Assembly",
        category="assembly",
        difficulty="L5",
        required_skills=["multi_part_sequencing", "grasp", "precision_placement", "force_feedback"],
        success_criteria={
            "all_parts_placed": 1.0,
            "assembly_valid": 1.0,
            "no_damage": 0.98,
        },
        max_episode_steps=800,
    ),
]

# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


class GrootN17EvalHarness:
    """Evaluation harness for GR00T N1.7 scenarios.

    The harness provides:
    - Scenario enumeration and filtering
    - A deterministic mock evaluator for CI / offline testing
    - Suite scoring with per-category and per-difficulty breakdowns
    - Blueprint config validation for Mimic and Dreams

    Real GR00T inference is opus-runtime gated; use the *mock_evaluate*
    path for all pure-Python / pytest contexts.
    """

    def __init__(
        self,
        scenarios: Optional[List[EvalScenario]] = None,
    ) -> None:
        self._scenarios: List[EvalScenario] = (
            list(scenarios) if scenarios is not None else list(EVAL_SCENARIOS)
        )

    # ------------------------------------------------------------------
    # Filtering
    # ------------------------------------------------------------------

    def filter_by_category(self, category: str) -> List[EvalScenario]:
        """Return all scenarios matching *category*."""
        return [s for s in self._scenarios if s.category == category]

    def filter_by_difficulty(self, difficulty: str) -> List[EvalScenario]:
        """Return all scenarios matching *difficulty* (e.g. ``"L2"``)."""
        return [s for s in self._scenarios if s.difficulty == difficulty]

    # ------------------------------------------------------------------
    # Mock evaluation  (deterministic, no external deps)
    # ------------------------------------------------------------------

    def mock_evaluate(
        self,
        scenario: EvalScenario,
        model_version: str = "gr00t-n1.7",
    ) -> EvalRun:
        """Return a *deterministic* mock EvalRun for *scenario*.

        Determinism is guaranteed by hashing (scenario_id + model_version);
        the hash drives success probability so results are repeatable across
        runs and environments.

        Difficulty scaling:
            L1 → ~88 % success, L2 → ~75 %, L3 → ~62 %, L4 → ~48 %, L5 → ~30 %
        """
        seed_bytes = f"{scenario.scenario_id}:{model_version}".encode()
        digest_int = int(hashlib.md5(seed_bytes).hexdigest(), 16)  # noqa: S324
        # Map digest to [0, 1)
        norm = (digest_int % 10_000) / 10_000.0

        difficulty_threshold: Dict[str, float] = {
            "L1": 0.88,
            "L2": 0.75,
            "L3": 0.62,
            "L4": 0.48,
            "L5": 0.30,
        }
        threshold = difficulty_threshold.get(scenario.difficulty, 0.60)
        success = norm < threshold
        score = round(norm if not success else min(0.95 + (1.0 - norm) * 0.1, 1.0), 4)
        if not success:
            score = round(norm * threshold, 4)

        # Determine which success criteria were met
        criteria_met: Dict[str, bool] = {}
        for i, (criterion, _) in enumerate(scenario.success_criteria.items()):
            # Each criterion gets a deterministic bool derived from digest + index
            crit_seed = (digest_int >> i) & 0xFF
            criteria_met[criterion] = success or (crit_seed > 64)

        failure_mode: Optional[str] = None
        if not success:
            failure_modes = [
                "grasping_failure",
                "joint_limit_exceeded",
                "collision",
                "timeout",
                "planning_failure",
            ]
            failure_mode = failure_modes[digest_int % len(failure_modes)]

        # Episode steps: scale deterministically within [max//4, max]
        step_range = max(1, scenario.max_episode_steps - scenario.max_episode_steps // 4)
        episode_steps = scenario.max_episode_steps // 4 + (digest_int % step_range)
        if success:
            # Successful episodes tend to use fewer steps
            episode_steps = min(episode_steps, int(scenario.max_episode_steps * 0.75))

        return EvalRun(
            scenario_id=scenario.scenario_id,
            model_version=model_version,
            success=success,
            score=score,
            episode_steps=episode_steps,
            success_criteria_met=criteria_met,
            failure_mode=failure_mode,
            run_at=datetime.now(timezone.utc).isoformat(),
        )

    # ------------------------------------------------------------------
    # Suite evaluation
    # ------------------------------------------------------------------

    def evaluate_suite(
        self,
        model_version: str,
        evaluator: Optional[Callable[[EvalScenario, str], EvalRun]] = None,
    ) -> List[EvalRun]:
        """Evaluate all scenarios in the harness.

        *evaluator* defaults to :meth:`mock_evaluate`.  Pass a custom
        callable to plug in a real inference backend (opus-runtime).
        """
        _eval = evaluator if evaluator is not None else self.mock_evaluate
        return [_eval(scenario, model_version) for scenario in self._scenarios]

    # ------------------------------------------------------------------
    # Scoring
    # ------------------------------------------------------------------

    def score_suite(self, runs: List[EvalRun]) -> Dict[str, Any]:
        """Compute aggregate metrics for a list of EvalRuns.

        Returns::

            {
                "total": int,
                "success_rate": float,      # 0–1
                "mean_score": float,        # 0–1
                "by_category": {cat: {"total": int, "success_rate": float, "mean_score": float}},
                "by_difficulty": {diff: {"total": int, "success_rate": float, "mean_score": float}},
            }
        """
        if not runs:
            return {
                "total": 0,
                "success_rate": 0.0,
                "mean_score": 0.0,
                "by_category": {},
                "by_difficulty": {},
            }

        total = len(runs)
        successes = sum(1 for r in runs if r.success)
        success_rate = successes / total
        mean_score = sum(r.score for r in runs) / total

        # Build lookup: scenario_id → scenario (for category/difficulty)
        scenario_map: Dict[str, EvalScenario] = {
            s.scenario_id: s for s in self._scenarios
        }

        def _bucket(runs_: List[EvalRun], key_fn: Callable[[EvalRun], str]) -> Dict[str, Any]:
            buckets: Dict[str, List[EvalRun]] = {}
            for r in runs_:
                k = key_fn(r)
                buckets.setdefault(k, []).append(r)
            result: Dict[str, Any] = {}
            for k, bucket_runs in buckets.items():
                n = len(bucket_runs)
                s = sum(1 for r in bucket_runs if r.success)
                result[k] = {
                    "total": n,
                    "success_rate": round(s / n, 4),
                    "mean_score": round(sum(r.score for r in bucket_runs) / n, 4),
                }
            return result

        def _cat(r: EvalRun) -> str:
            sc = scenario_map.get(r.scenario_id)
            return sc.category if sc else "unknown"

        def _diff(r: EvalRun) -> str:
            sc = scenario_map.get(r.scenario_id)
            return sc.difficulty if sc else "unknown"

        return {
            "total": total,
            "success_rate": round(success_rate, 4),
            "mean_score": round(mean_score, 4),
            "by_category": _bucket(runs, _cat),
            "by_difficulty": _bucket(runs, _diff),
        }

    # ------------------------------------------------------------------
    # Blueprint validation
    # ------------------------------------------------------------------

    def validate_mimic_blueprint(self, config: MimicBlueprintConfig) -> List[str]:
        """Validate a MimicBlueprintConfig.

        Returns a list of issue strings.  Empty list means the config is
        clean and the blueprint can be submitted to the Mimic runtime.
        """
        issues: List[str] = []

        if not config.blueprint_id or not config.blueprint_id.strip():
            issues.append("blueprint_id must be a non-empty string")

        if config.num_episodes <= 0:
            issues.append(
                f"num_episodes must be > 0, got {config.num_episodes}"
            )

        if config.episode_length_s <= 0.0:
            issues.append(
                f"episode_length_s must be > 0, got {config.episode_length_s}"
            )

        if not config.scene_template or not config.scene_template.strip():
            issues.append("scene_template must be a non-empty string")

        return issues

    def validate_dreams_blueprint(self, config: DreamsBlueprintConfig) -> List[str]:
        """Validate a DreamsBlueprintConfig.

        Returns a list of issue strings.  Empty list means the config is
        clean and the blueprint can be submitted to the Dreams runtime.
        """
        issues: List[str] = []

        if not config.blueprint_id or not config.blueprint_id.strip():
            issues.append("blueprint_id must be a non-empty string")

        if not config.language_dataset or not config.language_dataset.strip():
            issues.append("language_dataset must be a non-empty string")

        if config.dataset_size <= 0:
            issues.append(
                f"dataset_size must be > 0, got {config.dataset_size}"
            )

        if config.target_skill_count <= 0:
            issues.append(
                f"target_skill_count must be > 0, got {config.target_skill_count}"
            )

        if config.embedding_dim <= 0:
            issues.append(
                f"embedding_dim must be > 0, got {config.embedding_dim}"
            )

        return issues


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def expected_eval_categories() -> List[str]:
    """Return the canonical list of evaluation category names."""
    return ["pick_place", "handoff", "navigation", "manipulation", "assembly"]


# ---------------------------------------------------------------------------
# Phase metadata
# ---------------------------------------------------------------------------


def get_phase_metadata() -> Dict[str, Any]:
    """Return phase metadata for spec-coverage audits."""
    return {
        "phase": PHASE_ID,
        "title": PHASE_TITLE,
        "status": PHASE_STATUS,
        "spec_ref": "specs/IA_FULL_SPEC_2026-05-10.md Phase 62b",
    }
