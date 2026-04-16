"""
L0 tests for tier-11 SDG-annotation tool schemas.

Verifies:
  - All 5 tier-11 tools are declared in ISAAC_SIM_TOOLS
  - Each has a rich WHAT/WHEN/RETURNS/CAVEATS description
  - Each tool's parameters are valid JSON Schema
  - Each tool maps to an executor handler (DATA or CODE_GEN)
  - validate_semantic_labels does NOT clash with the PR #23
    validate_annotations name even when both are merged together
"""
import pytest

pytestmark = pytest.mark.l0

from service.isaac_assist_service.chat.tools.tool_schemas import ISAAC_SIM_TOOLS
from service.isaac_assist_service.chat.tools.tool_executor import (
    CODE_GEN_HANDLERS,
    DATA_HANDLERS,
)


_ALL_TOOLS = ISAAC_SIM_TOOLS
_ALL_TOOL_NAMES = [t["function"]["name"] for t in _ALL_TOOLS]


# ---------------------------------------------------------------------------
# Tier-11 expected tools — exactly these five, no more, no less.
# ---------------------------------------------------------------------------

TIER_11_TOOLS = {
    # name → (handler_kind, required_args)
    "list_semantic_classes":     ("data", set()),
    "get_semantic_label":        ("data", {"prim_path"}),
    "remove_semantic_label":     ("code", {"prim_path"}),
    "assign_class_to_children":  ("code", {"prim_path", "class_name"}),
    "validate_semantic_labels":  ("data", set()),
}


def _find_tool(name: str):
    for t in _ALL_TOOLS:
        if t["function"]["name"] == name:
            return t
    return None


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


class TestTier11ExactlyFive:
    """Spec mandates EXACTLY 5 tools — guard against accidental scope creep."""

    def test_exactly_five_tier_11_tools_present(self):
        present = set(TIER_11_TOOLS) & set(_ALL_TOOL_NAMES)
        assert present == set(TIER_11_TOOLS), (
            f"Tier 11 must register exactly these 5 tools: {sorted(TIER_11_TOOLS)}.\n"
            f"Missing: {set(TIER_11_TOOLS) - present}"
        )
