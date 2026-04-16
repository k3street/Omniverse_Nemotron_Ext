"""
L0 tests for Phase 10 — Autonomous Multi-Step Workflows.

Covers:
  - start_workflow: plan artifact generation, validation, scope
  - edit_workflow_plan: merging edits, state guard
  - approve_workflow_checkpoint: approve / reject / revise transitions
  - cancel_workflow: state transition + rollback hint
  - get_workflow_status / list_workflows: query handlers
  - execute_with_retry: validation + retry budget cap
  - proactive_check: trigger dispatch, auto-fix gating
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.l0

from service.isaac_assist_service.chat.tools.tool_executor import (
    DATA_HANDLERS,
    _WORKFLOWS,
    _WORKFLOW_TEMPLATES,
    _WORKFLOW_RETRY_HARD_CAP,
    _PROACTIVE_TRIGGER_PLAYBOOKS,
    _handle_start_workflow,
    _handle_edit_workflow_plan,
    _handle_approve_workflow_checkpoint,
    _handle_cancel_workflow,
    _handle_get_workflow_status,
    _handle_list_workflows,
    _handle_execute_with_retry,
    _handle_proactive_check,
)
from service.isaac_assist_service.chat.tools.tool_schemas import ISAAC_SIM_TOOLS


# ---------------------------------------------------------------------------
# Shared fixture: clear the workflow registry between tests so state from
# one test does not leak into the next.
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _clear_workflow_registry():
    _WORKFLOWS.clear()
    yield
    _WORKFLOWS.clear()


# ---------------------------------------------------------------------------
# Schema-level sanity — the new tools must be declared and reachable
# ---------------------------------------------------------------------------

PHASE10_TOOL_NAMES = {
    "start_workflow",
    "edit_workflow_plan",
    "approve_workflow_checkpoint",
    "cancel_workflow",
    "get_workflow_status",
    "list_workflows",
    "execute_with_retry",
    "proactive_check",
}


class TestPhase10ToolsRegistered:
    def test_all_phase10_tools_in_schemas(self):
        names = {t["function"]["name"] for t in ISAAC_SIM_TOOLS}
        missing = PHASE10_TOOL_NAMES - names
        assert not missing, f"Missing Phase 10 tools in schemas: {missing}"

    def test_all_phase10_tools_have_handlers(self):
        missing = [n for n in PHASE10_TOOL_NAMES if n not in DATA_HANDLERS]
        assert not missing, f"Phase 10 tools without handlers: {missing}"


# ---------------------------------------------------------------------------
# start_workflow
# ---------------------------------------------------------------------------

class TestStartWorkflow:
    @pytest.mark.asyncio
    async def test_unknown_workflow_type_returns_error(self):
        result = await _handle_start_workflow({"workflow_type": "totally_made_up", "goal": "x"})
        assert result["ok"] is False
        assert "Unknown workflow_type" in result["error"]

    @pytest.mark.asyncio
    async def test_missing_goal_returns_error(self):
        result = await _handle_start_workflow({"workflow_type": "rl_training"})
        assert result["ok"] is False
        assert "goal" in result["error"]

    @pytest.mark.asyncio
    async def test_rl_training_creates_workflow(self):
        result = await _handle_start_workflow({
            "workflow_type": "rl_training",
            "goal": "train Franka to pick up the cup",
            "params": {"num_envs": 128},
        })
        assert result["ok"] is True
        assert result["workflow_id"].startswith("wf_")
        assert result["status"] == "awaiting_plan_approval"
        plan = result["plan"]
        assert plan["workflow_type"] == "rl_training"
        # User-supplied param overrides template default (64 -> 128)
        assert plan["params"]["num_envs"] == 128
        # Template default merged when user didn't override
        assert plan["params"]["algo"] == "ppo"
        # Phase ordering matches W1 spec
        phase_names = [p["name"] for p in plan["phases"]]
        assert phase_names == [
            "plan", "env_creation", "reward", "training", "results", "deploy",
        ]
        # Plan phase has a checkpoint
        assert plan["phases"][0]["checkpoint"] is True
        # env_creation has the autonomous error-fix loop enabled
        assert plan["phases"][1]["error_fix"] is True

    @pytest.mark.asyncio
    async def test_robot_import_workflow_phases(self):
        result = await _handle_start_workflow({
            "workflow_type": "robot_import",
            "goal": "import this URDF and configure motion planning",
        })
        assert result["ok"] is True
        names = [p["name"] for p in result["plan"]["phases"]]
        assert names == [
            "plan", "import", "verify", "auto_fix", "motion_planning", "report",
        ]

    @pytest.mark.asyncio
    async def test_sim_debugging_workflow_phases(self):
        result = await _handle_start_workflow({
            "workflow_type": "sim_debugging",
            "goal": "find out why the cup falls through the table",
        })
        assert result["ok"] is True
        names = [p["name"] for p in result["plan"]["phases"]]
        assert names == ["diagnose", "hypothesis", "fix", "verify", "report"]
        # `fix` phase should have both checkpoint + error-fix per W4 spec
        fix_phase = next(p for p in result["plan"]["phases"] if p["name"] == "fix")
        assert fix_phase["checkpoint"] is True
        assert fix_phase["error_fix"] is True

    @pytest.mark.asyncio
    async def test_max_retries_clamped_to_hard_cap(self):
        result = await _handle_start_workflow({
            "workflow_type": "rl_training",
            "goal": "x",
            "max_retries": 99,
        })
        wf = _WORKFLOWS[result["workflow_id"]]
        assert wf["max_retries"] == _WORKFLOW_RETRY_HARD_CAP

    @pytest.mark.asyncio
    async def test_scope_prim_default_world(self):
        result = await _handle_start_workflow({"workflow_type": "rl_training", "goal": "x"})
        wf = _WORKFLOWS[result["workflow_id"]]
        assert wf["scope_prim"] == "/World"

    @pytest.mark.asyncio
    async def test_workflow_event_recorded(self):
        result = await _handle_start_workflow({"workflow_type": "rl_training", "goal": "x"})
        wf = _WORKFLOWS[result["workflow_id"]]
        assert wf["events"][0]["type"] == "workflow_started"
        assert wf["events"][0]["phase"] == "plan"


# ---------------------------------------------------------------------------
# edit_workflow_plan
# ---------------------------------------------------------------------------

class TestEditWorkflowPlan:
    @pytest.mark.asyncio
    async def test_unknown_workflow_id(self):
        result = await _handle_edit_workflow_plan({"workflow_id": "wf_nope", "plan_edits": {}})
        assert result["ok"] is False
        assert "Unknown workflow_id" in result["error"]

    @pytest.mark.asyncio
    async def test_can_edit_params(self):
        start = await _handle_start_workflow({"workflow_type": "rl_training", "goal": "x"})
        wf_id = start["workflow_id"]
        result = await _handle_edit_workflow_plan({
            "workflow_id": wf_id,
            "plan_edits": {"params": {"num_envs": 256, "env_spacing": 3.0}},
        })
        assert result["ok"] is True
        assert "params" in result["applied_edits"]
        assert result["plan"]["params"]["num_envs"] == 256
        assert result["plan"]["params"]["env_spacing"] == 3.0

    @pytest.mark.asyncio
    async def test_can_edit_phase_field(self):
        start = await _handle_start_workflow({"workflow_type": "rl_training", "goal": "x"})
        wf_id = start["workflow_id"]
        result = await _handle_edit_workflow_plan({
            "workflow_id": wf_id,
            "plan_edits": {"reward": {"checkpoint": False}},
        })
        assert result["ok"] is True
        reward_phase = next(p for p in result["plan"]["phases"] if p["name"] == "reward")
        assert reward_phase["checkpoint"] is False

    @pytest.mark.asyncio
    async def test_cannot_edit_after_approval(self):
        start = await _handle_start_workflow({"workflow_type": "rl_training", "goal": "x"})
        wf_id = start["workflow_id"]
        await _handle_approve_workflow_checkpoint({
            "workflow_id": wf_id, "phase": "plan", "action": "approve",
        })
        result = await _handle_edit_workflow_plan({
            "workflow_id": wf_id,
            "plan_edits": {"params": {"num_envs": 1}},
        })
        assert result["ok"] is False
        assert "before approval" in result["error"]

    @pytest.mark.asyncio
    async def test_invalid_edits_payload(self):
        start = await _handle_start_workflow({"workflow_type": "rl_training", "goal": "x"})
        result = await _handle_edit_workflow_plan({
            "workflow_id": start["workflow_id"],
            "plan_edits": "not a dict",
        })
        assert result["ok"] is False


# ---------------------------------------------------------------------------
# approve_workflow_checkpoint
# ---------------------------------------------------------------------------

class TestApproveCheckpoint:
    @pytest.mark.asyncio
    async def test_unknown_workflow(self):
        result = await _handle_approve_workflow_checkpoint({
            "workflow_id": "wf_nope", "phase": "plan", "action": "approve",
        })
        assert result["ok"] is False

    @pytest.mark.asyncio
    async def test_invalid_action(self):
        start = await _handle_start_workflow({"workflow_type": "rl_training", "goal": "x"})
        result = await _handle_approve_workflow_checkpoint({
            "workflow_id": start["workflow_id"], "phase": "plan", "action": "yolo",
        })
        assert result["ok"] is False

    @pytest.mark.asyncio
    async def test_phase_mismatch(self):
        start = await _handle_start_workflow({"workflow_type": "rl_training", "goal": "x"})
        result = await _handle_approve_workflow_checkpoint({
            "workflow_id": start["workflow_id"], "phase": "deploy", "action": "approve",
        })
        assert result["ok"] is False
        assert "current_phase" not in result  # should be a flat error string

    @pytest.mark.asyncio
    async def test_approve_advances_phase(self):
        start = await _handle_start_workflow({"workflow_type": "rl_training", "goal": "x"})
        wf_id = start["workflow_id"]
        result = await _handle_approve_workflow_checkpoint({
            "workflow_id": wf_id, "phase": "plan", "action": "approve",
        })
        assert result["ok"] is True
        assert result["current_phase"] == "env_creation"
        # env_creation has no checkpoint, so workflow should be executing
        assert result["status"].startswith("executing_")
        wf = _WORKFLOWS[wf_id]
        assert "plan" in wf["completed_phases"]

    @pytest.mark.asyncio
    async def test_approve_then_pause_at_next_checkpoint(self):
        # In rl_training, env_creation has no checkpoint but reward does.
        # Walking the plan: plan(approve) -> env_creation(no ckpt, but state
        # transitions to executing_env_creation). Approving env_creation
        # should land on the reward checkpoint.
        start = await _handle_start_workflow({"workflow_type": "rl_training", "goal": "x"})
        wf_id = start["workflow_id"]
        await _handle_approve_workflow_checkpoint({"workflow_id": wf_id, "phase": "plan", "action": "approve"})
        # Simulate the orchestrator marking env_creation done by approving it
        # (the handler accepts approve regardless of checkpoint flag).
        result = await _handle_approve_workflow_checkpoint({
            "workflow_id": wf_id, "phase": "env_creation", "action": "approve",
        })
        assert result["ok"] is True
        assert result["current_phase"] == "reward"
        # Reward IS a checkpoint
        assert result["status"] == "awaiting_reward_approval"

    @pytest.mark.asyncio
    async def test_reject_cancels_workflow(self):
        start = await _handle_start_workflow({"workflow_type": "rl_training", "goal": "x"})
        wf_id = start["workflow_id"]
        result = await _handle_approve_workflow_checkpoint({
            "workflow_id": wf_id, "phase": "plan", "action": "reject",
        })
        assert result["ok"] is True
        assert result["status"] == "cancelled"
        assert result["rollback_required"] is True

    @pytest.mark.asyncio
    async def test_revise_stays_on_phase(self):
        start = await _handle_start_workflow({"workflow_type": "rl_training", "goal": "x"})
        wf_id = start["workflow_id"]
        result = await _handle_approve_workflow_checkpoint({
            "workflow_id": wf_id, "phase": "plan", "action": "revise",
            "feedback": "use more parallel envs",
        })
        assert result["ok"] is True
        assert result["status"] == "revising"
        assert result["phase"] == "plan"
        wf = _WORKFLOWS[wf_id]
        # Phase has not been marked completed yet
        assert "plan" not in wf["completed_phases"]

    @pytest.mark.asyncio
    async def test_auto_approve_skips_intermediate_checkpoints(self):
        start = await _handle_start_workflow({
            "workflow_type": "rl_training", "goal": "x",
            "auto_approve_checkpoints": True,
        })
        wf_id = start["workflow_id"]
        # Approve plan; next phase env_creation has no checkpoint, status should
        # be executing_env_creation. Approving env_creation under auto_approve
        # should land on reward in executing state (not awaiting).
        await _handle_approve_workflow_checkpoint({"workflow_id": wf_id, "phase": "plan", "action": "approve"})
        result = await _handle_approve_workflow_checkpoint({
            "workflow_id": wf_id, "phase": "env_creation", "action": "approve",
        })
        assert result["status"] == "executing_reward"


# ---------------------------------------------------------------------------
# cancel_workflow
# ---------------------------------------------------------------------------

class TestCancelWorkflow:
    @pytest.mark.asyncio
    async def test_unknown_workflow(self):
        result = await _handle_cancel_workflow({"workflow_id": "wf_nope"})
        assert result["ok"] is False

    @pytest.mark.asyncio
    async def test_cancel_active_workflow(self):
        start = await _handle_start_workflow({"workflow_type": "rl_training", "goal": "x"})
        wf_id = start["workflow_id"]
        result = await _handle_cancel_workflow({"workflow_id": wf_id, "reason": "user_changed_mind"})
        assert result["ok"] is True
        assert result["status"] == "cancelled"
        assert result["rollback_required"] is True
        assert result["reason"] == "user_changed_mind"

    @pytest.mark.asyncio
    async def test_cancel_already_completed_is_noop(self):
        start = await _handle_start_workflow({"workflow_type": "rl_training", "goal": "x"})
        wf_id = start["workflow_id"]
        _WORKFLOWS[wf_id]["status"] = "completed"
        result = await _handle_cancel_workflow({"workflow_id": wf_id})
        assert result["ok"] is True
        assert "already finished" in result["message"]


# ---------------------------------------------------------------------------
# get_workflow_status / list_workflows
# ---------------------------------------------------------------------------

class TestQueryHandlers:
    @pytest.mark.asyncio
    async def test_get_status_unknown(self):
        result = await _handle_get_workflow_status({"workflow_id": "wf_nope"})
        assert result["ok"] is False

    @pytest.mark.asyncio
    async def test_get_status_returns_full_state(self):
        start = await _handle_start_workflow({"workflow_type": "robot_import", "goal": "x"})
        wf_id = start["workflow_id"]
        result = await _handle_get_workflow_status({"workflow_id": wf_id})
        assert result["ok"] is True
        assert result["type"] == "robot_import"
        assert result["status"] == "awaiting_plan_approval"
        assert result["current_phase"] == "plan"
        assert result["plan"]["workflow_type"] == "robot_import"

    @pytest.mark.asyncio
    async def test_list_excludes_completed_by_default(self):
        a = await _handle_start_workflow({"workflow_type": "rl_training", "goal": "a"})
        b = await _handle_start_workflow({"workflow_type": "rl_training", "goal": "b"})
        _WORKFLOWS[a["workflow_id"]]["status"] = "completed"
        result = await _handle_list_workflows({})
        wf_ids = [w["workflow_id"] for w in result["workflows"]]
        assert b["workflow_id"] in wf_ids
        assert a["workflow_id"] not in wf_ids

    @pytest.mark.asyncio
    async def test_list_includes_completed_when_requested(self):
        a = await _handle_start_workflow({"workflow_type": "rl_training", "goal": "a"})
        _WORKFLOWS[a["workflow_id"]]["status"] = "completed"
        result = await _handle_list_workflows({"include_completed": True})
        wf_ids = [w["workflow_id"] for w in result["workflows"]]
        assert a["workflow_id"] in wf_ids

    @pytest.mark.asyncio
    async def test_list_respects_limit(self):
        for i in range(5):
            await _handle_start_workflow({"workflow_type": "rl_training", "goal": f"g{i}"})
        result = await _handle_list_workflows({"limit": 2})
        assert len(result["workflows"]) == 2


# ---------------------------------------------------------------------------
# execute_with_retry
# ---------------------------------------------------------------------------

class TestExecuteWithRetry:
    @pytest.mark.asyncio
    async def test_missing_code(self):
        result = await _handle_execute_with_retry({"description": "x"})
        assert result["ok"] is False
        assert "code is required" in result["error"]

    @pytest.mark.asyncio
    async def test_blocked_by_validator(self):
        # patch_validator blocks legacy omni.isaac.* OmniGraph namespace
        # in Isaac Sim 5.1 — re-use that as a known-blocking pattern.
        result = await _handle_execute_with_retry({
            "code": "node_type = 'omni.isaac.ros2_bridge.ROS2Publisher'",
            "description": "uses legacy namespace",
        })
        assert result["ok"] is False
        assert result["type"] == "validation_blocked"
        assert "code" in result  # the offending code is returned for context

    @pytest.mark.asyncio
    async def test_max_retries_clamped(self, mock_kit_rpc, monkeypatch):
        async def fake_queue(code, description=""):
            return {"queued": True, "patch_id": "p"}
        import service.isaac_assist_service.chat.tools.tool_executor as te
        monkeypatch.setattr(te.kit_tools, "queue_exec_patch", fake_queue)
        result = await _handle_execute_with_retry({
            "code": "print('hello')",
            "description": "noop",
            "max_retries": 100,
        })
        assert result["ok"] is True
        assert result["max_retries"] == _WORKFLOW_RETRY_HARD_CAP

    @pytest.mark.asyncio
    async def test_queues_to_kit(self, mock_kit_rpc, monkeypatch):
        # The shared mock_kit_rpc fixture only stubs /exec, not /exec_patch
        # (queue_exec_patch hits the latter). Patch queue_exec_patch directly
        # so the test does not depend on the mock fixture's path table.
        async def fake_queue(code, description=""):
            return {"queued": True, "patch_id": "test_p1"}
        import service.isaac_assist_service.chat.tools.tool_executor as te
        monkeypatch.setattr(te.kit_tools, "queue_exec_patch", fake_queue)

        result = await _handle_execute_with_retry({
            "code": "print('hello')",
            "description": "noop",
        })
        assert result["ok"] is True
        assert result["type"] == "code_patch"
        assert result["queued"] is True
        assert result["patch_id"] == "test_p1"

    @pytest.mark.asyncio
    async def test_context_hints_propagated(self, mock_kit_rpc, monkeypatch):
        async def fake_queue(code, description=""):
            return {"queued": True, "patch_id": "p"}
        import service.isaac_assist_service.chat.tools.tool_executor as te
        monkeypatch.setattr(te.kit_tools, "queue_exec_patch", fake_queue)
        hints = ["use mdp.joint_pos not joint_positions"]
        result = await _handle_execute_with_retry({
            "code": "print('x')",
            "description": "x",
            "context_hints": hints,
        })
        assert result["context_hints"] == hints


# ---------------------------------------------------------------------------
# proactive_check
# ---------------------------------------------------------------------------

class TestProactiveCheck:
    @pytest.mark.asyncio
    async def test_unknown_trigger(self):
        result = await _handle_proactive_check({"trigger": "self_destruct"})
        assert result["ok"] is False
        assert "Unknown proactive trigger" in result["error"]

    @pytest.mark.asyncio
    async def test_scene_opened_runs_playbook(self, mock_kit_rpc):
        result = await _handle_proactive_check({"trigger": "scene_opened"})
        assert result["ok"] is True
        assert result["trigger"] == "scene_opened"
        assert result["playbook"] == _PROACTIVE_TRIGGER_PLAYBOOKS["scene_opened"]
        assert len(result["findings"]) == len(result["playbook"])

    @pytest.mark.asyncio
    async def test_console_error_skips_explain_error(self, mock_kit_rpc):
        # explain_error is a None handler in DATA_HANDLERS — proactive_check
        # must skip it gracefully without crashing.
        result = await _handle_proactive_check({
            "trigger": "console_error",
            "context": {"error_text": "PhysX: Invalid inertia tensor"},
        })
        assert result["ok"] is True
        # find the explain_error finding
        explain_finding = next(f for f in result["findings"] if f["tool"] == "explain_error")
        assert explain_finding.get("skipped") is True

    @pytest.mark.asyncio
    async def test_target_placed_needs_paths(self, mock_kit_rpc):
        result = await _handle_proactive_check({
            "trigger": "target_placed",
            "context": {},
        })
        # measure_distance should be skipped due to missing paths
        md = next(f for f in result["findings"] if f["tool"] == "measure_distance")
        assert md.get("skipped") is True

    @pytest.mark.asyncio
    async def test_auto_fix_disabled_by_default(self, mock_kit_rpc, monkeypatch):
        monkeypatch.delenv("AUTO_PROACTIVE_FIX", raising=False)
        result = await _handle_proactive_check({
            "trigger": "scene_opened",
            "auto_fix": True,  # requested but env var not set
        })
        assert result["auto_fix_enabled"] is False
        assert result["auto_fix_applied"] == []

    @pytest.mark.asyncio
    async def test_auto_fix_requires_both_flags(self, mock_kit_rpc, monkeypatch):
        monkeypatch.setenv("AUTO_PROACTIVE_FIX", "true")
        # Caller did NOT request auto_fix, so it should still be off.
        result = await _handle_proactive_check({"trigger": "scene_opened"})
        assert result["auto_fix_enabled"] is False

    @pytest.mark.asyncio
    async def test_auto_fix_enabled_when_both_set(self, mock_kit_rpc, monkeypatch):
        monkeypatch.setenv("AUTO_PROACTIVE_FIX", "true")
        result = await _handle_proactive_check({
            "trigger": "scene_opened",
            "auto_fix": True,
        })
        assert result["auto_fix_enabled"] is True


# ---------------------------------------------------------------------------
# Integration: full happy-path workflow lifecycle
# ---------------------------------------------------------------------------

class TestFullWorkflowLifecycle:
    @pytest.mark.asyncio
    async def test_rl_training_happy_path(self):
        # 1) Start
        start = await _handle_start_workflow({
            "workflow_type": "rl_training",
            "goal": "train Franka pick",
            "params": {"num_envs": 32},
        })
        wf_id = start["workflow_id"]
        assert start["status"] == "awaiting_plan_approval"

        # 2) Edit plan
        edit = await _handle_edit_workflow_plan({
            "workflow_id": wf_id,
            "plan_edits": {"params": {"num_envs": 64}},
        })
        assert edit["plan"]["params"]["num_envs"] == 64

        # 3) Approve plan -> env_creation (no checkpoint)
        ap = await _handle_approve_workflow_checkpoint({
            "workflow_id": wf_id, "phase": "plan", "action": "approve",
        })
        assert ap["current_phase"] == "env_creation"
        assert ap["status"] == "executing_env_creation"

        # 4) Approve env_creation -> reward (checkpoint)
        ap2 = await _handle_approve_workflow_checkpoint({
            "workflow_id": wf_id, "phase": "env_creation", "action": "approve",
        })
        assert ap2["current_phase"] == "reward"
        assert ap2["status"] == "awaiting_reward_approval"

        # 5) Status query
        status = await _handle_get_workflow_status({"workflow_id": wf_id})
        assert status["completed_phases"] == ["plan", "env_creation"]
        assert status["current_phase"] == "reward"

        # 6) Cancel
        cancel = await _handle_cancel_workflow({"workflow_id": wf_id, "reason": "test"})
        assert cancel["status"] == "cancelled"
