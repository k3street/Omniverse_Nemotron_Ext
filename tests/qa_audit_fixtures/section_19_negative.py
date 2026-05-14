"""NEGATIVE fixture for Q21 — all handlers here MUST NOT trigger.

Demonstrates the legitimate patterns the audit must allow:
- Explicit dict return on every path
- raise on error path
- Nested helpers with bare returns (their returns belong to themselves)
- Async with conditional dict returns
"""
from typing import Any, Dict


async def _handle_explicit_dict_return(args: Dict) -> Dict:
    """Every path returns a dict."""
    if not args.get("name"):
        return {"success": False, "error": "name required"}
    return {"success": True, "name": args["name"]}


async def _handle_raise_on_error(args: Dict) -> Dict:
    """Error path raises NotImplementedError, success returns dict."""
    if args.get("unsupported"):
        raise NotImplementedError("Unsupported mode")
    return {"success": True}


async def _handle_with_nested_helpers(args: Dict) -> Dict:
    """Nested helpers may have bare returns — they own their own scope."""

    def _filter(item):
        if item is None:
            return  # Bare return in inner def — must NOT be flagged
        return item.upper()

    def _another(x):
        return  # Bare return in another inner def — must NOT be flagged

    items = [_filter(x) for x in args.get("items", [])]
    return {"success": True, "filtered": [i for i in items if i]}


async def _handle_with_lambda(args: Dict) -> Dict:
    """Lambda returns are also out-of-scope for handler-level audit."""
    fn = lambda x: None  # Lambda body — not a handler return
    return {"success": True, "result": fn(args)}
