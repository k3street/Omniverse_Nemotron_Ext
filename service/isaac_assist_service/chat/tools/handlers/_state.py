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

# Phase 8 wave 28 (2026-05-13): async-task registry + lock migrated from
# tool_executor.py. Workflow theme owns both. Module-level so consumers
# import directly (no lazy proxy needed — they're plain Python objects).
import threading as _threading  # noqa: E402

ASYNC_TASKS_LOCK: _threading.Lock = _threading.Lock()
ASYNC_TASKS: Dict[str, Dict[str, Any]] = {}


# Phase 8 wave 25 (2026-05-13): TURN_RECORDER singleton migrated from
# tool_executor.py. Cross-theme: used by workflow + training.
# Lazy-instantiated to avoid import-time side effects.
#
# CONC-1 (2026-05-14): The lazy double-check pattern previously here was
# racy — two coroutines hitting `get_turn_recorder()` concurrently could
# both observe `_TURN_RECORDER_SINGLETON is None`, both instantiate, and
# the second instantiation would clobber the first (silently leaking the
# first instance's file handles). `TurnRecorder.__init__` writes to disk
# via `output_dir.mkdir(parents=True, exist_ok=True)`, so eager init at
# module import would create a directory at import time — undesirable for
# tests and tools that import this module without intending to record.
# The locked double-check below preserves lazy semantics while keeping
# the initialization race-free.
_TURN_RECORDER_SINGLETON = None
_TURN_RECORDER_LOCK: _threading.Lock = _threading.Lock()


# Phase 8 wave 29 (2026-05-13): _LockedPatch + _StageWriteLockQueue
# migrated from tool_executor.py. Workflow uses these to serialize
# stage-mutating patches through Kit RPC. The singleton lives here.
import asyncio as _asyncio  # noqa: E402
from typing import Tuple as _Tuple  # noqa: E402


@dataclass(order=True)
class LockedPatch:
    """Patch queue entry with priority + insertion-order ordering."""
    sort_key: _Tuple[int, int] = field(compare=True)
    code: str = field(compare=False, default="")
    description: str = field(compare=False, default="")
    priority: int = field(compare=False, default=0)


class StageWriteLockQueue:
    """Minimal serialized queue — mirrors the spec's StageWriteLock pattern."""

    def __init__(self) -> None:
        """Initialise the queue with an empty pending list and an asyncio lock."""
        self._lock = _asyncio.Lock()
        self._pending: list = []
        self._counter = 0

    async def submit(self, code: str, description: str, priority: int) -> Dict[str, Any]:
        """Queue a patch for Kit execution and return queue metadata.

        The patch is inserted into the priority-sorted pending list while the
        lock is held, then forwarded to kit_tools.queue_exec_patch.  The entry
        is removed from the pending list once execution completes.

        Args:
            code: Python source to execute inside Kit.
            description: Human-readable label for audit and tracing.
            priority: Higher values run first (sort key = -priority).

        Returns:
            Dict with keys:
                - queued (bool): Whether Kit accepted the patch.
                - priority (int): The priority passed in.
                - queue_depth (int): Snapshot of queue length at submit time.
        """
        from .. import kit_tools  # noqa: PLC0415
        self._counter += 1
        patch = LockedPatch(
            sort_key=(-int(priority), self._counter),
            code=code,
            description=description,
            priority=int(priority),
        )
        async with self._lock:
            self._pending.append(patch)
            self._pending.sort()
            queue_depth = len(self._pending)
        result = await kit_tools.queue_exec_patch(code, description)
        async with self._lock:
            for idx, p in enumerate(self._pending):
                if p is patch:
                    self._pending.pop(idx)
                    break
        return {
            "queued": bool(result.get("queued", False)) if isinstance(result, dict) else False,
            "priority": int(priority),
            "queue_depth": queue_depth,
        }

    def pending(self) -> int:
        """Return the number of patches currently in the queue."""
        return len(self._pending)


# Singleton instance — workflow uses get_write_lock_queue() to access.
WRITE_LOCK_QUEUE = StageWriteLockQueue()


def get_write_lock_queue() -> StageWriteLockQueue:
    """Return the shared StageWriteLockQueue singleton."""
    return WRITE_LOCK_QUEUE


def get_turn_recorder():
    """Return the shared TurnRecorder singleton.

    Phase 8 wave 25 — the singleton currently lives in
    `tool_executor.py:_turn_recorder` (a TurnRecorder instantiated at
    module load). We delegate to that instance so both old and new
    callers see the same recorder. A future wave can flip the
    canonical home to this module.

    CONC-1 (2026-05-14): The double-check below is protected by
    `_TURN_RECORDER_LOCK` so concurrent callers cannot race past the
    `is None` check and create two TurnRecorder instances (each of which
    would write to the same `workspace/finetune_data/sessions/` directory
    but would otherwise be disjoint). The lock is held only during the
    one-time instantiation; subsequent calls hit the fast path (no lock
    acquisition needed because Python attribute reads are atomic).
    """
    global _TURN_RECORDER_SINGLETON
    # Fast path: once initialized, no lock acquisition.
    if _TURN_RECORDER_SINGLETON is not None:
        return _TURN_RECORDER_SINGLETON
    with _TURN_RECORDER_LOCK:
        # Re-check under the lock (another waiter may have initialized
        # while we were blocked).
        if _TURN_RECORDER_SINGLETON is None:
            try:
                from .. import tool_executor as _te
                _TURN_RECORDER_SINGLETON = _te._turn_recorder
            except (ImportError, AttributeError):
                # Fallback: instantiate our own if tool_executor no longer has it.
                from ...finetune.turn_recorder import TurnRecorder
                _TURN_RECORDER_SINGLETON = TurnRecorder()
    return _TURN_RECORDER_SINGLETON



# ---------------------------------------------------------------------------
# Phase 14 + 16 (2026-05-13): migrated from tool_executor.py.

_WORKFLOWS: Dict[str, Dict[str, Any]] = {}

# CONC-1 (2026-05-14): the workflow-mutation handlers in handlers/workflow.py
# (approve_workflow_checkpoint, edit_workflow_plan, cancel_workflow, ...)
# all perform deep read-modify-write sequences against `_WORKFLOWS[wf_id]`
# (e.g. read `wf["status"]`, append to `wf["events"]`, write `wf["status"]`).
# Without serialization these can interleave on the same wf_id, producing
# inconsistent state such as missing event entries or status transitions
# that contradict the appended decision.
#
# Strategy: per-workflow lock attached to each workflow dict at creation
# time (key: "_lock"). Handlers acquire `wf["_lock"]` for the duration of
# their RMW. This avoids global serialization across unrelated wf_ids
# (two different wf_ids run in parallel) while still protecting deep
# mutations on the same wf_id.
#
# A small companion lock guards the membership of `_WORKFLOWS` itself —
# the get-or-create idiom needs it so two writers cannot race on the
# initial `_WORKFLOWS[wf_id] = workflow` assignment.

_WORKFLOWS_REGISTRY_LOCK: _threading.Lock = _threading.Lock()


def make_workflow_lock() -> _threading.Lock:
    """Create a per-workflow lock to attach at the `_lock` field.

    Centralized factory so callers don't need to import `threading`
    directly; also gives a single point to swap the implementation
    (e.g. to RLock) if a handler ever needs re-entrant acquisition.
    """
    return _threading.Lock()

_WORKFLOW_TEMPLATES: Dict[str, Dict[str, Any]] = {
    "rl_training": {
        "description": "Full RL training pipeline (W1 from spec)",
        "phases": [
            {"name": "plan",        "checkpoint": True,  "error_fix": False},
            {"name": "env_creation","checkpoint": False, "error_fix": True},
            {"name": "reward",      "checkpoint": True,  "error_fix": False},
            {"name": "training",    "checkpoint": False, "error_fix": False},
            {"name": "results",     "checkpoint": True,  "error_fix": False},
            {"name": "deploy",      "checkpoint": True,  "error_fix": False},
        ],
        "default_params": {
            "num_envs": 64,
            "env_spacing": 2.5,
            "algo": "ppo",
            "num_iterations": 5000,
        },
    },
    "robot_import": {
        "description": "Robot import & configuration (W2 from spec)",
        "phases": [
            {"name": "plan",            "checkpoint": True,  "error_fix": False},
            {"name": "import",          "checkpoint": False, "error_fix": True},
            {"name": "verify",          "checkpoint": False, "error_fix": False},
            {"name": "auto_fix",        "checkpoint": True,  "error_fix": False},
            {"name": "motion_planning", "checkpoint": False, "error_fix": True},
            {"name": "report",          "checkpoint": False, "error_fix": False},
        ],
        "default_params": {
            "fix_profile": "auto",
        },
    },
    "sim_debugging": {
        "description": "Simulation debugging with autonomous error-fix loop (W4 from spec)",
        "phases": [
            {"name": "diagnose",   "checkpoint": False, "error_fix": False},
            {"name": "hypothesis", "checkpoint": False, "error_fix": False},
            {"name": "fix",        "checkpoint": True,  "error_fix": True},
            {"name": "verify",     "checkpoint": False, "error_fix": False},
            {"name": "report",     "checkpoint": False, "error_fix": False},
        ],
        "default_params": {
            "max_hypothesis_iterations": 3,
        },
    },
    # Phase 34 — assemble_pick_place_cell (workflow_template_pick_place.py)
    "assemble_pick_place_cell": {
        "description": "Build a pick-place cell from scratch: robot + workpiece + destination",
        "phases": [
            {"name": "load_template",    "checkpoint": True,  "error_fix": False},
            {"name": "place_objects",    "checkpoint": False, "error_fix": True},
            {"name": "teach_grasp_pose", "checkpoint": True,  "error_fix": False},
            {"name": "setup_controller", "checkpoint": False, "error_fix": True},
            {"name": "smoke_test",       "checkpoint": True,  "error_fix": True},
        ],
        "default_params": {
            "robot_class": "franka_panda",
            "workpiece_class": "cube_small",
            "destination_class": "bin",
        },
    },
    # Phase 35 — validate_robot_import (workflow_template_validate_robot.py)
    "validate_robot_import": {
        "description": "Import robot + verify articulation + check collision meshes",
        "phases": [
            {"name": "import_robot",          "checkpoint": False, "error_fix": True},
            {"name": "verify_articulation",   "checkpoint": True,  "error_fix": True},
            {"name": "check_collision_meshes","checkpoint": False, "error_fix": True},
            {"name": "test_motion",           "checkpoint": True,  "error_fix": False},
        ],
        "default_params": {"robot_name": "franka_panda"},
    },
    # Phase 36 — generate_sdg_dataset (workflow_template_sdg.py)
    "generate_sdg_dataset": {
        "description": "Synthetic data pipeline: scene → DR ranges → render → export",
        "phases": [
            {"name": "configure_scene",     "checkpoint": True,  "error_fix": False},
            {"name": "configure_dr_ranges", "checkpoint": True,  "error_fix": False},
            {"name": "preview_render",      "checkpoint": True,  "error_fix": True},
            {"name": "generate_dataset",    "checkpoint": False, "error_fix": False},
            {"name": "validate_annotations","checkpoint": True,  "error_fix": True},
            {"name": "export",              "checkpoint": True,  "error_fix": False},
        ],
        "default_params": {"num_samples": 1000, "writer_format": "coco"},
    },
}

__all__ = [
    "WorkflowState",
    "EurekaState",
    "TrainingState",
    "DRState",
    "BridgeState",
    "WORKFLOWS",
    "_WORKFLOW_TEMPLATES",
    "_WORKFLOWS",
    "_WORKFLOWS_REGISTRY_LOCK",
    "make_workflow_lock",
    "EUREKA",
    "TRAINING",
    "ASYNC_TASKS",
    "ASYNC_TASKS_LOCK",
    "LockedPatch",
    "StageWriteLockQueue",
    "WRITE_LOCK_QUEUE",
    "get_write_lock_queue",
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
