"""Phase 10 — Generator for handlers/_models.py.

Reads `service/isaac_assist_service/chat/tools/tool_schemas.py` and emits
`service/isaac_assist_service/chat/tools/handlers/_models.py` with one
Pydantic input model per tool.

Mode: permissive. Optional fields are typed `Optional[<type>]` with
default `None`; loose `{"type": "object"}` parameters become
`Dict[str, Any]`; `anyOf`/`oneOf` collapse to `Any`. Handlers can adopt
the models incrementally (Phase 10 spec note: "generate a permissive
model and tighten over time").

This generator is invoked manually for now; Phase 17's
`regen_models_check.py` lint will detect when `_models.py` falls
behind `tool_schemas.py`.

Usage:
    python scripts/gen_handler_models.py        # writes _models.py
    python scripts/gen_handler_models.py --dry  # prints to stdout

Per spec/IA_FULL_SPEC_2026-05-10.md Phase 10.
"""
from __future__ import annotations

import argparse
import ast
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_REPO_ROOT = Path(__file__).parent.parent
_SCHEMAS_PATH = (
    _REPO_ROOT
    / "service"
    / "isaac_assist_service"
    / "chat"
    / "tools"
    / "tool_schemas.py"
)
_MODELS_PATH = (
    _REPO_ROOT
    / "service"
    / "isaac_assist_service"
    / "chat"
    / "tools"
    / "handlers"
    / "_models.py"
)


# ---------------------------------------------------------------------------
# Schema parsing


def _load_tool_list() -> List[Dict[str, Any]]:
    """AST-parse tool_schemas.py to extract ISAAC_SIM_TOOLS without
    importing it (avoids heavy module import side-effects).
    """
    text = _SCHEMAS_PATH.read_text()
    tree = ast.parse(text, filename=str(_SCHEMAS_PATH))
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "ISAAC_SIM_TOOLS":
                    return ast.literal_eval(node.value)
    raise RuntimeError("ISAAC_SIM_TOOLS not found in tool_schemas.py")


# ---------------------------------------------------------------------------
# Naming helpers


_RESERVED_PY_KEYWORDS = {
    "class", "def", "for", "if", "in", "is", "lambda", "not", "or",
    "pass", "return", "while", "with", "yield", "True", "False", "None",
    "from", "import", "global", "raise", "try", "except", "finally",
    "as", "async", "await",
    # Pydantic v1 BaseModel methods that field names would shadow
    "schema", "dict", "json", "copy", "construct", "validate",
    "parse_obj", "parse_raw", "parse_file", "from_orm", "model_dump",
    "model_dump_json", "model_validate", "model_config", "model_fields",
}


def to_camel(snake: str) -> str:
    """`create_prim` → `CreatePrim`."""
    return "".join(part.capitalize() for part in snake.split("_") if part)


def sanitize_field_name(name: str) -> Tuple[str, Optional[str]]:
    """Return (py_safe_name, alias_or_None) for a JSON-schema property.

    If the JSON name is a Python keyword or starts with a digit, the
    Pydantic field gets a `_` suffix/prefix and an explicit `alias=`.
    """
    if name in _RESERVED_PY_KEYWORDS:
        return f"{name}_", name
    if name[0].isdigit():
        return f"_{name}", name
    if not re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]*", name):
        # Punctuation/hyphens — sanitize and alias
        py_name = re.sub(r"[^a-zA-Z0-9_]", "_", name)
        return py_name, name
    return name, None


# ---------------------------------------------------------------------------
# JSON-schema → Python type


def jsonschema_to_type(prop: Dict[str, Any]) -> str:
    """Map a JSON-schema property to a Python type annotation string.

    Returns a string suitable for `<py_type>` in a Pydantic field. Always
    permissive — unknown shapes fall back to `Any`.
    """
    if not isinstance(prop, dict):
        return "Any"
    t = prop.get("type")
    if isinstance(t, list):
        # Mixed types (e.g. ["string", "null"]) — collapse to Any
        return "Any"
    if t == "string":
        if "enum" in prop:
            return "str"  # could emit Literal[...] but stay permissive
        return "str"
    if t == "integer":
        return "int"
    if t == "number":
        return "float"
    if t == "boolean":
        return "bool"
    if t == "array":
        item = prop.get("items")
        if isinstance(item, dict):
            inner = jsonschema_to_type(item)
        else:
            inner = "Any"
        return f"List[{inner}]"
    if t == "object":
        # Could recurse but keep flat for permissive mode
        return "Dict[str, Any]"
    if "anyOf" in prop or "oneOf" in prop:
        return "Any"
    return "Any"


# ---------------------------------------------------------------------------
# Model rendering


def render_field(prop_name: str, prop_schema: Dict[str, Any], required: bool) -> str:
    """Render one Pydantic field line.

    Required → `name: Type = Field(..., description="...")` (or just `name: Type`)
    Optional → `name: Optional[Type] = Field(None, description="...")`
    """
    py_name, alias = sanitize_field_name(prop_name)
    py_type = jsonschema_to_type(prop_schema)
    description = prop_schema.get("description", "")
    # Escape description for safe embedding
    desc_escaped = description.replace("\\", "\\\\").replace('"', '\\"')[:200]
    # Build Field() args
    field_args: List[str] = []
    if not required:
        py_type = f"Optional[{py_type}]"
        field_args.append("None")
    else:
        field_args.append("...")
    if alias:
        field_args.append(f'alias="{alias}"')
    if desc_escaped:
        field_args.append(f'description="{desc_escaped}"')
    if len(field_args) == 1 and field_args[0] == "...":
        # Pure required field with no extras — bare annotation
        return f"    {py_name}: {py_type}"
    return f"    {py_name}: {py_type} = Field({', '.join(field_args)})"


def render_model(tool: Dict[str, Any]) -> str:
    """Render one Pydantic input model for one tool."""
    fn = tool.get("function", {})
    name = fn.get("name", "")
    description = fn.get("description", "").replace("\\", "\\\\").replace('"', '\\"')
    params = fn.get("parameters") or {}
    props = params.get("properties") or {}
    required = set(params.get("required") or [])

    class_name = f"{to_camel(name)}Args"
    lines = [
        f"class {class_name}(BaseModel):",
        f'    """{description[:200].strip() or name}"""',
        "    model_config = ConfigDict(populate_by_name=True, extra='allow')",
        "",
    ]
    if not props:
        lines.append("    pass")
        return "\n".join(lines)
    # Required fields first for readability
    for prop_name in list(props):
        if prop_name in required:
            lines.append(render_field(prop_name, props[prop_name], required=True))
    for prop_name in list(props):
        if prop_name not in required:
            lines.append(render_field(prop_name, props[prop_name], required=False))
    return "\n".join(lines)


def render_models_module(tools: List[Dict[str, Any]]) -> str:
    """Render the full `_models.py` content."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    header = f'''"""Auto-generated Pydantic input models — DO NOT EDIT.

Generated by `scripts/gen_handler_models.py` from
`service/isaac_assist_service/chat/tools/tool_schemas.py`.

To regenerate after editing tool_schemas.py:
    python scripts/gen_handler_models.py

Mode: permissive (Phase 10 spec note: "generate a permissive model
and tighten over time"). Unknown property shapes fall back to `Any`;
mixed-type unions (anyOf/oneOf) collapse to `Any`; `extra="allow"`
on every model so unrecognised keys do not 400.

Generated: {now}
Tool count: {len(tools)}

Per spec/IA_FULL_SPEC_2026-05-10.md Phase 10.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

# ruff: noqa: E501
# Names with description-line lengths above the project limit are
# expected here — the schema descriptions are intentionally verbose.
'''
    body_blocks: List[str] = []
    seen: set[str] = set()
    for tool in tools:
        fn = tool.get("function", {})
        name = fn.get("name", "")
        if not name:
            continue
        cls = f"{to_camel(name)}Args"
        if cls in seen:
            continue  # Skip duplicates (defensive)
        seen.add(cls)
        body_blocks.append(render_model(tool))
    body = "\n\n\n".join(body_blocks)

    # Build the registry mapping tool-name → model-class
    registry_lines = ["", "", "# ---------------------------------------------------------------------------",
                      "# Tool-name → model-class lookup", "", "MODEL_REGISTRY = {"]
    for tool in tools:
        fn = tool.get("function", {})
        name = fn.get("name", "")
        if not name:
            continue
        cls = f"{to_camel(name)}Args"
        registry_lines.append(f'    "{name}": {cls},')
    registry_lines.append("}")
    return header + "\n\n" + body + "\n" + "\n".join(registry_lines) + "\n"


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry", action="store_true",
                        help="Print to stdout instead of writing _models.py")
    args = parser.parse_args(argv)

    tools = _load_tool_list()
    content = render_models_module(tools)

    if args.dry:
        print(content)
        return 0

    _MODELS_PATH.parent.mkdir(parents=True, exist_ok=True)
    _MODELS_PATH.write_text(content)
    print(f"Wrote {_MODELS_PATH} ({len(tools)} models, {len(content)} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
