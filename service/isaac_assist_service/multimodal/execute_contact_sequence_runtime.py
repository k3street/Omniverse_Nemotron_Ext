"""Phase 63 SPEC/SEQUENCE-RUNTIME — execute_contact_sequence_runtime.

N-step contact-sequence runtime: state machine, step validator,
dry-run executor with predicate evaluation.

Real Kit/PhysX contact reading is opus-runtime gated.
Pure-Python orchestrator layer can be exercised without hardware.

Per specs/IA_FULL_SPEC_2026-05-10.md Phase 63 SPEC/SEQUENCE-RUNTIME.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

ContactStepType = Literal[
    "approach",
    "make_contact",
    "apply_force",
    "slide",
    "twist",
    "release",
    "verify",
]

_ALL_STEP_TYPES: list[ContactStepType] = [
    "approach",
    "make_contact",
    "apply_force",
    "slide",
    "twist",
    "release",
    "verify",
]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PHASE_ID = 63
PHASE_TITLE = "execute_contact_sequence_plan — N-step contact-sequence runtime"
PHASE_STATUS = "landed"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class ContactStep:
    """Specification for a single step in a contact sequence plan."""

    step_idx: int
    step_type: ContactStepType
    prim_a: str
    prim_b: str
    target_force_N: float = 0.0
    target_torque_Nm: float = 0.0
    duration_s: float = 1.0
    success_predicate: str = "contact_established"
    retry_count: int = 0


@dataclass
class ContactObservation:
    """Observed physical state at a single contact-sequence step."""

    step_idx: int
    observed_force_N: float
    observed_torque_Nm: float
    in_contact: bool
    prim_distance_m: float


@dataclass
class ContactStepResult:
    """Outcome record for a single executed contact-sequence step."""

    step_idx: int
    step_type: ContactStepType
    success: bool
    observation: ContactObservation
    duration_s: float
    error: str | None = None


# ---------------------------------------------------------------------------
# ContactSequencePlan
# ---------------------------------------------------------------------------


class ContactSequencePlan:
    """Ordered plan of contact steps with state-machine bookkeeping.

    Parameters
    ----------
    steps:
        Ordered list of :class:`ContactStep` objects.  ``step_idx`` values
        must be unique, 0-indexed, and contiguous (0, 1, 2, …).
    abort_on_failure:
        When *True* (default) the runtime will stop at the first failed step.
    """

    def __init__(
        self,
        steps: list[ContactStep],
        abort_on_failure: bool = True,
    ) -> None:
        self._steps: list[ContactStep] = list(steps)
        self.abort_on_failure = abort_on_failure
        self._results: dict[int, ContactStepResult] = {}
        self._current_idx: int = 0

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def validate(self) -> list[str]:
        """Validate the plan; return a list of issue strings.

        Checks:
        * ``step_idx`` values must be unique.
        * ``step_idx`` values must form a 0-indexed contiguous sequence.
        * ``prim_a != prim_b`` for every step.
        * ``target_force_N >= 0`` for every step.
        """
        issues: list[str] = []

        indices = [s.step_idx for s in self._steps]

        # Uniqueness
        seen: set[int] = set()
        duplicates: list[int] = []
        for idx in indices:
            if idx in seen:
                duplicates.append(idx)
            seen.add(idx)
        if duplicates:
            issues.append(
                f"Duplicate step_idx values found: {sorted(set(duplicates))}"
            )

        # Contiguous 0-indexed
        expected = list(range(len(self._steps)))
        sorted_indices = sorted(indices)
        if sorted_indices != expected:
            issues.append(
                f"step_idx values must be contiguous 0-indexed "
                f"(expected {expected}, got {sorted_indices})"
            )

        # Per-step checks
        for step in self._steps:
            if step.prim_a == step.prim_b:
                issues.append(
                    f"Step {step.step_idx}: prim_a and prim_b must differ "
                    f"(both are '{step.prim_a}')"
                )
            if step.target_force_N < 0.0:
                issues.append(
                    f"Step {step.step_idx}: target_force_N must be >= 0 "
                    f"(got {step.target_force_N})"
                )

        return issues

    # ------------------------------------------------------------------
    # Iteration API
    # ------------------------------------------------------------------

    def next_step(self) -> ContactStep | None:
        """Return the next unexecuted step, or *None* if the plan is complete."""
        if self._current_idx >= len(self._steps):
            return None
        # Steps are stored in insertion order; look up by sorted step_idx
        ordered = sorted(self._steps, key=lambda s: s.step_idx)
        if self._current_idx >= len(ordered):
            return None
        return ordered[self._current_idx]

    def mark_complete(self, step_idx: int, result: ContactStepResult) -> None:
        """Record the result for *step_idx* and advance the internal cursor."""
        self._results[step_idx] = result
        # Advance cursor if this is the expected next step
        if step_idx == self._current_idx:
            self._current_idx += 1

    def is_complete(self) -> bool:
        """Return *True* when all steps have been marked complete."""
        return len(self._results) >= len(self._steps)

    def current_idx(self) -> int:
        """Return the index of the next step to execute (0-based cursor)."""
        return self._current_idx


# ---------------------------------------------------------------------------
# ContactSequenceRuntime
# ---------------------------------------------------------------------------


class ContactSequenceRuntime:
    """Executor for :class:`ContactSequencePlan` objects.

    In *dry_run* mode (default) all physical operations are simulated using
    either caller-supplied mock observations or synthetic success observations.
    Real Kit/PhysX contact execution requires opus-runtime infrastructure.

    Parameters
    ----------
    dry_run:
        When *True* (default), ``execute_step`` does not call Kit RPC and
        instead uses mock or synthetic observations.
    """

    def __init__(self, dry_run: bool = True) -> None:
        self.dry_run = dry_run

    # ------------------------------------------------------------------
    # Predicate evaluation
    # ------------------------------------------------------------------

    def evaluate_predicate(
        self,
        predicate: str,
        obs: ContactObservation,
        expected_force: float,
        tolerance_N: float = 0.5,
    ) -> bool:
        """Evaluate a named success predicate against an observation.

        Supported predicates
        --------------------
        ``"contact_established"``
            True when ``obs.in_contact`` is *True*.
        ``"force_within_tolerance"``
            True when ``|obs.observed_force_N - expected_force| < tolerance_N``.
        ``"no_contact"``
            True when ``obs.in_contact`` is *False*.
        ``"distance_min"``
            True when ``obs.prim_distance_m < 0.01``.

        Unknown predicates evaluate to *False*.
        """
        if predicate == "contact_established":
            return obs.in_contact
        if predicate == "force_within_tolerance":
            return abs(obs.observed_force_N - expected_force) < tolerance_N
        if predicate == "no_contact":
            return not obs.in_contact
        if predicate == "distance_min":
            return obs.prim_distance_m < 0.01
        return False

    # ------------------------------------------------------------------
    # Step execution
    # ------------------------------------------------------------------

    def execute_step(
        self,
        step: ContactStep,
        mock_obs: ContactObservation | None = None,
    ) -> ContactStepResult:
        """Execute a single contact step and return its result.

        In dry-run mode:
        * Uses *mock_obs* if provided.
        * Falls back to a synthetic success observation if *mock_obs* is None.

        The success predicate in ``step.success_predicate`` is evaluated
        against the observation to determine whether the step succeeded.

        Parameters
        ----------
        step:
            The step to execute.
        mock_obs:
            Optional observation to use instead of a synthetic one.
            Useful for testing failure paths.
        """
        t0 = time.monotonic()

        if not self.dry_run:
            raise NotImplementedError(
                "Real contact-sequence execution requires Kit RPC and PhysX "
                "(opus-runtime gate). Use dry_run=True for runtime testing."
            )

        obs: ContactObservation
        if mock_obs is not None:
            obs = mock_obs
        else:
            obs = self._synthetic_observation(step)

        success = self.evaluate_predicate(
            step.success_predicate,
            obs,
            expected_force=step.target_force_N,
        )

        duration_s = time.monotonic() - t0
        error: str | None = None
        if not success:
            error = (
                f"Predicate '{step.success_predicate}' not satisfied "
                f"(in_contact={obs.in_contact}, "
                f"observed_force_N={obs.observed_force_N:.3f}, "
                f"prim_distance_m={obs.prim_distance_m:.4f})"
            )

        return ContactStepResult(
            step_idx=step.step_idx,
            step_type=step.step_type,
            success=success,
            observation=obs,
            duration_s=round(duration_s, 6),
            error=error,
        )

    # ------------------------------------------------------------------
    # Plan execution
    # ------------------------------------------------------------------

    def execute_plan(
        self,
        plan: ContactSequencePlan,
        observations: dict[int, ContactObservation] | None = None,
    ) -> list[ContactStepResult]:
        """Walk all steps in *plan* in order, executing each step.

        Parameters
        ----------
        plan:
            The :class:`ContactSequencePlan` to execute.
        observations:
            Optional mapping from ``step_idx`` to a mock
            :class:`ContactObservation`.  Passed through to ``execute_step``
            for the corresponding step.

        Behaviour
        ---------
        * If ``plan.abort_on_failure`` is *True* and a step fails, the loop
          stops immediately and no further steps are executed.
        * If ``plan.abort_on_failure`` is *False*, all steps are executed
          regardless of individual failures.
        """
        obs_map: dict[int, ContactObservation] = observations or {}
        results: list[ContactStepResult] = []

        while True:
            step = plan.next_step()
            if step is None:
                break

            mock_obs = obs_map.get(step.step_idx)
            result = self.execute_step(step, mock_obs=mock_obs)
            plan.mark_complete(step.step_idx, result)
            results.append(result)

            if not result.success and plan.abort_on_failure:
                break

        return results

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _synthetic_observation(self, step: ContactStep) -> ContactObservation:
        """Produce a synthetic success observation for *step*.

        Returns an observation where:
        * ``in_contact`` is *True*.
        * ``observed_force_N`` equals ``step.target_force_N``.
        * ``observed_torque_Nm`` equals ``step.target_torque_Nm``.
        * ``prim_distance_m`` is 0.0 (prims are touching).
        """
        return ContactObservation(
            step_idx=step.step_idx,
            observed_force_N=step.target_force_N,
            observed_torque_Nm=step.target_torque_Nm,
            in_contact=True,
            prim_distance_m=0.0,
        )


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def expected_step_types() -> list[ContactStepType]:
    """Return the canonical list of supported ContactStepType values."""
    return list(_ALL_STEP_TYPES)


# ---------------------------------------------------------------------------
# Phase metadata
# ---------------------------------------------------------------------------


def get_phase_metadata() -> dict[str, Any]:
    """Return phase metadata for spec-coverage audits."""
    return {
        "phase": PHASE_ID,
        "title": PHASE_TITLE,
        "status": PHASE_STATUS,
        "spec_ref": "specs/IA_FULL_SPEC_2026-05-10.md Phase 63 SPEC/SEQUENCE-RUNTIME",
    }
