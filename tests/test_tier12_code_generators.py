"""
L0 tests for the tier-12 Asset Management CODE_GEN handlers.

Each test:
  1. Calls the handler with valid arguments
  2. Verifies the returned code compiles (compile())
  3. Checks for expected USD references / payloads API calls
  4. Verifies optional kwargs (ref_prim_path, layer_offset_seconds,
     instanceable) round-trip correctly
"""
import pytest

pytestmark = pytest.mark.l0

from service.isaac_assist_service.chat.tools.tool_executor import CODE_GEN_HANDLERS


def _assert_valid_python(code: str, handler_name: str):
    """Verify the generated code is syntactically valid Python."""
    try:
        compile(code, f"<generated:{handler_name}>", "exec")
    except SyntaxError as e:
        pytest.fail(f"{handler_name} generated invalid Python:\n{e}\n\nCode:\n{code}")


# ---------------------------------------------------------------------------
# Test vectors — exactly the tier-12 CODE_GEN handlers.
# Each entry: (handler_name, args_dict, expected_substrings)
# ---------------------------------------------------------------------------

_RAW_TEST_VECTORS = [
    # ── add_usd_reference — minimal call ──────────────────────────────────
    (
        "add_usd_reference",
        {"prim_path": "/World/Robot", "usd_url": "omniverse://localhost/Robots/Franka/franka.usd"},
        [
            "AddReference",
            "GetReferences",
            "/World/Robot",
            "omniverse://localhost/Robots/Franka/franka.usd",
        ],
    ),
    # ── add_usd_reference — ref_prim_path kwarg ───────────────────────────
    (
        "add_usd_reference",
        {
            "prim_path": "/World/Robot",
            "usd_url": "./assets/franka.usd",
            "ref_prim_path": "/Manipulator/panda_link0",
        },
        [
            "AddReference",
            "/Manipulator/panda_link0",
            "ref_prim_path",
        ],
    ),
    # ── add_usd_reference — layer_offset_seconds kwarg ────────────────────
    (
        "add_usd_reference",
        {
            "prim_path": "/World/Anim",
            "usd_url": "./assets/walk_cycle.usd",
            "layer_offset_seconds": 2.5,
        },
        [
            "AddReference",
            "Sdf.LayerOffset",
            "GetTimeCodesPerSecond",
            "2.5",
        ],
    ),
    # ── add_usd_reference — instanceable=True kwarg ───────────────────────
    (
        "add_usd_reference",
        {
            "prim_path": "/World/Tree",
            "usd_url": "./assets/tree.usd",
            "instanceable": True,
        },
        [
            "AddReference",
            "SetInstanceable",
            "True",
        ],
    ),
    # ── load_payload — basic ──────────────────────────────────────────────
    (
        "load_payload",
        {"prim_path": "/World/Environment"},
        [
            "LoadAndUnload",
            "/World/Environment",
            "GetLoadSet",
            "Sdf.Path",
        ],
    ),
    # ── load_payload — different path ─────────────────────────────────────
    (
        "load_payload",
        {"prim_path": "/World/Robot/manipulator"},
        [
            "LoadAndUnload",
            "/World/Robot/manipulator",
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


class TestTier12CodeGenEdgeCases:
    """Edge cases for tier-12 CODE_GEN tools."""

    @pytest.mark.skipif(
        "add_usd_reference" not in CODE_GEN_HANDLERS,
        reason="Tier 12 (Asset Management) not merged on this branch",
    )
    def test_add_usd_reference_minimal_no_kwargs(self):
        """When no optional kwargs are passed, the script must still compile and
        do the simple AddReference call."""
        code = CODE_GEN_HANDLERS["add_usd_reference"]({
            "prim_path": "/World/X",
            "usd_url": "./a.usd",
        })
        _assert_valid_python(code, "add_usd_reference")
        # When neither ref_prim_path nor layer_offset_seconds are passed, the
        # generated script must take the simple AddReference branch.
        assert "AddReference(usd_url)" in code
        # Defaults preserved as None / False:
        assert "ref_prim_path = None" in code
        assert "layer_offset_seconds = None" in code
        assert "instanceable = False" in code

    @pytest.mark.skipif(
        "add_usd_reference" not in CODE_GEN_HANDLERS,
        reason="Tier 12 (Asset Management) not merged on this branch",
    )
    def test_add_usd_reference_special_chars_in_path(self):
        """Special chars in prim path AND asset URL must round-trip via repr() without breaking syntax."""
        code = CODE_GEN_HANDLERS["add_usd_reference"]({
            "prim_path": "/World/My Asset (v2)/holder's_03",
            "usd_url": "omniverse://server/it's a path/file.usd",
        })
        _assert_valid_python(code, "add_usd_reference")
        assert "My Asset" in code
        assert "it's a path" in code

    @pytest.mark.skipif(
        "add_usd_reference" not in CODE_GEN_HANDLERS,
        reason="Tier 12 (Asset Management) not merged on this branch",
    )
    def test_add_usd_reference_auto_creates_xform(self):
        """Generator must auto-create the holding prim as Xform if missing —
        otherwise the LLM has to call create_prim first which doubles approval."""
        code = CODE_GEN_HANDLERS["add_usd_reference"]({
            "prim_path": "/World/NewHolder",
            "usd_url": "./a.usd",
        })
        _assert_valid_python(code, "add_usd_reference")
        assert "UsdGeom.Xform.Define" in code

    @pytest.mark.skipif(
        "add_usd_reference" not in CODE_GEN_HANDLERS,
        reason="Tier 12 (Asset Management) not merged on this branch",
    )
    def test_add_usd_reference_combined_kwargs(self):
        """All three optional kwargs at once must still produce valid Python."""
        code = CODE_GEN_HANDLERS["add_usd_reference"]({
            "prim_path": "/World/Robot",
            "usd_url": "./franka.usd",
            "ref_prim_path": "/Manipulator",
            "layer_offset_seconds": 1.25,
            "instanceable": True,
        })
        _assert_valid_python(code, "add_usd_reference")
        assert "/Manipulator" in code
        assert "1.25" in code
        assert "SetInstanceable" in code

    @pytest.mark.skipif(
        "load_payload" not in CODE_GEN_HANDLERS,
        reason="Tier 12 (Asset Management) not merged on this branch",
    )
    def test_load_payload_safe_when_already_loaded(self):
        """Generated code must be a soft no-op if the prim is already loaded."""
        code = CODE_GEN_HANDLERS["load_payload"]({"prim_path": "/World/Foo"})
        _assert_valid_python(code, "load_payload")
        assert "no-op" in code.lower() or "nothing to do" in code.lower()
        # And must still raise on a bad prim path so errors surface fast.
        assert "RuntimeError" in code

    @pytest.mark.skipif(
        "load_payload" not in CODE_GEN_HANDLERS,
        reason="Tier 12 (Asset Management) not merged on this branch",
    )
    def test_load_payload_loads_with_descendants(self):
        """Spec docs LoadWithDescendants is the policy; verify the script uses it."""
        code = CODE_GEN_HANDLERS["load_payload"]({"prim_path": "/World/Env"})
        _assert_valid_python(code, "load_payload")
        assert "LoadWithDescendants" in code

    @pytest.mark.skipif(
        "load_payload" not in CODE_GEN_HANDLERS,
        reason="Tier 12 (Asset Management) not merged on this branch",
    )
    def test_load_payload_special_chars_in_path(self):
        code = CODE_GEN_HANDLERS["load_payload"]({
            "prim_path": "/World/My Env (v2)/scene's_root",
        })
        _assert_valid_python(code, "load_payload")
        assert "My Env" in code
