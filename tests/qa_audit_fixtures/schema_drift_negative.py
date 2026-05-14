"""NEGATIVE fixture for Q14 — demonstrates all binding patterns the
audit must recognise. None of these tool names should be flagged as
orphan even though they're not declared via the default dispatch.

Patterns demonstrated:
1. `def _handle_X` — standard handler
2. `def _gen_X` — code-generator handler
3. `def handle_X` — ROS2 MCP-style (no underscore prefix)
4. `if tool_name == "X":` — case-dispatch
5. `codegen["alias_X"] = _gen_anything` — alias binding
6. `data["alias_Y"] = _handle_anything` — alias binding
"""
from typing import Any, Dict


# Pattern 1: standard handler — Q14 must recognise this name.
async def _handle_pattern_one(args: Dict[str, Any]) -> Dict[str, Any]:
    return {"success": True}


# Pattern 2: code generator
def _gen_pattern_two(args: Dict) -> str:
    return "generated code"


# Pattern 3: ROS2-style (no underscore prefix)
async def handle_pattern_three(args: Dict[str, Any]) -> Dict[str, Any]:
    return {"success": True}


# Pattern 4: case-dispatch (simulated)
def dispatch_pattern_four(tool_name: str):
    if tool_name == "pattern_four":  # Q14 should pick this up
        return {"success": True}
    return {"success": False, "error": "unknown"}


# Pattern 5+6: dict alias bindings (simulated)
def register_aliases():
    codegen = {}
    data = {}
    codegen["alias_pattern_five"] = _gen_pattern_two  # Q14 should pick this up
    data["alias_pattern_six"] = _handle_pattern_one   # Q14 should pick this up
    return codegen, data
