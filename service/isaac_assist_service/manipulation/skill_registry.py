"""
skill_registry.py
------------------
Canonical registry of RL skills available to the Task Spec planner.

Sourced from docs/04_rl_policy_bank.md.  The Continuity Manager validates
phase.skill_name against SKILL_REGISTRY at Task Spec ingest — unknown skills
trigger an immediate escalation / 422 response from /plan.

Adding a new skill here is a code change that must be paired with:
  - A trained policy in policies/<skill_name>/<embodiment_id>/
  - An Isaac Lab training env in manipulation_stack/isaac_lab_envs/
  - A card.md documenting eval results
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, FrozenSet, List, Literal


ArmMode = Literal["single", "assist", "bimanual_sync"]


@dataclass(frozen=True)
class SkillSpec:
    name:         str
    arm_mode:     ArmMode
    description:  str
    assist_role:  bool = False   # True when this skill runs as the ASSIST arm
    # Valid hand_assignment roles for this skill
    valid_roles:  FrozenSet[str] = field(
        default_factory=lambda: frozenset({"LEAD", "IDLE"})
    )
    # Typical training budget (informational)
    sim_gpu_hr:   float = 0.0
    real_demos:   int = 0


SKILL_REGISTRY: Dict[str, SkillSpec] = {
    # ── Single-arm skills ───────────────────────────────────────────────────
    "pick_rigid": SkillSpec(
        name="pick_rigid",
        arm_mode="single",
        description=(
            "Pick a rigid object via top, side, or angled grasp. "
            "Curriculum: top-down → angled → cluttered."
        ),
        sim_gpu_hr=12.0,
        real_demos=200,
    ),
    "pick_deformable": SkillSpec(
        name="pick_deformable",
        arm_mode="single",
        description="Pick cloth or soft objects. Requires deformable-capable sim (FleX/warp).",
        sim_gpu_hr=30.0,
        real_demos=200,
    ),
    "place_on_surface": SkillSpec(
        name="place_on_surface",
        arm_mode="single",
        description="Place the currently grasped object on a support surface.",
        sim_gpu_hr=6.0,
        real_demos=50,
    ),
    "transit_to_pose": SkillSpec(
        name="transit_to_pose",
        arm_mode="single",
        description=(
            "Move gripper to a world-frame pose with carry semantics. "
            "Often replaceable by cuRobo; keep RL for cluttered environments."
        ),
        sim_gpu_hr=3.0,
        real_demos=0,
    ),
    "push_object": SkillSpec(
        name="push_object",
        arm_mode="single",
        description="Push an object along a direction vector.",
        sim_gpu_hr=4.0,
        real_demos=50,
    ),
    "press_button": SkillSpec(
        name="press_button",
        arm_mode="single",
        description="Press a target point with force feedback.",
        sim_gpu_hr=3.0,
        real_demos=30,
    ),
    "open_drawer": SkillSpec(
        name="open_drawer",
        arm_mode="single",
        description="Pull a drawer open along an axis with compliance control.",
        sim_gpu_hr=10.0,
        real_demos=100,
    ),

    # ── Assist skills (run as ASSIST arm, paired with a LEAD skill) ─────────
    "stabilize_object": SkillSpec(
        name="stabilize_object",
        arm_mode="assist",
        assist_role=True,
        description="Hold an object steady at a pose against perturbations.",
        valid_roles=frozenset({"ASSIST", "IDLE"}),
        sim_gpu_hr=8.0,
        real_demos=50,
    ),
    "hold_cloth_taut": SkillSpec(
        name="hold_cloth_taut",
        arm_mode="assist",
        assist_role=True,
        description="Pin a fabric corner with controlled tension (co-trained with bimanual_fold).",
        valid_roles=frozenset({"ASSIST", "IDLE"}),
        sim_gpu_hr=25.0,
        real_demos=100,
    ),

    # ── Synchronized bimanual skills ─────────────────────────────────────────
    "bimanual_handover": SkillSpec(
        name="bimanual_handover",
        arm_mode="bimanual_sync",
        description=(
            "Pass an object between hands. Both arms are LEAD — single policy "
            "with both arms' observations and both arms' action heads."
        ),
        valid_roles=frozenset({"LEAD"}),
        sim_gpu_hr=40.0,
        real_demos=200,
    ),
    "bimanual_fold": SkillSpec(
        name="bimanual_fold",
        arm_mode="bimanual_sync",
        description="Primary fold motion (LEAD) + corner stabilization (ASSIST).",
        valid_roles=frozenset({"LEAD", "ASSIST"}),
        sim_gpu_hr=30.0,
        real_demos=200,
    ),
}

SKILL_NAMES: List[str] = sorted(SKILL_REGISTRY.keys())


def is_valid_skill(name: str) -> bool:
    return name in SKILL_REGISTRY


def get_skill(name: str) -> SkillSpec:
    if name not in SKILL_REGISTRY:
        raise KeyError(
            f"Unknown skill '{name}'. Valid skills: {SKILL_NAMES}"
        )
    return SKILL_REGISTRY[name]


def skills_for_prompt() -> str:
    """Return a compact string for injecting the skill list into LLM prompts."""
    lines = []
    for name, spec in sorted(SKILL_REGISTRY.items()):
        tag = "(ASSIST)" if spec.assist_role else f"({spec.arm_mode})"
        lines.append(f"  {name} {tag}: {spec.description}")
    return "\n".join(lines)
