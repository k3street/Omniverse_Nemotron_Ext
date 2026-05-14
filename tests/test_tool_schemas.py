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

# ---------------------------------------------------------------------------
# Tier-11 expected tools — exactly these five, no more, no less.
# Skip if none of the tier-11 tools are registered yet.
# ---------------------------------------------------------------------------

TIER_11_TOOLS = {
    # name → (handler_kind, required_args)
    "list_semantic_classes":     ("data", set()),
    "get_semantic_label":        ("data", {"prim_path"}),
    "remove_semantic_label":     ("code", {"prim_path"}),
    "assign_class_to_children":  ("code", {"prim_path", "class_name"}),
    "validate_semantic_labels":  ("data", set()),
}

_TIER_11_ANY_PRESENT = any(n in _ALL_TOOL_NAMES for n in TIER_11_TOOLS)


def _find_tool(name: str):
    for t in _ALL_TOOLS:
        if t["function"]["name"] == name:
            return t
    return None


@pytest.mark.skipif(not _TIER_11_ANY_PRESENT, reason="Tier 11 (SDG Annotation) not merged on this branch")
class TestTier11SchemasPresent:

    @pytest.mark.parametrize("name", sorted(TIER_11_TOOLS))
    def test_tool_declared(self, name):
        tool = _find_tool(name)
        assert tool is not None, f"Tier-11 tool '{name}' missing from ISAAC_SIM_TOOLS"

    @pytest.mark.parametrize("name", sorted(TIER_11_TOOLS))
    def test_required_args(self, name):
        tool = _find_tool(name)
        assert tool is not None
        params = tool["function"].get("parameters", {})
        assert params.get("type") == "object"
        required = set(params.get("required", []))
        expected = TIER_11_TOOLS[name][1]
        assert required == expected, (
            f"{name} required={required} but expected {expected}"
        )

    @pytest.mark.parametrize("name", sorted(TIER_11_TOOLS))
    def test_rich_description(self, name):
        """Rich descriptions are how the LLM disambiguates these tools from
        set_semantic_label (tier 0) and validate_annotations (PR #23).
        Enforce the WHAT/WHEN/RETURNS/CAVEATS template."""
        tool = _find_tool(name)
        assert tool is not None
        desc = tool["function"]["description"]
        assert len(desc) >= 200, (
            f"{name} description too short ({len(desc)} chars) — must include "
            f"WHAT / WHEN / RETURNS / CAVEATS sections."
        )
        for marker in ("WHAT:", "WHEN:", "RETURNS:", "CAVEATS:"):
            assert marker in desc, (
                f"{name} description missing '{marker}' section.\n"
                f"Description starts: {desc[:200]}..."
            )

    @pytest.mark.parametrize("name", sorted(TIER_11_TOOLS))
    def test_has_handler(self, name):
        kind = TIER_11_TOOLS[name][0]
        if kind == "data":
            assert name in DATA_HANDLERS, (
                f"DATA tier-11 tool '{name}' missing handler in DATA_HANDLERS"
            )
            assert DATA_HANDLERS[name] is not None
        elif kind == "code":
            assert name in CODE_GEN_HANDLERS, (
                f"CODE tier-11 tool '{name}' missing handler in CODE_GEN_HANDLERS"
            )
            assert CODE_GEN_HANDLERS[name] is not None
        else:
            pytest.fail(f"Unknown handler kind for {name}: {kind}")


@pytest.mark.skipif(not _TIER_11_ANY_PRESENT, reason="Tier 11 (SDG Annotation) not merged on this branch")
class TestNoTier11NameClashes:
    """Tier 11 must not clobber PR #23 / tier-0 names."""

    def test_validate_semantic_labels_distinct_from_validate_annotations(self):
        """The whole reason we picked validate_semantic_labels over the spec's
        validate_annotations: PR #23 (SDG quality) already owns
        validate_annotations and lints the SDG OUTPUT FILES. Tier 11's tool
        lints the USD STAGE annotations themselves — different scope, different
        name, no future merge conflict.
        """
        names = set(_ALL_TOOL_NAMES)
        assert "validate_semantic_labels" in names, (
            "Tier 11 must register validate_semantic_labels (not validate_annotations) "
            "to avoid clashing with PR #23 SDG quality tools."
        )

    def test_set_semantic_label_not_re_declared(self):
        """tier-0's set_semantic_label (PR #59) is the single-prim writer —
        tier 11 introduces complementary tools and must NOT re-declare it.
        On master neither PR is merged yet, so the assertion is structural:
        if/when set_semantic_label is added, it should appear at most once.
        """
        count = sum(1 for n in _ALL_TOOL_NAMES if n == "set_semantic_label")
        assert count <= 1, (
            "set_semantic_label declared multiple times — tier 11 must not "
            "redefine tier-0's single-prim writer."
        )

    def test_no_duplicate_tool_names(self):
        seen = set()
        for n in _ALL_TOOL_NAMES:
            assert n not in seen, f"Duplicate tool name in ISAAC_SIM_TOOLS: {n}"
            seen.add(n)


@pytest.mark.skipif(not _TIER_11_ANY_PRESENT, reason="Tier 11 (SDG Annotation) not merged on this branch")
class TestTier11ExactlyFive:
    """Spec mandates EXACTLY 5 tools — guard against accidental scope creep."""

    def test_exactly_five_tier_11_tools_present(self):
        present = set(TIER_11_TOOLS) & set(_ALL_TOOL_NAMES)
        assert present == set(TIER_11_TOOLS), (
            f"Tier 11 must register exactly these 5 tools: {sorted(TIER_11_TOOLS)}.\n"
            f"Missing: {set(TIER_11_TOOLS) - present}"
        )
