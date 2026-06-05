"""
canonical_instantiator.py
─────────────────────────
Hard-instantiate a verified canonical template (CP-N) by executing its
`code` field as a sequence of tool calls, bypassing the LLM tool-loop.

Why this exists: when a user prompt matches a canonical template with
high confidence (similarity > 0.85), the optimal path is plan-then-
execute — run the template's verified tool sequence directly. The
agent's role then collapses from "build + verify" to just "verify and
report". This eliminates iteration, keeps conversation history small,
sidesteps payload-driven Gemini 503 throttling, and gives deterministic
results for canonical task shapes.

The fallback path (agentic iteration with templates as guidance)
remains for prompts that don't strongly match any canonical.

Architecture:
  retrieve_templates_with_scores(prompt) → top match + similarity
  if similarity > threshold:
      execute_template_canonical(template) ← THIS MODULE
      → run code field in sandbox, intercept tool calls, dispatch via
        execute_tool_call. Return summary.
  else:
      normal LLM tool-loop with templates as few-shot

Sandbox safety: only standard builtins (enumerate, range, etc.) +
the registered tool functions are bound. No file I/O, network, or
Python imports are reachable from template code.

Template JSON format (workspace/templates/CP-NN.json):
  {
    "task_id":      "CP-NN",
    "goal":         "<one-paragraph user-facing description>",
    "tools_used":   ["create_prim", "robot_wizard", ...],
    "thoughts":     "<numbered list of non-obvious build specifics>",
    "code":         "<Python source executed in sandbox; only tool
                     calls + safe builtins are bound>",
    "settle_state": {
      "cubes":     {"/World/Cube_1": [x,y,z], ...},
      "conveyors": {"/World/ConveyorBelt": [vx,vy,vz], ...}
    },
    "verify_args":  { ... },   # form-gate input
    "simulate_args": { ... }   # function-gate input
  }

  - settle_state (preferred): explicit dict, source of truth for
    settle_after_canonical. Use this for any new template; required
    for templates with f-string-templated paths (regex can't parse
    those).
  - Templates predating settle_state fall back to regex extraction
    from the code field (works for simple create_prim/create_conveyor
    calls only). Migration is back-compat: add settle_state, leave
    code unchanged.

Optional fields: failure_modes, extension_notes, verified_status,
benchmark_vs_alternatives, blocked (when shipping is paused).
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


#: Tool names allowed to remain in the LLM schema after a hard-instantiate.
#: These are verify/inspect/fix tools — the agent uses them to confirm the
#: scaffolded scene works and to make targeted adjustments (teleport, set
#: gains/attrs) when verify reports issues. Build tools (create_*,
#: robot_wizard, setup_pick_place_controller, run_usd_script, etc.) are
#: deliberately excluded — the canonical already executed them.
ALLOWED_AFTER_INSTANTIATE = frozenset({
    # Form gate
    "verify_pickplace_pipeline",
    # Function gate
    "simulate_traversal_check",
    # Targeted fix tools (physics state adjustment without rebuilding)
    "teleport_prim",
    "set_attribute",
    "set_drive_gains",
    "set_joint_targets",
    "set_joint_limits",
    "set_physics_params",
    "set_physics_scene_config",
    # Inspection (read-only)
    "scene_summary",
    "list_all_prims",
    "find_prims_by_name",
    "find_prims_by_schema",
    "get_bounding_box",
    "get_articulation_state",
    "get_world_transform",
    "get_attribute",
    "list_attributes",
    "list_applied_schemas",
    "get_console_errors",
    "get_physics_errors",
    # Knowledge (allows agent to look up corrective recipes)
    "lookup_knowledge",
    "explain_error",
})


_SAFE_BUILTINS = {
    "enumerate": enumerate, "range": range, "len": len, "list": list,
    "dict": dict, "tuple": tuple, "set": set, "frozenset": frozenset,
    "str": str, "int": int, "float": float, "bool": bool, "bytes": bytes,
    "min": min, "max": max, "sum": sum, "abs": abs, "round": round,
    "sorted": sorted, "reversed": reversed, "zip": zip, "map": map, "filter": filter,
    "True": True, "False": False, "None": None,
    "print": print,  # diagnostic only
}


_SETTLE_CREATE_PAT = __import__("re").compile(
    r'create_prim\(\s*prim_path\s*=\s*([^,]+?),\s*[^)]*?position\s*=\s*\[([^\]]+)\]'
)
# Loop pattern for "for i, x in enumerate([..]): path = f'.../{i+1}'"
_SETTLE_ENUM_PAT = __import__("re").compile(
    r'for\s+i,\s*x\s+in\s+enumerate\(\[([^\]]+)\]\)\s*:\s*\n.*?path\s*=\s*f["\'](\S+?){i\s*\+\s*1}["\']\s*\n.*?create_prim\([^)]*?position\s*=\s*\[([^\]]+)\]',
    __import__("re").DOTALL,
)
_SETTLE_CONV_PAT = __import__("re").compile(
    r'create_conveyor\(\s*prim_path\s*=\s*["\'](/[^"\']+)["\'][^)]*?surface_velocity\s*=\s*\[([^\]]+)\]',
    __import__("re").DOTALL,
)


def _extract_cube_positions_from_code(code: str) -> Dict[str, List[float]]:
    """Pure helper: parse template `code` field and extract authored
    cube positions to restore during settle_after_canonical. Handles
    both simple `create_prim(prim_path=..., position=[...])` and the
    enumerate-loop pattern used by CP-01/CP-04."""
    cube_pos: Dict[str, List[float]] = {}
    if not code:
        return cube_pos

    # Detect simple loop: for i, x in enumerate([...]):
    for m in _SETTLE_ENUM_PAT.finditer(code):
        xs_str = m.group(1)
        path_prefix = m.group(2)
        pos_template = m.group(3)
        try:
            xs = [float(s.strip()) for s in xs_str.split(",")]
        except Exception:
            continue
        for i, xv in enumerate(xs, start=1):
            full_path = f"{path_prefix}{i}"
            try:
                pos_filled = []
                for tok in pos_template.split(","):
                    tok = tok.strip()
                    if tok == "x":
                        pos_filled.append(xv)
                    else:
                        pos_filled.append(float(tok))
                cube_pos[full_path] = pos_filled
            except Exception:
                pass

    # Match standalone create_prim calls
    for m in _SETTLE_CREATE_PAT.finditer(code):
        path_token = m.group(1).strip().strip("'\"")
        if "/" not in path_token or "{" in path_token:
            continue  # skip f-string templates that didn't substitute
        try:
            pos = [float(s.strip()) for s in m.group(2).split(",")]
            if path_token not in cube_pos:
                cube_pos[path_token] = pos
        except Exception:
            continue
    return cube_pos


def _extract_conveyor_velocities_from_code(code: str) -> Dict[str, List[float]]:
    """Pure helper: parse template `code` and extract authored conveyor
    surface_velocity values to restore during settle_after_canonical."""
    conv_vel: Dict[str, List[float]] = {}
    if not code:
        return conv_vel
    for m in _SETTLE_CONV_PAT.finditer(code):
        path = m.group(1)
        try:
            vel = [float(s.strip()) for s in m.group(2).split(",")]
            conv_vel[path] = vel
        except Exception:
            continue
    return conv_vel


async def settle_after_canonical(template: Dict[str, Any]) -> Dict[str, Any]:
    """After execute_template_canonical, controller install starts the
    timeline and may have already fired _pause_belt for cubes that entered
    reach. That mutates conveyor surface_velocity to (0,0,0) and drifts
    cube positions, which then makes cube_source_bridged in verify fail
    for compact scenes (e.g. CP-04 with cubes close to robot).

    This settle step:
      1. Stops the timeline
      2. Restores cube positions to their template-authored translate
      3. Restores surface velocities on conveyors to template-authored values

    Source of truth (preferred): template["settle_state"] explicit dict
    with shape {"cubes": {path: [x,y,z], ...}, "conveyors": {path: [vx,vy,vz], ...}}.
    Fall-back: regex extraction from template["code"] (works for simple
    create_prim/create_conveyor calls but breaks on f-string-templated
    paths like f"/World/Conv{i}" used in multi-station factories).

    Idempotent — running on a clean scene is a no-op.
    """
    from .tools import kit_tools

    settle_state = template.get("settle_state") or {}
    if settle_state and isinstance(settle_state, dict):
        cube_pos = dict(settle_state.get("cubes") or {})
        conv_vel = dict(settle_state.get("conveyors") or {})
        source = "settle_state"
    else:
        code = template.get("code") or ""
        if not code:
            return {"settled": False, "reason": "template has no code field nor settle_state"}
        cube_pos = _extract_cube_positions_from_code(code)
        conv_vel = _extract_conveyor_velocities_from_code(code)
        source = "regex"

    # Build the kit-side script: stop, restore translate + velocity
    import json as _j
    settle_code = f"""
import omni.usd, omni.timeline
from pxr import Gf
omni.timeline.get_timeline_interface().stop()
omni.timeline.get_timeline_interface().set_current_time(0.0)

stage = omni.usd.get_context().get_stage()
restored_cubes = []
for path, pos in {_j.dumps(cube_pos)}.items():
    p = stage.GetPrimAtPath(path)
    if p and p.IsValid():
        attr = p.GetAttribute('xformOp:translate')
        if attr and attr.IsValid():
            attr.Set(Gf.Vec3d(*pos))
            restored_cubes.append(path)

restored_conveyors = []
for path, vel in {_j.dumps(conv_vel)}.items():
    p = stage.GetPrimAtPath(path)
    if p and p.IsValid():
        attr = p.GetAttribute('physxSurfaceVelocity:surfaceVelocity')
        if attr and attr.IsValid():
            attr.Set(Gf.Vec3f(*vel))
            restored_conveyors.append(path)

import json
print(json.dumps({{
    'restored_cubes': restored_cubes,
    'restored_conveyors': restored_conveyors,
}}))
"""
    res = await kit_tools.exec_sync(settle_code, timeout=10)
    if not res.get("success"):
        return {"settled": False, "error": (res.get("output") or "")[:200]}
    out = (res.get("output") or "").strip()
    parsed = None
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                import json as _jp
                parsed = _jp.loads(line); break
            except Exception:
                continue
    return {
        "settled": True,
        "source": source,
        "n_cubes_restored": len((parsed or {}).get("restored_cubes", [])),
        "n_conveyors_restored": len((parsed or {}).get("restored_conveyors", [])),
    }


async def execute_template_verify(template: Dict[str, Any]) -> Dict[str, Any]:
    """Run the canonical's verify_args (form gate) and parse the result.
    Called by the orchestrator AFTER execute_template_canonical, BEFORE the
    LLM call — so the LLM sees the verification outcome as ground truth in
    the prompt rather than having to call verify itself with maybe-wrong
    paths derived from the user's natural-language prompt."""
    from .tools.tool_executor import execute_tool_call

    verify_args = template.get("verify_args")
    if not verify_args:
        return {"executed": False, "reason": "template has no verify_args field"}

    try:
        res = await execute_tool_call("verify_pickplace_pipeline", verify_args)
    except Exception as e:
        return {"executed": False, "error": f"{type(e).__name__}: {e}"}

    if res.get("type") == "error":
        return {"executed": False, "error": res.get("error", "?")}

    out = (res.get("output") or "").strip()
    parsed = None
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                import json as _j
                parsed = _j.loads(line)
                break
            except Exception:
                continue

    if parsed is None:
        return {"executed": True, "parsed": None, "raw": out[:300]}

    return {
        "executed": True,
        "pipeline_ok": bool(parsed.get("pipeline_ok")),
        "issues": list(parsed.get("issues", [])),
        "stages": parsed.get("stages", []),
        "cube_source_bridged": parsed.get("cube_source_bridged"),
        "cube_source_note": parsed.get("cube_source_note"),
    }


def substitute_template_params(
    code: str,
    parameters: Dict[str, Any] | None,
    overrides: Dict[str, Any] | None = None,
) -> tuple[str, Dict[str, Any]]:
    """T2 (parameterized canonicals) — substitute {{name}} placeholders in
    a template's code field with values from parameters + overrides.

    Returns (substituted_code, effective_params). effective_params lists
    the values actually used (defaults + overrides) for transparency in
    the directive shown to the LLM.

    Backwards-compat: templates without `parameters` field or with no
    placeholders in `code` are returned unchanged.

    Future work: extract param values from user prompt via resolvers
    (resolve_count_vagueness for n_cubes, resolve_robot_class for
    robot_name, etc.). Today this just substitutes defaults.

    Substitution is unconditional string replacement. Values are
    str()-coerced. For lists/dicts, callers should serialize as JSON
    in the parameters field if they want literal Python syntax, e.g.,
    `"colors": "['red', 'blue']"` so substitution yields valid Python.
    """
    if not parameters:
        return code, {}
    eff = dict(parameters)
    if overrides:
        eff.update(overrides)
    if not code:
        return code, eff
    out = code
    for k, v in eff.items():
        placeholder = "{{" + str(k) + "}}"
        out = out.replace(placeholder, str(v))
    return out, eff


# ---------------------------------------------------------------------------
# Block 1B Step 18 — Role-based template substitution.
# Replaces hardcoded prim paths in CP-N templates with role placeholders.
# When LayoutSpec has no objects (text-only / canonical path), substitution
# uses the template's authored `role_defaults`. When ratified bindings come
# from LayoutSpec, substitution uses those.
# ---------------------------------------------------------------------------

_ROLE_INDEXED_PAT = __import__("re").compile(r"\{\{(\w+)\[(\d+)\]\.(\w+)\}\}")
_ROLE_DOTTED_PAT = __import__("re").compile(r"\{\{(\w+)\.(\w+)\}\}")

# Matches {{#each role.listfield}} ... {{/each}} blocks.
# Group 1 = role name, group 2 = field name (may be empty → role itself is the list).
_EACH_OPEN_PAT = __import__("re").compile(r"\{\{#each (\w+)(?:\.(\w+))?\}\}")
_EACH_CLOSE_PAT = __import__("re").compile(r"\{\{/each\}\}")
# Inside a block, {{this}} → whole item, {{this.field}} → item[field].
_THIS_FIELD_PAT = __import__("re").compile(r"\{\{this\.(\w+)\}\}")
_THIS_PAT = __import__("re").compile(r"\{\{this\}\}")


def _expand_each_blocks(template_str: str, role_defaults: Dict[str, Any]) -> str:
    """Expand {{#each role.list}}...{{/each}} blocks in *template_str*.

    Rules:
    - ``{{#each role.field}}`` iterates over ``role_defaults[role][field]``
      (must be a list).
    - ``{{#each role}}`` (no dot) iterates over ``role_defaults[role]``
      directly when it is a list.
    - Inside the block, ``{{this.field}}`` accesses the current item's field.
    - ``{{this}}`` formats the whole current item via _format_for_code.
    - Leading indentation of the ``{{#each ...}}`` line is preserved on each
      expanded line within the block.
    - Empty list → produces empty string (block body dropped entirely).
    - Nested ``{{#each}}`` blocks → raises ValueError (not supported).
    - Mismatched open/close tags → raises ValueError with approximate position.

    Returns the template string with all each-blocks expanded.
    """
    lines = template_str.split("\n")
    out_lines: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        open_m = _EACH_OPEN_PAT.search(line)
        if open_m is None:
            out_lines.append(line)
            i += 1
            continue

        # Found an opening tag — collect block lines until matching {{/each}}.
        role = open_m.group(1)
        field = open_m.group(2)  # may be None

        # Indentation = text before the {{#each tag on its line
        indent = line[: open_m.start()]

        # Resolve the list value from role_defaults.
        role_val = role_defaults.get(role)
        if field:
            if isinstance(role_val, dict):
                items = role_val.get(field, [])
            else:
                items = []
        else:
            items = role_val if isinstance(role_val, list) else []

        # Collect body lines (everything between open and close tags).
        block_lines: list[str] = []
        i += 1
        depth = 0  # track nested {{#each}} for rejection
        found_close = False
        while i < len(lines):
            inner = lines[i]
            if _EACH_OPEN_PAT.search(inner):
                depth += 1
                if depth > 0:
                    raise ValueError(
                        f"Nested {{{{#each}}}} blocks are not supported "
                        f"(found at line {i + 1})"
                    )
            if _EACH_CLOSE_PAT.search(inner):
                if depth == 0:
                    found_close = True
                    i += 1
                    break
                depth -= 1
            block_lines.append(inner)
            i += 1

        if not found_close:
            raise ValueError(
                f"Unmatched {{{{#each {role}{'.' + field if field else ''}}}}} "
                f"— no matching {{{{/each}}}} found"
            )

        # Expand: for each item, substitute {{this.field}} and {{this}} in body.
        for item in items:
            for body_line in block_lines:
                expanded = body_line
                if isinstance(item, dict):
                    def _sub_this_field(m: Any, _item: Any = item) -> str:
                        f = m.group(1)
                        if f not in _item:
                            return m.group(0)
                        return _format_for_code(_item[f])
                    expanded = _THIS_FIELD_PAT.sub(_sub_this_field, expanded)
                expanded = _THIS_PAT.sub(_format_for_code(item), expanded)
                # Apply block indentation to non-empty lines
                if expanded.strip():
                    out_lines.append(indent + expanded)
                else:
                    out_lines.append(expanded)

    return "\n".join(out_lines)


def _format_for_code(v: Any) -> str:
    """Format a python value as a literal usable inside tool-call args.

    Strings get quoted via repr (so quotes are preserved). Lists/tuples
    are rendered as bracket literals with elements recursively formatted.
    Numbers and booleans are emitted plain. Dicts use Python literal form.
    """
    if v is None:
        return "None"
    if isinstance(v, bool):
        return "True" if v else "False"
    if isinstance(v, str):
        return repr(v)
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(_format_for_code(x) for x in v) + "]"
    if isinstance(v, dict):
        return (
            "{"
            + ", ".join(
                f"{_format_for_code(k)}: {_format_for_code(val)}"
                for k, val in v.items()
            )
            + "}"
        )
    return repr(v)


def substitute_role_placeholders(
    code_template: str,
    role_defaults: Dict[str, Any],
) -> str:
    """Substitute {{role.field}} and {{role[N].field}} placeholders.

    role_defaults shape (example):
        {
          "primary_robot": {
            "path": "/World/Franka",
            "class": "franka_panda",
            "position": [0, 0, 0.75],
            "orientation": [0.7071068, 0, 0, 0.7071068],
          },
          "workpieces": [
            {"path": "/World/Cube_1", "position": [-1.4, 0.4, 0.835]},
            {"path": "/World/Cube_2", "position": [-1.15, 0.4, 0.835]},
            ...
          ],
        }

    Also expands {{#each role.list}}...{{/each}} blocks before applying
    scalar substitutions.  See _expand_each_blocks for full semantics.

    Placeholders for which no value is found pass through unchanged so
    failure modes are visible in the captured tool-call args.
    """
    if not code_template or not role_defaults:
        return code_template

    # Phase 1: expand loop blocks before scalar substitution.
    code_template = _expand_each_blocks(code_template, role_defaults)

    def _indexed(m):
        """Substitute ``{{role[N].field}}`` placeholders from a list-typed role spec."""
        role, idx_str, field = m.group(1), m.group(2), m.group(3)
        spec = role_defaults.get(role)
        if not isinstance(spec, list):
            return m.group(0)
        idx = int(idx_str)
        if idx < 0 or idx >= len(spec):
            return m.group(0)
        entry = spec[idx]
        if not isinstance(entry, dict) or field not in entry:
            return m.group(0)
        return _format_for_code(entry[field])

    def _dotted(m):
        """Substitute ``{{role.field}}`` placeholders from a dict-typed role spec."""
        role, field = m.group(1), m.group(2)
        spec = role_defaults.get(role)
        if not isinstance(spec, dict) or field not in spec:
            return m.group(0)
        return _format_for_code(spec[field])

    out = _ROLE_INDEXED_PAT.sub(_indexed, code_template)
    out = _ROLE_DOTTED_PAT.sub(_dotted, out)
    return out


def instantiate_role_based_code(
    template: Dict[str, Any],
    role_bindings: Dict[str, Any] | None = None,
) -> str:
    """Produce executable `code` from a role-based template.

    Behavior:
    - If template has `code_template`, substitute placeholders using
      role_bindings (preferred) or template["role_defaults"] (fallback).
    - If template has only legacy `code`, return that unchanged.

    role_bindings shape mirrors role_defaults (path/class/position/etc per role).
    During Block 1B, the canonical hard-instantiate path passes None and
    we use role_defaults. Block 2+ will pass ratified bindings sourced
    from a LayoutSpec.
    """
    code_template = template.get("code_template")
    if not code_template:
        return template.get("code", "")
    effective = role_bindings or template.get("role_defaults") or {}
    return substitute_role_placeholders(code_template, effective)


async def execute_template_canonical(
    template: Dict[str, Any],
    param_overrides: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Run a canonical template's `code` field as tool-call sequence.

    Returns:
        {
            "task_id": str,
            "n_calls": int,
            "executed": [{"tool": str, "ok": bool, "args_preview": str}, ...],
            "errors": [str, ...],
            "instantiated": bool,
        }
    """
    from .tools.tool_executor import (
        DATA_HANDLERS, CODE_GEN_HANDLERS, execute_tool_call,
    )

    task_id = template.get("task_id", "?")

    # Route to role-based path when template carries all three role fields.
    # Backward-compat: templates without code_template/roles/role_defaults fall
    # through to the legacy `code` field path below.
    if template.get("code_template") and template.get("roles") and template.get("role_defaults"):
        logger.debug(f"[CanonicalInst] {task_id} using role-based code_template path")
        raw_code = instantiate_role_based_code(template)
    else:
        raw_code = template.get("code") or ""

    if not raw_code.strip():
        return {
            "task_id": task_id, "n_calls": 0, "executed": [], "errors": ["empty code field"],
            "instantiated": False,
        }

    # T2 parameter substitution — `{{name}}` placeholders → values from
    # template's `parameters` field + caller's `param_overrides`.
    code, effective_params = substitute_template_params(
        raw_code, template.get("parameters"), param_overrides
    )

    captured: List[tuple] = []  # list of (tool_name, kwargs)

    def _make_capturer(tool_name: str):
        """Return a no-op callable that records calls into ``captured`` instead of executing them."""
        def _capture(**kwargs):
            """Record a sandboxed tool call without executing it."""
            captured.append((tool_name, dict(kwargs)))
        return _capture

    tool_names = set(DATA_HANDLERS.keys()) | set(CODE_GEN_HANDLERS.keys())
    sandbox: Dict[str, Any] = {"__builtins__": dict(_SAFE_BUILTINS)}
    for name in tool_names:
        sandbox[name] = _make_capturer(name)

    # Capture phase — exec the code in sandbox, intercept calls into `captured`.
    # Intentional: canonical templates ship Python code that must be executed
    # to discover the tool-call sequence. Sandbox restricts builtins to
    # _SAFE_BUILTINS and replaces every tool name with a capturer that records
    # rather than invokes. No untrusted user input reaches this exec.
    try:
        exec(compile(code, f"<{task_id}>", "exec"), sandbox)  # noqa: audit-Q9
    except Exception as e:
        logger.warning(f"[CanonicalInst] {task_id} sandbox exec failed: {e}")
        return {
            "task_id": task_id, "n_calls": 0, "executed": [],
            "errors": [f"sandbox exec failed: {type(e).__name__}: {e}"],
            "instantiated": False,
        }

    if not captured:
        return {
            "task_id": task_id, "n_calls": 0, "executed": [],
            "errors": ["no tool calls captured from code field"],
            "instantiated": False,
        }

    # Execute phase — actually invoke each captured call via execute_tool_call
    executed: List[Dict[str, Any]] = []
    errors: List[str] = []
    for tool_name, args in captured:
        try:
            result = await execute_tool_call(tool_name, args)
            rtype = result.get("type")
            ok = (rtype != "error") and (result.get("success") is not False)
            args_preview = ", ".join(f"{k}={v!r}" for k, v in list(args.items())[:2])[:80]
            executed.append({"tool": tool_name, "ok": ok, "args_preview": args_preview})
            if not ok:
                err_text = result.get("error") or (result.get("output") or "").strip()
                errors.append(f"{tool_name}: {str(err_text)[:200]}")
        except Exception as e:
            executed.append({"tool": tool_name, "ok": False, "args_preview": ""})
            errors.append(f"{tool_name} raised: {type(e).__name__}: {e}")

    n_ok = sum(1 for e in executed if e["ok"])
    logger.info(
        f"[CanonicalInst] {task_id} instantiated: "
        f"{n_ok}/{len(executed)} tool calls succeeded, {len(errors)} errors"
    )
    return {
        "task_id": task_id,
        "n_calls": len(executed),
        "n_ok": n_ok,
        "executed": executed,
        "errors": errors,
        "instantiated": True,
        "effective_params": effective_params,
    }


_PRIM_PATH_RE = __import__("re").compile(
    r"prim_path=['\"]([^'\"]+)['\"]|dest_path=['\"]([^'\"]+)['\"]|"
    r"sensor_path=['\"]([^'\"]+)['\"]|robot_path=['\"]([^'\"]+)['\"]"
)


def _extract_prim_paths(template: Dict[str, Any]) -> List[str]:
    """Pull prim paths from the template's code field so we can tell the
    LLM exactly which paths were created (no guessing from naming heuristics)."""
    code = template.get("code") or ""
    paths: List[str] = []
    seen: set = set()
    for m in _PRIM_PATH_RE.finditer(code):
        for g in m.groups():
            if g and g.startswith("/") and g not in seen:
                paths.append(g)
                seen.add(g)
    return paths


def format_instantiation_summary(
    result: Dict[str, Any],
    template: Dict[str, Any] = None,
    verify_result: Dict[str, Any] = None,
) -> str:
    """Strong directive system-prompt addendum — tells the LLM the scene is
    already built, lists FORBIDDEN tools, and enumerates the actual prim
    paths so the agent uses them verbatim instead of guessing."""
    if not result.get("instantiated"):
        return ""
    tid = result.get("task_id", "?")
    n_ok = result.get("n_ok", 0)
    n_total = result.get("n_calls", 0)
    err_count = len(result.get("errors", []))

    # Names of tools that built the scene — agent must NOT call them again.
    forbidden = sorted({e["tool"] for e in result.get("executed", [])
                        if e.get("ok")})

    prim_paths = _extract_prim_paths(template) if template else []

    lines = [
        "## CRITICAL: Scene already built — do NOT rebuild",
        "",
        f"The scene has been pre-scaffolded from verified canonical template "
        f"**{tid}**. {n_ok}/{n_total} build tool calls executed successfully"
        + (f"; {err_count} returned errors." if err_count else "."),
        "",
    ]

    if prim_paths:
        lines.append("**Prims created (use these EXACT paths in your tool calls — do not invent variants):**")
        for p in prim_paths:
            lines.append(f"  - `{p}`")
        lines.append("")

    lines.append(
        "**Your role for this turn is VERIFICATION ONLY.** The build phase is "
        "complete. The LLM tool schema has been filtered to remove build tools "
        "— you literally cannot call them. Only verify/inspect/fix tools are "
        "available."
    )

    # If the orchestrator already pre-executed verify (recommended for
    # canonicals — eliminates LLM path-naming creativity), surface those
    # results directly so the LLM doesn't need to call verify itself.
    if verify_result and verify_result.get("executed"):
        lines.append("")
        lines.append("**Form-gate verification (already executed for you):**")
        if verify_result.get("pipeline_ok") is True:
            lines.append("  ✓ pipeline_ok = TRUE — all reach + bridge + controller checks passed")
        elif verify_result.get("pipeline_ok") is False:
            lines.append("  ✗ pipeline_ok = FALSE")
            issues = verify_result.get("issues", [])
            if issues:
                lines.append(f"  Issues ({len(issues)}):")
                for issue in issues[:8]:
                    lines.append(f"    - {issue}")
        bridged = verify_result.get("cube_source_bridged")
        if bridged is not None:
            lines.append(f"  cube_source_bridged: {bridged}"
                         + (f" — {verify_result.get('cube_source_note', '')}"
                            if verify_result.get("cube_source_note") else ""))
        lines.append("")
        lines.append(
            "**Your task this turn:** report the form-gate result above to the "
            "user in plain Swedish/English (whichever they used), then call "
            "`simulate_traversal_check` (function gate) using the args from "
            "this template's `simulate_args` field if you want to confirm the "
            "cube actually arrives. After simulate returns, write the final "
            "summary. Do NOT call verify_pickplace_pipeline again — it has "
            "already been called for you. Do NOT use any build tools."
        )

    # Always show the canonical's prescribed args as literal JSON — this is
    # the source-of-truth, even when verify is pre-executed (so the LLM has
    # the exact prim paths to reference + can call simulate verbatim).
    if template:
        simulate_args = template.get("simulate_args")
        verify_args = template.get("verify_args")
        if verify_args or simulate_args:
            import json as _j
            lines.append("")
            lines.append("**Canonical tool-call args (use these EXACT args, do not invent new paths):**")
            if verify_args:
                lines.append("")
                lines.append("verify_pickplace_pipeline (already called above, shown for path reference):")
                lines.append("```json")
                lines.append(_j.dumps(verify_args, indent=2))
                lines.append("```")
            if simulate_args:
                lines.append("")
                lines.append("simulate_traversal_check (call this if not already verified by simulation):")
                lines.append("```json")
                lines.append(_j.dumps(simulate_args, indent=2))
                lines.append("```")

    if err_count:
        lines.append("")
        lines.append("Errors during instantiation (do not retry the same call; "
                     "if these block verification, surface them honestly):")
        for err in result.get("errors", [])[:5]:
            lines.append(f"  - {err}")

    lines.extend([
        "",
        "**Required tool calls for this turn (in this order):**",
        "  1. `verify_pickplace_pipeline` — form gate. Pass stages list "
        "(robot_path, pick_path, place_path) inferred from the scene.",
        "  2. `simulate_traversal_check` — function gate. Pass cube_path + "
        "target_path; default duration_s=60s.",
        "  3. Reply with the results, honestly. If either gate fails, "
        "surface the specific issue and propose a targeted fix using "
        "tools like `teleport_prim`, `set_attribute`, or `set_drive_gains` "
        "— do NOT re-run any of the forbidden build tools.",
    ])
    return "\n".join(lines)
