"""
L0 tests for the 10 Tier 2 — Physics Bodies & Scene atomic tools (see
docs/specs/atomic_tools_catalog.md).

Seven handlers are DATA handlers that ship a print-json snippet to Kit through
queue_exec_patch; we stub queue_exec_patch so the test suite never needs a
running Kit RPC server. Three handlers are CODE_GEN handlers
(set_linear_velocity, set_physics_scene_config, apply_force) and are exercised
through CODE_GEN_HANDLERS.
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
# Catalog of the 10 Tier 2 tools
# ---------------------------------------------------------------------------

TIER2_TOOLS = [
    "get_linear_velocity",      # T2.1 DATA
    "get_angular_velocity",     # T2.2 DATA
    "set_linear_velocity",      # T2.3 CODE_GEN
    "get_mass",                 # T2.4 DATA
    "get_inertia",              # T2.5 DATA
    "get_physics_scene_config", # T2.6 DATA
    "set_physics_scene_config", # T2.7 CODE_GEN
    "list_contacts",            # T2.8 DATA
    "apply_force",              # T2.9 CODE_GEN
    "get_kinematic_state",      # T2.10 DATA
]

TIER2_DATA_HANDLERS = [
    "get_linear_velocity",
    "get_angular_velocity",
    "get_mass",
    "get_inertia",
    "get_physics_scene_config",
    "list_contacts",
    "get_kinematic_state",
]

TIER2_CODE_GEN_HANDLERS = [
    "set_linear_velocity",
    "set_physics_scene_config",
    "apply_force",
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
        return {"queued": True, "patch_id": "test_tier2"}

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

class TestTier2Coverage:
    """Every Tier 2 tool must be in the schema and have a handler registered."""

    def test_exactly_ten_tier2_tools(self):
        assert len(TIER2_TOOLS) == 10
        assert len(set(TIER2_TOOLS)) == 10

    def test_split_is_seven_data_three_code_gen(self):
        assert len(TIER2_DATA_HANDLERS) == 7
        assert len(TIER2_CODE_GEN_HANDLERS) == 3
        assert set(TIER2_DATA_HANDLERS) | set(TIER2_CODE_GEN_HANDLERS) == set(TIER2_TOOLS)
        assert set(TIER2_DATA_HANDLERS) & set(TIER2_CODE_GEN_HANDLERS) == set()

    @pytest.mark.parametrize("name", TIER2_TOOLS)
    def test_tool_in_schema(self, name):
        names = {t["function"]["name"] for t in ISAAC_SIM_TOOLS}
        assert name in names, f"Tier 2 tool '{name}' missing from ISAAC_SIM_TOOLS"

    @pytest.mark.parametrize("name", TIER2_TOOLS)
    def test_tool_has_handler(self, name):
        assert name in CODE_GEN_HANDLERS or name in DATA_HANDLERS, (
            f"Tier 2 tool '{name}' has no handler registered"
        )

    @pytest.mark.parametrize("name", TIER2_DATA_HANDLERS)
    def test_data_handler_registered(self, name):
        assert name in DATA_HANDLERS, f"{name} should be a DATA handler"
        assert name not in CODE_GEN_HANDLERS, f"{name} should NOT also be CODE_GEN"
        assert callable(DATA_HANDLERS[name]), f"{name} handler is not callable"

    @pytest.mark.parametrize("name", TIER2_CODE_GEN_HANDLERS)
    def test_code_gen_handler_registered(self, name):
        assert name in CODE_GEN_HANDLERS, f"{name} should be a CODE_GEN handler"
        assert name not in DATA_HANDLERS, f"{name} should NOT also be DATA"
        assert callable(CODE_GEN_HANDLERS[name]), f"{name} handler is not callable"


# ---------------------------------------------------------------------------
# Per-tool DATA handler tests — verify the snippet emitted to Kit
# ---------------------------------------------------------------------------

class TestGetLinearVelocity:
    @pytest.mark.asyncio
    async def test_emits_velocity_attr_read(self, capture_kit_patches):
        handler = DATA_HANDLERS["get_linear_velocity"]
        result = await handler({"prim_path": "/World/Cube"})
        assert result["queued"] is True
        assert len(capture_kit_patches) == 1
        code = capture_kit_patches[0]["code"]
        _assert_compiles(code, "get_linear_velocity")
        assert "'/World/Cube'" in code
        assert "UsdPhysics.RigidBodyAPI" in code
        assert "GetVelocityAttr" in code
        assert "linear_velocity" in code
        assert "json.dumps" in code

    @pytest.mark.asyncio
    async def test_path_with_special_chars(self, capture_kit_patches):
        handler = DATA_HANDLERS["get_linear_velocity"]
        await handler({"prim_path": "/World/My Robot (v2)"})
        code = capture_kit_patches[0]["code"]
        _assert_compiles(code, "get_linear_velocity")
        assert "/World/My Robot (v2)" in code


class TestGetAngularVelocity:
    @pytest.mark.asyncio
    async def test_emits_angular_velocity_attr_read(self, capture_kit_patches):
        handler = DATA_HANDLERS["get_angular_velocity"]
        await handler({"prim_path": "/World/Cube"})
        code = capture_kit_patches[0]["code"]
        _assert_compiles(code, "get_angular_velocity")
        assert "'/World/Cube'" in code
        assert "GetAngularVelocityAttr" in code
        assert "angular_velocity" in code
        assert "deg/s" in code


class TestGetMass:
    @pytest.mark.asyncio
    async def test_emits_mass_attr_read(self, capture_kit_patches):
        handler = DATA_HANDLERS["get_mass"]
        await handler({"prim_path": "/World/Cube"})
        code = capture_kit_patches[0]["code"]
        _assert_compiles(code, "get_mass")
        assert "UsdPhysics.MassAPI" in code
        assert "GetMassAttr" in code
        assert "'mass'" in code
        # Returns 0 + note when MassAPI not applied (PhysX auto-computes)
        assert "has_mass_api" in code


class TestGetInertia:
    @pytest.mark.asyncio
    async def test_emits_inertia_attr_read(self, capture_kit_patches):
        handler = DATA_HANDLERS["get_inertia"]
        await handler({"prim_path": "/World/Cube"})
        code = capture_kit_patches[0]["code"]
        _assert_compiles(code, "get_inertia")
        assert "UsdPhysics.MassAPI" in code
        assert "GetDiagonalInertiaAttr" in code
        assert "diagonal_inertia" in code
        # Should also surface CoM + principal axes when authored
        assert "GetCenterOfMassAttr" in code
        assert "GetPrincipalAxesAttr" in code


class TestGetPhysicsSceneConfig:
    @pytest.mark.asyncio
    async def test_emits_scene_inspection_with_default_path(self, capture_kit_patches):
        handler = DATA_HANDLERS["get_physics_scene_config"]
        await handler({})
        code = capture_kit_patches[0]["code"]
        _assert_compiles(code, "get_physics_scene_config")
        assert "UsdPhysics.Scene" in code
        assert "GravityMagnitudeAttr" in code
        assert "GravityDirectionAttr" in code
        assert "PhysxSceneAPI" in code
        # Falls back to first physics scene if none specified
        assert "stage.Traverse" in code

    @pytest.mark.asyncio
    async def test_explicit_scene_path(self, capture_kit_patches):
        handler = DATA_HANDLERS["get_physics_scene_config"]
        await handler({"scene_path": "/World/Physics"})
        code = capture_kit_patches[0]["code"]
        _assert_compiles(code, "get_physics_scene_config")
        assert "'/World/Physics'" in code

    @pytest.mark.asyncio
    async def test_reads_solver_iterations_and_dt(self, capture_kit_patches):
        handler = DATA_HANDLERS["get_physics_scene_config"]
        await handler({})
        code = capture_kit_patches[0]["code"]
        # Spec demands solver / iterations / dt / GPU
        assert "GetSolverTypeAttr" in code
        assert "PositionIterationCountAttr" in code
        assert "VelocityIterationCountAttr" in code
        assert "EnableGPUDynamicsAttr" in code
        assert "TimeStepsPerSecondAttr" in code


class TestListContacts:
    @pytest.mark.asyncio
    async def test_emits_contact_subscription(self, capture_kit_patches):
        handler = DATA_HANDLERS["list_contacts"]
        await handler({"prim_path": "/World/Cube"})
        code = capture_kit_patches[0]["code"]
        _assert_compiles(code, "list_contacts")
        assert "PhysxContactReportAPI" in code
        assert "subscribe_contact_report_events" in code
        assert "contacts" in code
        assert "'/World/Cube'" in code

    @pytest.mark.asyncio
    async def test_min_impulse_filter(self, capture_kit_patches):
        handler = DATA_HANDLERS["list_contacts"]
        await handler({"prim_path": "/World/Cube", "duration": 1.5, "min_impulse": 2.5})
        code = capture_kit_patches[0]["code"]
        _assert_compiles(code, "list_contacts")
        assert "1.5" in code
        assert "2.5" in code


class TestGetKinematicState:
    @pytest.mark.asyncio
    async def test_emits_full_state_read(self, capture_kit_patches):
        handler = DATA_HANDLERS["get_kinematic_state"]
        await handler({"prim_path": "/World/Cube"})
        code = capture_kit_patches[0]["code"]
        _assert_compiles(code, "get_kinematic_state")
        assert "ComputeLocalToWorldTransform" in code
        assert "GetVelocityAttr" in code
        assert "GetAngularVelocityAttr" in code
        # Spec: pos + vel + accel
        assert "linear_acceleration" in code
        assert "angular_acceleration" in code
        assert "orientation_quat" in code

    @pytest.mark.asyncio
    async def test_custom_sample_dt(self, capture_kit_patches):
        handler = DATA_HANDLERS["get_kinematic_state"]
        await handler({"prim_path": "/World/Cube", "sample_dt": 0.25})
        code = capture_kit_patches[0]["code"]
        _assert_compiles(code, "get_kinematic_state")
        assert "0.25" in code


# ---------------------------------------------------------------------------
# Per-tool CODE_GEN handler tests
# ---------------------------------------------------------------------------

class TestSetLinearVelocity:
    def test_generates_velocity_set(self):
        gen = CODE_GEN_HANDLERS["set_linear_velocity"]
        code = gen({"prim_path": "/World/Cube", "vel": [1.0, 0.0, -2.0]})
        _assert_compiles(code, "set_linear_velocity")
        assert "UsdPhysics.RigidBodyAPI" in code
        assert "GetVelocityAttr" in code
        assert "Gf.Vec3f(1.0, 0.0, -2.0)" in code
        # Auto-applies RigidBodyAPI if missing
        assert "RigidBodyAPI.Apply" in code

    def test_default_velocity_is_zero(self):
        gen = CODE_GEN_HANDLERS["set_linear_velocity"]
        code = gen({"prim_path": "/World/Cube"})
        _assert_compiles(code, "set_linear_velocity")
        assert "Gf.Vec3f(0.0, 0.0, 0.0)" in code

    def test_path_with_special_chars(self):
        gen = CODE_GEN_HANDLERS["set_linear_velocity"]
        code = gen({"prim_path": "/World/My Robot (v2)", "vel": [1.0, 2.0, 3.0]})
        _assert_compiles(code, "set_linear_velocity")
        assert "/World/My Robot (v2)" in code


class TestSetPhysicsSceneConfig:
    def test_full_config_emits_every_field(self):
        gen = CODE_GEN_HANDLERS["set_physics_scene_config"]
        code = gen({"config": {
            "solver_type": "TGS",
            "position_iterations": 8,
            "velocity_iterations": 1,
            "time_steps_per_second": 120,
            "enable_gpu_dynamics": True,
            "broadphase_type": "GPU",
            "gravity_direction": [0, 0, -1],
            "gravity_magnitude": 9.81,
        }})
        _assert_compiles(code, "set_physics_scene_config")
        assert "PhysxSceneAPI" in code
        assert "'TGS'" in code
        assert "'GPU'" in code
        assert "9.81" in code
        assert "120" in code
        assert "PositionIterationCountAttr" in code
        assert "VelocityIterationCountAttr" in code
        assert "EnableGPUDynamicsAttr" in code
        assert "BroadphaseTypeAttr" in code

    def test_empty_config_still_emits_valid_setup(self):
        gen = CODE_GEN_HANDLERS["set_physics_scene_config"]
        code = gen({"config": {}})
        _assert_compiles(code, "set_physics_scene_config")
        # No-op config should still ensure a PhysicsScene exists
        assert "UsdPhysics.Scene" in code

    def test_explicit_scene_path(self):
        gen = CODE_GEN_HANDLERS["set_physics_scene_config"]
        code = gen({"config": {"scene_path": "/MyPhysicsScene", "gravity_magnitude": 1.62}})
        _assert_compiles(code, "set_physics_scene_config")
        assert "'/MyPhysicsScene'" in code
        assert "1.62" in code


class TestApplyForce:
    def test_emits_force_and_torque(self):
        gen = CODE_GEN_HANDLERS["apply_force"]
        code = gen({
            "prim_path": "/World/Cube",
            "force": [10.0, 0.0, 0.0],
            "torque": [0.0, 0.0, 1.0],
            "position": [0.0, 0.0, 0.5],
        })
        _assert_compiles(code, "apply_force")
        assert "'/World/Cube'" in code
        assert "[10.0, 0.0, 0.0]" in code
        assert "[0.0, 0.0, 1.0]" in code
        assert "[0.0, 0.0, 0.5]" in code
        # Auto-applies RigidBodyAPI if missing
        assert "RigidBodyAPI.Apply" in code

    def test_position_optional(self):
        gen = CODE_GEN_HANDLERS["apply_force"]
        code = gen({"prim_path": "/World/Cube", "force": [5.0, 0.0, 0.0]})
        _assert_compiles(code, "apply_force")
        # No position → falls through to body CoM (None)
        assert "position = None" in code

    def test_force_and_torque_default_to_zero(self):
        gen = CODE_GEN_HANDLERS["apply_force"]
        code = gen({"prim_path": "/World/Cube"})
        _assert_compiles(code, "apply_force")
        assert "[0.0, 0.0, 0.0]" in code


# ---------------------------------------------------------------------------
# Round-trip dispatch through execute_tool_call
# ---------------------------------------------------------------------------

class TestExecuteToolCallTier2:
    """Ensure each Tier 2 tool round-trips cleanly through execute_tool_call."""

    @pytest.mark.asyncio
    async def test_data_tool_returns_data_envelope(self, capture_kit_patches):
        from service.isaac_assist_service.chat.tools.tool_executor import execute_tool_call
        result = await execute_tool_call("get_linear_velocity", {"prim_path": "/World/Cube"})
        assert result["type"] == "data"
        assert result.get("queued") is True

    @pytest.mark.asyncio
    async def test_code_gen_tool_returns_code_patch_envelope(self, monkeypatch):
        from service.isaac_assist_service.chat.tools import tool_executor as te
        async def fake_queue(code, description=""):
            return {"queued": True, "patch_id": "tier2_test"}
        monkeypatch.setattr(te.kit_tools, "queue_exec_patch", fake_queue)
        result = await te.execute_tool_call(
            "set_linear_velocity",
            {"prim_path": "/World/Cube", "vel": [1.0, 0.0, 0.0]},
        )
        assert result["type"] == "code_patch"
        assert "code" in result
        assert "Gf.Vec3f(1.0, 0.0, 0.0)" in result["code"]

    @pytest.mark.asyncio
    async def test_apply_force_round_trip(self, monkeypatch):
        from service.isaac_assist_service.chat.tools import tool_executor as te
        async def fake_queue(code, description=""):
            return {"queued": True}
        monkeypatch.setattr(te.kit_tools, "queue_exec_patch", fake_queue)
        result = await te.execute_tool_call(
            "apply_force",
            {"prim_path": "/World/Cube", "force": [1.0, 0.0, 0.0]},
        )
        assert result["type"] == "code_patch"
        assert "[1.0, 0.0, 0.0]" in result["code"]
