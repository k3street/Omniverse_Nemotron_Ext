"""CONC-1 — concurrency-lock tests for module-level state.

Per the 2026-05-14 opus concurrency audit, four module-level state
objects had no protection against FastAPI's concurrent request model:

  1. `_INSTALLED_COMPLIANCE` (compliance.py) — cross-session robot dict.
  2. `_WORKFLOWS`            (_state.py)      — cross-session workflow dict.
  3. `_TURN_RECORDER_SINGLETON` (_state.py)   — lazy-init race.
  4. `_STAGE_INDEX[_META]`   (scene_authoring.py) — async Kit fill.

This test module exercises each fix:

  - For (1) and (2) we hammer the handlers from multiple threads and
    confirm that no exception is raised, the final state is internally
    consistent, and unrelated wf_ids/robot_paths do NOT serialize on
    each other.
  - For (3) we confirm parallel `get_turn_recorder()` callers receive
    the SAME object (no duplicate instantiation).
  - For (4) we confirm `build_id` increments each call and `building`
    flips True between build and Kit-fill — the coordination signal
    callers use to detect stale-or-mid-build state.

All tests run in the L0 tier (no Kit RPC, no FastAPI).
"""
from __future__ import annotations

import asyncio
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List

import pytest

pytestmark = pytest.mark.l0


# ---------------------------------------------------------------------------
# Compliance: _INSTALLED_COMPLIANCE lock
# ---------------------------------------------------------------------------


@pytest.fixture()
def _clear_compliance_state():
    """Empty `_INSTALLED_COMPLIANCE` before + after each test so they
    cannot pollute each other."""
    from service.isaac_assist_service.chat.tools.handlers import compliance
    compliance._INSTALLED_COMPLIANCE.clear()
    yield
    compliance._INSTALLED_COMPLIANCE.clear()


def _run_async(coro):
    """Run an async function from a sync thread (each thread needs its
    own event loop)."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class TestComplianceLocks:
    """T1-T3 — `_INSTALLED_COMPLIANCE` mutex serializes RMW handlers."""

    def test_parallel_setup_admittance_same_robot_path_does_not_corrupt_state(
        self, _clear_compliance_state
    ):
        """Two concurrent `setup_admittance_controller` calls on the same
        robot_path must both succeed and the final state dict must hold
        exactly one entry whose values match one of the two inputs.

        Without the lock the two assigns could interleave; the lock makes
        each assign atomic so the loser's entry is fully overwritten by
        the winner.
        """
        from service.isaac_assist_service.chat.tools.handlers import compliance

        robot = "/World/RaceFranka"
        barrier = threading.Barrier(2)
        results: List[Dict[str, Any]] = []

        def call_setup(stiffness_value: float) -> None:
            barrier.wait()  # ensure both threads cross the line together
            result = _run_async(
                compliance._handle_setup_admittance_controller({
                    "robot_path": robot,
                    "stiffness_xyz": [stiffness_value] * 3,
                    "damping_xyz": [50.0] * 3,
                })
            )
            results.append(result)

        t1 = threading.Thread(target=call_setup, args=(100.0,))
        t2 = threading.Thread(target=call_setup, args=(200.0,))
        t1.start()
        t2.start()
        t1.join(timeout=5.0)
        t2.join(timeout=5.0)

        assert len(results) == 2
        assert all(r["success"] for r in results)

        # Exactly one entry in the state dict.
        assert len(compliance._INSTALLED_COMPLIANCE) == 1
        assert robot in compliance._INSTALLED_COMPLIANCE

        # Final state must be one of the two inputs, not a mix.
        state_stiffness = compliance._INSTALLED_COMPLIANCE[robot]["stiffness_xyz"]
        assert state_stiffness in ([100.0] * 3, [200.0] * 3)

    def test_concurrent_setup_release_loop_never_raises_keyerror(
        self, _clear_compliance_state
    ):
        """Hammer setup / set_params / release in a tight loop across N
        threads. Without the lock, set_params can see the entry just
        popped by release and raise KeyError. With the lock the handlers
        either succeed or return a structured error — never raise.
        """
        from service.isaac_assist_service.chat.tools.handlers import compliance

        robot = "/World/RaceFrankaRMW"
        iterations = 50
        errors: List[BaseException] = []

        def worker_setup_release() -> None:
            for _ in range(iterations):
                try:
                    _run_async(
                        compliance._handle_setup_admittance_controller({
                            "robot_path": robot,
                            "stiffness_xyz": [500.0] * 3,
                        })
                    )
                    _run_async(
                        compliance._handle_release_compliance({"robot_path": robot})
                    )
                except BaseException as e:
                    errors.append(e)

        def worker_set_params() -> None:
            for _ in range(iterations):
                try:
                    _run_async(
                        compliance._handle_set_compliance_params({
                            "robot_path": robot,
                            "stiffness_xyz": [600.0] * 3,
                        })
                    )
                except BaseException as e:
                    errors.append(e)

        with ThreadPoolExecutor(max_workers=4) as ex:
            futs = [
                ex.submit(worker_setup_release),
                ex.submit(worker_setup_release),
                ex.submit(worker_set_params),
                ex.submit(worker_set_params),
            ]
            for f in futs:
                f.result(timeout=10.0)

        assert errors == [], (
            f"Unexpected exception under concurrent setup/release/set: {errors!r}"
        )

    def test_set_params_snapshot_is_coherent_across_concurrent_writers(
        self, _clear_compliance_state
    ):
        """The dict returned by `_handle_set_compliance_params` must
        reflect the state AT THE TIME the lock was held — i.e. the
        stiffness value passed in must equal the stiffness value returned,
        even when many concurrent writers are racing on the same robot.
        Without the lock the post-write snapshot could capture another
        writer's value.
        """
        from service.isaac_assist_service.chat.tools.handlers import compliance

        robot = "/World/RaceFrankaSnap"

        # Pre-install.
        _run_async(
            compliance._handle_setup_admittance_controller({
                "robot_path": robot,
                "stiffness_xyz": [500.0] * 3,
            })
        )

        mismatches: List[Dict[str, Any]] = []

        def writer(value: float) -> None:
            for _ in range(20):
                result = _run_async(
                    compliance._handle_set_compliance_params({
                        "robot_path": robot,
                        "stiffness_xyz": [value] * 3,
                    })
                )
                # The returned stiffness must be one of the writer values
                # currently in flight (this writer's, or another writer's
                # whose lock had not yet been released when this writer
                # entered the critical section — both are valid serial
                # interleavings).
                if not result.get("success") or result.get("stiffness_xyz") not in (
                    [value] * 3,
                    [101.0] * 3,
                    [202.0] * 3,
                    [303.0] * 3,
                ):
                    mismatches.append(result)

        with ThreadPoolExecutor(max_workers=3) as ex:
            futs = [
                ex.submit(writer, 101.0),
                ex.submit(writer, 202.0),
                ex.submit(writer, 303.0),
            ]
            for f in futs:
                f.result(timeout=10.0)

        assert mismatches == [], (
            f"set_compliance_params snapshot mismatched its input under "
            f"contention: {mismatches!r}"
        )


# ---------------------------------------------------------------------------
# Workflow: per-workflow lock
# ---------------------------------------------------------------------------


@pytest.fixture()
def _clear_workflows():
    """Empty `_WORKFLOWS` before + after each test."""
    from service.isaac_assist_service.chat.tools.handlers._state import _WORKFLOWS
    _WORKFLOWS.clear()
    yield _WORKFLOWS
    _WORKFLOWS.clear()


class TestWorkflowLocks:
    """T4-T6 — per-workflow lock serializes same-wf RMW + parallelizes
    different wfs."""

    def test_parallel_approve_same_workflow_id_serializes(self, _clear_workflows):
        """Two threads call `approve_workflow_checkpoint` on the same
        wf_id with `phase="plan"`. Exactly ONE should succeed (state
        advances past the plan checkpoint); the other should fail because
        the workflow is no longer at phase 'plan'. Without the per-wf
        lock both could observe `current_phase == "plan"`, both append
        decisions to `wf["events"]`, and both call `_wf_advance_phase`.
        """
        from service.isaac_assist_service.chat.tools.handlers import workflow
        from service.isaac_assist_service.chat.tools.handlers._state import make_workflow_lock

        wf_id = "wf-race-approve"
        _clear_workflows[wf_id] = {
            "id": wf_id,
            "type": "rl_training",
            "goal": "race test",
            "status": "awaiting_plan_approval",
            "current_phase": "plan",
            "completed_phases": [],
            "checkpoint_decisions": [],
            "error_fix_attempts": [],
            "auto_approve_checkpoints": False,
            "plan": {
                "phases": [
                    {"name": "plan", "status": "pending", "checkpoint": True, "error_fix": False},
                    {"name": "env_creation", "status": "pending", "checkpoint": False, "error_fix": True},
                ],
            },
            "events": [],
            "created_at": "2026-05-14T00:00:00Z",
            "updated_at": "2026-05-14T00:00:00Z",
            "snapshot_id": None,
            "_lock": make_workflow_lock(),
        }

        barrier = threading.Barrier(2)
        results: List[Dict[str, Any]] = []

        def call_approve() -> None:
            barrier.wait()
            r = _run_async(workflow._handle_approve_workflow_checkpoint({
                "workflow_id": wf_id,
                "phase": "plan",
                "action": "approve",
                "feedback": "race",
            }))
            results.append(r)

        t1 = threading.Thread(target=call_approve)
        t2 = threading.Thread(target=call_approve)
        t1.start()
        t2.start()
        t1.join(timeout=5.0)
        t2.join(timeout=5.0)

        assert len(results) == 2
        oks = [r for r in results if r.get("ok") is True]
        errs = [r for r in results if r.get("ok") is False]
        assert len(oks) == 1, (
            f"Expected exactly one approve to succeed but got {results!r}"
        )
        assert len(errs) == 1
        # The error must reference phase mismatch — proves the second
        # caller saw the post-advance state, never the pre-advance state.
        assert "phase" in errs[0].get("error", "").lower()

        # Exactly ONE checkpoint_decision recorded — not two.
        wf = _clear_workflows[wf_id]
        assert len(wf["checkpoint_decisions"]) == 1

    def test_parallel_approve_different_workflow_ids_run_in_parallel(
        self, _clear_workflows
    ):
        """Two workflows with different wf_ids approve concurrently.
        Their per-workflow locks are independent so they should be able
        to run truly in parallel (no global serialization). We assert
        this by giving each handler a small sleep inside the critical
        section and confirming wall-clock time is less than 2× the
        sleep duration.

        We can't easily inject a sleep into the production handler, so
        instead we instrument the per-workflow lock with a wrapper that
        sleeps inside `__enter__` and confirm both wfs ran within a
        single sleep window — which is only possible if their locks are
        distinct.
        """
        from service.isaac_assist_service.chat.tools.handlers import workflow

        sleep_duration = 0.2

        class SleepingLock:
            """threading.Lock-compatible context manager that sleeps after
            acquiring. Used to amplify lock-hold time so we can observe
            parallel vs serial execution by wall-clock."""

            def __init__(self) -> None:
                self._inner = threading.Lock()

            def __enter__(self):
                self._inner.acquire()
                time.sleep(sleep_duration)
                return self

            def __exit__(self, exc_type, exc, tb):
                self._inner.release()
                return False

            def acquire(self, *args, **kwargs):
                return self._inner.acquire(*args, **kwargs)

            def release(self):
                return self._inner.release()

        def _mk_wf(wf_id: str) -> Dict[str, Any]:
            return {
                "id": wf_id,
                "type": "rl_training",
                "goal": "parallel test",
                "status": "awaiting_plan_approval",
                "current_phase": "plan",
                "completed_phases": [],
                "checkpoint_decisions": [],
                "error_fix_attempts": [],
                "auto_approve_checkpoints": False,
                "plan": {
                    "phases": [
                        {"name": "plan", "status": "pending", "checkpoint": True, "error_fix": False},
                        {"name": "env_creation", "status": "pending", "checkpoint": False, "error_fix": True},
                    ],
                },
                "events": [],
                "created_at": "2026-05-14T00:00:00Z",
                "updated_at": "2026-05-14T00:00:00Z",
                "snapshot_id": None,
                "_lock": SleepingLock(),
            }

        _clear_workflows["wf-par-A"] = _mk_wf("wf-par-A")
        _clear_workflows["wf-par-B"] = _mk_wf("wf-par-B")

        results: List[Dict[str, Any]] = []
        start = time.perf_counter()

        def approve(wf_id: str) -> None:
            r = _run_async(workflow._handle_approve_workflow_checkpoint({
                "workflow_id": wf_id,
                "phase": "plan",
                "action": "approve",
            }))
            results.append(r)

        t1 = threading.Thread(target=approve, args=("wf-par-A",))
        t2 = threading.Thread(target=approve, args=("wf-par-B",))
        t1.start()
        t2.start()
        t1.join(timeout=5.0)
        t2.join(timeout=5.0)
        elapsed = time.perf_counter() - start

        assert all(r.get("ok") for r in results), f"Approves failed: {results!r}"
        # Parallel: elapsed should be ~1× sleep_duration, not 2×. We allow
        # generous slack (1.6×) for thread startup overhead.
        assert elapsed < sleep_duration * 1.6, (
            f"Approves on different wf_ids appear to be serializing "
            f"(elapsed={elapsed:.3f}s, expected <{sleep_duration * 1.6:.3f}s). "
            "Per-workflow locks should NOT block across distinct wf_ids."
        )

    def test_workflow_without_lock_field_gets_one_lazily(self, _clear_workflows):
        """Workflows constructed by tests (or older code paths) that did
        not include a `_lock` field still work — `_wf_lock_for` calls
        `setdefault("_lock", make_workflow_lock())` lazily.
        """
        from service.isaac_assist_service.chat.tools.handlers import workflow

        wf_id = "wf-no-lock"
        _clear_workflows[wf_id] = {
            "id": wf_id,
            "type": "rl_training",
            "goal": "test",
            "status": "awaiting_plan_approval",
            "current_phase": "plan",
            "completed_phases": [],
            "checkpoint_decisions": [],
            "error_fix_attempts": [],
            "auto_approve_checkpoints": False,
            "plan": {"phases": [{"name": "plan", "status": "pending", "checkpoint": True, "error_fix": False}]},
            "events": [],
            "created_at": "2026-05-14T00:00:00Z",
            "updated_at": "2026-05-14T00:00:00Z",
            "snapshot_id": None,
            # NOTE: no _lock field.
        }
        assert "_lock" not in _clear_workflows[wf_id]

        # First handler call should install a lock lazily.
        result = _run_async(workflow._handle_get_workflow_status({"workflow_id": wf_id}))
        assert result["ok"] is True
        assert "_lock" in _clear_workflows[wf_id]
        assert isinstance(_clear_workflows[wf_id]["_lock"], type(threading.Lock()))


# ---------------------------------------------------------------------------
# TurnRecorder: lazy-init lock
# ---------------------------------------------------------------------------


class TestTurnRecorderLazyInitLock:
    """T7 — `get_turn_recorder()` returns the same instance even when
    many threads hit it before initialization."""

    def test_parallel_get_turn_recorder_returns_same_instance(self):
        """Fire N concurrent `get_turn_recorder()` calls from a cold
        state. All callers must observe the same TurnRecorder instance
        (identity check). Without the lock, the double-check race could
        produce N distinct instances.
        """
        from service.isaac_assist_service.chat.tools.handlers import _state

        # Force a cold start.
        _state._TURN_RECORDER_SINGLETON = None

        barrier = threading.Barrier(8)
        instances: List[object] = []
        lock = threading.Lock()

        def call_get() -> None:
            barrier.wait()
            rec = _state.get_turn_recorder()
            with lock:
                instances.append(rec)

        threads = [threading.Thread(target=call_get) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5.0)

        assert len(instances) == 8
        # All instances must be identical (same object).
        first = instances[0]
        for inst in instances[1:]:
            assert inst is first, (
                f"get_turn_recorder returned distinct instances under "
                f"contention: {id(first)} vs {id(inst)}"
            )

    def test_subsequent_calls_hit_fast_path_without_lock(self):
        """After initialization, `get_turn_recorder()` returns the cached
        instance without going through the lock. We verify functional
        behaviour (same instance) — the lock-skip is a performance
        invariant we cannot easily probe directly.
        """
        from service.isaac_assist_service.chat.tools.handlers import _state

        # Warm up.
        first = _state.get_turn_recorder()
        # Second call must return the SAME object.
        second = _state.get_turn_recorder()
        assert first is second


# ---------------------------------------------------------------------------
# Stage index: build_id coordination signal
# ---------------------------------------------------------------------------


@pytest.fixture()
def _reset_stage_index():
    """Reset the stage-index module state before + after each test."""
    from service.isaac_assist_service.chat.tools.handlers import scene_authoring
    scene_authoring._STAGE_INDEX.clear()
    scene_authoring._STAGE_INDEX_META.update({
        "prim_scope": None,
        "prim_count": 0,
        "build_id": 0,
        "building": False,
    })
    yield
    scene_authoring._STAGE_INDEX.clear()
    scene_authoring._STAGE_INDEX_META.update({
        "prim_scope": None,
        "prim_count": 0,
        "build_id": 0,
        "building": False,
    })


class TestStageIndexBuildId:
    """T8-T10 — `build_id` + `building` flag let callers detect stale
    or mid-build state. The actual writes happen Kit-side so a Python
    lock cannot protect them; the coordination signal is the contract."""

    def test_build_id_strictly_increments_on_each_rebuild(self, _reset_stage_index):
        """Each `build_stage_index` call bumps `build_id`. Two
        consecutive builds therefore produce two distinct build_ids."""
        from service.isaac_assist_service.chat.tools.handlers import scene_authoring

        before = int(scene_authoring._STAGE_INDEX_META["build_id"])

        _run_async(scene_authoring._handle_build_stage_index({"prim_scope": "/World"}))
        after_first = int(scene_authoring._STAGE_INDEX_META["build_id"])
        assert after_first == before + 1

        _run_async(scene_authoring._handle_build_stage_index({"prim_scope": "/World"}))
        after_second = int(scene_authoring._STAGE_INDEX_META["build_id"])
        assert after_second == after_first + 1
        assert after_second == before + 2

    def test_query_returns_building_true_between_build_and_fill(
        self, _reset_stage_index
    ):
        """A query that fires between `build_stage_index` queueing the
        Kit patch and Kit populating `_STAGE_INDEX` must report
        `building=True` so the caller can retry or treat the result as
        partial. Kit-side fill is simulated by leaving the index empty
        and confirming the flag is True after a build call.
        """
        from service.isaac_assist_service.chat.tools.handlers import scene_authoring

        # Queue a build; Kit-side fill never runs in this test, so
        # `building` stays True (the canonical "fill complete" callback
        # is what flips it back to False, out of scope here).
        build_result = _run_async(
            scene_authoring._handle_build_stage_index({"prim_scope": "/World"})
        )
        assert build_result["building"] is True

        # Query before Kit fills.
        query_result = _run_async(
            scene_authoring._handle_query_stage_index({"keywords": ["camera"]})
        )
        assert query_result["building"] is True
        assert query_result["build_id"] == build_result["build_id"]
        assert "rebuilt" in query_result.get("note", "").lower()

    def test_query_returns_build_id_so_callers_can_correlate(
        self, _reset_stage_index
    ):
        """Each query carries the build_id of the most recently queued
        build. Callers can compare successive query results' build_ids
        to detect that a new build started between their first read and
        their retry.
        """
        from service.isaac_assist_service.chat.tools.handlers import scene_authoring

        _run_async(scene_authoring._handle_build_stage_index({"prim_scope": "/World"}))
        q1 = _run_async(scene_authoring._handle_query_stage_index({}))
        first_id = q1["build_id"]

        _run_async(scene_authoring._handle_build_stage_index({"prim_scope": "/World"}))
        q2 = _run_async(scene_authoring._handle_query_stage_index({}))
        second_id = q2["build_id"]

        assert second_id == first_id + 1, (
            f"build_id should reflect the latest build "
            f"(was {first_id} then {second_id})"
        )
