"""
L0 tests for the 9 Tier 3 — Articulation & Joints atomic tools (see
docs/specs/atomic_tools_catalog.md).

Seven handlers are DATA handlers that ship a print-json snippet to Kit through
queue_exec_patch; we stub queue_exec_patch so the test suite never needs a
running Kit RPC server. Two handlers are CODE_GEN handlers
(set_joint_limits, set_joint_velocity_limit) and are exercised through
CODE_GEN_HANDLERS.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.l0

from service.isaac_assist_service.chat.tools.tool_executor import (
    CODE_GEN_HANDLERS,
    DATA_HANDLERS,
)
from service.isaac_assist_service.chat.tools.tool_schemas import ISAAC_SIM_TOOLS


# ---------------------------------------------------------------------------
# Catalog of the 9 Tier 3 tools
# ---------------------------------------------------------------------------

TIER3_TOOLS = [
    "get_joint_positions",       # T3.1 DATA
    "get_joint_velocities",      # T3.2 DATA
    "get_joint_torques",         # T3.3 DATA
    "get_drive_gains",           # T3.4 DATA
    "set_joint_limits",          # T3.5 CODE_GEN
    "set_joint_velocity_limit",  # T3.6 CODE_GEN
    "get_articulation_mass",     # T3.7 DATA
    "get_center_of_mass",        # T3.8 DATA
    "get_gripper_state",         # T3.9 DATA
]

TIER3_DATA_HANDLERS = [
    "get_joint_positions",
    "get_joint_velocities",
    "get_joint_torques",
    "get_drive_gains",
    "get_articulation_mass",
    "get_center_of_mass",
    "get_gripper_state",
]

TIER3_CODE_GEN_HANDLERS = [
    "set_joint_limits",
    "set_joint_velocity_limit",
]


# ---------------------------------------------------------------------------
# Shared fixture — capture queue_exec_patch submissions
# ---------------------------------------------------------------------------

@pytest.fixture()
def capture_kit_patches(monkeypatch):
    """Intercept kit_tools.queue_exec_patch and record the submitted code."""
    captured: list = []

    async def fake_queue(code, description=""):
        captured.append({"code": code, "description": description})
        return {"queued": True, "patch_id": "test_tier3"}

    import service.isaac_assist_service.chat.tools.kit_tools as kt
    monkeypatch.setattr(kt, "queue_exec_patch", fake_queue)
    return captured


def _assert_compiles(code: str, label: str) -> None:
    try:
        compile(code, f"<{label}>", "exec")
    except SyntaxError as exc:
        pytest.fail(f"{label} produced invalid python:\n{exc}\n\nCode:\n{code}")


# ---------------------------------------------------------------------------
# Coverage / dispatch tests
# ---------------------------------------------------------------------------

class TestTier3Coverage:
    """Every Tier 3 tool must be in the schema and have a handler registered."""

    def test_exactly_nine_tier3_tools(self):
        assert len(TIER3_TOOLS) == 9
        assert len(set(TIER3_TOOLS)) == 9

    def test_split_is_seven_data_two_code_gen(self):
        assert len(TIER3_DATA_HANDLERS) == 7
        assert len(TIER3_CODE_GEN_HANDLERS) == 2
        assert set(TIER3_DATA_HANDLERS) | set(TIER3_CODE_GEN_HANDLERS) == set(TIER3_TOOLS)
        assert set(TIER3_DATA_HANDLERS) & set(TIER3_CODE_GEN_HANDLERS) == set()

    @pytest.mark.parametrize("name", TIER3_TOOLS)
    def test_tool_in_schema(self, name):
        names = {t["function"]["name"] for t in ISAAC_SIM_TOOLS}
        assert name in names, f"Tier 3 tool '{name}' missing from ISAAC_SIM_TOOLS"

    @pytest.mark.parametrize("name", TIER3_TOOLS)
    def test_tool_has_handler(self, name):
        assert name in CODE_GEN_HANDLERS or name in DATA_HANDLERS, (
            f"Tier 3 tool '{name}' has no handler registered"
        )

    @pytest.mark.parametrize("name", TIER3_DATA_HANDLERS)
    def test_data_handler_registered(self, name):
        assert name in DATA_HANDLERS, f"{name} should be a DATA handler"
        assert name not in CODE_GEN_HANDLERS, f"{name} should NOT also be CODE_GEN"
        assert callable(DATA_HANDLERS[name]), f"{name} handler is not callable"

    @pytest.mark.parametrize("name", TIER3_CODE_GEN_HANDLERS)
    def test_code_gen_handler_registered(self, name):
        assert name in CODE_GEN_HANDLERS, f"{name} should be a CODE_GEN handler"
        assert name not in DATA_HANDLERS, f"{name} should NOT also be DATA"
        assert callable(CODE_GEN_HANDLERS[name]), f"{name} handler is not callable"

    @pytest.mark.parametrize("name", TIER3_TOOLS)
    def test_schema_documents_required_params(self, name):
        for tool in ISAAC_SIM_TOOLS:
            if tool["function"]["name"] != name:
                continue
            params = tool["function"]["parameters"]
            assert params["type"] == "object"
            assert "properties" in params
            assert "required" in params
            return
        pytest.fail(f"{name} not in schema list")


# ---------------------------------------------------------------------------
# Per-tool DATA handler tests — verify the snippet emitted to Kit
# ---------------------------------------------------------------------------

class TestGetJointPositions:
    @pytest.mark.asyncio
    async def test_emits_joint_positions_walk(self, capture_kit_patches):
        handler = DATA_HANDLERS["get_joint_positions"]
        result = await handler({"articulation": "/World/Franka"})
        assert result["queued"] is True
        assert len(capture_kit_patches) == 1
        code = capture_kit_patches[0]["code"]
        _assert_compiles(code, "get_joint_positions")
        assert "'/World/Franka'" in code
        assert "Usd.PrimRange" in code
        assert "UsdPhysics.RevoluteJoint" in code
        assert "UsdPhysics.PrismaticJoint" in code
        assert "positions" in code
        assert "json.dumps" in code

    @pytest.mark.asyncio
    async def test_handles_missing_articulation_in_emitted_code(self, capture_kit_patches):
        handler = DATA_HANDLERS["get_joint_positions"]
        await handler({"articulation": "/World/MissingRobot"})
        code = capture_kit_patches[0]["code"]
        _assert_compiles(code, "get_joint_positions")
        assert "'articulation not found'" in code


class TestGetJointVelocities:
    @pytest.mark.asyncio
    async def test_emits_joint_velocities_walk(self, capture_kit_patches):
        handler = DATA_HANDLERS["get_joint_velocities"]
        await handler({"articulation": "/World/Franka"})
        code = capture_kit_patches[0]["code"]
        _assert_compiles(code, "get_joint_velocities")
        assert "'/World/Franka'" in code
        assert "physics:velocity" in code
        assert "velocities" in code
        # Must distinguish revolute vs prismatic units in the result envelope
        assert "deg/s" in code
        assert "m/s" in code


class TestGetJointTorques:
    @pytest.mark.asyncio
    async def test_emits_torque_read(self, capture_kit_patches):
        handler = DATA_HANDLERS["get_joint_torques"]
        await handler({"articulation": "/World/Franka"})
        code = capture_kit_patches[0]["code"]
        _assert_compiles(code, "get_joint_torques")
        assert "'/World/Franka'" in code
        # Spec: applied actuator torque/force per joint
        assert "appliedJointTorque" in code
        assert "appliedJointForce" in code
        assert "torques" in code
        # Units differ for revolute vs prismatic
        assert "N*m" in code
        assert "'N'" in code


class TestGetDriveGains:
    @pytest.mark.asyncio
    async def test_emits_drive_inspection(self, capture_kit_patches):
        handler = DATA_HANDLERS["get_drive_gains"]
        await handler({"joint_path": "/World/Franka/panda_link0/panda_joint1"})
        code = capture_kit_patches[0]["code"]
        _assert_compiles(code, "get_drive_gains")
        assert "UsdPhysics.DriveAPI" in code
        assert "GetStiffnessAttr" in code
        assert "GetDampingAttr" in code
        assert "GetMaxForceAttr" in code
        # 'auto' should consider both drive tokens
        assert "'angular'" in code
        assert "'linear'" in code

    @pytest.mark.asyncio
    async def test_explicit_drive_type(self, capture_kit_patches):
        handler = DATA_HANDLERS["get_drive_gains"]
        await handler({"joint_path": "/World/Franka/j1", "drive_type": "linear"})
        code = capture_kit_patches[0]["code"]
        _assert_compiles(code, "get_drive_gains")
        # Requested string is shipped to Kit — auto split should NOT fire
        assert "'linear'" in code
        assert "requested = 'linear'" in code


class TestGetArticulationMass:
    @pytest.mark.asyncio
    async def test_emits_link_walk(self, capture_kit_patches):
        handler = DATA_HANDLERS["get_articulation_mass"]
        await handler({"articulation": "/World/Franka"})
        code = capture_kit_patches[0]["code"]
        _assert_compiles(code, "get_articulation_mass")
        assert "Usd.PrimRange" in code
        assert "UsdPhysics.RigidBodyAPI" in code
        assert "UsdPhysics.MassAPI" in code
        assert "GetMassAttr" in code
        assert "total_mass" in code
        assert "'kg'" in code


class TestGetCenterOfMass:
    @pytest.mark.asyncio
    async def test_emits_world_com_compute(self, capture_kit_patches):
        handler = DATA_HANDLERS["get_center_of_mass"]
        await handler({"articulation": "/World/Franka"})
        code = capture_kit_patches[0]["code"]
        _assert_compiles(code, "get_center_of_mass")
        assert "ComputeLocalToWorldTransform" in code
        assert "GetCenterOfMassAttr" in code
        # Mass-weighted average
        assert "total_mass" in code
        assert "center_of_mass" in code
        # Skip zero-mass links rather than crashing
        assert "no mass-bearing links found" in code


class TestGetGripperState:
    @pytest.mark.asyncio
    async def test_emits_gripper_classification(self, capture_kit_patches):
        handler = DATA_HANDLERS["get_gripper_state"]
        await handler({
            "articulation": "/World/Franka",
            "gripper_joints": ["panda_finger_joint1", "panda_finger_joint2"],
        })
        code = capture_kit_patches[0]["code"]
        _assert_compiles(code, "get_gripper_state")
        assert "'/World/Franka'" in code
        assert "'panda_finger_joint1'" in code
        assert "'panda_finger_joint2'" in code
        # Spec: open / closed / in-between + force estimate
        assert "'open'" in code
        assert "'closed'" in code
        assert "'midway'" in code
        assert "force_estimate" in code

    @pytest.mark.asyncio
    async def test_custom_thresholds(self, capture_kit_patches):
        handler = DATA_HANDLERS["get_gripper_state"]
        await handler({
            "articulation": "/World/Franka",
            "gripper_joints": ["finger_left", "finger_right"],
            "open_threshold": 0.8,
            "closed_threshold": 0.2,
        })
        code = capture_kit_patches[0]["code"]
        _assert_compiles(code, "get_gripper_state")
        assert "open_threshold = 0.8" in code
        assert "closed_threshold = 0.2" in code

    @pytest.mark.asyncio
    async def test_empty_gripper_joints_surfaces_error(self, capture_kit_patches):
        handler = DATA_HANDLERS["get_gripper_state"]
        await handler({"articulation": "/World/Franka", "gripper_joints": []})
        code = capture_kit_patches[0]["code"]
        _assert_compiles(code, "get_gripper_state")
        assert "gripper_joints must not be empty" in code


# ---------------------------------------------------------------------------
# Per-tool CODE_GEN handler tests
# ---------------------------------------------------------------------------

class TestSetJointLimits:
    def test_generates_limit_set(self):
        gen = CODE_GEN_HANDLERS["set_joint_limits"]
        code = gen({"joint_path": "/World/Franka/j1", "lower": -2.5, "upper": 2.5})
        _assert_compiles(code, "set_joint_limits")
        assert "'/World/Franka/j1'" in code
        # Both limits must end up in the patch
        assert "-2.5" in code
        assert "2.5" in code
        assert "physics:lowerLimit" in code
        assert "physics:upperLimit" in code
        # Auto-creates the attr if it isn't defined yet
        assert "CreateLowerLimitAttr" in code
        assert "CreateUpperLimitAttr" in code
        # Rejects non-Revolute/Prismatic joints with a clear error
        assert "is not Revolute or Prismatic" in code

    def test_path_with_special_chars(self):
        gen = CODE_GEN_HANDLERS["set_joint_limits"]
        code = gen({"joint_path": "/World/My Robot (v2)/j1", "lower": 0.0, "upper": 1.0})
        _assert_compiles(code, "set_joint_limits")
        assert "/World/My Robot (v2)/j1" in code


class TestSetJointVelocityLimit:
    def test_generates_max_velocity_set(self):
        gen = CODE_GEN_HANDLERS["set_joint_velocity_limit"]
        code = gen({"joint_path": "/World/Franka/j1", "vel_limit": 3.14})
        _assert_compiles(code, "set_joint_velocity_limit")
        assert "'/World/Franka/j1'" in code
        assert "3.14" in code
        # Prefer PhysxJointAPI but fall back to raw attribute write
        assert "PhysxSchema" in code
        assert "PhysxJointAPI" in code
        assert "MaxJointVelocity" in code
        assert "physxJoint:maxJointVelocity" in code
        assert "is not Revolute or Prismatic" in code

    def test_zero_velocity_locks_joint(self):
        gen = CODE_GEN_HANDLERS["set_joint_velocity_limit"]
        code = gen({"joint_path": "/World/Franka/j1", "vel_limit": 0.0})
        _assert_compiles(code, "set_joint_velocity_limit")
        assert "0.0" in code


# ---------------------------------------------------------------------------
# Round-trip dispatch through execute_tool_call
# ---------------------------------------------------------------------------

class TestExecuteToolCallTier3:
    """Ensure each Tier 3 tool round-trips cleanly through execute_tool_call."""

    @pytest.mark.asyncio
    async def test_data_tool_returns_data_envelope(self, capture_kit_patches):
        from service.isaac_assist_service.chat.tools.tool_executor import execute_tool_call
        result = await execute_tool_call("get_joint_positions", {"articulation": "/World/Franka"})
        assert result["type"] == "data"
        assert result.get("queued") is True

    @pytest.mark.asyncio
    async def test_code_gen_tool_returns_code_patch_envelope(self, monkeypatch):
        from service.isaac_assist_service.chat.tools import tool_executor as te

        async def fake_queue(code, description=""):
            return {"queued": True, "patch_id": "tier3_test"}

        monkeypatch.setattr(te.kit_tools, "queue_exec_patch", fake_queue)
        result = await te.execute_tool_call(
            "set_joint_limits",
            {"joint_path": "/World/Franka/j1", "lower": -1.0, "upper": 1.0},
        )
        assert result["type"] == "code_patch"
        assert "code" in result
        assert "physics:lowerLimit" in result["code"]
        assert "physics:upperLimit" in result["code"]

    @pytest.mark.asyncio
    async def test_set_joint_velocity_limit_round_trip(self, monkeypatch):
        from service.isaac_assist_service.chat.tools import tool_executor as te

        async def fake_queue(code, description=""):
            return {"queued": True}

        monkeypatch.setattr(te.kit_tools, "queue_exec_patch", fake_queue)
        result = await te.execute_tool_call(
            "set_joint_velocity_limit",
            {"joint_path": "/World/Franka/j1", "vel_limit": 5.0},
        )
        assert result["type"] == "code_patch"
        assert "5.0" in result["code"]

    @pytest.mark.asyncio
    async def test_get_gripper_state_round_trip(self, capture_kit_patches):
        from service.isaac_assist_service.chat.tools.tool_executor import execute_tool_call
        result = await execute_tool_call(
            "get_gripper_state",
            {
                "articulation": "/World/Franka",
                "gripper_joints": ["panda_finger_joint1", "panda_finger_joint2"],
            },
        )
        assert result["type"] == "data"
        assert result.get("queued") is True
