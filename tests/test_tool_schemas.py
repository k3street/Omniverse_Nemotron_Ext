"""
L0 tests for tool schema validation.
Ensures every declared tool has well-formed JSON Schema definitions
and a matching handler in tool_executor.
"""
import pytest

pytestmark = pytest.mark.l0

from service.isaac_assist_service.chat.tools.tool_schemas import ISAAC_SIM_TOOLS
from service.isaac_assist_service.chat.tools.tool_executor import (
    CODE_GEN_HANDLERS,
    DATA_HANDLERS,
)

# ---------------------------------------------------------------------------
# Collect all tool function dicts once
# ---------------------------------------------------------------------------

_ALL_TOOLS = ISAAC_SIM_TOOLS
_ALL_TOOL_NAMES = [t["function"]["name"] for t in _ALL_TOOLS]

# These tools are handled inline by the LLM (no executor dispatch) or
# through special-case code in execute_tool_call.
_SPECIAL_CASE_TOOLS = {
    "run_usd_script",          # handled as a direct pass-through
    "generate_scene_blueprint",  # LLM-handled
    "create_isaaclab_env",     # LLM-handled
    "launch_training",         # LLM-handled
    "vision_detect_objects",   # LLM-handled
    "vision_bounding_boxes",   # LLM-handled
    "vision_plan_trajectory",  # LLM-handled
    "vision_analyze_scene",    # LLM-handled
    "export_scene_package",    # LLM-handled
    "get_physics_errors",      # LLM-handled
    "check_collisions",        # LLM-handled
    "fix_error",               # LLM-handled
}


class TestToolSchemaStructure:
    """Each tool schema must have the required OpenAI function-calling fields."""

    @pytest.mark.parametrize("tool", _ALL_TOOLS, ids=_ALL_TOOL_NAMES)
    def test_has_type_function(self, tool):
        assert tool.get("type") == "function"

    @pytest.mark.parametrize("tool", _ALL_TOOLS, ids=_ALL_TOOL_NAMES)
    def test_has_function_name(self, tool):
        fn = tool.get("function", {})
        assert "name" in fn, "Missing 'name' in function definition"
        assert isinstance(fn["name"], str) and len(fn["name"]) > 0

    @pytest.mark.parametrize("tool", _ALL_TOOLS, ids=_ALL_TOOL_NAMES)
    def test_has_function_description(self, tool):
        fn = tool.get("function", {})
        assert "description" in fn, f"Tool '{fn.get('name')}' missing description"
        assert len(fn["description"]) >= 10, "Description too short to be useful"

    @pytest.mark.parametrize("tool", _ALL_TOOLS, ids=_ALL_TOOL_NAMES)
    def test_has_parameters_object(self, tool):
        fn = tool.get("function", {})
        params = fn.get("parameters", {})
        assert params.get("type") == "object", (
            f"Tool '{fn['name']}' parameters must be type=object"
        )

    @pytest.mark.parametrize("tool", _ALL_TOOLS, ids=_ALL_TOOL_NAMES)
    def test_parameter_types_are_valid(self, tool):
        """Every declared property must have a valid JSON Schema type."""
        VALID_TYPES = {"string", "number", "integer", "boolean", "array", "object"}
        fn = tool["function"]
        props = fn.get("parameters", {}).get("properties", {})
        for prop_name, prop_def in props.items():
            if "type" in prop_def:
                assert prop_def["type"] in VALID_TYPES, (
                    f"{fn['name']}.{prop_name}: invalid type '{prop_def['type']}'"
                )

    @pytest.mark.parametrize("tool", _ALL_TOOLS, ids=_ALL_TOOL_NAMES)
    def test_required_fields_exist_in_properties(self, tool):
        """Required fields must be defined in properties."""
        fn = tool["function"]
        params = fn.get("parameters", {})
        required = params.get("required", [])
        props = params.get("properties", {})
        for req in required:
            assert req in props, (
                f"{fn['name']}: required field '{req}' not in properties"
            )


class TestNoDuplicateToolNames:
    def test_no_duplicates(self):
        seen = set()
        for name in _ALL_TOOL_NAMES:
            assert name not in seen, f"Duplicate tool name: {name}"
            seen.add(name)


class TestToolHandlerMapping:
    """Every tool in schemas must have a matching handler in tool_executor."""

    @pytest.mark.parametrize("name", _ALL_TOOL_NAMES)
    def test_tool_has_handler(self, name):
        has_code = name in CODE_GEN_HANDLERS
        has_data = name in DATA_HANDLERS
        has_special = name in _SPECIAL_CASE_TOOLS
        assert has_code or has_data or has_special, (
            f"Tool '{name}' declared in schemas but has no handler"
        )

    def test_all_code_gen_handlers_have_schema(self):
        """CODE_GEN_HANDLERS should not contain orphaned handlers."""
        for handler_name in CODE_GEN_HANDLERS:
            assert handler_name in _ALL_TOOL_NAMES, (
                f"CODE_GEN_HANDLER '{handler_name}' has no matching schema"
            )

    def test_all_data_handlers_have_schema(self):
        """DATA_HANDLERS should not contain orphaned handlers (except ROS2 fallbacks)."""
        for handler_name in DATA_HANDLERS:
            if handler_name.startswith("ros2_") and DATA_HANDLERS[handler_name] is None:
                continue  # disabled fallback stubs
            assert handler_name in _ALL_TOOL_NAMES, (
                f"DATA_HANDLER '{handler_name}' has no matching schema"
            )
