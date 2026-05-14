"""POSITIVE fixture for Q21 Section 19 honesty check.

This file contains 4 distinct violations that the audit MUST detect.
Each is annotated with `# AUDIT_EXPECT: ...` so the test harness can
verify the exact hits.
"""
from typing import Any, Dict


async def _handle_bare_return_none(args: Dict) -> Dict:  # AUDIT_EXPECT: handler-level violation
    """Returns bare None — honesty hole."""
    if not args:
        return None  # AUDIT_EXPECT: return None at this line


async def _handle_implicit_return(args: Dict) -> Dict:  # AUDIT_EXPECT: fall-through violation
    """Falls through without explicit return or raise."""
    pass  # AUDIT_EXPECT: no return statement


async def _handle_return_constant_none(args: Dict) -> Dict:
    """Returns ast.Constant(value=None) — honesty hole."""
    if args.get("skip"):
        return None  # AUDIT_EXPECT: return None at this line
    return {"success": True}


async def _handle_mixed_with_nested(args: Dict) -> Dict:
    """Direct return is OK; nested helper has bare return — must NOT flag.

    The audit's _direct_descendants helper should stop at the nested def
    and not see this bare return as a handler-level violation.
    """
    def _inner_helper(x):
        if x is None:
            return  # Inner-scope return; NOT a handler-level honesty hole
        return x * 2

    val = _inner_helper(args.get("x"))
    return {"success": True, "value": val}
