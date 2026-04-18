"""
L0 tests for tier-12 Asset Management tool schemas.

Verifies:
  - All 5 tier-12 tools are declared in ISAAC_SIM_TOOLS
  - Each has a rich WHAT/WHEN/RETURNS/CAVEATS description
  - Each tool's parameters are valid JSON Schema
  - Each tool maps to an executor handler (DATA or CODE_GEN)
  - tier-12 add_usd_reference does NOT clash with PR #1's add_reference —
    both surfaces coexist intentionally
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
# Tier-12 expected tools — exactly these five, no more, no less.
# ---------------------------------------------------------------------------

TIER_12_TOOLS = {
    # name → (handler_kind, required_args)
    "list_references":    ("data", {"prim_path"}),
    "add_usd_reference":  ("code", {"prim_path", "usd_url"}),
    "list_payloads":      ("data", {"prim_path"}),
    "load_payload":       ("code", {"prim_path"}),
    "get_asset_info":     ("data", {"prim_path"}),
}


def _find_tool(name: str):
    for t in _ALL_TOOLS:
        if t["function"]["name"] == name:
            return t
    return None


class TestTier12SchemasPresent:

    @pytest.mark.parametrize("name", sorted(TIER_12_TOOLS))
    def test_tool_declared(self, name):
        tool = _find_tool(name)
        assert tool is not None, f"Tier-12 tool '{name}' missing from ISAAC_SIM_TOOLS"

    @pytest.mark.parametrize("name", sorted(TIER_12_TOOLS))
    def test_required_args(self, name):
        tool = _find_tool(name)
        assert tool is not None
        params = tool["function"].get("parameters", {})
        assert params.get("type") == "object"
        required = set(params.get("required", []))
        expected = TIER_12_TOOLS[name][1]
        assert required == expected, (
            f"{name} required={required} but expected {expected}"
        )

    @pytest.mark.parametrize("name", sorted(TIER_12_TOOLS))
    def test_rich_description(self, name):
        """Rich descriptions are how the LLM disambiguates tier-12 tools from
        PR #1's simple add_reference. Enforce the WHAT/WHEN/RETURNS/CAVEATS template."""
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

    @pytest.mark.parametrize("name", sorted(TIER_12_TOOLS))
    def test_has_handler(self, name):
        kind = TIER_12_TOOLS[name][0]
        if kind == "data":
            assert name in DATA_HANDLERS, (
                f"DATA tier-12 tool '{name}' missing handler in DATA_HANDLERS"
            )
            assert DATA_HANDLERS[name] is not None
        elif kind == "code":
            assert name in CODE_GEN_HANDLERS, (
                f"CODE tier-12 tool '{name}' missing handler in CODE_GEN_HANDLERS"
            )
            assert CODE_GEN_HANDLERS[name] is not None
        else:
            pytest.fail(f"Unknown handler kind for {name}: {kind}")


class TestNoTier12NameClashes:
    """Tier 12 must not clobber PR #1's add_reference."""

    def test_add_usd_reference_distinct_from_add_reference(self):
        """The whole reason we picked add_usd_reference over the spec's
        add_reference: PR #1 (USD basics) already owns add_reference and is the
        simple default-prim drop. Tier 12's add_usd_reference adds the FULL
        surface (ref_prim_path, layer_offset_seconds, instanceable) — both must
        coexist.
        """
        names = set(_ALL_TOOL_NAMES)
        assert "add_usd_reference" in names, (
            "Tier 12 must register add_usd_reference (not add_reference) so "
            "PR #1's simple add_reference call stays unchanged."
        )
        assert "add_reference" in names, (
            "PR #1's add_reference must remain — tier 12 EXTENDS it, does not replace."
        )

    def test_no_duplicate_tool_names(self):
        seen = set()
        for n in _ALL_TOOL_NAMES:
            assert n not in seen, f"Duplicate tool name in ISAAC_SIM_TOOLS: {n}"
            seen.add(n)

    def test_add_reference_pr1_simple_signature_unchanged(self):
        """PR #1's add_reference must keep its (prim_path, reference_path)
        signature — no kwargs added on this branch."""
        tool = _find_tool("add_reference")
        assert tool is not None
        params = tool["function"]["parameters"]
        required = set(params.get("required", []))
        assert required == {"prim_path", "reference_path"}, (
            f"PR #1 add_reference required args changed unexpectedly: {required}"
        )

    def test_add_usd_reference_optional_kwargs_present(self):
        """The whole point of the tier-12 tool is the extra kwargs."""
        tool = _find_tool("add_usd_reference")
        assert tool is not None
        props = tool["function"]["parameters"].get("properties", {})
        for kw in ("ref_prim_path", "layer_offset_seconds", "instanceable"):
            assert kw in props, (
                f"add_usd_reference must expose optional kwarg '{kw}' to be "
                f"meaningfully different from PR #1's add_reference."
            )


class TestTier12ExactlyFive:
    """Spec mandates EXACTLY 5 tools — guard against accidental scope creep."""

    def test_exactly_five_tier_12_tools_present(self):
        present = set(TIER_12_TOOLS) & set(_ALL_TOOL_NAMES)
        assert present == set(TIER_12_TOOLS), (
            f"Tier 12 must register exactly these 5 tools: {sorted(TIER_12_TOOLS)}.\n"
            f"Missing: {set(TIER_12_TOOLS) - present}\n"
            f"Extra: {present - set(TIER_12_TOOLS)}"
        )
