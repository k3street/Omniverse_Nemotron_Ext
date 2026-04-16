"""
L0 tests for the Phase 7B Addendum: Advanced SDG tools.

Covers:
  - scatter_on_surface             (CODE_GEN)
  - configure_differential_sdg     (CODE_GEN)
  - configure_coco_yolo_writer     (CODE_GEN)
  - enforce_class_balance          (CODE_GEN)
  - benchmark_sdg                  (DATA)

All tests are L0 — no Kit, no GPU, no network.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.l0

from service.isaac_assist_service.chat.tools.tool_executor import (
    CODE_GEN_HANDLERS,
    DATA_HANDLERS,
    _handle_benchmark_sdg,
    _gen_scatter_on_surface,
    _gen_configure_differential_sdg,
    _gen_configure_coco_yolo_writer,
    _gen_enforce_class_balance,
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

NEW_TOOLS = [
    "scatter_on_surface",
    "configure_differential_sdg",
    "configure_coco_yolo_writer",
    "benchmark_sdg",
    "enforce_class_balance",
]


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
        assert len(tool["function"]["description"]) >= 20, (
            f"{name} description too short to be useful"
        )

    def test_scatter_on_surface_required_fields(self):
        tool = next(t for t in ISAAC_SIM_TOOLS if t["function"]["name"] == "scatter_on_surface")
        required = tool["function"]["parameters"]["required"]
        assert "source_prims" in required
        assert "target_mesh" in required
        assert "count" in required

    def test_configure_differential_sdg_required_fields(self):
        tool = next(t for t in ISAAC_SIM_TOOLS if t["function"]["name"] == "configure_differential_sdg")
        required = tool["function"]["parameters"]["required"]
        assert "static_elements" in required
        assert "dynamic_elements" in required

    def test_configure_coco_yolo_writer_format_enum(self):
        tool = next(t for t in ISAAC_SIM_TOOLS if t["function"]["name"] == "configure_coco_yolo_writer")
        fmt_prop = tool["function"]["parameters"]["properties"]["format"]
        assert "enum" in fmt_prop
        assert set(fmt_prop["enum"]) == {"coco", "yolo"}


# ---------------------------------------------------------------------------
# scatter_on_surface
# ---------------------------------------------------------------------------

class TestScatterOnSurface:
    def test_minimal_args_valid_python(self):
        code = _gen_scatter_on_surface({
            "source_prims": ["/World/Apple"],
            "target_mesh": "/World/Tree",
            "count": 10,
        })
        _assert_valid_python(code, "scatter_on_surface")
        assert "trimesh" in code  # filesystem mesh fallback path is included
        assert "_sample_surface_points" in code
        assert "_poisson_filter" in code

    def test_full_args_with_spacing_and_normal_align(self):
        code = _gen_scatter_on_surface({
            "source_prims": ["/World/A", "/World/B"],
            "target_mesh": "/path/to/branch.usd",
            "count": 25,
            "spacing": 0.05,
            "normal_align": True,
            "penetration_check": True,
            "seed": 42,
        })
        _assert_valid_python(code, "scatter_on_surface")
        assert "random.seed(42)" in code
        assert "spacing = 0.05" in code
        assert "normal_align = True" in code
        assert "penetration_check = True" in code

    def test_normal_align_false(self):
        code = _gen_scatter_on_surface({
            "source_prims": ["/World/Apple"],
            "target_mesh": "/World/Tree",
            "count": 5,
            "normal_align": False,
        })
        _assert_valid_python(code, "scatter_on_surface")
        assert "normal_align = False" in code

    def test_poisson_disk_spacing_present(self):
        """Spec: spacing parameter enforces Poisson-disk minimum distance."""
        code = _gen_scatter_on_surface({
            "source_prims": ["/World/Apple"],
            "target_mesh": "/World/Tree",
            "count": 10,
            "spacing": 0.1,
        })
        _assert_valid_python(code, "scatter_on_surface")
        # The poisson rejection helper must compare distances against min_dist
        assert "min_dist" in code
        assert ".GetLength()" in code

    def test_default_count_handling(self):
        """Even without optional params, the generator must produce valid code."""
        code = _gen_scatter_on_surface({
            "source_prims": ["/World/X"],
            "target_mesh": "/World/Y",
            "count": 0,
        })
        _assert_valid_python(code, "scatter_on_surface")


# ---------------------------------------------------------------------------
# configure_differential_sdg
# ---------------------------------------------------------------------------

class TestConfigureDifferentialSDG:
    def test_minimal_static_dynamic(self):
        code = _gen_configure_differential_sdg({
            "static_elements": ["/World/Floor"],
            "dynamic_elements": ["/World/Light"],
        })
        _assert_valid_python(code, "configure_differential_sdg")
        assert "rep.new_layer" in code
        assert "rep.trigger.on_frame" in code
        assert "/World/Floor" in code
        assert "/World/Light" in code

    def test_default_randomizers_are_rotation_and_color(self):
        code = _gen_configure_differential_sdg({
            "static_elements": [],
            "dynamic_elements": ["/World/Cam"],
        })
        _assert_valid_python(code, "configure_differential_sdg")
        assert "rep.randomizer.rotation" in code
        assert "rep.randomizer.color" in code

    def test_all_randomizers(self):
        code = _gen_configure_differential_sdg({
            "static_elements": ["/World/Wall"],
            "dynamic_elements": ["/World/Obj"],
            "randomize": ["rotation", "position", "color", "intensity", "scale"],
        })
        _assert_valid_python(code, "configure_differential_sdg")
        assert "rep.randomizer.rotation" in code
        assert "rep.randomizer.position" in code
        assert "rep.randomizer.color" in code
        assert "rep.randomizer.light_intensity" in code
        assert "rep.randomizer.scale" in code

    def test_empty_dynamic_still_compiles(self):
        code = _gen_configure_differential_sdg({
            "static_elements": ["/World/Floor"],
            "dynamic_elements": [],
        })
        _assert_valid_python(code, "configure_differential_sdg")


# ---------------------------------------------------------------------------
# configure_coco_yolo_writer
# ---------------------------------------------------------------------------

class TestConfigureCocoYoloWriter:
    def test_coco_default(self):
        code = _gen_configure_coco_yolo_writer({
            "output_dir": "/tmp/sdg_out",
            "cameras": ["/World/Cam1", "/World/Cam2"],
        })
        _assert_valid_python(code, "configure_coco_yolo_writer")
        assert "/tmp/sdg_out" in code
        assert "/World/Cam1" in code
        assert "/World/Cam2" in code
        # Per-camera namespacing helper present
        assert "_image_id_for" in code
        # Single merged category map
        assert "categories.json" in code

    def test_yolo_format(self):
        code = _gen_configure_coco_yolo_writer({
            "output_dir": "/tmp/yolo_out",
            "cameras": ["/World/Cam"],
            "format": "yolo",
            "categories": ["apple", "orange"],
        })
        _assert_valid_python(code, "configure_coco_yolo_writer")
        assert "FORMAT = 'yolo'" in code
        assert "apple" in code and "orange" in code

    def test_globally_unique_ann_ids(self):
        """Spec: every annotation gets a globally-unique ID across cameras."""
        code = _gen_configure_coco_yolo_writer({
            "output_dir": "/tmp/sdg",
            "cameras": ["/World/A", "/World/B", "/World/C"],
        })
        _assert_valid_python(code, "configure_coco_yolo_writer")
        assert "_GLOBAL_ANN_ID" in code
        assert "_next_ann_id" in code

    def test_custom_id_offset(self):
        code = _gen_configure_coco_yolo_writer({
            "output_dir": "/tmp/sdg",
            "cameras": ["/World/A"],
            "id_offset": 500,
        })
        _assert_valid_python(code, "configure_coco_yolo_writer")
        assert "ID_OFFSET = 500" in code


# ---------------------------------------------------------------------------
# enforce_class_balance
# ---------------------------------------------------------------------------

class TestEnforceClassBalance:
    def test_minimal_args(self):
        code = _gen_enforce_class_balance({})
        _assert_valid_python(code, "enforce_class_balance")
        assert "MIN_PER_CLASS = 1" in code
        assert "MAX_RETRIES = 5" in code

    def test_with_explicit_classes(self):
        code = _gen_enforce_class_balance({
            "min_per_class": 2,
            "max_retries": 10,
            "classes": ["apple", "orange"],
        })
        _assert_valid_python(code, "enforce_class_balance")
        assert "MIN_PER_CLASS = 2" in code
        assert "MAX_RETRIES = 10" in code
        assert "apple" in code
        assert "orange" in code

    def test_retry_loop_present(self):
        code = _gen_enforce_class_balance({"min_per_class": 1, "max_retries": 3})
        _assert_valid_python(code, "enforce_class_balance")
        # Spec: retry up to max_retries before writing a partial frame
        assert "_class_counts" in code
        assert "class_balance_gate" in code
        assert "_RETRY_STATE" in code

    def test_write_partial_on_fail_false(self):
        code = _gen_enforce_class_balance({
            "min_per_class": 1,
            "max_retries": 5,
            "write_partial_on_fail": False,
        })
        _assert_valid_python(code, "enforce_class_balance")
        assert "WRITE_PARTIAL_ON_FAIL = False" in code


# ---------------------------------------------------------------------------
# benchmark_sdg (DATA handler)
# ---------------------------------------------------------------------------

class TestBenchmarkSdg:
    @pytest.mark.asyncio
    async def test_default_args_returns_baseline(self, mock_kit_rpc):
        result = await _handle_benchmark_sdg({})
        assert isinstance(result, dict)
        # rgb-only is the default annotator combo and has a preset baseline
        assert result.get("expected_fps_range") == [30, 60]
        assert result["annotators"] == ["rgb"]
        assert result["num_frames"] == 100
        assert result["resolution"] == [1280, 720]

    @pytest.mark.asyncio
    async def test_known_annotator_combo_baseline(self, mock_kit_rpc):
        result = await _handle_benchmark_sdg({
            "annotators": ["rgb", "depth", "bounding_box_2d"],
            "num_frames": 50,
        })
        assert result["expected_fps_range"] == [15, 25]

    @pytest.mark.asyncio
    async def test_unknown_annotator_combo_no_baseline(self, mock_kit_rpc):
        result = await _handle_benchmark_sdg({
            "annotators": ["rgb", "normals"],
        })
        assert result["expected_fps_range"] is None

    @pytest.mark.asyncio
    async def test_invalid_pipeline_id_rejected(self):
        result = await _handle_benchmark_sdg({
            "pipeline_id": "bad; rm -rf /",
        })
        assert "error" in result

    @pytest.mark.asyncio
    async def test_invalid_annotator_rejected(self):
        result = await _handle_benchmark_sdg({
            "annotators": ["rgb", "$(whoami)"],
        })
        assert "error" in result

    @pytest.mark.asyncio
    async def test_invalid_resolution_rejected(self):
        result = await _handle_benchmark_sdg({
            "resolution": [1280],  # missing height
        })
        assert "error" in result

    @pytest.mark.asyncio
    async def test_handler_registered_under_data_handlers(self):
        assert "benchmark_sdg" in DATA_HANDLERS
        assert DATA_HANDLERS["benchmark_sdg"] is _handle_benchmark_sdg
