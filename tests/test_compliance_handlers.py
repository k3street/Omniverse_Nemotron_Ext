"""CRM-A2 — L0 tests for setup_admittance_controller.

Covers:
  - dry-run default config dict
  - custom stiffness/damping reflected in output
  - compliance_mode field
  - controller_chain composition
  - tool reachable via tool_executor.execute_tool_call
  - ft_sensor_path included/omitted
  - live mode NotImplementedError
  - schema presence in ISAAC_SIM_TOOLS
  - dispatch module membership
  - stiffness_xyz must be positive

Per docs/specs/2026-05-11-contact-rich-manipulation-spec.md §5.1 (CRM-A2).
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.l0


class TestAdmittance:
    """L0 test suite for setup_admittance_controller."""

    # ------------------------------------------------------------------
    # Helpers

    def _get_handler(self):
        from service.isaac_assist_service.chat.tools.handlers.compliance import (
            _handle_setup_admittance_controller,
        )
        return _handle_setup_admittance_controller

    # ------------------------------------------------------------------
    # T1 — dry-run default config dict

    @pytest.mark.asyncio
    async def test_dry_run_returns_config_dict_with_defaults(self):
        """Dry-run with only robot_path returns complete config dict."""
        handler = self._get_handler()
        result = await handler({"robot_path": "/World/Franka"})

        assert result["success"] is True
        assert result["dry_run"] is True
        assert result["robot_path"] == "/World/Franka"
        assert result["target_frame"] == "tool0"
        assert result["mass_xyz"] == [1.0, 1.0, 1.0]
        assert result["stiffness_xyz"] == [500.0, 500.0, 500.0]
        assert result["damping_xyz"] == [50.0, 50.0, 50.0]
        assert result["mass_rot"] == [0.1, 0.1, 0.1]
        assert result["stiffness_rot"] == [50.0, 50.0, 50.0]
        assert result["damping_rot"] == [5.0, 5.0, 5.0]

    # ------------------------------------------------------------------
    # T2 — custom stiffness/damping reflected in output

    @pytest.mark.asyncio
    async def test_custom_stiffness_damping_reflected(self):
        """Custom stiffness and damping values appear in the returned dict."""
        handler = self._get_handler()
        result = await handler({
            "robot_path": "/World/UR10",
            "stiffness_xyz": [800.0, 800.0, 800.0],
            "damping_xyz": [100.0, 100.0, 100.0],
            "stiffness_rot": [80.0, 80.0, 80.0],
            "damping_rot": [8.0, 8.0, 8.0],
        })

        assert result["success"] is True
        assert result["stiffness_xyz"] == [800.0, 800.0, 800.0]
        assert result["damping_xyz"] == [100.0, 100.0, 100.0]
        assert result["stiffness_rot"] == [80.0, 80.0, 80.0]
        assert result["damping_rot"] == [8.0, 8.0, 8.0]

    # ------------------------------------------------------------------
    # T3 — compliance_mode field

    @pytest.mark.asyncio
    async def test_compliance_mode_is_admittance(self):
        """The result dict must report compliance_mode == 'admittance'."""
        handler = self._get_handler()
        result = await handler({"robot_path": "/World/Robot"})
        assert result["compliance_mode"] == "admittance"

    # ------------------------------------------------------------------
    # T4 — controller_chain includes chain_after + admittance_controller

    @pytest.mark.asyncio
    async def test_controller_chain_default(self):
        """Default chain: ['joint_trajectory_controller', 'admittance_controller']."""
        handler = self._get_handler()
        result = await handler({"robot_path": "/World/Franka"})
        chain = result["controller_chain"]
        assert chain[-1] == "admittance_controller"
        assert "joint_trajectory_controller" in chain

    @pytest.mark.asyncio
    async def test_controller_chain_custom_chain_after(self):
        """custom chain_after appears before admittance_controller."""
        handler = self._get_handler()
        result = await handler({
            "robot_path": "/World/UR10",
            "chain_after": "scaled_joint_trajectory_controller",
        })
        chain = result["controller_chain"]
        assert chain[0] == "scaled_joint_trajectory_controller"
        assert chain[-1] == "admittance_controller"

    # ------------------------------------------------------------------
    # T5 — tool reachable via tool_executor.execute_tool_call

    @pytest.mark.asyncio
    async def test_tool_reachable_via_execute_tool_call(self):
        """setup_admittance_controller is wired in DATA_HANDLERS."""
        from service.isaac_assist_service.chat.tools import tool_executor
        result = await tool_executor.execute_tool_call(
            "setup_admittance_controller",
            {"robot_path": "/World/Franka"},
        )
        # execute_tool_call wraps result in {"type": "data", ...}
        assert result.get("success") is True
        assert result.get("dry_run") is True

    # ------------------------------------------------------------------
    # T6 — ft_sensor_path included when set, omitted when None

    @pytest.mark.asyncio
    async def test_ft_sensor_path_included_when_set(self):
        """ft_sensor_path present in result when explicitly provided."""
        handler = self._get_handler()
        result = await handler({
            "robot_path": "/World/Franka",
            "ft_sensor_path": "/World/Franka/FTSensor",
        })
        assert result["success"] is True
        assert result["ft_sensor_path"] == "/World/Franka/FTSensor"

    @pytest.mark.asyncio
    async def test_ft_sensor_path_omitted_when_none(self):
        """ft_sensor_path key absent from result when not provided."""
        handler = self._get_handler()
        result = await handler({"robot_path": "/World/Franka"})
        assert result["success"] is True
        assert "ft_sensor_path" not in result

    # ------------------------------------------------------------------
    # T7 — dry_run=False raises NotImplementedError with actionable text

    @pytest.mark.asyncio
    async def test_live_mode_raises_not_implemented(self):
        """dry_run=False must raise NotImplementedError with bridge message."""
        handler = self._get_handler()
        with pytest.raises(NotImplementedError) as exc_info:
            await handler({"robot_path": "/World/Franka", "dry_run": False})
        msg = str(exc_info.value)
        assert "Kit RPC" in msg or "ros2_control" in msg

    # ------------------------------------------------------------------
    # T8 — schema in ISAAC_SIM_TOOLS

    def test_schema_in_isaac_sim_tools(self):
        """setup_admittance_controller appears in ISAAC_SIM_TOOLS."""
        from service.isaac_assist_service.chat.tools.tool_schemas import (
            ISAAC_SIM_TOOLS,
        )
        names = {t["function"]["name"] for t in ISAAC_SIM_TOOLS}
        assert "setup_admittance_controller" in names

    def test_schema_robot_path_required(self):
        """Schema marks robot_path as required."""
        from service.isaac_assist_service.chat.tools.tool_schemas import (
            ISAAC_SIM_TOOLS,
        )
        schema = next(
            t for t in ISAAC_SIM_TOOLS
            if t["function"]["name"] == "setup_admittance_controller"
        )
        required = schema["function"]["parameters"].get("required", [])
        assert "robot_path" in required

    # ------------------------------------------------------------------
    # T9 — dispatch: compliance in _THEME_MODULES

    def test_compliance_in_theme_modules(self):
        """_dispatch._THEME_MODULES includes the compliance module."""
        from service.isaac_assist_service.chat.tools.handlers import _dispatch
        from service.isaac_assist_service.chat.tools.handlers import compliance
        assert compliance in _dispatch._THEME_MODULES

    # ------------------------------------------------------------------
    # T10 — param validation: stiffness_xyz must be positive

    @pytest.mark.asyncio
    async def test_stiffness_xyz_must_be_positive(self):
        """Zero or negative stiffness_xyz returns error dict with success=False."""
        handler = self._get_handler()
        result = await handler({
            "robot_path": "/World/Franka",
            "stiffness_xyz": [0.0, 500.0, 500.0],
        })
        assert result["success"] is False
        assert "stiffness_xyz" in result.get("error", "")

    @pytest.mark.asyncio
    async def test_stiffness_rot_must_be_positive(self):
        """Zero or negative stiffness_rot returns error dict with success=False."""
        handler = self._get_handler()
        result = await handler({
            "robot_path": "/World/Franka",
            "stiffness_rot": [-1.0, 50.0, 50.0],
        })
        assert result["success"] is False
        assert "stiffness_rot" in result.get("error", "")

    @pytest.mark.asyncio
    async def test_missing_robot_path_returns_error(self):
        """Missing robot_path returns error dict with success=False."""
        handler = self._get_handler()
        result = await handler({})
        assert result["success"] is False
        assert "robot_path" in result.get("error", "")
