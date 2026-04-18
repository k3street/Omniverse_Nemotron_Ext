"""
L0 tests for the onboarding & first-time UX tools.
Tests: scene_aware_starter_prompts, hardware_compatibility_check,
       slash_command_discovery, console_error_autodetect,
       post_action_suggestions, load_scene_template.
"""
import pytest
from unittest.mock import AsyncMock, patch

pytestmark = pytest.mark.l0

from service.isaac_assist_service.chat.tools.tool_executor import (
    DATA_HANDLERS,
    CODE_GEN_HANDLERS,
    _handle_scene_aware_starter_prompts,
    _handle_hardware_compatibility_check,
    _handle_slash_command_discovery,
    _handle_console_error_autodetect,
    _handle_post_action_suggestions,
    _gen_load_scene_template,
)


# ---------------------------------------------------------------------------
# scene_aware_starter_prompts
# ---------------------------------------------------------------------------

class TestSceneAwareStarterPrompts:
    """Starter prompt generation based on scene archetype."""

    @pytest.mark.asyncio
    async def test_empty_scene(self, mock_kit_rpc):
        mock_kit_rpc["/context"] = {
            "stage": {"prim_count": 0, "prims_by_type": {}, "has_physics_scene": False},
            "recent_logs": [],
        }
        result = await _handle_scene_aware_starter_prompts({})
        assert result["archetype"] == "empty"
        assert len(result["prompts"]) == 3
        assert any("Import" in p or "import" in p.lower() for p in result["prompts"])

    @pytest.mark.asyncio
    async def test_robot_and_objects_scene(self, mock_kit_rpc):
        mock_kit_rpc["/context"] = {
            "stage": {
                "prim_count": 15,
                "prims_by_type": {
                    "Articulation": ["/World/Franka"],
                    "Mesh": [f"/World/Cube_{i}" for i in range(5)],
                },
                "has_physics_scene": True,
            },
            "recent_logs": [],
        }
        result = await _handle_scene_aware_starter_prompts({})
        assert result["archetype"] == "robot_and_objects"
        assert result["has_physics"] is True
        assert len(result["robot_paths"]) >= 1

    @pytest.mark.asyncio
    async def test_mobile_robot_scene(self, mock_kit_rpc):
        mock_kit_rpc["/context"] = {
            "stage": {
                "prim_count": 10,
                "prims_by_type": {
                    "Xform": ["/World/jetbot"],
                    "Mesh": ["/World/Ground"],
                },
                "has_physics_scene": True,
            },
            "recent_logs": [],
        }
        result = await _handle_scene_aware_starter_prompts({})
        assert result["archetype"] == "mobile_robot"
        assert any("jetbot" in p.lower() for p in result["robot_paths"])

    @pytest.mark.asyncio
    async def test_no_physics_scene(self, mock_kit_rpc):
        mock_kit_rpc["/context"] = {
            "stage": {
                "prim_count": 8,
                "prims_by_type": {"Mesh": [f"/World/Mesh_{i}" for i in range(5)]},
                "has_physics_scene": False,
            },
            "recent_logs": [],
        }
        result = await _handle_scene_aware_starter_prompts({})
        assert result["archetype"] == "no_physics"
        assert any("physics" in p.lower() for p in result["prompts"])

    @pytest.mark.asyncio
    async def test_handler_registered(self):
        assert "scene_aware_starter_prompts" in DATA_HANDLERS

    @pytest.mark.asyncio
    async def test_kit_rpc_failure_graceful(self):
        """Should not crash if Kit RPC is unavailable."""
        with patch("service.isaac_assist_service.chat.tools.tool_executor.kit_tools") as mock_kit:
            mock_kit.get_stage_context = AsyncMock(side_effect=Exception("no connection"))
            result = await _handle_scene_aware_starter_prompts({})
            assert result["archetype"] == "empty"
            assert len(result["prompts"]) == 3


# ---------------------------------------------------------------------------
# hardware_compatibility_check
# ---------------------------------------------------------------------------

class TestHardwareCompatibilityCheck:
    """Hardware probe returns structured check results."""

    @pytest.mark.asyncio
    async def test_returns_checks_list(self, mock_kit_rpc):
        result = await _handle_hardware_compatibility_check({})
        assert "checks" in result
        assert isinstance(result["checks"], list)
        assert len(result["checks"]) >= 2  # at least GPU + Python

    @pytest.mark.asyncio
    async def test_python_version_present(self, mock_kit_rpc):
        result = await _handle_hardware_compatibility_check({})
        py_checks = [c for c in result["checks"] if c["component"] == "Python"]
        assert len(py_checks) == 1
        assert py_checks[0]["status"] == "pass"  # we're running 3.10+

    @pytest.mark.asyncio
    async def test_llm_mode_present(self, mock_kit_rpc):
        result = await _handle_hardware_compatibility_check({})
        llm_checks = [c for c in result["checks"] if c["component"] == "LLM"]
        assert len(llm_checks) == 1

    @pytest.mark.asyncio
    async def test_overall_status(self, mock_kit_rpc):
        result = await _handle_hardware_compatibility_check({})
        assert result["overall_status"] in ("pass", "warn")

    @pytest.mark.asyncio
    async def test_handler_registered(self):
        assert "hardware_compatibility_check" in DATA_HANDLERS


# ---------------------------------------------------------------------------
# slash_command_discovery
# ---------------------------------------------------------------------------

class TestSlashCommandDiscovery:
    """Slash command filtering by scene state."""

    @pytest.mark.asyncio
    async def test_always_commands_present(self):
        result = await _handle_slash_command_discovery({
            "scene_has_robot": False,
            "scene_has_physics": False,
        })
        command_names = [c["command"] for c in result["commands"]]
        assert "/help" in command_names
        assert "/scene" in command_names
        assert "/import" in command_names

    @pytest.mark.asyncio
    async def test_workspace_hidden_without_robot(self):
        result = await _handle_slash_command_discovery({
            "scene_has_robot": False,
            "scene_has_physics": True,
        })
        command_names = [c["command"] for c in result["commands"]]
        assert "/workspace" not in command_names

    @pytest.mark.asyncio
    async def test_workspace_shown_with_robot(self):
        result = await _handle_slash_command_discovery({
            "scene_has_robot": True,
            "scene_has_physics": True,
        })
        command_names = [c["command"] for c in result["commands"]]
        assert "/workspace" in command_names

    @pytest.mark.asyncio
    async def test_debug_hidden_without_physics(self):
        result = await _handle_slash_command_discovery({
            "scene_has_robot": True,
            "scene_has_physics": False,
        })
        command_names = [c["command"] for c in result["commands"]]
        assert "/debug" not in command_names

    @pytest.mark.asyncio
    async def test_debug_shown_with_physics(self):
        result = await _handle_slash_command_discovery({
            "scene_has_robot": False,
            "scene_has_physics": True,
        })
        command_names = [c["command"] for c in result["commands"]]
        assert "/debug" in command_names

    @pytest.mark.asyncio
    async def test_handler_registered(self):
        assert "slash_command_discovery" in DATA_HANDLERS


# ---------------------------------------------------------------------------
# console_error_autodetect
# ---------------------------------------------------------------------------

class TestConsoleErrorAutodetect:
    """Detect new console errors since last message."""

    @pytest.mark.asyncio
    async def test_no_errors(self, mock_kit_rpc):
        mock_kit_rpc["/context"]["recent_logs"] = []
        result = await _handle_console_error_autodetect({"since_timestamp": 0})
        assert result["new_error_count"] == 0
        assert "proactive_message" not in result

    @pytest.mark.asyncio
    async def test_errors_detected(self, mock_kit_rpc):
        mock_kit_rpc["/context"]["recent_logs"] = [
            {"level": "error", "message": "PhysX crash", "timestamp": 100},
            {"level": "error", "message": "USD prim invalid", "timestamp": 200},
            {"level": "warning", "message": "Performance warning", "timestamp": 150},
        ]
        result = await _handle_console_error_autodetect({"since_timestamp": 50})
        assert result["new_error_count"] == 2  # warnings excluded
        assert "proactive_message" in result
        assert "2 new error" in result["proactive_message"]

    @pytest.mark.asyncio
    async def test_since_filter(self, mock_kit_rpc):
        mock_kit_rpc["/context"]["recent_logs"] = [
            {"level": "error", "message": "Old error", "timestamp": 50},
            {"level": "error", "message": "New error", "timestamp": 200},
        ]
        result = await _handle_console_error_autodetect({"since_timestamp": 100})
        assert result["new_error_count"] == 1
        assert result["errors"][0]["message"] == "New error"

    @pytest.mark.asyncio
    async def test_kit_unavailable(self):
        with patch("service.isaac_assist_service.chat.tools.tool_executor.kit_tools") as mock_kit:
            mock_kit.get_stage_context = AsyncMock(side_effect=Exception("unavailable"))
            result = await _handle_console_error_autodetect({})
            assert result["new_error_count"] == 0

    @pytest.mark.asyncio
    async def test_handler_registered(self):
        assert "console_error_autodetect" in DATA_HANDLERS


# ---------------------------------------------------------------------------
# post_action_suggestions
# ---------------------------------------------------------------------------

class TestPostActionSuggestions:
    """Next-step suggestions after tool execution."""

    @pytest.mark.asyncio
    async def test_import_robot_suggestions(self):
        result = await _handle_post_action_suggestions({
            "completed_tool": "import_robot",
            "tool_args": {"file_path": "franka"},
        })
        assert result["completed_tool"] == "import_robot"
        assert len(result["suggestions"]) >= 2
        assert any("gripper" in s.lower() for s in result["suggestions"])

    @pytest.mark.asyncio
    async def test_mobile_robot_override(self):
        result = await _handle_post_action_suggestions({
            "completed_tool": "import_robot",
            "tool_args": {"file_path": "jetbot"},
        })
        assert any("navigation" in s.lower() or "drive" in s.lower() for s in result["suggestions"])

    @pytest.mark.asyncio
    async def test_create_prim_suggestions(self):
        result = await _handle_post_action_suggestions({
            "completed_tool": "create_prim",
        })
        assert len(result["suggestions"]) >= 2

    @pytest.mark.asyncio
    async def test_unknown_tool_gets_defaults(self):
        result = await _handle_post_action_suggestions({
            "completed_tool": "some_unknown_tool_xyz",
        })
        assert len(result["suggestions"]) >= 2

    @pytest.mark.asyncio
    async def test_handler_registered(self):
        assert "post_action_suggestions" in DATA_HANDLERS


# ---------------------------------------------------------------------------
# load_scene_template (code generation)
# ---------------------------------------------------------------------------

class TestLoadSceneTemplate:
    """Code generation for quick-start templates."""

    def test_pick_and_place_compiles(self):
        code = _gen_load_scene_template({"template_name": "pick_and_place"})
        compile(code, "<pick_and_place>", "exec")
        assert "PhysicsScene" in code
        assert "Cube" in code
        assert "Table" in code

    def test_mobile_nav_compiles(self):
        code = _gen_load_scene_template({"template_name": "mobile_nav"})
        compile(code, "<mobile_nav>", "exec")
        assert "Wall" in code
        assert "Ground" in code

    def test_sdg_basic_compiles(self):
        code = _gen_load_scene_template({"template_name": "sdg_basic"})
        compile(code, "<sdg_basic>", "exec")
        assert "Camera" in code
        assert "semantic" in code

    def test_empty_robot_compiles(self):
        code = _gen_load_scene_template({"template_name": "empty_robot"})
        compile(code, "<empty_robot>", "exec")
        assert "PhysicsScene" in code
        assert "Ground" in code

    def test_unknown_template(self):
        code = _gen_load_scene_template({"template_name": "nonexistent"})
        assert "Unknown template" in code

    def test_handler_registered(self):
        assert "load_scene_template" in CODE_GEN_HANDLERS


# ---------------------------------------------------------------------------
# Integration: execute_tool_call dispatch
# ---------------------------------------------------------------------------

class TestOnboardingToolDispatch:
    """Verify tools dispatch correctly through execute_tool_call."""

    @pytest.mark.asyncio
    async def test_dispatch_starter_prompts(self, mock_kit_rpc):
        from service.isaac_assist_service.chat.tools.tool_executor import execute_tool_call
        result = await execute_tool_call("scene_aware_starter_prompts", {})
        assert result["type"] == "data"

    @pytest.mark.asyncio
    async def test_dispatch_hardware_check(self, mock_kit_rpc):
        from service.isaac_assist_service.chat.tools.tool_executor import execute_tool_call
        result = await execute_tool_call("hardware_compatibility_check", {})
        assert result["type"] == "data"

    @pytest.mark.asyncio
    async def test_dispatch_slash_commands(self):
        from service.isaac_assist_service.chat.tools.tool_executor import execute_tool_call
        result = await execute_tool_call("slash_command_discovery", {
            "scene_has_robot": True,
            "scene_has_physics": True,
        })
        assert result["type"] == "data"

    @pytest.mark.asyncio
    async def test_dispatch_console_errors(self, mock_kit_rpc):
        from service.isaac_assist_service.chat.tools.tool_executor import execute_tool_call
        result = await execute_tool_call("console_error_autodetect", {"since_timestamp": 0})
        assert result["type"] == "data"

    @pytest.mark.asyncio
    async def test_dispatch_post_action(self):
        from service.isaac_assist_service.chat.tools.tool_executor import execute_tool_call
        result = await execute_tool_call("post_action_suggestions", {
            "completed_tool": "create_prim",
        })
        assert result["type"] == "data"

    @pytest.mark.asyncio
    async def test_dispatch_load_template(self, mock_kit_rpc):
        from service.isaac_assist_service.chat.tools.tool_executor import execute_tool_call
        result = await execute_tool_call("load_scene_template", {
            "template_name": "empty_robot",
        })
        assert result["type"] == "code_patch"
        assert "PhysicsScene" in result["code"]
