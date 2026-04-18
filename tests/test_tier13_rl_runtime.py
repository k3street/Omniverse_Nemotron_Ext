"""
L0 tests for Tier 13 — IsaacLab RL Runtime tools.

These tools introspect a RUNNING training subprocess. The tests stub out
both the run registry (_RUN_REGISTRY) and the IPC channel (_query_run_ipc)
so no live IsaacLab process is required.

Tools under test:
    T13.1  get_env_observations(env_id, run_id?)
    T13.2  get_env_rewards(env_id, run_id?)
    T13.3  get_env_termination_state(env_id, run_id?)
    T13.4  pause_training(run_id?)
    T13.5  checkpoint_training(run_id?, include_replay_buffer?, tag?)
"""
from __future__ import annotations

import time
import pytest

pytestmark = pytest.mark.l0

from service.isaac_assist_service.chat.tools.tool_schemas import ISAAC_SIM_TOOLS
from service.isaac_assist_service.chat.tools.tool_executor import (
    DATA_HANDLERS,
    _handle_get_env_observations,
    _handle_get_env_rewards,
    _handle_get_env_termination_state,
    _handle_pause_training,
    _handle_checkpoint_training,
)


TIER13_TOOL_NAMES = [
    "get_env_observations",
    "get_env_rewards",
    "get_env_termination_state",
    "pause_training",
    "checkpoint_training",
]


# ---------------------------------------------------------------------------
# Fixtures: stub the run registry + IPC channel
# ---------------------------------------------------------------------------

@pytest.fixture()
def fake_run(monkeypatch):
    """Install a single fake training run into _RUN_REGISTRY.

    Returns the (run_id, registry_entry, ipc_calls_log) tuple. Tests can
    customize ipc_responses to inject canned IPC replies per op.
    """
    import service.isaac_assist_service.chat.tools.tool_executor as te

    ipc_calls: list = []
    ipc_responses: dict = {
        "get_observations": {
            "step": 12345,
            "episode_step": 7,
            "observations": {
                "joint_pos": [0.1, 0.2, -0.3, 0.0, 1.5, -1.2, 0.8],
                "joint_vel": [0.0] * 7,
                "target_pose": [0.5, 0.0, 0.4, 1.0, 0.0, 0.0, 0.0],
            },
            "dtype": "float32",
            "shape": [21],
        },
        "get_rewards": {
            "step": 12345,
            "terms": [
                {"name": "tracking_lin_vel", "raw_value": 0.85, "weight": 1.0, "weighted": 0.85},
                {"name": "action_penalty", "raw_value": 0.02, "weight": -0.01, "weighted": -0.0002},
            ],
            "total_reward": 0.8498,
            "episode_return": 5.42,
        },
        "get_termination": {
            "termination_terms": {"time_out": False, "robot_fell": False, "success": False},
            "episode_step": 7,
            "max_episode_steps": 500,
            "last_reset_step": 12338,
        },
        "pause": {"signal_sent": "SIGUSR1", "step": 12345, "iteration": 514},
        "checkpoint": {
            "checkpoint_path": "/tmp/rl/manual_step_12345.pt",
            "step": 12345,
            "iteration": 514,
            "size_bytes": 12_345_678,
            "includes_replay_buffer": False,
            "save_duration_ms": 87.3,
        },
    }

    async def fake_ipc(entry, request):
        ipc_calls.append(request)
        op = request.get("op")
        resp = ipc_responses.get(op, {})
        # Allow per-op overrides via attribute mutation in tests
        if include_replay := request.get("include_replay_buffer"):
            resp = {**resp, "includes_replay_buffer": include_replay}
        return resp

    monkeypatch.setattr(te, "_query_run_ipc", fake_ipc)

    run_id = "test_run_42"
    entry = {
        "pid": 99999,
        "num_envs": 64,
        "state": "running",
        "launch_time": time.time(),
        "last_known_step": 12000,
        "last_known_iteration": 500,
        "max_episode_steps": 500,
        "ipc_socket": "/tmp/fake.sock",
        "ipc_handler": fake_ipc,
    }
    monkeypatch.setattr(te, "_RUN_REGISTRY", {run_id: entry})
    return run_id, entry, ipc_calls, ipc_responses


@pytest.fixture()
def empty_registry(monkeypatch):
    """Empty run registry — used for negative tests."""
    import service.isaac_assist_service.chat.tools.tool_executor as te
    monkeypatch.setattr(te, "_RUN_REGISTRY", {})


# ---------------------------------------------------------------------------
# Schema-level tests: every Tier 13 tool is declared and wired
# ---------------------------------------------------------------------------

class TestTier13Schemas:
    """The five Tier 13 tools must appear in ISAAC_SIM_TOOLS with rich descriptions."""

    @pytest.mark.parametrize("name", TIER13_TOOL_NAMES)
    def test_tool_in_schemas(self, name):
        names = [t["function"]["name"] for t in ISAAC_SIM_TOOLS]
        assert name in names, f"Tier 13 tool '{name}' missing from ISAAC_SIM_TOOLS"

    @pytest.mark.parametrize("name", TIER13_TOOL_NAMES)
    def test_tool_in_data_handlers(self, name):
        assert name in DATA_HANDLERS, f"Tier 13 tool '{name}' missing from DATA_HANDLERS"
        assert DATA_HANDLERS[name] is not None, f"Tier 13 tool '{name}' has None handler"

    @pytest.mark.parametrize("name", TIER13_TOOL_NAMES)
    def test_description_is_rich(self, name):
        """Rich descriptions must include WHAT/WHEN/RETURNS/CAVEATS sections."""
        tool = next(t for t in ISAAC_SIM_TOOLS if t["function"]["name"] == name)
        desc = tool["function"]["description"]
        for marker in ("WHAT:", "WHEN:", "RETURNS:", "CAVEATS:"):
            assert marker in desc, f"{name} description missing '{marker}' marker"
        assert len(desc) > 400, f"{name} description suspiciously short ({len(desc)} chars)"

    @pytest.mark.parametrize("name", TIER13_TOOL_NAMES)
    def test_parameters_well_formed(self, name):
        tool = next(t for t in ISAAC_SIM_TOOLS if t["function"]["name"] == name)
        params = tool["function"]["parameters"]
        assert params["type"] == "object"
        assert "properties" in params
        # The three env-introspection tools require env_id; pause/checkpoint do not.
        if name in ("get_env_observations", "get_env_rewards", "get_env_termination_state"):
            assert "env_id" in params["required"]
            assert params["properties"]["env_id"]["type"] == "integer"


# ---------------------------------------------------------------------------
# T13.1 — get_env_observations
# ---------------------------------------------------------------------------

class TestGetEnvObservations:

    @pytest.mark.asyncio
    async def test_returns_observation_tensor(self, fake_run):
        run_id, _entry, ipc_calls, _ = fake_run
        result = await _handle_get_env_observations({"env_id": 5, "run_id": run_id})
        assert "error" not in result
        assert result["env_id"] == 5
        assert result["run_id"] == run_id
        assert "observations" in result
        assert "joint_pos" in result["observations"]
        assert result["dtype"] == "float32"
        assert result["wall_time_ms"] >= 0
        # Verify the IPC call shape
        assert ipc_calls[-1] == {"op": "get_observations", "env_id": 5}

    @pytest.mark.asyncio
    async def test_defaults_to_most_recent_run(self, fake_run):
        run_id, _entry, _calls, _ = fake_run
        result = await _handle_get_env_observations({"env_id": 0})
        assert result["run_id"] == run_id

    @pytest.mark.asyncio
    async def test_rejects_out_of_range_env_id(self, fake_run):
        run_id, _entry, _calls, _ = fake_run
        result = await _handle_get_env_observations({"env_id": 999, "run_id": run_id})
        assert "error" in result
        assert "out of range" in result["error"]

    @pytest.mark.asyncio
    async def test_rejects_negative_env_id(self, fake_run):
        run_id, *_ = fake_run
        result = await _handle_get_env_observations({"env_id": -1, "run_id": run_id})
        assert "error" in result

    @pytest.mark.asyncio
    async def test_no_active_run(self, empty_registry):
        result = await _handle_get_env_observations({"env_id": 0})
        assert "error" in result
        assert "No active training run" in result["error"]


# ---------------------------------------------------------------------------
# T13.2 — get_env_rewards
# ---------------------------------------------------------------------------

class TestGetEnvRewards:

    @pytest.mark.asyncio
    async def test_returns_reward_breakdown(self, fake_run):
        run_id, *_ = fake_run
        result = await _handle_get_env_rewards({"env_id": 3, "run_id": run_id})
        assert "error" not in result
        assert result["env_id"] == 3
        assert "terms" in result
        assert len(result["terms"]) == 2
        names = {t["name"] for t in result["terms"]}
        assert "tracking_lin_vel" in names
        assert "action_penalty" in names
        assert result["total_reward"] == pytest.approx(0.8498)
        assert result["episode_return"] == pytest.approx(5.42)

    @pytest.mark.asyncio
    async def test_total_reward_computed_when_missing(self, fake_run, monkeypatch):
        """If IPC reply omits total_reward, handler should sum the weighted terms."""
        run_id, _entry, _calls, ipc_responses = fake_run
        ipc_responses["get_rewards"] = {
            "step": 100,
            "terms": [
                {"name": "a", "raw_value": 1.0, "weight": 1.0, "weighted": 0.5},
                {"name": "b", "raw_value": 1.0, "weight": 1.0, "weighted": 0.25},
            ],
            "episode_return": 1.5,
        }
        result = await _handle_get_env_rewards({"env_id": 0, "run_id": run_id})
        assert result["total_reward"] == pytest.approx(0.75)

    @pytest.mark.asyncio
    async def test_invalid_env_id(self, fake_run):
        run_id, *_ = fake_run
        result = await _handle_get_env_rewards({"env_id": 64, "run_id": run_id})
        assert "error" in result

    @pytest.mark.asyncio
    async def test_no_active_run(self, empty_registry):
        result = await _handle_get_env_rewards({"env_id": 0})
        assert "error" in result


# ---------------------------------------------------------------------------
# T13.3 — get_env_termination_state
# ---------------------------------------------------------------------------

class TestGetEnvTerminationState:

    @pytest.mark.asyncio
    async def test_returns_termination_flags(self, fake_run):
        run_id, *_ = fake_run
        result = await _handle_get_env_termination_state({"env_id": 2, "run_id": run_id})
        assert "error" not in result
        assert result["env_id"] == 2
        assert result["done"] is False
        assert result["success"] is False
        assert result["timeout"] is False
        assert result["crashed"] is False
        assert "termination_terms" in result
        assert result["max_episode_steps"] == 500

    @pytest.mark.asyncio
    async def test_detects_success_flag(self, fake_run):
        run_id, _entry, _calls, ipc_responses = fake_run
        ipc_responses["get_termination"] = {
            "termination_terms": {"success": True, "time_out": False},
            "episode_step": 250,
            "max_episode_steps": 500,
            "last_reset_step": 12000,
        }
        result = await _handle_get_env_termination_state({"env_id": 0, "run_id": run_id})
        assert result["success"] is True
        assert result["done"] is True
        assert result["timeout"] is False
        assert result["crashed"] is False

    @pytest.mark.asyncio
    async def test_detects_timeout(self, fake_run):
        run_id, _entry, _calls, ipc_responses = fake_run
        ipc_responses["get_termination"] = {
            "termination_terms": {"time_out": True, "success": False},
            "episode_step": 500,
            "max_episode_steps": 500,
            "last_reset_step": 12000,
        }
        result = await _handle_get_env_termination_state({"env_id": 0, "run_id": run_id})
        assert result["timeout"] is True
        assert result["done"] is True

    @pytest.mark.asyncio
    async def test_detects_crash(self, fake_run):
        run_id, _entry, _calls, ipc_responses = fake_run
        ipc_responses["get_termination"] = {
            "termination_terms": {"robot_fell": True, "success": False, "time_out": False},
            "episode_step": 137,
            "max_episode_steps": 500,
            "last_reset_step": 12000,
        }
        result = await _handle_get_env_termination_state({"env_id": 0, "run_id": run_id})
        assert result["crashed"] is True
        assert result["done"] is True

    @pytest.mark.asyncio
    async def test_invalid_env_id(self, fake_run):
        run_id, *_ = fake_run
        result = await _handle_get_env_termination_state({"env_id": -5, "run_id": run_id})
        assert "error" in result

    @pytest.mark.asyncio
    async def test_no_active_run(self, empty_registry):
        result = await _handle_get_env_termination_state({"env_id": 0})
        assert "error" in result


# ---------------------------------------------------------------------------
# T13.4 — pause_training
# ---------------------------------------------------------------------------

class TestPauseTraining:

    @pytest.mark.asyncio
    async def test_pauses_running_run(self, fake_run):
        run_id, entry, ipc_calls, _ = fake_run
        assert entry["state"] == "running"
        result = await _handle_pause_training({"run_id": run_id})
        assert "error" not in result
        assert result["paused"] is True
        assert result["previous_state"] == "running"
        assert result["pid"] == 99999
        assert result["signal_sent"] == "SIGUSR1"
        assert entry["state"] == "paused"
        assert ipc_calls[-1] == {"op": "pause"}

    @pytest.mark.asyncio
    async def test_pause_already_paused_is_noop(self, fake_run):
        run_id, entry, _calls, _ = fake_run
        entry["state"] = "paused"
        result = await _handle_pause_training({"run_id": run_id})
        assert result["paused"] is True
        assert result["previous_state"] == "paused"
        assert "no-op" in result.get("note", "").lower()

    @pytest.mark.asyncio
    async def test_pause_uses_default_run(self, fake_run):
        run_id, *_ = fake_run
        result = await _handle_pause_training({})
        assert result["run_id"] == run_id
        assert result["paused"] is True

    @pytest.mark.asyncio
    async def test_pause_finished_run_rejected(self, fake_run):
        run_id, entry, _calls, _ = fake_run
        entry["state"] = "finished"
        result = await _handle_pause_training({"run_id": run_id})
        assert "error" in result

    @pytest.mark.asyncio
    async def test_no_active_run(self, empty_registry):
        result = await _handle_pause_training({})
        assert "error" in result


# ---------------------------------------------------------------------------
# T13.5 — checkpoint_training
# ---------------------------------------------------------------------------

class TestCheckpointTraining:

    @pytest.mark.asyncio
    async def test_triggers_checkpoint(self, fake_run):
        run_id, _entry, ipc_calls, _ = fake_run
        result = await _handle_checkpoint_training({"run_id": run_id})
        assert "error" not in result
        assert result["checkpoint_path"].endswith(".pt")
        assert result["size_bytes"] > 0
        assert result["includes_replay_buffer"] is False
        assert result["tag"] == "manual"
        assert ipc_calls[-1]["op"] == "checkpoint"

    @pytest.mark.asyncio
    async def test_include_replay_buffer_passed_through(self, fake_run):
        run_id, *_ = fake_run
        result = await _handle_checkpoint_training({
            "run_id": run_id,
            "include_replay_buffer": True,
            "tag": "pre_lr_change",
        })
        assert result["includes_replay_buffer"] is True
        assert result["tag"] == "pre_lr_change"

    @pytest.mark.asyncio
    async def test_checkpoint_works_when_paused(self, fake_run):
        """A paused run should still allow checkpoint saves."""
        run_id, entry, _calls, _ = fake_run
        entry["state"] = "paused"
        result = await _handle_checkpoint_training({"run_id": run_id})
        assert "error" not in result

    @pytest.mark.asyncio
    async def test_checkpoint_finished_run_rejected(self, fake_run):
        run_id, entry, _calls, _ = fake_run
        entry["state"] = "finished"
        result = await _handle_checkpoint_training({"run_id": run_id})
        assert "error" in result

    @pytest.mark.asyncio
    async def test_no_active_run(self, empty_registry):
        result = await _handle_checkpoint_training({})
        assert "error" in result


# ---------------------------------------------------------------------------
# Integration through execute_tool_call dispatcher
# ---------------------------------------------------------------------------

class TestDispatch:
    """All five tools must dispatch through execute_tool_call cleanly."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("tool_name,args", [
        ("get_env_observations", {"env_id": 0}),
        ("get_env_rewards", {"env_id": 0}),
        ("get_env_termination_state", {"env_id": 0}),
        ("pause_training", {}),
        ("checkpoint_training", {}),
    ])
    async def test_dispatch_returns_data_envelope(self, fake_run, tool_name, args):
        from service.isaac_assist_service.chat.tools.tool_executor import execute_tool_call
        result = await execute_tool_call(tool_name, args)
        assert result["type"] == "data"
        assert "error" not in result, f"{tool_name} unexpectedly errored: {result}"
