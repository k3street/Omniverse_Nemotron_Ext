"""Per-theme state singletons for the themed handler modules.

Each handler theme imports its own state slice; cross-theme state
imports are forbidden (Phase 9 lint enforces this).

Pattern: each state-bearing theme defines a `@dataclass` with default-
factory fields and exports a module-level singleton. Handlers mutate
the singleton through methods that hold the named slice's invariants.

Per `specs/IA_FULL_SPEC_2026-05-10.md` Phase 8.

NB: Phase 8 is bounded by its dependency on Phases 3-7 (the actual
handler moves). This file ships the state container types up front so
that, as each theme moves out of `tool_executor.py`, its handlers
import their slice from here rather than reaching into `tool_executor`
globals. Wave 3 §3 #2 noted `_eureka_runs` is read but never written
in the monolith; making it a real namespace `EurekaState.runs` here
fixes that surface even before Phase 64 lands the writer.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict


# ---------------------------------------------------------------------------
# Workflow state — used by handlers.workflow (Phase 15 move target)


@dataclass
class WorkflowState:
    """Workflows by id. Populated by `start_workflow`; consumed by
    `approve_workflow_checkpoint` / `edit_workflow_plan` / etc."""

    workflows: Dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Eureka state — used by handlers.training (Phase 6 move target)


@dataclass
class EurekaState:
    """Eureka reward-generation runs by id. Wave 3 §3 #2 flagged that
    `_eureka_runs` is read but never written in `tool_executor.py`;
    Phase 64 implements the writer and consumes this state."""

    runs: Dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Training state — used by handlers.training (Phase 6 move target)


@dataclass
class TrainingState:
    """Subprocess-supervised training-run bookkeeping."""

    pid_files: Dict[str, str] = field(default_factory=dict)
    ipc_handlers: Dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# DR / SDG state — used by handlers.sdg (Phase 6 move target)


@dataclass
class DRState:
    """Domain-randomization range hints + correlation matrices."""

    range_hints: Dict[str, Any] = field(default_factory=dict)
    correlations: Dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Bridge state — used by handlers.ros2 (Phase 7 move target; also Phase 31b)


@dataclass
class BridgeState:
    """Industrial-bridge subprocess registry. Used by Modbus / OPC-UA /
    MQTT-Sparkplug / OpenPLC bridge lifecycle handlers."""

    attached: Dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Singletons — themed handler modules import these directly.
#
# Each theme is allowed to import ONLY its named slice. The Phase 9 lint
# (CI rule) catches violations: handlers/training.py may import
# `EUREKA, TRAINING`; handlers/sdg.py may import `DR`; etc.

WORKFLOWS = WorkflowState()
EUREKA = EurekaState()
TRAINING = TrainingState()
DR = DRState()
BRIDGES = BridgeState()

# Phase 8 wave 25 (2026-05-13): TURN_RECORDER singleton migrated from
# tool_executor.py. Cross-theme: used by workflow + training.
# Lazy-instantiated to avoid import-time side effects.
_TURN_RECORDER_SINGLETON = None


def get_turn_recorder():
    """Return the shared TurnRecorder singleton.

    Phase 8 wave 25 — the singleton currently lives in
    `tool_executor.py:_turn_recorder` (a TurnRecorder instantiated at
    module load). We delegate to that instance so both old and new
    callers see the same recorder. A future wave can flip the
    canonical home to this module.
    """
    global _TURN_RECORDER_SINGLETON
    if _TURN_RECORDER_SINGLETON is None:
        try:
            from .. import tool_executor as _te
            _TURN_RECORDER_SINGLETON = _te._turn_recorder
        except (ImportError, AttributeError):
            # Fallback: instantiate our own if tool_executor no longer has it.
            from ...finetune.turn_recorder import TurnRecorder
            _TURN_RECORDER_SINGLETON = TurnRecorder()
    return _TURN_RECORDER_SINGLETON


__all__ = [
    "WorkflowState",
    "EurekaState",
    "TrainingState",
    "DRState",
    "BridgeState",
    "WORKFLOWS",
    "EUREKA",
    "TRAINING",
    "get_turn_recorder",
    "DR",
    "BRIDGES",
]


def reset_all_state() -> None:
    """Test-only: zero out every state singleton's contents.

    Tests that mutate state (Phase 64 EurekaState writers, Phase 31b
    bridge lifecycle tests, etc.) must call this in their teardown to
    avoid cross-test pollution. NOT to be used in production code paths.
    """
    WORKFLOWS.workflows.clear()
    EUREKA.runs.clear()
    TRAINING.pid_files.clear()
    TRAINING.ipc_handlers.clear()
    DR.range_hints.clear()
    DR.correlations.clear()
    BRIDGES.attached.clear()
