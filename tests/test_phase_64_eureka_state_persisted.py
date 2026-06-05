"""Phase 64 contract — Eureka run state actually persisted.

Landed 2026-05-14. Covers the writer wire-up in
``handlers/training.py``: generate_reward initialises a run entry,
iterate_reward increments + auto-completes the run, and eureka_status
surfaces live state instead of always returning "not_found".
"""
import pytest

pytestmark = pytest.mark.l0


def test_phase_64_metadata():
    from service.isaac_assist_service.multimodal.eureka_state_persisted import (
        get_phase_metadata,
    )
    md = get_phase_metadata()
    assert md["phase"] == 64
    assert md["status"] == "landed"
    assert "Eureka" in md["title"]
    assert "Phase 64" in md["spec_ref"]


@pytest.fixture(autouse=True)
def _reset_eureka_runs():
    """Clear EUREKA.runs between tests so they don't leak ids."""
    from service.isaac_assist_service.chat.tools.handlers._state import EUREKA
    EUREKA.runs.clear()
    yield
    EUREKA.runs.clear()


@pytest.fixture
def tmp_env_source(tmp_path):
    """Minimal DirectRLEnv source file so generate_reward returns success."""
    env_path = tmp_path / "env.py"
    env_path.write_text(
        "class ReachEnv:\n"
        "    \"\"\"DirectRLEnv subclass for unit-test scope.\"\"\"\n"
        "    def __init__(self): ...\n"
    )
    return str(env_path)


@pytest.mark.asyncio
async def test_generate_reward_initialises_run_state(tmp_env_source):
    from service.isaac_assist_service.chat.tools.handlers.training import (
        _handle_generate_reward,
    )
    from service.isaac_assist_service.chat.tools.handlers._state import EUREKA

    result = await _handle_generate_reward({
        "task_description": "reach the target",
        "env_source_path": tmp_env_source,
        "num_iterations": 3,
        "num_candidates": 4,
    })

    run_id = result["run_id"]
    assert run_id and isinstance(run_id, str)
    assert run_id in EUREKA.runs

    run = EUREKA.runs[run_id]
    assert run["status"] == "initialized"
    assert run["task_description"] == "reach the target"
    assert run["total_iterations"] == 3
    assert run["num_candidates"] == 4
    assert run["current_iteration"] == 0
    assert run["candidates_evaluated"] == 0
    assert run["best_fitness"] == 0.0
    assert run["best_reward_code"] is None
    assert "started_at" in run


@pytest.mark.asyncio
async def test_eureka_status_returns_live_state_after_generate(tmp_env_source):
    from service.isaac_assist_service.chat.tools.handlers.training import (
        _handle_generate_reward,
        _handle_eureka_status,
    )

    gen = await _handle_generate_reward({
        "task_description": "lift cube",
        "env_source_path": tmp_env_source,
    })
    run_id = gen["run_id"]

    status = await _handle_eureka_status({"run_id": run_id})
    assert status["run_id"] == run_id
    assert status["status"] == "initialized"
    assert status["current_iteration"] == 0
    assert status["total_iterations"] == 5  # default
    assert status["candidates_evaluated"] == 0


@pytest.mark.asyncio
async def test_iterate_reward_increments_state(tmp_env_source):
    from service.isaac_assist_service.chat.tools.handlers.training import (
        _handle_generate_reward,
        _handle_iterate_reward,
    )
    from service.isaac_assist_service.chat.tools.handlers._state import EUREKA

    gen = await _handle_generate_reward({
        "task_description": "balance pole",
        "env_source_path": tmp_env_source,
        "num_iterations": 3,
        "num_candidates": 2,
    })
    run_id = gen["run_id"]

    iter1 = await _handle_iterate_reward({
        "prev_reward_code": "def compute_reward(self): return 0.1",
        "metrics": {"fitness": 0.5, "task_success_rate": 0.2, "components": {}},
        "run_id": run_id,
    })

    assert iter1["run_id"] == run_id
    assert iter1["run_status"] == "running"
    assert iter1["current_iteration"] == 1
    assert iter1["candidates_evaluated"] == 2

    run = EUREKA.runs[run_id]
    assert run["best_fitness"] == 0.5
    assert run["best_reward_code"] == "def compute_reward(self): return 0.1"


@pytest.mark.asyncio
async def test_iterate_reward_auto_completes_on_final_iteration(tmp_env_source):
    from service.isaac_assist_service.chat.tools.handlers.training import (
        _handle_generate_reward,
        _handle_iterate_reward,
    )
    from service.isaac_assist_service.chat.tools.handlers._state import EUREKA

    gen = await _handle_generate_reward({
        "task_description": "open drawer",
        "env_source_path": tmp_env_source,
        "num_iterations": 2,
    })
    run_id = gen["run_id"]

    metrics = {"fitness": 0.3, "task_success_rate": 0.1, "components": {}}
    _ = await _handle_iterate_reward({
        "prev_reward_code": "def compute_reward(self): return 0.0",
        "metrics": metrics,
        "run_id": run_id,
    })
    second = await _handle_iterate_reward({
        "prev_reward_code": "def compute_reward(self): return 0.0",
        "metrics": metrics,
        "run_id": run_id,
    })

    assert second["run_status"] == "completed"
    assert second["current_iteration"] == 2

    run = EUREKA.runs[run_id]
    assert run["status"] == "completed"
    assert "finished_at" in run


@pytest.mark.asyncio
async def test_iterate_reward_tracks_best_fitness_monotonically(tmp_env_source):
    from service.isaac_assist_service.chat.tools.handlers.training import (
        _handle_generate_reward,
        _handle_iterate_reward,
    )
    from service.isaac_assist_service.chat.tools.handlers._state import EUREKA

    gen = await _handle_generate_reward({
        "task_description": "walk forward",
        "env_source_path": tmp_env_source,
        "num_iterations": 5,
    })
    run_id = gen["run_id"]

    fitnesses = [0.2, 0.7, 0.5, 0.9, 0.6]
    for fit in fitnesses:
        await _handle_iterate_reward({
            "prev_reward_code": f"def compute_reward(self): return {fit}",
            "metrics": {"fitness": fit, "task_success_rate": 0.0, "components": {}},
            "run_id": run_id,
        })

    run = EUREKA.runs[run_id]
    assert run["best_fitness"] == max(fitnesses)
    assert run["best_reward_code"] == f"def compute_reward(self): return {max(fitnesses)}"


@pytest.mark.asyncio
async def test_iterate_reward_unknown_run_id():
    from service.isaac_assist_service.chat.tools.handlers.training import (
        _handle_iterate_reward,
    )

    result = await _handle_iterate_reward({
        "prev_reward_code": "def compute_reward(self): return 0.0",
        "metrics": {"fitness": 0.5, "components": {}},
        "run_id": "does-not-exist",
    })

    assert result["run_id"] == "does-not-exist"
    assert result["run_status"] == "unknown_run_id"
    # Mutation prompt is still produced — caller can still iterate manually.
    assert "mutation_prompt" in result


@pytest.mark.asyncio
async def test_iterate_reward_without_run_id_is_stateless():
    from service.isaac_assist_service.chat.tools.handlers.training import (
        _handle_iterate_reward,
    )
    from service.isaac_assist_service.chat.tools.handlers._state import EUREKA

    result = await _handle_iterate_reward({
        "prev_reward_code": "def compute_reward(self): return 0.0",
        "metrics": {"fitness": 0.5, "components": {}},
    })

    assert "run_id" not in result
    assert "run_status" not in result
    assert "mutation_prompt" in result
    assert EUREKA.runs == {}  # no run created
