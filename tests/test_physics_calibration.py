"""
L0 tests for the Physics Parameter Calibration toolset
(spec: docs/specs/new_physics_calibration.md).

Covers:
  - calibrate_physics  (Bayesian-optimization launcher)
  - quick_calibrate    (fast preset calibration)
  - validate_calibration (BO objective / RMSE report)
  - train_actuator_net (LSTM neural-actuator trainer launcher)

All four are DATA handlers — they validate inputs, write a script, and
return a launch command. No GPU, IsaacLab, or Ray is touched at import time.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.l0

from service.isaac_assist_service.chat.tools.tool_executor import (
    DATA_HANDLERS,
    _DEFAULT_CALIBRATE_PARAMS,
    _QUICK_CALIBRATE_PARAMS,
    _VALID_CALIBRATE_PARAMS,
    _generate_actuator_net_script,
    _generate_calibration_script,
    _handle_calibrate_physics,
    _handle_quick_calibrate,
    _handle_train_actuator_net,
    _handle_validate_calibration,
    _per_joint_rmse,
    _safe_robot_name,
    _suggested_dr_ranges,
)
from service.isaac_assist_service.chat.tools.tool_schemas import ISAAC_SIM_TOOLS


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TOOL_NAMES = {t["function"]["name"] for t in ISAAC_SIM_TOOLS}


def _fake_h5(tmp_path: Path, name: str = "real.h5") -> Path:
    """Touch an empty file with .h5 extension — handlers only check existence + suffix."""
    p = tmp_path / name
    p.write_bytes(b"")  # we mock h5py reads where actual content matters
    return p


# ---------------------------------------------------------------------------
# Schema-side smoke tests
# ---------------------------------------------------------------------------

class TestSchemasRegistered:
    """Each spec'd tool must appear in ISAAC_SIM_TOOLS and DATA_HANDLERS."""

    @pytest.mark.parametrize(
        "name",
        ["calibrate_physics", "quick_calibrate", "validate_calibration", "train_actuator_net"],
    )
    def test_schema_present(self, name):
        assert name in _TOOL_NAMES, f"Tool '{name}' missing from ISAAC_SIM_TOOLS"

    @pytest.mark.parametrize(
        "name",
        ["calibrate_physics", "quick_calibrate", "validate_calibration", "train_actuator_net"],
    )
    def test_handler_registered(self, name):
        assert name in DATA_HANDLERS, f"Tool '{name}' missing DATA_HANDLERS entry"
        assert callable(DATA_HANDLERS[name]), f"{name} handler is not callable"

    @pytest.mark.parametrize(
        "name,required",
        [
            ("calibrate_physics", {"real_data_path", "articulation_path"}),
            ("quick_calibrate", {"real_data_path", "articulation_path"}),
            ("validate_calibration", {"calibrated_params", "test_data_path"}),
            ("train_actuator_net", {"real_data_path", "articulation_path"}),
        ],
    )
    def test_required_fields(self, name, required):
        tool = next(t for t in ISAAC_SIM_TOOLS if t["function"]["name"] == name)
        params = tool["function"]["parameters"]
        assert set(params.get("required", [])) == required


# ---------------------------------------------------------------------------
# Pure utility helpers
# ---------------------------------------------------------------------------

class TestUtilityHelpers:
    def test_safe_robot_name_strips_path(self):
        assert _safe_robot_name("/World/Franka") == "franka"
        assert _safe_robot_name("/World/Robot/UR10e") == "ur10e"

    def test_safe_robot_name_sanitizes(self):
        assert _safe_robot_name("/World/Robot With Spaces!") == "robot_with_spaces_"

    def test_safe_robot_name_fallback(self):
        assert _safe_robot_name("/") == "robot"

    def test_suggested_dr_ranges_filters_unknown(self):
        out = _suggested_dr_ranges(["friction", "damping", "made_up"])
        assert "friction" in out
        assert "damping" in out
        assert "made_up" not in out

    def test_default_param_set_subset_of_valid(self):
        for p in _DEFAULT_CALIBRATE_PARAMS:
            assert p in _VALID_CALIBRATE_PARAMS

    def test_quick_param_set_subset_of_valid(self):
        for p in _QUICK_CALIBRATE_PARAMS:
            assert p in _VALID_CALIBRATE_PARAMS


# ---------------------------------------------------------------------------
# BO objective / RMSE math (L0 — spec test "BO objective function")
# ---------------------------------------------------------------------------

class TestPerJointRMSE:
    def test_zero_when_identical(self):
        traj = [[1.0, 2.0, 3.0], [1.5, 2.5, 3.5]]
        rmses = _per_joint_rmse(traj, traj)
        assert rmses == [0.0, 0.0, 0.0]

    def test_known_rmse(self):
        sim = [[0.0, 0.0], [0.0, 0.0]]
        real = [[1.0, 2.0], [1.0, 2.0]]
        rmses = _per_joint_rmse(sim, real)
        assert rmses[0] == pytest.approx(1.0)
        assert rmses[1] == pytest.approx(2.0)

    def test_empty_input(self):
        assert _per_joint_rmse([], []) == []
        assert _per_joint_rmse([[]], [[]]) == []

    def test_handles_shape_mismatch(self):
        sim = [[0.0, 0.0, 0.0]]
        real = [[1.0, 2.0]]  # fewer joints
        rmses = _per_joint_rmse(sim, real)
        # truncated to common min n_joints = 2
        assert len(rmses) == 2


# ---------------------------------------------------------------------------
# Script generators — verify structure (L0 — spec test "parameter bounds")
# ---------------------------------------------------------------------------

class TestCalibrationScript:
    def test_script_includes_all_requested_params(self):
        script = _generate_calibration_script(
            real_data_path="/data/real.h5",
            articulation_path="/World/Franka",
            parameters=["friction", "damping", "armature", "viscous_friction", "masses"],
            num_samples=50,
            num_workers=2,
            output_dir="/tmp/cal",
        )
        # Every param branch present in search-space construction
        assert 'tune.uniform(0.1, 2.0)' in script  # friction
        assert 'tune.uniform(0.01, 1.0)' in script  # damping
        assert 'tune.uniform(0.0, 0.5)' in script  # armature / viscous
        assert 'tune.uniform(0.8, 1.2)' in script  # masses scale
        # Per-spec mass scale stays within physically sensible bounds (no negative scale)
        assert 'tune.uniform(-' not in script

    def test_script_is_valid_python(self):
        script = _generate_calibration_script(
            real_data_path="/data/real.h5",
            articulation_path="/World/Franka",
            parameters=["friction"],
            num_samples=10,
            num_workers=1,
            output_dir="/tmp/cal",
        )
        compile(script, "<calibrate>", "exec")

    def test_script_uses_optuna_search(self):
        script = _generate_calibration_script(
            real_data_path="/data/real.h5",
            articulation_path="/World/Franka",
            parameters=["friction"],
            num_samples=10,
            num_workers=1,
            output_dir="/tmp/cal",
        )
        assert "OptunaSearch" in script
        assert "ray.tune" in script or "from ray import tune" in script


class TestActuatorNetScript:
    def test_script_is_valid_python(self):
        script = _generate_actuator_net_script(
            real_data_path="/data/real.h5",
            articulation_path="/World/Franka",
            hidden_dim=32,
            num_layers=2,
            num_epochs=100,
            output_dir="/tmp/an",
        )
        compile(script, "<actuator>", "exec")

    def test_script_uses_lstm(self):
        script = _generate_actuator_net_script(
            real_data_path="/data/real.h5",
            articulation_path="/World/Franka",
            hidden_dim=32,
            num_layers=2,
            num_epochs=100,
            output_dir="/tmp/an",
        )
        assert "nn.LSTM" in script
        assert "actuator_net.pt" in script


# ---------------------------------------------------------------------------
# calibrate_physics handler
# ---------------------------------------------------------------------------

class TestCalibratePhysics:
    @pytest.mark.asyncio
    async def test_missing_real_data(self, tmp_path):
        result = await _handle_calibrate_physics({
            "real_data_path": "",
            "articulation_path": "/World/Franka",
        })
        assert "error" in result

    @pytest.mark.asyncio
    async def test_real_data_not_found(self):
        result = await _handle_calibrate_physics({
            "real_data_path": "/nope/nowhere.h5",
            "articulation_path": "/World/Franka",
        })
        assert "error" in result
        assert "not found" in result["error"]

    @pytest.mark.asyncio
    async def test_wrong_extension_rejected(self, tmp_path):
        bad = tmp_path / "real.csv"
        bad.write_text("")
        result = await _handle_calibrate_physics({
            "real_data_path": str(bad),
            "articulation_path": "/World/Franka",
        })
        assert "error" in result
        assert "HDF5" in result["error"]

    @pytest.mark.asyncio
    async def test_missing_articulation_path(self, tmp_path):
        h5 = _fake_h5(tmp_path)
        result = await _handle_calibrate_physics({
            "real_data_path": str(h5),
            "articulation_path": "",
        })
        assert "error" in result

    @pytest.mark.asyncio
    async def test_invalid_param_filtered(self, tmp_path):
        h5 = _fake_h5(tmp_path)
        result = await _handle_calibrate_physics({
            "real_data_path": str(h5),
            "articulation_path": "/World/Franka",
            "parameters_to_calibrate": ["bogus"],
        })
        assert "error" in result

    @pytest.mark.asyncio
    async def test_negative_num_samples_rejected(self, tmp_path):
        h5 = _fake_h5(tmp_path)
        result = await _handle_calibrate_physics({
            "real_data_path": str(h5),
            "articulation_path": "/World/Franka",
            "num_samples": 0,
        })
        assert "error" in result

    @pytest.mark.asyncio
    async def test_happy_path_writes_script(self, tmp_path):
        h5 = _fake_h5(tmp_path)
        out_dir = tmp_path / "calibration_out"
        result = await _handle_calibrate_physics({
            "real_data_path": str(h5),
            "articulation_path": "/World/Franka",
            "num_samples": 25,
            "num_workers": 2,
            "output_dir": str(out_dir),
        })
        assert "error" not in result
        assert result["type"] == "calibration_job"
        assert result["always_require_approval"] is True
        assert result["robot"] == "franka"
        assert result["num_samples"] == 25
        # Script written + launch command points at it
        script_path = Path(result["script_path"])
        assert script_path.exists()
        assert result["launch_command"] == f"python {script_path}"
        # DR range hints accompany the calibrated params
        assert set(result["suggested_dr_ranges"]) == set(result["parameters_to_calibrate"])

    @pytest.mark.asyncio
    async def test_default_parameters(self, tmp_path):
        h5 = _fake_h5(tmp_path)
        result = await _handle_calibrate_physics({
            "real_data_path": str(h5),
            "articulation_path": "/World/UR10",
            "output_dir": str(tmp_path / "out"),
        })
        assert result["parameters_to_calibrate"] == _DEFAULT_CALIBRATE_PARAMS


# ---------------------------------------------------------------------------
# quick_calibrate handler
# ---------------------------------------------------------------------------

class TestQuickCalibrate:
    @pytest.mark.asyncio
    async def test_happy_path(self, tmp_path):
        h5 = _fake_h5(tmp_path)
        out_dir = tmp_path / "qc"
        result = await _handle_quick_calibrate({
            "real_data_path": str(h5),
            "articulation_path": "/World/Franka",
            "output_dir": str(out_dir),
        })
        assert "error" not in result
        assert result["mode"] == "quick"
        assert result["estimated_minutes"] == 5
        # Highest-impact params per spec
        assert "armature" in result["parameters_to_calibrate"]
        assert "friction" in result["parameters_to_calibrate"]
        # Script written to disk
        assert Path(result["script_path"]).exists()

    @pytest.mark.asyncio
    async def test_include_masses_false(self, tmp_path):
        h5 = _fake_h5(tmp_path)
        result = await _handle_quick_calibrate({
            "real_data_path": str(h5),
            "articulation_path": "/World/Franka",
            "include_masses": False,
            "output_dir": str(tmp_path / "qc"),
        })
        assert "masses" not in result["parameters_to_calibrate"]

    @pytest.mark.asyncio
    async def test_validates_inputs(self):
        result = await _handle_quick_calibrate({
            "real_data_path": "/nope.h5",
            "articulation_path": "/World/Franka",
        })
        assert "error" in result


# ---------------------------------------------------------------------------
# validate_calibration handler
# ---------------------------------------------------------------------------

class TestValidateCalibration:
    @pytest.mark.asyncio
    async def test_requires_calibrated_params(self, tmp_path):
        h5 = _fake_h5(tmp_path)
        result = await _handle_validate_calibration({
            "calibrated_params": {},
            "test_data_path": str(h5),
        })
        assert "error" in result

    @pytest.mark.asyncio
    async def test_requires_test_data(self):
        result = await _handle_validate_calibration({
            "calibrated_params": {"friction": 0.5},
            "test_data_path": "/not/here.h5",
        })
        assert "error" in result

    @pytest.mark.asyncio
    async def test_needs_replay_when_no_sim_data(self, tmp_path):
        """If the HDF5 lacks sim_joint_positions, handler reports needs_replay=True."""
        try:
            import h5py
            import numpy as np
        except ImportError:
            pytest.skip("h5py / numpy not installed in test env")

        h5 = tmp_path / "real_only.h5"
        with h5py.File(h5, "w") as f:
            f.create_dataset("joint_positions", data=np.zeros((10, 3)))
            # Note: no sim_joint_positions
        result = await _handle_validate_calibration({
            "calibrated_params": {"friction": 0.5, "damping": 0.1},
            "test_data_path": str(h5),
        })
        assert "error" not in result
        assert result["needs_replay"] is True
        assert result["trajectory_error"] is None
        assert sorted(result["calibrated_param_keys"]) == ["damping", "friction"]

    @pytest.mark.asyncio
    async def test_with_real_h5_data(self, tmp_path):
        """End-to-end: write a real HDF5 with sim+real, expect RMSE computed."""
        try:
            import h5py
            import numpy as np
        except ImportError:
            pytest.skip("h5py / numpy not installed in test env")

        h5_path = tmp_path / "test_traj.h5"
        with h5py.File(h5_path, "w") as f:
            f.create_dataset("joint_positions", data=np.zeros((10, 3)))
            f.create_dataset("sim_joint_positions", data=np.ones((10, 3)))
        result = await _handle_validate_calibration({
            "calibrated_params": {"friction": [0.5, 0.4, 0.3]},
            "test_data_path": str(h5_path),
            "baseline_error": 5.0,
        })
        assert "error" not in result
        assert result["needs_replay"] is False
        # All-zeros vs. all-ones → per-joint RMSE = 1.0 → overall RMSE = 1.0
        assert result["trajectory_error"] == pytest.approx(1.0)
        assert result["per_joint_rmse"] == pytest.approx([1.0, 1.0, 1.0])
        # baseline 5.0 → calibrated 1.0 → 80 % improvement
        assert result["improvement_pct"] == pytest.approx(80.0)


# ---------------------------------------------------------------------------
# train_actuator_net handler
# ---------------------------------------------------------------------------

class TestTrainActuatorNet:
    @pytest.mark.asyncio
    async def test_validates_real_data(self):
        result = await _handle_train_actuator_net({
            "real_data_path": "",
            "articulation_path": "/World/Franka",
        })
        assert "error" in result

    @pytest.mark.asyncio
    async def test_validates_hyperparams(self, tmp_path):
        h5 = _fake_h5(tmp_path)
        result = await _handle_train_actuator_net({
            "real_data_path": str(h5),
            "articulation_path": "/World/Franka",
            "hidden_dim": 0,
        })
        assert "error" in result

    @pytest.mark.asyncio
    async def test_happy_path(self, tmp_path):
        h5 = _fake_h5(tmp_path)
        out_dir = tmp_path / "an_out"
        result = await _handle_train_actuator_net({
            "real_data_path": str(h5),
            "articulation_path": "/World/Franka",
            "hidden_dim": 64,
            "num_layers": 3,
            "num_epochs": 50,
            "output_dir": str(out_dir),
        })
        assert "error" not in result
        assert result["type"] == "actuator_net_job"
        assert result["always_require_approval"] is True
        assert result["hidden_dim"] == 64
        assert result["num_layers"] == 3
        # Script written + valid python
        script_path = Path(result["script_path"])
        assert script_path.exists()
        compile(script_path.read_text(), str(script_path), "exec")
        # Checkpoint path computed
        assert result["checkpoint_path"].endswith("actuator_net.pt")
