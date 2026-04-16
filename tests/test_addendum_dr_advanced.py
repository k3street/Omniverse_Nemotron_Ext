"""
L0 tests for the Domain Randomization Advanced Addendum.

Covers:
  - configure_correlated_dr     (CODE_GEN)
  - suggest_dr_ranges           (DATA)
  - apply_dr_preset             (DATA)
  - add_latency_randomization   (CODE_GEN)
  - preview_dr                  (CODE_GEN)

All tests are L0 — no Kit, no GPU, no network.
"""
from __future__ import annotations

import json
import math

import pytest

pytestmark = pytest.mark.l0

from service.isaac_assist_service.chat.tools.tool_executor import (
    CODE_GEN_HANDLERS,
    DATA_HANDLERS,
    _DR_PRESETS,
    _DR_TASK_DEFAULTS,
    _gen_add_latency_randomization,
    _gen_configure_correlated_dr,
    _gen_preview_dr,
    _handle_apply_dr_preset,
    _handle_suggest_dr_ranges,
)
from service.isaac_assist_service.chat.tools.tool_schemas import ISAAC_SIM_TOOLS


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _assert_valid_python(code: str, handler_name: str) -> None:
    try:
        compile(code, f"<generated:{handler_name}>", "exec")
    except SyntaxError as exc:
        pytest.fail(f"{handler_name} generated invalid Python:\n{exc}\n\nCode:\n{code}")


NEW_TOOLS = [
    "configure_correlated_dr",
    "suggest_dr_ranges",
    "apply_dr_preset",
    "add_latency_randomization",
    "preview_dr",
]


# ---------------------------------------------------------------------------
# Schema registration
# ---------------------------------------------------------------------------

class TestSchemaRegistration:
    """Each new tool must be both in ISAAC_SIM_TOOLS and dispatch-registered."""

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

    @pytest.mark.parametrize("name", NEW_TOOLS)
    def test_tool_has_description(self, name):
        tool = next(t for t in ISAAC_SIM_TOOLS if t["function"]["name"] == name)
        assert len(tool["function"]["description"]) >= 20

    def test_configure_correlated_dr_required_fields(self):
        tool = next(t for t in ISAAC_SIM_TOOLS if t["function"]["name"] == "configure_correlated_dr")
        required = tool["function"]["parameters"]["required"]
        assert "parameter_groups" in required

    def test_suggest_dr_ranges_required_fields(self):
        tool = next(t for t in ISAAC_SIM_TOOLS if t["function"]["name"] == "suggest_dr_ranges")
        required = tool["function"]["parameters"]["required"]
        assert "task_type" in required

    def test_apply_dr_preset_enum(self):
        tool = next(t for t in ISAAC_SIM_TOOLS if t["function"]["name"] == "apply_dr_preset")
        enum = tool["function"]["parameters"]["properties"]["preset"]["enum"]
        # Every enum entry must be a known preset key
        for name in enum:
            assert name in _DR_PRESETS, f"preset enum '{name}' missing from _DR_PRESETS"
        for name in _DR_PRESETS:
            assert name in enum, f"_DR_PRESETS '{name}' missing from schema enum"


# ---------------------------------------------------------------------------
# DR.1 — configure_correlated_dr (CODE_GEN)
# ---------------------------------------------------------------------------

class TestConfigureCorrelatedDR:

    def test_basic_copula_compiles(self):
        code = _gen_configure_correlated_dr({
            "parameter_groups": [
                {
                    "params": ["mass", "static_friction"],
                    "ranges": {"mass": [0.5, 2.0], "static_friction": [0.3, 0.8]},
                    "correlation": 0.6,
                    "method": "copula",
                }
            ],
            "target_path": "/World/Robot",
            "seed": 42,
        })
        _assert_valid_python(code, "configure_correlated_dr")
        assert "import numpy" in code
        assert "/World/Robot" in code
        assert "sample_correlated_dr" in code
        assert "_sample_copula" in code

    def test_linear_method_compiles(self):
        code = _gen_configure_correlated_dr({
            "parameter_groups": [
                {
                    "params": ["damping", "temperature"],
                    "ranges": {"damping": [0.5, 1.5], "temperature": [273.0, 320.0]},
                    "correlation": 0.4,
                    "method": "linear",
                }
            ],
            "seed": 7,
        })
        _assert_valid_python(code, "configure_correlated_dr")
        assert "linear" in code
        assert "_sample_linear" in code

    def test_unknown_method_falls_back_to_copula(self):
        code = _gen_configure_correlated_dr({
            "parameter_groups": [
                {"params": ["a", "b"], "ranges": {"a": [0, 1], "b": [0, 1]},
                 "correlation": 0.5, "method": "bogus"}
            ],
        })
        _assert_valid_python(code, "configure_correlated_dr")
        # Embedded JSON must contain "copula" (the fallback), not "bogus".
        assert '"method": "copula"' in code
        assert "bogus" not in code

    def test_default_seed_and_target(self):
        code = _gen_configure_correlated_dr({"parameter_groups": []})
        _assert_valid_python(code, "configure_correlated_dr")
        assert "/World" in code
        assert "default_rng(0)" in code

    def test_handler_registered_under_code_gen(self):
        assert CODE_GEN_HANDLERS["configure_correlated_dr"] is _gen_configure_correlated_dr

    def test_generated_groups_serialize_as_json(self):
        # Ensure the JSON literal embedded in the script is parseable.
        code = _gen_configure_correlated_dr({
            "parameter_groups": [
                {"params": ["mass"], "ranges": {"mass": [0.1, 2.0]}, "correlation": 0.0}
            ],
        })
        # Locate the assignment "_GROUPS = [..."
        start = code.index("_GROUPS = ") + len("_GROUPS = ")
        end = code.index("\n_TARGET_PATH")
        groups_json = code[start:end].strip()
        parsed = json.loads(groups_json)
        assert parsed[0]["params"] == ["mass"]
        assert parsed[0]["ranges"]["mass"] == [0.1, 2.0]


# ---------------------------------------------------------------------------
# DR.2 — suggest_dr_ranges (DATA)
# ---------------------------------------------------------------------------

class TestSuggestDRRanges:

    @pytest.mark.asyncio
    async def test_pick_and_place_franka(self):
        result = await _handle_suggest_dr_ranges({
            "task_type": "pick_and_place",
            "robot": "Franka Panda",
        })
        assert result["task_matched"] == "pick_and_place"
        assert result["robot_matched"] == "franka"
        ranges = result["suggested_ranges"]
        assert "object_mass_kg" in ranges
        assert ranges["gripper_friction"] == [0.5, 1.0]
        assert "gravity_m_s2" in ranges

    @pytest.mark.asyncio
    async def test_locomotion_anymal(self):
        result = await _handle_suggest_dr_ranges({
            "task_type": "locomotion",
            "robot": "Anymal-C",
        })
        assert result["task_matched"] == "locomotion"
        assert result["robot_matched"] == "anymal"
        assert "ground_friction" in result["suggested_ranges"]

    @pytest.mark.asyncio
    async def test_unknown_task_falls_back(self):
        result = await _handle_suggest_dr_ranges({"task_type": "weird-novel-task"})
        # Falls back to pick_and_place defaults.
        assert result["task_matched"] == "pick_and_place"
        assert result["robot_matched"] is None
        assert "object_mass_kg" in result["suggested_ranges"]

    @pytest.mark.asyncio
    async def test_missing_task_type_errors(self):
        result = await _handle_suggest_dr_ranges({})
        assert "error" in result

    @pytest.mark.asyncio
    async def test_real_data_path_missing(self):
        result = await _handle_suggest_dr_ranges({
            "task_type": "pick_and_place",
            "real_data_path": "/nonexistent/path/sensors.csv",
        })
        assert result["real_data_used"] is False
        assert any("not found" in n for n in result["notes"])

    @pytest.mark.asyncio
    async def test_real_data_csv_refines_ranges(self, tmp_path):
        csv_path = tmp_path / "sensors.csv"
        csv_path.write_text(
            "mass,friction\n"
            "0.50,0.42\n"
            "0.80,0.55\n"
            "1.20,0.61\n"
            "1.50,0.70\n",
            encoding="utf-8",
        )
        result = await _handle_suggest_dr_ranges({
            "task_type": "pick_and_place",
            "real_data_path": str(csv_path),
        })
        assert result["real_data_used"] is True
        assert result["suggested_ranges"]["empirical_mass"] == [0.5, 1.5]
        assert result["suggested_ranges"]["empirical_friction"] == [0.42, 0.70]
        assert any("refined" in n for n in result["notes"])

    @pytest.mark.asyncio
    async def test_real_data_json_refines_ranges(self, tmp_path):
        json_path = tmp_path / "sensors.json"
        json_path.write_text(json.dumps([
            {"latency_ms": 12.0},
            {"latency_ms": 28.0},
            {"latency_ms": 45.0},
        ]))
        result = await _handle_suggest_dr_ranges({
            "task_type": "pick_and_place",
            "real_data_path": str(json_path),
        })
        assert result["real_data_used"] is True
        assert result["suggested_ranges"]["empirical_latency_ms"] == [12.0, 45.0]


# ---------------------------------------------------------------------------
# DR.3 — apply_dr_preset (DATA)
# ---------------------------------------------------------------------------

class TestApplyDRPreset:

    @pytest.mark.asyncio
    @pytest.mark.parametrize("preset", list(_DR_PRESETS.keys()))
    async def test_each_preset_loads(self, preset):
        result = await _handle_apply_dr_preset({"preset": preset})
        assert "error" not in result
        assert result["preset"] == preset
        assert "description" in result
        assert isinstance(result["parameters"], dict)
        assert "description" not in result["parameters"]

    @pytest.mark.asyncio
    async def test_indoor_industrial_has_lighting(self):
        result = await _handle_apply_dr_preset({"preset": "indoor_industrial"})
        assert result["parameters"]["lighting_lux"] == [300, 2000]

    @pytest.mark.asyncio
    async def test_aggressive_sim2real_has_latency(self):
        result = await _handle_apply_dr_preset({"preset": "aggressive_sim2real"})
        assert "action_latency_ms" in result["parameters"]
        assert result["parameters"]["action_latency_ms"] == [10, 80]

    @pytest.mark.asyncio
    async def test_unknown_preset(self):
        result = await _handle_apply_dr_preset({"preset": "does_not_exist"})
        assert "error" in result
        assert "available" in result
        assert "indoor_industrial" in result["available"]

    @pytest.mark.asyncio
    async def test_missing_preset_arg(self):
        result = await _handle_apply_dr_preset({})
        assert "error" in result


# ---------------------------------------------------------------------------
# DR.4 — add_latency_randomization (CODE_GEN)
# ---------------------------------------------------------------------------

class TestAddLatencyRandomization:

    def test_default_args_compile(self):
        code = _gen_add_latency_randomization({})
        _assert_valid_python(code, "add_latency_randomization")
        assert "ActionLatencyEvent" in code
        assert "_MIN_MS = 10.0" in code
        assert "_MAX_MS = 50.0" in code

    def test_custom_range(self):
        code = _gen_add_latency_randomization({
            "min_ms": 20, "max_ms": 80, "physics_dt": 0.01,
        })
        _assert_valid_python(code, "add_latency_randomization")
        assert "_MIN_MS = 20.0" in code
        assert "_MAX_MS = 80.0" in code
        assert "_PHYSICS_DT = 0.01" in code

    def test_swapped_min_max_corrected(self):
        code = _gen_add_latency_randomization({"min_ms": 100, "max_ms": 50})
        _assert_valid_python(code, "add_latency_randomization")
        # After swap, min=50, max=100
        assert "_MIN_MS = 50.0" in code
        assert "_MAX_MS = 100.0" in code

    def test_buffer_size_auto_at_least_max_steps(self):
        # max_ms=50, dt=0.005s => 5 ms/step => max_steps=10; buffer = max_steps + 1 = 11
        code = _gen_add_latency_randomization({
            "min_ms": 10, "max_ms": 50, "physics_dt": 0.005,
        })
        assert "_BUFFER_SIZE = 11" in code

    def test_user_buffer_size_floored(self):
        # User asks for buffer=2 but max_ms=50/dt=0.005 needs at least 11.
        code = _gen_add_latency_randomization({
            "min_ms": 10, "max_ms": 50, "physics_dt": 0.005, "buffer_size": 2,
        })
        assert "_BUFFER_SIZE = 11" in code

    def test_user_buffer_size_respected_when_large_enough(self):
        code = _gen_add_latency_randomization({
            "min_ms": 10, "max_ms": 50, "physics_dt": 0.005, "buffer_size": 32,
        })
        assert "_BUFFER_SIZE = 32" in code

    def test_handler_registered_under_code_gen(self):
        assert CODE_GEN_HANDLERS["add_latency_randomization"] is _gen_add_latency_randomization


# ---------------------------------------------------------------------------
# DR.5 — preview_dr (CODE_GEN)
# ---------------------------------------------------------------------------

class TestPreviewDR:

    def test_default_args_compile(self):
        code = _gen_preview_dr({})
        _assert_valid_python(code, "preview_dr")
        assert "_NUM_SAMPLES = 9" in code
        assert "workspace/dr_previews" in code
        assert "(512, 512)" in code

    def test_custom_resolution(self):
        code = _gen_preview_dr({"num_samples": 4, "resolution": [256, 256]})
        _assert_valid_python(code, "preview_dr")
        assert "_NUM_SAMPLES = 4" in code
        assert "(256, 256)" in code

    def test_zero_samples_clamped_to_one(self):
        code = _gen_preview_dr({"num_samples": 0})
        _assert_valid_python(code, "preview_dr")
        assert "_NUM_SAMPLES = 1" in code

    def test_invalid_resolution_uses_default(self):
        code = _gen_preview_dr({"resolution": [800]})  # malformed
        _assert_valid_python(code, "preview_dr")
        assert "(512, 512)" in code

    def test_custom_output_dir(self):
        code = _gen_preview_dr({"output_dir": "/tmp/dr_check"})
        _assert_valid_python(code, "preview_dr")
        assert "/tmp/dr_check" in code

    def test_handler_registered_under_code_gen(self):
        assert CODE_GEN_HANDLERS["preview_dr"] is _gen_preview_dr
