"""CRM-A2 / CRM-B1 — L0 tests for setup_admittance_controller and
setup_impedance_controller.

TestAdmittance covers:
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

TestImpedance covers:
  - dry-run with torque_mode=True returns config dict
  - dry-run with torque_mode=False returns error + recommended_alternative=admittance
  - compliance_mode == "impedance" in successful return
  - Kx/Kr/Dx/Dr defaults match spec
  - custom Kx/Kr/Dx/Dr reflected in output
  - null_space_stiffness + null_space_damping in output
  - tool reachable via tool_executor.execute_tool_call
  - dry_run=False raises NotImplementedError with required message text

Per docs/specs/2026-05-11-contact-rich-manipulation-spec.md §5.1 (CRM-A2)
and §5.2 (CRM-B1).
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


class TestImpedance:
    """L0 test suite for setup_impedance_controller (CRM-B1).

    Cartesian impedance law: τ = J^T · (Kx·Δx + Dx·v + Kr·Δr + Dr·ω)
    Requires torque_mode=True; returns structured error with
    recommended_alternative="admittance" when torque_mode=False.
    """

    # ------------------------------------------------------------------
    # Helpers

    def _get_handler(self):
        from service.isaac_assist_service.chat.tools.handlers.compliance import (
            _handle_setup_impedance_controller,
        )
        return _handle_setup_impedance_controller

    # ------------------------------------------------------------------
    # T1 — dry-run with torque_mode=True returns config dict

    @pytest.mark.asyncio
    async def test_dry_run_torque_mode_true_returns_config_dict(self):
        """Dry-run with torque_mode=True returns complete config dict."""
        handler = self._get_handler()
        result = await handler({"robot_path": "/World/Franka"})

        assert result["success"] is True
        assert result["dry_run"] is True
        assert result["robot_path"] == "/World/Franka"

    # ------------------------------------------------------------------
    # T2 — dry-run with torque_mode=False returns error + recommended_alternative

    @pytest.mark.asyncio
    async def test_dry_run_torque_mode_false_returns_error(self):
        """torque_mode=False returns error dict with success=False and
        recommended_alternative='admittance'."""
        handler = self._get_handler()
        result = await handler({
            "robot_path": "/World/UR10",
            "torque_mode": False,
        })

        assert result["success"] is False
        assert "error" in result
        assert result.get("recommended_alternative") == "admittance"

    @pytest.mark.asyncio
    async def test_torque_mode_false_error_message_mentions_admittance(self):
        """Error message on torque_mode=False must explain why and name admittance."""
        handler = self._get_handler()
        result = await handler({
            "robot_path": "/World/UR10",
            "torque_mode": False,
        })
        assert "admittance" in result.get("error", "").lower()

    # ------------------------------------------------------------------
    # T3 — compliance_mode == "impedance" in successful return

    @pytest.mark.asyncio
    async def test_compliance_mode_is_impedance(self):
        """Successful dry-run must report compliance_mode == 'impedance'."""
        handler = self._get_handler()
        result = await handler({"robot_path": "/World/Franka"})
        assert result["compliance_mode"] == "impedance"

    # ------------------------------------------------------------------
    # T4 — Kx / Kr / Dx / Dr defaults match spec §5.2

    @pytest.mark.asyncio
    async def test_default_gains_match_spec(self):
        """Default Kx=[400]*3, Kr=[40]*3, Dx=[40]*3, Dr=[4]*3 per §5.2."""
        handler = self._get_handler()
        result = await handler({"robot_path": "/World/Franka"})

        assert result["Kx"] == [400.0, 400.0, 400.0]
        assert result["Kr"] == [40.0, 40.0, 40.0]
        assert result["Dx"] == [40.0, 40.0, 40.0]
        assert result["Dr"] == [4.0, 4.0, 4.0]

    # ------------------------------------------------------------------
    # T5 — custom Kx / Kr / Dx / Dr reflected in output

    @pytest.mark.asyncio
    async def test_custom_gains_reflected_in_output(self):
        """Custom gain values appear unchanged in the returned dict."""
        handler = self._get_handler()
        result = await handler({
            "robot_path": "/World/Franka",
            "Kx": [600.0, 600.0, 300.0],
            "Kr": [60.0, 60.0, 30.0],
            "Dx": [60.0, 60.0, 30.0],
            "Dr": [6.0, 6.0, 3.0],
        })

        assert result["success"] is True
        assert result["Kx"] == [600.0, 600.0, 300.0]
        assert result["Kr"] == [60.0, 60.0, 30.0]
        assert result["Dx"] == [60.0, 60.0, 30.0]
        assert result["Dr"] == [6.0, 6.0, 3.0]

    # ------------------------------------------------------------------
    # T6 — null_space_stiffness + null_space_damping in output

    @pytest.mark.asyncio
    async def test_null_space_defaults_in_output(self):
        """null_space_stiffness and null_space_damping present with spec defaults."""
        handler = self._get_handler()
        result = await handler({"robot_path": "/World/Franka"})

        assert result["null_space_stiffness"] == 0.5
        assert result["null_space_damping"] == 0.5

    @pytest.mark.asyncio
    async def test_custom_null_space_params_reflected(self):
        """Custom null-space params appear in the returned dict."""
        handler = self._get_handler()
        result = await handler({
            "robot_path": "/World/Franka",
            "null_space_stiffness": 1.5,
            "null_space_damping": 0.8,
        })

        assert result["null_space_stiffness"] == 1.5
        assert result["null_space_damping"] == 0.8

    # ------------------------------------------------------------------
    # T7 — tool reachable via tool_executor.execute_tool_call

    @pytest.mark.asyncio
    async def test_tool_reachable_via_execute_tool_call(self):
        """setup_impedance_controller is wired in DATA_HANDLERS."""
        from service.isaac_assist_service.chat.tools import tool_executor
        result = await tool_executor.execute_tool_call(
            "setup_impedance_controller",
            {"robot_path": "/World/Franka"},
        )
        assert result.get("success") is True
        assert result.get("dry_run") is True

    # ------------------------------------------------------------------
    # T8 — dry_run=False raises NotImplementedError with required text

    @pytest.mark.asyncio
    async def test_live_mode_raises_not_implemented(self):
        """dry_run=False raises NotImplementedError referencing Kit RPC,
        ros2_control bridge, and torque-mode robot."""
        handler = self._get_handler()
        with pytest.raises(NotImplementedError) as exc_info:
            await handler({"robot_path": "/World/Franka", "dry_run": False})
        msg = str(exc_info.value)
        assert "Kit RPC" in msg or "ros2_control" in msg or "torque-mode" in msg


class TestParamMutation:
    """L0 test suite for set_compliance_params (CRM-B2).

    Tests runtime mutation of an already-installed compliance controller
    via the in-memory _INSTALLED_COMPLIANCE state dict.
    """

    # ------------------------------------------------------------------
    # Helpers

    def _get_setup_handler(self):
        from service.isaac_assist_service.chat.tools.handlers.compliance import (
            _handle_setup_admittance_controller,
        )
        return _handle_setup_admittance_controller

    def _get_set_handler(self):
        from service.isaac_assist_service.chat.tools.handlers.compliance import (
            _handle_set_compliance_params,
        )
        return _handle_set_compliance_params

    def _get_state_dict(self):
        from service.isaac_assist_service.chat.tools.handlers import compliance
        return compliance._INSTALLED_COMPLIANCE

    # ------------------------------------------------------------------
    # T1 — install admittance → set new stiffness → state reflects update,
    #       other fields unchanged

    @pytest.mark.asyncio
    async def test_stiffness_update_reflected_other_fields_unchanged(self):
        """After admittance install, set_compliance_params with new stiffness_xyz
        updates that field while leaving damping_xyz untouched."""
        setup = self._get_setup_handler()
        set_p = self._get_set_handler()
        robot = "/World/FrankaB2T1"

        install_result = await setup({
            "robot_path": robot,
            "stiffness_xyz": [500.0, 500.0, 500.0],
            "damping_xyz": [50.0, 50.0, 50.0],
        })
        assert install_result["success"] is True

        new_k = [200.0, 200.0, 200.0]
        result = await set_p({
            "robot_path": robot,
            "stiffness_xyz": new_k,
        })

        assert result["success"] is True
        assert result["stiffness_xyz"] == new_k
        # Damping must remain unchanged
        assert result["damping_xyz"] == [50.0, 50.0, 50.0]

    # ------------------------------------------------------------------
    # T2 — mutation with all-None args → state unchanged (no-op)

    @pytest.mark.asyncio
    async def test_all_none_args_is_noop(self):
        """Calling set_compliance_params with no param overrides leaves the
        state completely unchanged."""
        setup = self._get_setup_handler()
        set_p = self._get_set_handler()
        robot = "/World/FrankaB2T2"

        await setup({
            "robot_path": robot,
            "stiffness_xyz": [600.0, 600.0, 600.0],
            "damping_xyz": [60.0, 60.0, 60.0],
        })

        result = await set_p({"robot_path": robot})

        assert result["success"] is True
        assert result["stiffness_xyz"] == [600.0, 600.0, 600.0]
        assert result["damping_xyz"] == [60.0, 60.0, 60.0]

    # ------------------------------------------------------------------
    # T3 — set_compliance_params on robot with no installed controller → error

    @pytest.mark.asyncio
    async def test_missing_controller_returns_structured_error(self):
        """set_compliance_params on an unregistered robot_path returns
        success=False with error referencing the robot_path and
        available_robots key."""
        set_p = self._get_set_handler()
        robot = "/World/UnknownRobotB2T3"

        # Ensure this path is not in the state dict.
        state = self._get_state_dict()
        state.pop(robot, None)

        result = await set_p({
            "robot_path": robot,
            "stiffness_xyz": [100.0, 100.0, 100.0],
        })

        assert result["success"] is False
        assert robot in result.get("error", "")
        assert "available_robots" in result

    # ------------------------------------------------------------------
    # T4 — state dict isolated between two different robot_paths

    @pytest.mark.asyncio
    async def test_state_isolated_between_robots(self):
        """Mutating params for robot A must not affect robot B's state."""
        setup = self._get_setup_handler()
        set_p = self._get_set_handler()
        robot_a = "/World/FrankaB2T4A"
        robot_b = "/World/FrankaB2T4B"

        await setup({"robot_path": robot_a, "stiffness_xyz": [500.0, 500.0, 500.0]})
        await setup({"robot_path": robot_b, "stiffness_xyz": [500.0, 500.0, 500.0]})

        # Mutate only robot_a
        await set_p({"robot_path": robot_a, "stiffness_xyz": [100.0, 100.0, 100.0]})

        # robot_b must be unaffected
        result_b = await set_p({"robot_path": robot_b})
        assert result_b["success"] is True
        assert result_b["stiffness_xyz"] == [500.0, 500.0, 500.0]

    # ------------------------------------------------------------------
    # T5 — dry_run=False raises NotImplementedError

    @pytest.mark.asyncio
    async def test_live_mode_raises_not_implemented(self):
        """set_compliance_params with dry_run=False raises NotImplementedError
        with a message referencing Kit RPC or ros2_control bridge."""
        setup = self._get_setup_handler()
        set_p = self._get_set_handler()
        robot = "/World/FrankaB2T5"

        await setup({"robot_path": robot})

        with pytest.raises(NotImplementedError) as exc_info:
            await set_p({"robot_path": robot, "dry_run": False})
        msg = str(exc_info.value)
        assert "Kit RPC" in msg or "ros2_control" in msg or "bridge" in msg
