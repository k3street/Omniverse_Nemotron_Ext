"""Unit tests for the @honesty_checked scaffold (tool_honesty.py).

L0 — no Kit, no USD. Tests the generated code string structure only.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.l0


def test_require_prim_exists_snippet_contains_raise():
    from service.isaac_assist_service.chat.tools.tool_honesty import (
        require_prim_exists_snippet,
    )
    code = require_prim_exists_snippet("/World/X", "set_mass")
    assert "GetPrimAtPath" in code
    assert "IsValid()" in code
    assert "raise RuntimeError" in code
    assert "'set_mass'" in code
    assert "'/World/X'" in code


def test_require_file_exists_snippet_skips_url_schemes():
    from service.isaac_assist_service.chat.tools.tool_honesty import (
        require_file_exists_snippet,
    )
    code = require_file_exists_snippet("/tmp/asset.usd", "add_reference")
    assert "os.path.exists" in code
    assert "FileNotFoundError" in code
    # The URL-scheme allowlist is there
    assert "omniverse://" in code
    assert "http://" in code


def test_post_check_schema_applied_raises_on_missing():
    from service.isaac_assist_service.chat.tools.tool_honesty import (
        post_check_schema_applied_snippet,
    )
    code = post_check_schema_applied_snippet(
        "/World/Cube", "PhysicsRigidBodyAPI", "apply_rigid_body"
    )
    assert "GetAppliedSchemas" in code
    assert "raise RuntimeError" in code
    assert "'PhysicsRigidBodyAPI'" in code


def test_honesty_checked_decorator_prepends_prim_check():
    from service.isaac_assist_service.chat.tools.tool_honesty import honesty_checked

    @honesty_checked(require_prim_paths=("prim_path",))
    def _gen_demo(args):
        return "print('inner ran')"

    code = _gen_demo({"prim_path": "/World/X"})
    # Pre-check block comes before the inner handler's output
    assert code.index("GetPrimAtPath") < code.index("print('inner ran')")
    assert "'demo'" in code  # tool name from _gen_demo → 'demo'
    assert "raise RuntimeError" in code


def test_honesty_checked_decorator_skips_missing_arg():
    """If args doesn't carry the required key, the pre-check is omitted
    (not all tools receive every arg; the decorator must degrade gracefully)."""
    from service.isaac_assist_service.chat.tools.tool_honesty import honesty_checked

    @honesty_checked(require_prim_paths=("prim_path",))
    def _gen_demo(args):
        return "print('ok')"

    code = _gen_demo({})  # no prim_path
    assert "GetPrimAtPath" not in code
    assert code.strip() == "print('ok')"


def test_honesty_checked_decorator_appends_post_schema_check():
    from service.isaac_assist_service.chat.tools.tool_honesty import honesty_checked

    @honesty_checked(
        require_prim_paths=("prim_path",),
        post_schema_checks=(("prim_path", "PhysicsCollisionAPI"),),
    )
    def _gen_apply_collision(args):
        return "UsdPhysics.CollisionAPI.Apply(prim)"

    code = _gen_apply_collision({"prim_path": "/World/Cube"})
    assert "GetPrimAtPath" in code  # pre-check
    # Both present
    assert "IsValid()" in code
    assert "GetAppliedSchemas" in code
    # Order: pre-check first, handler body in the middle, post-check last
    pre_idx = code.index("IsValid()")
    body_idx = code.index("UsdPhysics.CollisionAPI.Apply")
    post_idx = code.index("GetAppliedSchemas")
    assert pre_idx < body_idx < post_idx


def test_post_check_prim_exists_snippet():
    from service.isaac_assist_service.chat.tools.tool_honesty import (
        post_check_prim_exists_snippet,
    )
    code = post_check_prim_exists_snippet("/World/NewCube", "create_prim")
    assert "GetPrimAtPath" in code
    assert "IsValid()" in code
    assert "raise RuntimeError" in code
    assert "was expected at" in code


def test_post_check_prim_absent_snippet():
    from service.isaac_assist_service.chat.tools.tool_honesty import (
        post_check_prim_absent_snippet,
    )
    code = post_check_prim_absent_snippet("/World/ToDelete", "delete_prim")
    assert "GetPrimAtPath" in code
    assert "still exists after" in code
    assert "raise RuntimeError" in code


def test_honesty_checked_post_exists_and_absent():
    from service.isaac_assist_service.chat.tools.tool_honesty import honesty_checked

    @honesty_checked(post_exists_checks=("out_path",))
    def _gen_create(args):
        return "stage.DefinePrim(args['out_path'], 'Cube')"

    code = _gen_create({"out_path": "/World/Made"})
    assert "stage.DefinePrim" in code
    post_idx = code.index("was expected at")
    body_idx = code.index("stage.DefinePrim")
    assert body_idx < post_idx

    @honesty_checked(post_absent_checks=("victim",))
    def _gen_remove(args):
        return "stage.RemovePrim(args['victim'])"

    code = _gen_remove({"victim": "/World/Gone"})
    assert "still exists after" in code


def test_honesty_checked_quote_safe_for_paths_with_special_chars():
    """Paths containing single quotes or backslashes must not corrupt the
    generated code — repr() escapes them."""
    from service.isaac_assist_service.chat.tools.tool_honesty import (
        require_prim_exists_snippet,
    )
    code = require_prim_exists_snippet("/World/O'Hare", "tool_x")
    import ast
    ast.parse(code)  # must be valid Python despite the apostrophe
