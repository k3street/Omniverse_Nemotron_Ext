"""
L0 tests for the Phase 7C Addendum: Teleoperation Quality tools.

Covers:
  - check_teleop_hardware       (DATA)
  - validate_teleop_demo        (DATA)
  - summarize_teleop_session    (DATA)
  - export_teleop_mapping       (CODE_GEN)
  - generate_teleop_watchdog_script (CODE_GEN)

All tests are L0 — no Kit, no XR device, no network.
"""
from __future__ import annotations

import builtins
import math
import sys

import pytest

pytestmark = pytest.mark.l0

from service.isaac_assist_service.chat.tools.tool_executor import (
    CODE_GEN_HANDLERS,
    DATA_HANDLERS,
    _handle_check_teleop_hardware,
    _handle_summarize_teleop_session,
    _handle_validate_teleop_demo,
    _open_hdf5_safely,
)
from service.isaac_assist_service.chat.tools.tool_schemas import ISAAC_SIM_TOOLS


# ---------------------------------------------------------------------------
# Helper: compile check
# ---------------------------------------------------------------------------

def _assert_valid_python(code: str, handler_name: str):
    try:
        compile(code, f"<generated:{handler_name}>", "exec")
    except SyntaxError as e:
        pytest.fail(f"{handler_name} generated invalid Python:\n{e}\n\nCode:\n{code}")


# ---------------------------------------------------------------------------
# Schema registration sanity
# ---------------------------------------------------------------------------

class TestSchemaRegistration:
    """Each new tool must be both in ISAAC_SIM_TOOLS and dispatch-registered."""

    NEW_TOOLS = [
        "check_teleop_hardware",
        "validate_teleop_demo",
        "summarize_teleop_session",
        "export_teleop_mapping",
        "generate_teleop_watchdog_script",
    ]

    def _all_tool_names(self):
        return {t["function"]["name"] for t in ISAAC_SIM_TOOLS}

    @pytest.mark.parametrize("name", NEW_TOOLS)
    def test_tool_in_schema(self, name):
        assert name in self._all_tool_names(), f"{name} not declared in ISAAC_SIM_TOOLS"

    @pytest.mark.parametrize("name", NEW_TOOLS)
    def test_tool_has_handler(self, name):
        assert (name in DATA_HANDLERS) or (name in CODE_GEN_HANDLERS), (
            f"{name} declared in schema but has no DATA / CODE_GEN handler"
        )


# ---------------------------------------------------------------------------
# check_teleop_hardware
# ---------------------------------------------------------------------------

class TestCheckTeleopHardware:
    @pytest.mark.asyncio
    async def test_quest_3_supported(self):
        result = await _handle_check_teleop_hardware({"device": "quest_3"})
        assert result["supported"] is True
        assert result["transport"] == "webxr"
        assert result["latency_budget_ms"] == 80
        assert any("Meta Browser" in lim for lim in result["known_limitations"])

    @pytest.mark.asyncio
    async def test_vision_pro_cloudxr_note(self):
        result = await _handle_check_teleop_hardware({"device": "vision_pro"})
        assert result["supported"] is True
        assert result["transport"] == "cloudxr"
        assert result["latency_budget_ms"] == 60
        assert any("CloudXR" in lim or "Safari" in lim for lim in result["known_limitations"])

    @pytest.mark.asyncio
    async def test_spacemouse_local_probe(self):
        result = await _handle_check_teleop_hardware({"device": "spacemouse"})
        assert result["supported"] is True
        assert result["transport"] == "usb-hid"
        assert "local_probe" in result
        # /dev/input may or may not exist depending on host; just verify the field shape
        assert "dev_input_exists" in result["local_probe"] or "error" in result["local_probe"]

    @pytest.mark.asyncio
    async def test_unknown_device_unsupported(self):
        result = await _handle_check_teleop_hardware({"device": "leap_motion"})
        assert result["supported"] is False
        assert "leap_motion" in result["reason"]


# ---------------------------------------------------------------------------
# _open_hdf5_safely + validate_teleop_demo
# ---------------------------------------------------------------------------

class TestOpenHdf5Safely:
    def test_missing_file_returns_reason(self, tmp_path):
        try:
            import h5py  # noqa: F401
        except ImportError:
            pytest.skip("h5py not installed — covered by test_h5py_missing")
        f, reason = _open_hdf5_safely(str(tmp_path / "nope.hdf5"))
        assert f is None
        assert "does not exist" in reason

    def test_h5py_missing(self, monkeypatch, tmp_path):
        # Force h5py import failure
        real_import = builtins.__import__

        def _blocking_import(name, *args, **kwargs):
            if name == "h5py":
                raise ImportError("h5py is not installed")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", _blocking_import)
        # Drop any cached h5py module
        sys.modules.pop("h5py", None)
        f, reason = _open_hdf5_safely(str(tmp_path / "any.hdf5"))
        assert f is None
        assert reason.startswith("h5py")


# ---------------------------------------------------------------------------
# validate_teleop_demo
# ---------------------------------------------------------------------------

@pytest.fixture
def _h5py_or_skip():
    try:
        import h5py  # noqa: F401
    except ImportError:
        pytest.skip("h5py not installed in this environment")
    return True


class TestValidateTeleopDemo:
    @pytest.mark.asyncio
    async def test_missing_h5py_marks_unavailable(self, monkeypatch, tmp_path):
        real_import = builtins.__import__

        def _blocking_import(name, *args, **kwargs):
            if name == "h5py":
                raise ImportError("h5py is not installed")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", _blocking_import)
        sys.modules.pop("h5py", None)
        result = await _handle_validate_teleop_demo({"hdf5_path": str(tmp_path / "x.hdf5")})
        assert result["available"] is False
        assert result["ready_for_training"] is False
        assert any("h5py" in i["problem"] for i in result["issues"])

    @pytest.mark.asyncio
    async def test_missing_file(self, _h5py_or_skip, tmp_path):
        result = await _handle_validate_teleop_demo({"hdf5_path": str(tmp_path / "missing.hdf5")})
        assert result["available"] is True
        assert result["ready_for_training"] is False
        assert any("does not exist" in i["problem"] for i in result["issues"])

    @pytest.mark.asyncio
    async def test_good_hdf5_passes(self, _h5py_or_skip, tmp_path):
        import h5py
        import numpy as np

        path = tmp_path / "good.hdf5"
        with h5py.File(path, "w") as f:
            data = f.create_group("data")
            for i in range(2):
                d = data.create_group(f"demo_{i}")
                d.create_dataset("actions", data=np.zeros((20, 7), dtype=np.float32))
                obs = d.create_group("obs")
                obs.create_dataset("joint_pos", data=np.zeros((20, 7), dtype=np.float32))

        result = await _handle_validate_teleop_demo({"hdf5_path": str(path)})
        assert result["available"] is True
        assert result["demos_checked"] == 2
        assert result["demos_ok"] == 2
        assert result["ready_for_training"] is True
        assert result["total_transitions"] == 40

    @pytest.mark.asyncio
    async def test_nan_action_flagged(self, _h5py_or_skip, tmp_path):
        import h5py
        import numpy as np

        path = tmp_path / "bad_nan.hdf5"
        with h5py.File(path, "w") as f:
            data = f.create_group("data")
            d0 = data.create_group("demo_0")
            d0.create_dataset("actions", data=np.zeros((10, 7), dtype=np.float32))
            obs0 = d0.create_group("obs")
            obs0.create_dataset("joint_pos", data=np.zeros((10, 7), dtype=np.float32))
            d1 = data.create_group("demo_1")
            bad = np.zeros((10, 7), dtype=np.float32)
            bad[3, 4] = float("nan")
            d1.create_dataset("actions", data=bad)
            obs1 = d1.create_group("obs")
            obs1.create_dataset("joint_pos", data=np.zeros((10, 7), dtype=np.float32))

        result = await _handle_validate_teleop_demo({"hdf5_path": str(path)})
        assert result["demos_checked"] == 2
        assert result["demos_ok"] == 1
        assert result["ready_for_training"] is False
        bad_demo = next(i for i in result["issues"] if i["demo"] == "demo_1")
        assert "NaN" in bad_demo["problem"]


# ---------------------------------------------------------------------------
# summarize_teleop_session
# ---------------------------------------------------------------------------

class TestSummarizeTeleopSession:
    @pytest.mark.asyncio
    async def test_missing_h5py(self, monkeypatch, tmp_path):
        real_import = builtins.__import__

        def _blocking_import(name, *args, **kwargs):
            if name == "h5py":
                raise ImportError("h5py is not installed")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", _blocking_import)
        sys.modules.pop("h5py", None)
        result = await _handle_summarize_teleop_session({"hdf5_path": str(tmp_path / "x.hdf5")})
        assert result["available"] is False
        assert "h5py" in result["reason"]

    @pytest.mark.asyncio
    async def test_two_demos_summary(self, _h5py_or_skip, tmp_path):
        import h5py
        import numpy as np

        path = tmp_path / "summary.hdf5"
        with h5py.File(path, "w") as f:
            data = f.create_group("data")
            for i in range(2):
                d = data.create_group(f"demo_{i}")
                # 60 steps × 7 joints — at 30 fps that's 2 seconds per demo
                a = np.zeros((60, 7), dtype=np.float32)
                a[:, 0] = np.linspace(0.0, 1.0, 60)
                d.create_dataset("actions", data=a)

        result = await _handle_summarize_teleop_session({"hdf5_path": str(path), "fps": 30})
        assert result["available"] is True
        assert result["demos"] == 2
        assert result["total_transitions"] == 120
        assert result["fps"] == 30
        assert math.isclose(result["total_duration_s"], 4.0, abs_tol=0.05)
        assert len(result["per_joint"]) == 7
        # Joint 0 should have a non-zero range and non-zero vel_mean
        j0 = result["per_joint"][0]
        assert j0["range_rad"] > 0.5
        assert j0["vel_mean"] > 0


# ---------------------------------------------------------------------------
# export_teleop_mapping (CODE_GEN)
# ---------------------------------------------------------------------------

class TestExportTeleopMapping:
    def test_compiles_minimum(self):
        code = CODE_GEN_HANDLERS["export_teleop_mapping"]({
            "session_name": "session_a",
            "device": "quest_3",
            "joint_map": [
                {"name": "panda_joint1", "source": "right_thumb", "gain": 1.0, "limit_rad": [-2.8, 2.8]},
            ],
        })
        _assert_valid_python(code, "export_teleop_mapping")
        assert "session_a" in code
        assert "quest_3" in code
        assert "teleop_mappings" in code
        assert "Wrote mapping to" in code

    def test_path_injection_safe(self):
        # session_name with a quote must not break the generated source
        code = CODE_GEN_HANDLERS["export_teleop_mapping"]({
            "session_name": "weird'name\"with quotes",
            "device": "spacemouse",
            "joint_map": [],
        })
        _assert_valid_python(code, "export_teleop_mapping (injection)")

    def test_includes_gains(self):
        code = CODE_GEN_HANDLERS["export_teleop_mapping"]({
            "session_name": "g1",
            "device": "keyboard",
            "joint_map": [],
            "gains": {"position": 999, "velocity": 88},
        })
        _assert_valid_python(code, "export_teleop_mapping (gains)")
        assert "999" in code
        assert "88" in code


# ---------------------------------------------------------------------------
# generate_teleop_watchdog_script (CODE_GEN)
# ---------------------------------------------------------------------------

class TestGenerateTeleopWatchdogScript:
    def test_compiles_with_defaults(self):
        code = CODE_GEN_HANDLERS["generate_teleop_watchdog_script"]({
            "robot_path": "/World/Franka",
        })
        _assert_valid_python(code, "generate_teleop_watchdog_script")
        assert "/World/Franka" in code
        assert "TIMEOUT_MS = 500" in code
        assert "HOLD_TIME_MS = 2000" in code
        assert "/ws/teleop" in code

    def test_custom_timings(self):
        code = CODE_GEN_HANDLERS["generate_teleop_watchdog_script"]({
            "robot_path": "/World/UR10",
            "timeout_ms": 250,
            "hold_time_ms": 1000,
            "socket_path": "/ws/teleop_v2",
        })
        _assert_valid_python(code, "generate_teleop_watchdog_script (custom)")
        assert "TIMEOUT_MS = 250" in code
        assert "HOLD_TIME_MS = 1000" in code
        assert "/ws/teleop_v2" in code
        assert "/World/UR10" in code

    def test_zeroes_velocity_targets(self):
        code = CODE_GEN_HANDLERS["generate_teleop_watchdog_script"]({
            "robot_path": "/World/Robot",
        })
        _assert_valid_python(code, "generate_teleop_watchdog_script (zero)")
        # Sanity: the script must reference UsdPhysics drive zeroing
        assert "DriveAPI" in code
        assert "TargetVelocityAttr" in code or "GetTargetVelocityAttr" in code
        assert "watchdog" in code
