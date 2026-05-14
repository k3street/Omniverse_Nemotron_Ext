"""Phase 103 — User-facing onboarding tutorial.

Provides TutorialStep definitions matching the six guided steps in
docs/onboarding_tutorial.md, plus an OnboardingTracker state machine
for tracking user progress through the tutorial.

Per specs/IA_FULL_SPEC_2026-05-10.md Phase 103.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Phase metadata
# ---------------------------------------------------------------------------

PHASE_ID = 103
PHASE_TITLE = "User-facing onboarding tutorial"
PHASE_STATUS = "landed"


def get_phase_metadata() -> Dict[str, Any]:
    """Return phase identification and status for Phase 103.

    Returns:
        Dict[str, Any]: Keys ``phase``, ``title``, ``status``, and ``spec_ref``.
    """
    return {
        "phase": PHASE_ID,
        "title": PHASE_TITLE,
        "status": PHASE_STATUS,
        "spec_ref": "specs/IA_FULL_SPEC_2026-05-10.md Phase 103",
    }


# ---------------------------------------------------------------------------
# TutorialStep dataclass
# ---------------------------------------------------------------------------

@dataclass
class TutorialStep:
    """One step in the onboarding tutorial."""

    step_id: int
    title: str
    completion_check: str  # semantic description of what to verify
    expected_tools: List[str]


# ---------------------------------------------------------------------------
# Canonical tutorial steps (matching markdown Steps 1-6)
# ---------------------------------------------------------------------------

TUTORIAL_STEPS: List[TutorialStep] = [
    TutorialStep(
        step_id=1,
        title="First Connection",
        completion_check=(
            "Service is reachable, tool list sidebar shows at least one tool "
            "including scene_summary or list_all_prims."
        ),
        expected_tools=["scene_summary", "list_all_prims", "list_extensions"],
    ),
    TutorialStep(
        step_id=2,
        title="Your First Scene",
        completion_check=(
            "A Cube prim named TestCube exists in the stage at the requested "
            "position; scene_summary confirms prim count >= 1."
        ),
        expected_tools=["create_prim", "scene_summary", "get_prim_type"],
    ),
    TutorialStep(
        step_id=3,
        title="Adding a Robot",
        completion_check=(
            "A robot articulation root is present in the stage; "
            "list_all_prims shows the robot path and get_joint_limits returns "
            "at least one joint."
        ),
        expected_tools=["assemble_robot", "robot_wizard", "list_all_prims", "get_joint_limits"],
    ),
    TutorialStep(
        step_id=4,
        title="Running a Pick-and-Place",
        completion_check=(
            "PickPlaceController is configured; one full pick-and-place cycle "
            "completes without error; get_gripper_state shows 'open' after "
            "releasing the object."
        ),
        expected_tools=[
            "setup_pick_place_with_vision",
            "sim_control",
            "get_gripper_state",
            "get_joint_positions",
        ],
    ),
    TutorialStep(
        step_id=5,
        title="Domain Randomisation",
        completion_check=(
            "A DR preset is applied; preview_dr returns a thumbnail; "
            "analyze_randomization lists active randomisers."
        ),
        expected_tools=["apply_dr_preset", "preview_dr", "analyze_randomization", "suggest_dr_ranges"],
    ),
    TutorialStep(
        step_id=6,
        title="Workflows",
        completion_check=(
            "start_workflow enqueues the named template; at least one "
            "checkpoint is approved via approve_workflow_checkpoint; "
            "get_workflow_status shows the workflow completed or in-progress."
        ),
        expected_tools=[
            "start_workflow",
            "approve_workflow_checkpoint",
            "get_workflow_status",
            "cancel_workflow",
        ],
    ),
]


# ---------------------------------------------------------------------------
# OnboardingTracker
# ---------------------------------------------------------------------------

class OnboardingTracker:
    """Tracks user progress through the onboarding tutorial steps."""

    def __init__(self) -> None:
        """Initialise the tracker at Step 1 with no completed steps."""
        self._current_step: int = 1
        self.completed_steps: set[int] = set()

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def current_step(self) -> int:
        """Return the step_id of the active (not-yet-completed) step.

        Returns:
            int: Current step ID; exceeds the last step ID when tutorial is complete.
        """
        return self._current_step

    # ------------------------------------------------------------------
    # Mutations
    # ------------------------------------------------------------------

    def mark_complete(self, step_id: int) -> None:
        """Mark *step_id* as completed.

        If *step_id* equals the current step, advances ``current_step``
        to the next non-completed step id (or to ``total + 1`` when all
        are done).
        """
        self.completed_steps.add(step_id)
        if step_id == self._current_step:
            # Advance to the next uncompleted step
            all_ids = [s.step_id for s in TUTORIAL_STEPS]
            for sid in all_ids:
                if sid not in self.completed_steps:
                    self._current_step = sid
                    return
            # All steps completed — set current_step beyond last
            self._current_step = all_ids[-1] + 1

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def is_complete(self) -> bool:
        """Return ``True`` only after every tutorial step has been completed.

        Returns:
            bool: ``True`` when all 6 steps are in ``completed_steps``.
        """
        return len(self.completed_steps) >= len(TUTORIAL_STEPS)

    def next_step(self) -> Optional[TutorialStep]:
        """Return the first non-completed TutorialStep, or ``None`` if done.

        Returns:
            Optional[TutorialStep]: Next pending step, or ``None`` when the tutorial
                is finished.
        """
        for step in TUTORIAL_STEPS:
            if step.step_id not in self.completed_steps:
                return step
        return None

    def progress(self) -> Dict[str, Any]:
        """Return a progress summary dict for the current session.

        Returns:
            Dict[str, Any]: Keys ``current_step`` (int), ``completed`` (int),
                ``total`` (int), and ``pct`` (float, 0–100).
        """
        total = len(TUTORIAL_STEPS)
        completed = len(self.completed_steps)
        pct = round(completed / total * 100.0, 1) if total > 0 else 0.0
        return {
            "current_step": self._current_step,
            "completed": completed,
            "total": total,
            "pct": pct,
        }


# ---------------------------------------------------------------------------
# Markdown reader
# ---------------------------------------------------------------------------

def read_tutorial_markdown() -> str:
    """Read and return the content of docs/onboarding_tutorial.md.

    Resolves the path relative to the repository root (three levels above
    this file: multimodal → isaac_assist_service → service → repo-root).
    """
    here = Path(__file__).resolve()
    repo_root = here.parent.parent.parent.parent  # …/Omniverse_Nemotron_Ext
    tutorial_path = repo_root / "docs" / "onboarding_tutorial.md"
    return tutorial_path.read_text(encoding="utf-8")
