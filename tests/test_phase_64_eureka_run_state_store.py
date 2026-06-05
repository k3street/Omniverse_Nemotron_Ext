"""Phase 64 — Eureka run state actually persisted.

Tests for EurekaRunStateStore (SQLite-backed).

All tests use l0 (no external deps).
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

pytestmark = pytest.mark.l0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _make_store(tmp_path: Path):
    from service.isaac_assist_service.multimodal.eureka_run_state_store import EurekaRunStateStore
    return EurekaRunStateStore(db_path=tmp_path / "eureka_test.db")


def _make_run(run_id: str = "run-001", status: str = "running"):
    from service.isaac_assist_service.multimodal.eureka_run_state_store import EurekaRun
    return EurekaRun(
        run_id=run_id,
        task_description="balance a pole",
        environment_id="env-cartpole-v1",
        started_at=_iso(),
        status=status,
    )


def _make_iteration(run_id: str = "run-001", idx: int = 0, score: float = 0.5):
    from service.isaac_assist_service.multimodal.eureka_run_state_store import EurekaIteration
    return EurekaIteration(
        run_id=run_id,
        iteration_idx=idx,
        reward_function_text="def reward(obs): return obs[0]",
        score=score,
        success_rate=0.6,
        n_episodes=10,
        errors=[],
        created_at=_iso(),
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestPhaseMetadata:
    def test_metadata(self):
        from service.isaac_assist_service.multimodal.eureka_run_state_store import get_phase_metadata
        md = get_phase_metadata()
        assert md["phase"] == 64
        assert md["status"] == "landed"
        assert "title" in md
        assert "spec_ref" in md


class TestSchemaInit:
    def test_schema_created_on_init(self, tmp_path: Path):
        """Store creation must produce both tables without raising."""
        import sqlite3
        store = _make_store(tmp_path)
        db_file = tmp_path / "eureka_test.db"
        assert db_file.exists()
        cx = sqlite3.connect(str(db_file))
        tables = {row[0] for row in cx.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        cx.close()
        store.close()
        assert "eureka_runs" in tables
        assert "eureka_iterations" in tables


class TestRunRoundTrip:
    def test_create_run_and_get_run(self, tmp_path: Path):
        store = _make_store(tmp_path)
        run = _make_run("run-rt-1")
        store.create_run(run)
        fetched = store.get_run("run-rt-1")
        assert fetched is not None
        assert fetched.run_id == "run-rt-1"
        assert fetched.task_description == "balance a pole"
        assert fetched.environment_id == "env-cartpole-v1"
        assert fetched.status == "running"
        assert fetched.best_score is None
        assert fetched.total_iterations == 0

    def test_get_run_missing_returns_none(self, tmp_path: Path):
        store = _make_store(tmp_path)
        assert store.get_run("does-not-exist") is None


class TestIterations:
    def test_record_iteration_adds_row(self, tmp_path: Path):
        store = _make_store(tmp_path)
        store.create_run(_make_run("r1"))
        store.record_iteration(_make_iteration("r1", 0, 0.42))
        iters = store.get_iterations("r1")
        assert len(iters) == 1
        assert iters[0].iteration_idx == 0
        assert iters[0].score == pytest.approx(0.42)

    def test_get_iterations_returns_list_ordered(self, tmp_path: Path):
        store = _make_store(tmp_path)
        store.create_run(_make_run("r2"))
        for i, s in enumerate([0.1, 0.3, 0.2]):
            store.record_iteration(_make_iteration("r2", i, s))
        iters = store.get_iterations("r2")
        assert [it.iteration_idx for it in iters] == [0, 1, 2]

    def test_record_iteration_higher_score_updates_best(self, tmp_path: Path):
        store = _make_store(tmp_path)
        store.create_run(_make_run("r3"))
        store.record_iteration(_make_iteration("r3", 0, 0.3))
        store.record_iteration(_make_iteration("r3", 1, 0.9))
        run = store.get_run("r3")
        assert run.best_score == pytest.approx(0.9)
        assert run.best_iteration == 1

    def test_record_iteration_lower_score_does_not_update_best(self, tmp_path: Path):
        store = _make_store(tmp_path)
        store.create_run(_make_run("r4"))
        store.record_iteration(_make_iteration("r4", 0, 0.8))
        store.record_iteration(_make_iteration("r4", 1, 0.2))
        run = store.get_run("r4")
        assert run.best_score == pytest.approx(0.8)
        assert run.best_iteration == 0

    def test_record_iteration_increments_total_iterations(self, tmp_path: Path):
        store = _make_store(tmp_path)
        store.create_run(_make_run("r5"))
        for i in range(5):
            store.record_iteration(_make_iteration("r5", i, float(i) * 0.1))
        run = store.get_run("r5")
        assert run.total_iterations == 5


class TestRunLifecycle:
    def test_mark_completed(self, tmp_path: Path):
        store = _make_store(tmp_path)
        store.create_run(_make_run("r6"))
        done_at = _iso()
        store.mark_completed("r6", done_at)
        run = store.get_run("r6")
        assert run.status == "completed"
        assert run.finished_at == done_at

    def test_mark_failed(self, tmp_path: Path):
        store = _make_store(tmp_path)
        store.create_run(_make_run("r7"))
        done_at = _iso()
        store.mark_failed("r7", done_at)
        run = store.get_run("r7")
        assert run.status == "failed"
        assert run.finished_at == done_at


class TestListRuns:
    def test_list_runs_filters_by_status(self, tmp_path: Path):
        store = _make_store(tmp_path)
        store.create_run(_make_run("rA", "running"))
        store.create_run(_make_run("rB", "running"))
        store.create_run(_make_run("rC", "completed"))
        running = store.list_runs(status_filter="running")
        completed = store.list_runs(status_filter="completed")
        assert len(running) == 2
        assert len(completed) == 1
        assert completed[0].run_id == "rC"

    def test_list_runs_no_filter_returns_all(self, tmp_path: Path):
        store = _make_store(tmp_path)
        for i in range(4):
            store.create_run(_make_run(f"list-{i}", "running"))
        all_runs = store.list_runs()
        assert len(all_runs) == 4


class TestBestIteration:
    def test_best_iteration_for_returns_highest_score(self, tmp_path: Path):
        store = _make_store(tmp_path)
        store.create_run(_make_run("r8"))
        store.record_iteration(_make_iteration("r8", 0, 0.1))
        store.record_iteration(_make_iteration("r8", 1, 0.95))
        store.record_iteration(_make_iteration("r8", 2, 0.7))
        best = store.best_iteration_for("r8")
        assert best is not None
        assert best.iteration_idx == 1
        assert best.score == pytest.approx(0.95)

    def test_best_iteration_for_missing_run_returns_none(self, tmp_path: Path):
        store = _make_store(tmp_path)
        assert store.best_iteration_for("ghost") is None


class TestCountRuns:
    def test_count_runs_accurate_after_multiple_inserts(self, tmp_path: Path):
        store = _make_store(tmp_path)
        assert store.count_runs() == 0
        for i in range(7):
            store.create_run(_make_run(f"cnt-{i}"))
        assert store.count_runs() == 7


class TestInMemoryDb:
    def test_in_memory_db_works(self):
        from service.isaac_assist_service.multimodal.eureka_run_state_store import EurekaRunStateStore
        store = EurekaRunStateStore(":memory:")
        store.create_run(_make_run("mem-1"))
        assert store.get_run("mem-1") is not None
        assert store.count_runs() == 1
        store.close()
