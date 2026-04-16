"""
L0 tests for the tier-11 SDG-annotation CODE_GEN handlers.

Each test:
  1. Calls the handler with valid arguments
  2. Verifies the returned code compiles (compile())
  3. Checks for expected USD / Semantics API calls

Skipif guards keep the file runnable on every other tier branch — the
suite stays green when these handlers haven't been merged yet.
"""
import pytest

pytestmark = pytest.mark.l0

from service.isaac_assist_service.chat.tools.tool_executor import CODE_GEN_HANDLERS


# ---------------------------------------------------------------------------
# Helper: compile check
# ---------------------------------------------------------------------------

def _assert_valid_python(code: str, handler_name: str):
    """Verify the generated code is syntactically valid Python."""
    try:
        compile(code, f"<generated:{handler_name}>", "exec")
    except SyntaxError as e:
        pytest.fail(f"{handler_name} generated invalid Python:\n{e}\n\nCode:\n{code}")


# ---------------------------------------------------------------------------
# Test vectors — exactly the tier-11 CODE_GEN handlers.
# Each entry: (handler_name, args_dict, expected_substrings)
# ---------------------------------------------------------------------------

_RAW_TEST_VECTORS = [
    # ── Tier 11 — SDG Annotation ────────────────────────────────────────
    (
        "remove_semantic_label",
        {"prim_path": "/World/Tray/bottle_03"},
        [
            "Semantics.SemanticsAPI",
            "RemoveAPI",
            "GetAll",
            "/World/Tray/bottle_03",
        ],
    ),
    (
        "remove_semantic_label",
        {"prim_path": "/World/Robot/panda_link0"},
        [
            "RemoveAPI",
            "/World/Robot/panda_link0",
        ],
    ),
    (
        "assign_class_to_children",
        {"prim_path": "/World/Tray", "class_name": "medicine_bottle"},
        [
            "Semantics.SemanticsAPI",
            "Apply",
            "Usd.PrimRange",
            "UsdGeom.Mesh",
            "medicine_bottle",
            "Semantics_class",
            "/World/Tray",
        ],
    ),
    (
        "assign_class_to_children",
        {
            "prim_path": "/World/Robot",
            "class_name": "panda_link",
            "semantic_type": "instance_id",
        },
        [
            "Semantics.SemanticsAPI",
            "Semantics_instance_id",
            "instance_id",
            "panda_link",
        ],
    ),
]


# Filter out vectors whose handlers do not exist on this branch.
_TEST_VECTORS = [v for v in _RAW_TEST_VECTORS if v[0] in CODE_GEN_HANDLERS]


class TestCodeGenerators:

    @pytest.mark.parametrize(
        "handler_name,args,expected_substrings",
        _TEST_VECTORS,
        ids=[f"{v[0]}_{i}" for i, v in enumerate(_TEST_VECTORS)],
    )
    def test_generates_valid_python(self, handler_name, args, expected_substrings):
        gen = CODE_GEN_HANDLERS[handler_name]
        code = gen(args)
        _assert_valid_python(code, handler_name)

    @pytest.mark.parametrize(
        "handler_name,args,expected_substrings",
        _TEST_VECTORS,
        ids=[f"{v[0]}_{i}" for i, v in enumerate(_TEST_VECTORS)],
    )
    def test_contains_expected_fragments(self, handler_name, args, expected_substrings):
        gen = CODE_GEN_HANDLERS[handler_name]
        code = gen(args)
        for frag in expected_substrings:
            assert frag in code, (
                f"{handler_name}: expected '{frag}' in generated code.\n"
                f"Code:\n{code[:800]}"
            )


class TestTier11CodeGenEdgeCases:
    """Edge cases for tier-11 CODE_GEN tools — guard with skipif so the file
    stays runnable on branches where tier 11 isn't merged yet."""

    @pytest.mark.skipif(
        "remove_semantic_label" not in CODE_GEN_HANDLERS,
        reason="Tier 11 (SDG Annotation) not merged on this branch",
    )
    def test_remove_semantic_label_safe_when_no_api(self):
        """Generated code must NOT raise when the prim has no SemanticsAPI applied."""
        code = CODE_GEN_HANDLERS["remove_semantic_label"](
            {"prim_path": "/World/UnlabeledPrim"}
        )
        _assert_valid_python(code, "remove_semantic_label")
        # The "no labels found" branch should be a soft no-op (printed message,
        # not a RuntimeError) so the LLM can call this tool defensively.
        assert "no-op" in code.lower() or "nothing to remove" in code.lower()
        # And the prim-not-found case must still raise so bad paths surface fast.
        assert "RuntimeError" in code

    @pytest.mark.skipif(
        "remove_semantic_label" not in CODE_GEN_HANDLERS,
        reason="Tier 11 (SDG Annotation) not merged on this branch",
    )
    def test_remove_semantic_label_special_chars_in_path(self):
        """Special chars in prim path must round-trip via repr() without breaking syntax."""
        code = CODE_GEN_HANDLERS["remove_semantic_label"](
            {"prim_path": "/World/My Asset (v2)/bottle's_03"}
        )
        _assert_valid_python(code, "remove_semantic_label")
        assert "My Asset" in code
        assert "bottle" in code

    @pytest.mark.skipif(
        "assign_class_to_children" not in CODE_GEN_HANDLERS,
        reason="Tier 11 (SDG Annotation) not merged on this branch",
    )
    def test_assign_class_default_semantic_type_is_class(self):
        """Omitting semantic_type must default to 'class' (the standard SDG bucket)."""
        code = CODE_GEN_HANDLERS["assign_class_to_children"]({
            "prim_path": "/World/Tray",
            "class_name": "bottle",
        })
        _assert_valid_python(code, "assign_class_to_children")
        assert "Semantics_class" in code
        # The semantic type literal itself must also be 'class'
        assert "'class'" in code

    @pytest.mark.skipif(
        "assign_class_to_children" not in CODE_GEN_HANDLERS,
        reason="Tier 11 (SDG Annotation) not merged on this branch",
    )
    def test_assign_class_walks_subtree_not_only_root(self):
        """Generator must use Usd.PrimRange so every descendant gets visited."""
        code = CODE_GEN_HANDLERS["assign_class_to_children"]({
            "prim_path": "/World/Asset",
            "class_name": "pallet",
        })
        _assert_valid_python(code, "assign_class_to_children")
        assert "PrimRange" in code

    @pytest.mark.skipif(
        "assign_class_to_children" not in CODE_GEN_HANDLERS,
        reason="Tier 11 (SDG Annotation) not merged on this branch",
    )
    def test_assign_class_skips_xforms_only(self):
        """Generator must restrict labeling to Mesh / Imageable (Gprim) prims."""
        code = CODE_GEN_HANDLERS["assign_class_to_children"]({
            "prim_path": "/World/Group",
            "class_name": "thing",
        })
        _assert_valid_python(code, "assign_class_to_children")
        # The skip-xform branch must reference the Imageable / Gprim type so the
        # LLM can rely on Xforms being skipped.
        assert "UsdGeom.Gprim" in code or "UsdGeom.Mesh" in code
        assert "skipped" in code

    @pytest.mark.skipif(
        "assign_class_to_children" not in CODE_GEN_HANDLERS,
        reason="Tier 11 (SDG Annotation) not merged on this branch",
    )
    def test_assign_class_special_chars_in_class_name(self):
        """Class names with quotes / spaces must round-trip via repr() without breaking syntax."""
        code = CODE_GEN_HANDLERS["assign_class_to_children"]({
            "prim_path": "/World/Tray",
            "class_name": "medicine bottle (250ml)",
        })
        _assert_valid_python(code, "assign_class_to_children")
        assert "medicine bottle" in code
