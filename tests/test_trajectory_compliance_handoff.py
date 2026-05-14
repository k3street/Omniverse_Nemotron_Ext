"""CRM-C4 — L0 tests for `follow_trajectory_with_compliance`.

The Phase 63b ↔ Layer 1 bridge.  Splits a Phase-63b trajectory at
`compliance_handoff_at` into a rigid prefix (`n_rigid` waypoints executed
as exact joint targets) and a compliant suffix (`n_compliant` waypoints
fed to the compliance controller as desired-pose references that yield
to F/T feedback).

Test coverage:
  - empty trajectory → error
  - non-list trajectory → error
  - trajectory with non-dict waypoint → error
  - trajectory waypoint without joint_positions or pose → error
  - handoff_at out of [0, 1] (negative, > 1) → error
  - handoff_at non-numeric → error
  - unknown compliance_controller → error with valid_modes
  - compliance_controller == "null" → error (incompatible with bridge)
  - compliance_controller wrong type → error
  - robot with no compliance installed → error suggesting setup_admittance
  - timeout_s ≤ 0 or non-numeric → error
  - velocity_scaling ≤ 0 or non-numeric → error
  - happy path 10 wps, handoff_at=0.5 → n_rigid=5, n_compliant=5
  - happy path handoff_at=0.0 → n_rigid=0, all compliant
  - happy path handoff_at=1.0 → all rigid, no compliant
  - lock_orientation_from match → no warning
  - lock_orientation_from mismatch > 0.01 → handoff_mismatch_warning set
  - lock_orientation_from within tolerance (≤ 0.01) → no warning
  - lock_orientation_from non-numeric → no warning (treated as absent)
  - final_pose returned matches last waypoint
  - t_handoff_observed == compliance_handoff_at
  - all live-mode fields (contact_detected_at, ft_at_handoff) are None in dry-run
  - tool reachable via execute_tool_call
  - dry_run=False raises NotImplementedError
  - public follow_trajectory_with_compliance signature mirrors §5.5

Per docs/specs/2026-05-11-contact-rich-manipulation-spec.md §5.5 (CRM-C4).
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.l0


# ---------------------------------------------------------------------------
# Helpers


def _get_handler():
    from service.isaac_assist_service.chat.tools.handlers.compliance import (
        _handle_follow_trajectory_with_compliance,
    )
    return _handle_follow_trajectory_with_compliance


def _get_setup_admittance():
    from service.isaac_assist_service.chat.tools.handlers.compliance import (
        _handle_setup_admittance_controller,
    )
    return _handle_setup_admittance_controller


def _get_state_dict():
    from service.isaac_assist_service.chat.tools.handlers import compliance
    return compliance._INSTALLED_COMPLIANCE


def _make_trajectory(n: int, lock_orientation_from: float | None = None):
    """Build a synthetic Phase-63b-shaped trajectory of length n."""
    traj = []
    for i in range(n):
        wp = {
            "joint_positions": [0.1 * i] * 7,
            "pose": [0.5 + 0.01 * i, 0.0, 0.3, 1.0, 0.0, 0.0, 0.0],
        }
        traj.append(wp)
    if lock_orientation_from is not None and traj:
        traj[0]["lock_orientation_from"] = lock_orientation_from
    return traj


async def _install_admittance(robot_path: str) -> None:
    """Install an admittance controller so the bridge has somewhere to go."""
    setup = _get_setup_admittance()
    result = await setup({"robot_path": robot_path})
    assert result["success"] is True, f"setup failed: {result}"


# ---------------------------------------------------------------------------
# Validation: trajectory shape


class TestTrajectoryValidation:
    """Trajectory must be a non-empty list of dicts with joint_positions/pose."""

    @pytest.mark.asyncio
    async def test_empty_trajectory_returns_error(self):
        """Empty list → success=False with actionable error."""
        handler = _get_handler()
        result = await handler({"trajectory": []})
        assert result["success"] is False
        assert result["ok"] is False
        assert "trajectory must contain at least one waypoint" in result["error"]

    @pytest.mark.asyncio
    async def test_non_list_trajectory_returns_error(self):
        """A non-list trajectory → success=False, error mentions list."""
        handler = _get_handler()
        result = await handler({"trajectory": "not a list"})
        assert result["success"] is False
        assert "list" in result["error"]

    @pytest.mark.asyncio
    async def test_trajectory_with_non_dict_waypoint_returns_error(self):
        """A trajectory containing a non-dict element → success=False."""
        handler = _get_handler()
        result = await handler({"trajectory": [{"joint_positions": [0] * 7}, "bogus"]})
        assert result["success"] is False
        assert "must be a dict" in result["error"]

    @pytest.mark.asyncio
    async def test_trajectory_waypoint_missing_required_key_returns_error(self):
        """Waypoint without joint_positions or pose → success=False."""
        handler = _get_handler()
        result = await handler({"trajectory": [{"velocity": [0] * 7}]})
        assert result["success"] is False
        assert "joint_positions" in result["error"] or "pose" in result["error"]


# ---------------------------------------------------------------------------
# Validation: compliance_handoff_at


class TestHandoffFractionValidation:
    """compliance_handoff_at must be a float in [0, 1]."""

    @pytest.mark.asyncio
    async def test_handoff_negative_returns_error(self):
        """handoff_at < 0 → success=False."""
        handler = _get_handler()
        result = await handler({
            "trajectory": _make_trajectory(5),
            "compliance_handoff_at": -0.1,
        })
        assert result["success"] is False
        assert "[0, 1]" in result["error"]

    @pytest.mark.asyncio
    async def test_handoff_greater_than_one_returns_error(self):
        """handoff_at > 1 → success=False."""
        handler = _get_handler()
        result = await handler({
            "trajectory": _make_trajectory(5),
            "compliance_handoff_at": 1.5,
        })
        assert result["success"] is False
        assert "[0, 1]" in result["error"]

    @pytest.mark.asyncio
    async def test_handoff_non_numeric_returns_error(self):
        """Non-numeric handoff_at → success=False (graceful type error)."""
        handler = _get_handler()
        result = await handler({
            "trajectory": _make_trajectory(5),
            "compliance_handoff_at": "halfway",
        })
        assert result["success"] is False
        assert "float" in result["error"]


# ---------------------------------------------------------------------------
# Validation: compliance_controller


class TestControllerValidation:
    """compliance_controller must be in COMPLIANCE_MODE_ENUM excluding 'null'."""

    @pytest.mark.asyncio
    async def test_unknown_controller_returns_error_with_valid_modes(self):
        """An unknown mode name → success=False with valid_modes list."""
        handler = _get_handler()
        result = await handler({
            "trajectory": _make_trajectory(5),
            "compliance_controller": "no_such_mode",
        })
        assert result["success"] is False
        assert "no_such_mode" in result["error"]
        assert "valid_modes" in result
        assert "admittance" in result["valid_modes"]

    @pytest.mark.asyncio
    async def test_null_controller_returns_error(self):
        """'null' mode is incompatible with this bridge → success=False."""
        handler = _get_handler()
        result = await handler({
            "trajectory": _make_trajectory(5),
            "compliance_controller": "null",
        })
        assert result["success"] is False
        assert "null" in result["error"].lower()
        # 'null' must be excluded from the suggested valid_modes
        assert "null" not in result["valid_modes"]

    @pytest.mark.asyncio
    async def test_controller_wrong_type_returns_error(self):
        """A non-string controller → success=False."""
        handler = _get_handler()
        result = await handler({
            "trajectory": _make_trajectory(5),
            "compliance_controller": 42,
        })
        assert result["success"] is False
        assert "string" in result["error"]


# ---------------------------------------------------------------------------
# Validation: robot has compliance installed


class TestRobotPreflight:
    """Robot must have a compliance controller installed before handoff."""

    @pytest.mark.asyncio
    async def test_no_compliance_installed_returns_error_suggesting_setup(self):
        """Uninstalled robot → success=False with suggested_tool."""
        handler = _get_handler()
        # Ensure clean state for this robot_path
        state = _get_state_dict()
        robot = "/World/C4T_no_install"
        state.pop(robot, None)

        result = await handler({
            "trajectory": _make_trajectory(5),
            "robot_path": robot,
        })
        assert result["success"] is False
        assert robot in result["error"]
        assert "setup_admittance" in result["error"]
        assert result.get("suggested_tool") == "setup_admittance_controller"
        assert "available_robots" in result


# ---------------------------------------------------------------------------
# Validation: timeout + velocity scaling


class TestNumericGuards:
    """timeout_s and velocity_scaling must be positive numbers."""

    @pytest.mark.asyncio
    async def test_timeout_negative_returns_error(self):
        handler = _get_handler()
        result = await handler({
            "trajectory": _make_trajectory(5),
            "timeout_s": -1.0,
        })
        assert result["success"] is False
        assert "timeout_s" in result["error"]

    @pytest.mark.asyncio
    async def test_timeout_zero_returns_error(self):
        handler = _get_handler()
        result = await handler({
            "trajectory": _make_trajectory(5),
            "timeout_s": 0.0,
        })
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_timeout_non_numeric_returns_error(self):
        handler = _get_handler()
        result = await handler({
            "trajectory": _make_trajectory(5),
            "timeout_s": "thirty",
        })
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_velocity_scaling_zero_returns_error(self):
        handler = _get_handler()
        result = await handler({
            "trajectory": _make_trajectory(5),
            "velocity_scaling": 0.0,
        })
        assert result["success"] is False
        assert "velocity_scaling" in result["error"]

    @pytest.mark.asyncio
    async def test_velocity_scaling_negative_returns_error(self):
        handler = _get_handler()
        result = await handler({
            "trajectory": _make_trajectory(5),
            "velocity_scaling": -0.5,
        })
        assert result["success"] is False


# ---------------------------------------------------------------------------
# Happy path: split math


class TestHandoffSplit:
    """n_rigid = int(handoff_at * n_waypoints); n_compliant = n - n_rigid."""

    @pytest.mark.asyncio
    async def test_happy_path_10_waypoints_handoff_half(self):
        """10 wps, handoff_at=0.5 → n_rigid=5, n_compliant=5."""
        await _install_admittance("/World/C4T_split_half")
        handler = _get_handler()
        result = await handler({
            "trajectory": _make_trajectory(10),
            "robot_path": "/World/C4T_split_half",
            "compliance_handoff_at": 0.5,
        })
        assert result["success"] is True
        assert result["ok"] is True
        assert result["n_waypoints"] == 10
        assert result["n_rigid"] == 5
        assert result["n_compliant"] == 5
        assert result["n_rigid"] + result["n_compliant"] == result["n_waypoints"]

    @pytest.mark.asyncio
    async def test_handoff_zero_means_all_compliant(self):
        """handoff_at=0.0 → n_rigid=0, n_compliant=n."""
        await _install_admittance("/World/C4T_split_zero")
        handler = _get_handler()
        result = await handler({
            "trajectory": _make_trajectory(8),
            "robot_path": "/World/C4T_split_zero",
            "compliance_handoff_at": 0.0,
        })
        assert result["success"] is True
        assert result["n_rigid"] == 0
        assert result["n_compliant"] == 8

    @pytest.mark.asyncio
    async def test_handoff_one_means_all_rigid(self):
        """handoff_at=1.0 → n_rigid=n, n_compliant=0."""
        await _install_admittance("/World/C4T_split_one")
        handler = _get_handler()
        result = await handler({
            "trajectory": _make_trajectory(8),
            "robot_path": "/World/C4T_split_one",
            "compliance_handoff_at": 1.0,
        })
        assert result["success"] is True
        assert result["n_rigid"] == 8
        assert result["n_compliant"] == 0

    @pytest.mark.asyncio
    async def test_t_handoff_observed_equals_handoff_at(self):
        """t_handoff_observed mirrors compliance_handoff_at in dry-run."""
        await _install_admittance("/World/C4T_obs")
        handler = _get_handler()
        result = await handler({
            "trajectory": _make_trajectory(10),
            "robot_path": "/World/C4T_obs",
            "compliance_handoff_at": 0.37,
        })
        assert result["success"] is True
        assert result["t_handoff_observed"] == pytest.approx(0.37)

    @pytest.mark.asyncio
    async def test_handoff_one_third_with_nine_waypoints(self):
        """9 wps × 0.333... = 2 (int truncation toward zero)."""
        await _install_admittance("/World/C4T_third")
        handler = _get_handler()
        result = await handler({
            "trajectory": _make_trajectory(9),
            "robot_path": "/World/C4T_third",
            "compliance_handoff_at": 1.0 / 3.0,
        })
        assert result["success"] is True
        assert result["n_rigid"] == 3
        assert result["n_compliant"] == 6


# ---------------------------------------------------------------------------
# Lock-orientation mismatch warning


class TestLockOrientationMismatch:
    """Trajectory's lock_orientation_from must match compliance_handoff_at."""

    @pytest.mark.asyncio
    async def test_lock_matches_handoff_no_warning(self):
        """lock_orientation_from == handoff_at → no warning."""
        await _install_admittance("/World/C4T_lock_match")
        handler = _get_handler()
        traj = _make_trajectory(10, lock_orientation_from=0.5)
        result = await handler({
            "trajectory": traj,
            "robot_path": "/World/C4T_lock_match",
            "compliance_handoff_at": 0.5,
        })
        assert result["success"] is True
        assert result["handoff_mismatch_warning"] is None

    @pytest.mark.asyncio
    async def test_lock_mismatch_emits_structured_warning(self):
        """lock_orientation_from differs by >0.01 → warning set, success unchanged."""
        await _install_admittance("/World/C4T_lock_mismatch")
        handler = _get_handler()
        # Phase 63b says 0.3, caller passes 0.5
        traj = _make_trajectory(10, lock_orientation_from=0.3)
        result = await handler({
            "trajectory": traj,
            "robot_path": "/World/C4T_lock_mismatch",
            "compliance_handoff_at": 0.5,
        })
        assert result["success"] is True  # warning is not failure
        assert result["ok"] is True
        warning = result["handoff_mismatch_warning"]
        assert warning is not None
        assert "0.3" in warning or "0.30" in warning
        assert "0.5" in warning or "0.50" in warning
        assert "lock_orientation_from" in warning

    @pytest.mark.asyncio
    async def test_lock_within_tolerance_no_warning(self):
        """lock_orientation_from within 0.01 of handoff_at → no warning."""
        await _install_admittance("/World/C4T_lock_close")
        handler = _get_handler()
        traj = _make_trajectory(10, lock_orientation_from=0.505)
        result = await handler({
            "trajectory": traj,
            "robot_path": "/World/C4T_lock_close",
            "compliance_handoff_at": 0.5,
        })
        assert result["success"] is True
        assert result["handoff_mismatch_warning"] is None

    @pytest.mark.asyncio
    async def test_lock_non_numeric_treated_as_absent(self):
        """Non-numeric lock_orientation_from → no warning (gracefully ignored)."""
        await _install_admittance("/World/C4T_lock_bad")
        handler = _get_handler()
        traj = _make_trajectory(10)
        traj[0]["lock_orientation_from"] = "broken"
        result = await handler({
            "trajectory": traj,
            "robot_path": "/World/C4T_lock_bad",
            "compliance_handoff_at": 0.5,
        })
        assert result["success"] is True
        assert result["handoff_mismatch_warning"] is None


# ---------------------------------------------------------------------------
# Plan-dict shape


class TestPlanDictShape:
    """§5.5 return-shape contract."""

    @pytest.mark.asyncio
    async def test_final_pose_is_last_waypoint(self):
        """result['final_pose'] is identical to trajectory[-1]."""
        await _install_admittance("/World/C4T_final")
        handler = _get_handler()
        traj = _make_trajectory(7)
        result = await handler({
            "trajectory": traj,
            "robot_path": "/World/C4T_final",
        })
        assert result["success"] is True
        assert result["final_pose"] == traj[-1]

    @pytest.mark.asyncio
    async def test_live_only_fields_are_none_in_dry_run(self):
        """contact_detected_at and ft_at_handoff are None in dry-run."""
        await _install_admittance("/World/C4T_live_fields")
        handler = _get_handler()
        result = await handler({
            "trajectory": _make_trajectory(10),
            "robot_path": "/World/C4T_live_fields",
        })
        assert result["success"] is True
        assert result["contact_detected_at"] is None
        assert result["ft_at_handoff"] is None

    @pytest.mark.asyncio
    async def test_plan_dict_contains_all_required_keys(self):
        """The plan dict exposes every §5.5 field."""
        await _install_admittance("/World/C4T_shape")
        handler = _get_handler()
        result = await handler({
            "trajectory": _make_trajectory(10),
            "robot_path": "/World/C4T_shape",
            "compliance_handoff_at": 0.4,
            "compliance_controller": "admittance",
            "timeout_s": 25.0,
            "velocity_scaling": 0.8,
        })
        assert result["success"] is True
        required_keys = {
            "success", "ok", "robot_path", "compliance_controller",
            "n_waypoints", "n_rigid", "n_compliant",
            "compliance_handoff_at", "t_handoff_observed",
            "velocity_scaling", "timeout_s",
            "contact_detected_at", "final_pose", "ft_at_handoff",
            "handoff_mismatch_warning",
        }
        missing = required_keys - set(result.keys())
        assert not missing, f"missing keys in plan dict: {missing}"
        # Confirm scalar pass-throughs are byte-identical to inputs.
        assert result["compliance_handoff_at"] == pytest.approx(0.4)
        assert result["compliance_controller"] == "admittance"
        assert result["timeout_s"] == pytest.approx(25.0)
        assert result["velocity_scaling"] == pytest.approx(0.8)


# ---------------------------------------------------------------------------
# Dispatch + schema wiring


class TestWiring:
    """Tool must be reachable via execute_tool_call and registered in schema."""

    @pytest.mark.asyncio
    async def test_tool_reachable_via_execute_tool_call(self):
        """follow_trajectory_with_compliance is in DATA_HANDLERS."""
        from service.isaac_assist_service.chat.tools import tool_executor
        await _install_admittance("/World/C4T_exec")
        result = await tool_executor.execute_tool_call(
            "follow_trajectory_with_compliance",
            {
                "trajectory": _make_trajectory(4),
                "robot_path": "/World/C4T_exec",
            },
        )
        assert result.get("success") is True
        assert result.get("ok") is True
        assert "n_rigid" in result

    def test_schema_in_isaac_sim_tools(self):
        """follow_trajectory_with_compliance appears in ISAAC_SIM_TOOLS."""
        from service.isaac_assist_service.chat.tools.tool_schemas import (
            ISAAC_SIM_TOOLS,
        )
        names = {t["function"]["name"] for t in ISAAC_SIM_TOOLS}
        assert "follow_trajectory_with_compliance" in names

    def test_schema_only_trajectory_required(self):
        """Schema's required list contains only 'trajectory'."""
        from service.isaac_assist_service.chat.tools.tool_schemas import (
            ISAAC_SIM_TOOLS,
        )
        schema = next(
            t for t in ISAAC_SIM_TOOLS
            if t["function"]["name"] == "follow_trajectory_with_compliance"
        )
        required = schema["function"]["parameters"].get("required", [])
        assert required == ["trajectory"]

    def test_schema_trajectory_is_array_of_objects(self):
        """trajectory parameter is typed as array-of-objects."""
        from service.isaac_assist_service.chat.tools.tool_schemas import (
            ISAAC_SIM_TOOLS,
        )
        schema = next(
            t for t in ISAAC_SIM_TOOLS
            if t["function"]["name"] == "follow_trajectory_with_compliance"
        )
        props = schema["function"]["parameters"]["properties"]
        assert props["trajectory"]["type"] == "array"
        assert props["trajectory"]["items"]["type"] == "object"


# ---------------------------------------------------------------------------
# Live mode


class TestLiveMode:
    """dry_run=False raises NotImplementedError with actionable message."""

    @pytest.mark.asyncio
    async def test_dry_run_false_raises_not_implemented(self):
        """dry_run=False raises NotImplementedError referencing Kit RPC."""
        await _install_admittance("/World/C4T_live")
        handler = _get_handler()
        with pytest.raises(NotImplementedError) as exc_info:
            await handler({
                "trajectory": _make_trajectory(5),
                "robot_path": "/World/C4T_live",
                "dry_run": False,
            })
        msg = str(exc_info.value)
        assert "Kit RPC" in msg or "ros2_control" in msg
        # Live-mode raise must come AFTER all validation (validation must
        # have passed by the time we hit the live branch).
        assert "bridge" in msg or "Kit RPC" in msg


# ---------------------------------------------------------------------------
# Public signature parity


class TestPublicSignature:
    """`follow_trajectory_with_compliance` mirrors §5.5 signature exactly."""

    @pytest.mark.asyncio
    async def test_public_function_callable_with_only_trajectory(self):
        """The public coroutine accepts only `trajectory` (other args default)."""
        from service.isaac_assist_service.chat.tools.handlers.compliance import (
            follow_trajectory_with_compliance,
        )
        await _install_admittance("/World/Franka")  # the default robot_path
        result = await follow_trajectory_with_compliance(_make_trajectory(6))
        assert result["success"] is True
        assert result["robot_path"] == "/World/Franka"
        assert result["compliance_controller"] == "admittance"
        assert result["compliance_handoff_at"] == pytest.approx(0.5)

    @pytest.mark.asyncio
    async def test_public_function_signature_has_expected_params(self):
        """Inspect the signature parameter set matches §5.5."""
        import inspect
        from service.isaac_assist_service.chat.tools.handlers.compliance import (
            follow_trajectory_with_compliance,
        )
        sig = inspect.signature(follow_trajectory_with_compliance)
        params = list(sig.parameters.keys())
        expected = [
            "trajectory",
            "robot_path",
            "compliance_handoff_at",
            "compliance_controller",
            "timeout_s",
            "velocity_scaling",
            "dry_run",
        ]
        assert params == expected
