"""Honesty-check scaffold for code-gen tool handlers.

Helpers that emit validation snippets to be prepended/appended inside
the generated code string that gets exec'd by Kit RPC. Designed to
retrofit existing `_gen_*` handlers without rewriting them — the
`@honesty_checked` decorator wraps a handler and injects the pre/post
validation blocks around the handler's own output.

Design principles:

1. **Validation runs INSIDE the generated code**, not in the Python
   code-gen layer. This way the check happens in the same Kit context
   as the operation, and can read the real stage.

2. **Emit clear RuntimeError messages** that name the specific arg,
   path, and tool. The agent relays these back in plain text; users
   should understand from a single error line what went wrong.

3. **Opt-in per handler** via the decorator. Don't break existing
   handlers by auto-wrapping — too many edge cases. Audit + convert
   handlers one at a time.

4. **Quote-safe**: all emitted code uses `{name!r}`-wrapped literals
   rather than raw string interpolation, so arg values containing
   quotes or backslashes don't produce broken generated code.

Usage:

    @honesty_checked(require_prim_paths=("prim_path",))
    def _gen_set_visibility(args):
        p = args["prim_path"]
        return f"attr = stage.GetPrimAtPath({p!r}).GetAttribute('visibility')\\nattr.Set('invisible')"

    # Equivalent manually-written code gets prepended:
    #   stage = omni.usd.get_context().get_stage()
    #   if not stage.GetPrimAtPath('/World/X').IsValid():
    #       raise RuntimeError('set_visibility: prim not found: /World/X')
"""
from __future__ import annotations

import functools
from typing import Callable, Dict, Iterable


def require_prim_exists_snippet(path: str, tool_name: str) -> str:
    """Return Python source that raises RuntimeError if `path` is not a valid prim."""
    return (
        "import omni.usd as _honesty_usd\n"
        f"_honesty_stage = _honesty_usd.get_context().get_stage()\n"
        f"_honesty_prim = _honesty_stage.GetPrimAtPath({path!r})\n"
        f"if not _honesty_prim or not _honesty_prim.IsValid():\n"
        f"    raise RuntimeError({tool_name!r} + ': prim not found: ' + {path!r})\n"
    )


def require_file_exists_snippet(path: str, tool_name: str) -> str:
    """Return Python source that raises FileNotFoundError for missing local files.

    URL schemes (omniverse://, http(s)://, file://, anon:) pass through to
    the USD asset resolver — skip the os.path.exists check for those.
    """
    return (
        "import os as _honesty_os\n"
        f"_honesty_path = {path!r}\n"
        "if not any(_honesty_path.startswith(p) for p in "
        "('omniverse://','http://','https://','file://','anon:')):\n"
        "    if not _honesty_os.path.exists(_honesty_path):\n"
        f"        raise FileNotFoundError({tool_name!r} + ': asset not found: ' + {path!r})\n"
    )


def post_check_schema_applied_snippet(
    prim_path: str, schema: str, tool_name: str
) -> str:
    """Return code that raises if `schema` isn't in prim.GetAppliedSchemas() after apply."""
    return (
        "import omni.usd as _hon_post_usd\n"
        f"_hon_post_stage = _hon_post_usd.get_context().get_stage()\n"
        f"_hon_post_prim = _hon_post_stage.GetPrimAtPath({prim_path!r})\n"
        f"_hon_post_applied = list(_hon_post_prim.GetAppliedSchemas() or [])\n"
        f"if {schema!r} not in _hon_post_applied:\n"
        f"    raise RuntimeError(\n"
        f"        {tool_name!r} + ': schema ' + {schema!r} + ' not applied on ' + {prim_path!r} + "
        f"        ' — GetAppliedSchemas returned: ' + repr(_hon_post_applied)\n"
        f"    )\n"
    )


def post_check_prim_exists_snippet(prim_path: str, tool_name: str) -> str:
    """Post-check: verify a prim EXISTS (use after create/define operations)."""
    return (
        "import omni.usd as _hon_exists_usd\n"
        f"_hon_exists_stage = _hon_exists_usd.get_context().get_stage()\n"
        f"_hon_exists_prim = _hon_exists_stage.GetPrimAtPath({prim_path!r})\n"
        f"if not _hon_exists_prim.IsValid():\n"
        f"    raise RuntimeError({tool_name!r} + ': prim was expected at ' + {prim_path!r} + ' after the operation but is not in the stage')\n"
    )


def post_check_prim_absent_snippet(prim_path: str, tool_name: str) -> str:
    """Post-check: verify a prim does NOT exist (use after delete operations).

    stage.RemovePrim returns False silently on a missing path — pairing the
    delete with this post-check ensures the effect actually landed.
    """
    return (
        "import omni.usd as _hon_absent_usd\n"
        f"_hon_absent_stage = _hon_absent_usd.get_context().get_stage()\n"
        f"_hon_absent_prim = _hon_absent_stage.GetPrimAtPath({prim_path!r})\n"
        f"if _hon_absent_prim.IsValid():\n"
        f"    raise RuntimeError({tool_name!r} + ': prim at ' + {prim_path!r} + ' still exists after the operation — delete/remove silently failed')\n"
    )


def honesty_checked(
    *,
    require_prim_paths: Iterable[str] = (),
    require_files: Iterable[str] = (),
    post_schema_checks: Iterable[tuple] = (),  # (prim_arg, schema_arg) pairs
    post_exists_checks: Iterable[str] = (),     # arg keys expected to exist after
    post_absent_checks: Iterable[str] = (),     # arg keys expected to be gone after
) -> Callable:
    """Wrap a `_gen_*` handler, prepending pre-checks and appending post-checks.

    Args:
        require_prim_paths: arg-dict keys whose value should be a /World/...
            path that MUST resolve to a valid prim before the handler's
            own code runs.
        require_files: arg-dict keys whose value is a local filesystem path
            or URL; local paths must exist on disk.
        post_schema_checks: iterable of (prim_arg_key, schema_name) tuples.
            After the handler's code runs, we verify that the schema named
            `schema_name` is in GetAppliedSchemas on the prim at
            args[prim_arg_key]. If `schema_name` is itself a dict key
            rather than a literal, the caller should prefer manual
            post-checks — this wrapper keeps the literal case simple.
        post_exists_checks: arg keys whose value is a prim path expected to
            EXIST after the operation (use for create/define handlers).
        post_absent_checks: arg keys whose value is a prim path expected to
            be ABSENT after the operation (use for delete/remove handlers).
    """
    def _deco(gen_fn: Callable[[Dict], str]) -> Callable[[Dict], str]:
        @functools.wraps(gen_fn)
        def wrapper(args: Dict) -> str:
            tool_name = gen_fn.__name__.removeprefix("_gen_")
            pre_blocks = []
            for key in require_prim_paths:
                if key in args and args[key]:
                    pre_blocks.append(require_prim_exists_snippet(str(args[key]), tool_name))
            for key in require_files:
                if key in args and args[key]:
                    pre_blocks.append(require_file_exists_snippet(str(args[key]), tool_name))
            base = gen_fn(args)
            post_blocks = []
            for prim_key, schema_val in post_schema_checks:
                if prim_key in args and args[prim_key]:
                    schema = schema_val if isinstance(schema_val, str) else args.get(schema_val, "")
                    if schema:
                        post_blocks.append(
                            post_check_schema_applied_snippet(
                                str(args[prim_key]), str(schema), tool_name
                            )
                        )
            for key in post_exists_checks:
                if key in args and args[key]:
                    post_blocks.append(post_check_prim_exists_snippet(str(args[key]), tool_name))
            for key in post_absent_checks:
                if key in args and args[key]:
                    post_blocks.append(post_check_prim_absent_snippet(str(args[key]), tool_name))
            return "\n".join(pre_blocks) + ("\n" if pre_blocks else "") + base + (
                ("\n" + "\n".join(post_blocks)) if post_blocks else ""
            )
        return wrapper
    return _deco
