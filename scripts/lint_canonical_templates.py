#!/usr/bin/env python3
"""
lint_canonical_templates.py — Conformance lint for workspace/templates/*.json

Usage:
    python scripts/lint_canonical_templates.py [--strict] [--fix] [--json] [TEMPLATE_PATHS...]
    python scripts/lint_canonical_templates.py --validate-tool-calls [TEMPLATE_PATHS...]

Options:
    --strict               Exit 1 if any ERROR is found (default: exit 0 unless parse failure)
    --fix                  Auto-fix safe, mechanical issues (e.g. add default verified_status for CP-*
                           templates missing it). Never touches code field content.
    --json                 Machine-readable JSON output
    --validate-tool-calls  Also AST-parse the 'code' field and validate each tool call's
                           kwargs against the corresponding Pydantic model in _models.py.
    --strict-tool-calls    Also validate 'code_template' (template vars replaced with string
                           placeholders before parse). Implies --validate-tool-calls.

Exit codes:
    0  No errors (WARNs and INFOs are OK)
    1  At least one ERROR found (or --strict + any WARN)
    2  JSON parse failure in at least one template

Reads the schema from scripts/canonical_schema.py (importable module).
"""

import argparse
import ast
import json
import re
import sys
from pathlib import Path

# Allow running from project root or scripts/ directory
_HERE = Path(__file__).parent
sys.path.insert(0, str(_HERE))

import canonical_schema as schema  # noqa: E402


# ── Project-root relative defaults ──────────────────────────────────────────

_REPO_ROOT = _HERE.parent
_TEMPLATES_DIR = _REPO_ROOT / "workspace" / "templates"


# ── Issue dataclass ──────────────────────────────────────────────────────────

class Issue:
    __slots__ = ("level", "rule", "message", "fixable")

    def __init__(self, level: str, rule: str, message: str, fixable: bool = False):
        self.level = level      # "ERROR" | "WARN" | "INFO"
        self.rule = rule        # short rule code, e.g. "C1_MISSING_CORE"
        self.message = message  # human-readable description
        self.fixable = fixable  # whether --fix can address this

    def as_dict(self):
        return {
            "level": self.level,
            "rule": self.rule,
            "message": self.message,
            "fixable": self.fixable,
        }


# ── Tool-call validation (--validate-tool-calls) ─────────────────────────────

# Regex that matches Jinja-style template variables: {{...}}
# Used to sanitize code_template before AST parsing.
_TEMPLATE_VAR_RE = re.compile(r"\{\{[^}]*\}\}")

# Python built-ins and common stdlib / method names that appear in template
# code but are NOT isaac_assist tools.  Names containing underscores that are
# in this set are silently skipped during TC_UNKNOWN_TOOL filtering.
_PYTHON_BUILTINS: frozenset[str] = frozenset({
    # built-in functions
    "abs", "all", "any", "bin", "bool", "breakpoint", "bytearray", "bytes",
    "callable", "chr", "compile", "complex", "delattr", "dict", "dir",
    "divmod", "enumerate", "eval", "exec", "filter", "float", "format",
    "frozenset", "getattr", "globals", "hasattr", "hash", "help", "hex",
    "id", "input", "int", "isinstance", "issubclass", "iter", "len",
    "list", "locals", "map", "max", "memoryview", "min", "next", "object",
    "oct", "open", "ord", "pow", "print", "property", "range", "repr",
    "reversed", "round", "set", "setattr", "slice", "sorted", "staticmethod",
    "str", "sum", "super", "tuple", "type", "vars", "zip",
    # common math / stdlib
    "math", "radians", "degrees", "sin", "cos", "tan", "sqrt", "ceil",
    "floor", "log", "exp", "pi", "inf",
    # common method names (called on objects, e.g. list.append)
    "append", "extend", "insert", "remove", "pop", "clear", "sort",
    "reverse", "copy", "update", "get", "items", "keys", "values",
    "format_map", "join", "split", "strip", "lower", "upper",
    # common Isaac-Sim helper patterns that are NOT tools
    "get_stage", "get_context", "get_prim_at_path",
    # type-annotation helpers
    "Optional", "List", "Dict", "Tuple", "Union", "Any",
})

# Sentinel string used to replace template vars so string literals parse cleanly.
_TEMPLATE_SENTINEL = '"__TEMPLATE_VAR__"'


def _sanitize_template_vars(code: str) -> str:
    """Replace ``{{var.field}}`` placeholders with a string literal sentinel."""
    return _TEMPLATE_VAR_RE.sub(_TEMPLATE_SENTINEL, code)


def _extract_tool_calls(code: str) -> "list[tuple[str, set[str], bool, int, ast.Call]]":
    """
    AST-parse *code* and return all top-level and nested tool calls.

    Returns a list of (tool_name, kwargs_set, has_star_kwargs, lineno, call_node).
    Only calls whose function name matches a known tool in the model map are
    included; unrecognised function names are reported separately.

    ``has_star_kwargs`` is True if the call contains ``**kwargs`` unpacking —
    in that case required-field checks are skipped (dynamic dispatch).
    ``call_node`` is the raw ``ast.Call`` node for deeper argument inspection.
    """
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return []

    results = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name):
            fn = node.func.id
        elif isinstance(node.func, ast.Attribute):
            fn = node.func.attr
        else:
            continue

        kwargs = {kw.arg for kw in node.keywords if kw.arg is not None}
        has_star = any(kw.arg is None for kw in node.keywords)
        results.append((fn, kwargs, has_star, node.lineno, node))
    return results


def _extract_kw_ast_values(
    call_node: "ast.Call",
) -> "dict[str, ast.expr]":
    """Return a mapping from kwarg name → AST value node for a call node."""
    result = {}
    for kw in call_node.keywords:
        if kw.arg is not None:
            result[kw.arg] = kw.value
    return result


def _ast_string_value(node: "ast.expr") -> "str | None":
    """If *node* is a string literal AST node, return the string; else None."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _ast_dict_keys(node: "ast.expr") -> "list[str] | None":
    """
    If *node* is a dict literal with all string-constant keys, return those
    key strings.  Returns None if node is not a dict literal or has
    non-constant keys (e.g. variable-key dicts).
    """
    if not isinstance(node, ast.Dict):
        return None
    keys = []
    for k in node.keys:
        s = _ast_string_value(k)
        if s is None:
            return None  # non-constant key — skip validation
        keys.append(s)
    return keys


def _ast_list_of_dicts_keys(node: "ast.expr") -> "list[list[str]] | None":
    """
    If *node* is a list literal (or list comp) containing at least one dict
    literal with all-constant string keys, return a list-of-lists of those
    key sets.  Returns None if node is not a list literal.
    Skips non-dict items and dicts with non-constant keys.
    """
    if not isinstance(node, ast.List):
        return None
    result = []
    for elt in node.elts:
        keys = _ast_dict_keys(elt)
        if keys is not None:
            result.append(keys)
    return result if result else None


def lint_tool_calls(
    path: Path,
    data: dict,
    include_code_template: bool = False,
) -> "list[Issue]":
    """
    Validate tool call kwargs in the template ``code`` (and optionally
    ``code_template``) fields against the Pydantic models in _models.py
    and the JSONSchema definitions in tool_schemas.py.

    Rule codes emitted:
      TC_MODEL_UNAVAILABLE   WARN  — _models.py could not be imported; skipping
      TC_SYNTAX_ERROR        WARN  — code field has a SyntaxError; cannot parse
      TC_UNKNOWN_TOOL        WARN  — tool name not found in model map
      TC_REQUIRED_MISSING    ERROR — required field(s) absent from call
      TC_UNKNOWN_KWARG       WARN  — kwarg not in model (extra='allow' absorbed it)
      TC_BAD_ENUM_VALUE      ERROR — string literal does not match the JSONSchema enum
      TC_BAD_NESTED_KEY      ERROR — dict literal contains key(s) not in JSONSchema properties
      TC_BAD_NESTED_ITEM     ERROR — list-of-dicts item is missing JSONSchema items.required key(s)

    Returns a list of Issue objects.
    """
    issues: list[Issue] = []

    def err(rule: str, msg: str) -> None:
        issues.append(Issue("ERROR", rule, msg))

    def warn(rule: str, msg: str) -> None:
        issues.append(Issue("WARN", rule, msg))

    # Obtain model map — lazy import via canonical_schema helper
    tool_map = schema.get_tool_model_map()
    if not tool_map:
        warn(
            "TC_MODEL_UNAVAILABLE",
            "Tool model map is empty (could not import _models.py); "
            "skipping tool-call validation",
        )
        return issues

    # Obtain JSONSchema map for enum / nested-key / nested-item checks
    jsonschema_map = schema.get_tool_jsonschema_map()

    fields_to_check: list[tuple[str, str]] = [("code", "code")]
    if include_code_template:
        fields_to_check.append(("code_template", "code_template"))

    for field_name, display_name in fields_to_check:
        raw_code = data.get(field_name)
        if not raw_code or not isinstance(raw_code, str):
            continue

        # For code_template: sanitize Jinja-style placeholders
        is_template = field_name == "code_template"
        code = _sanitize_template_vars(raw_code) if is_template else raw_code

        # Attempt AST parse
        try:
            ast.parse(code)
        except SyntaxError as exc:
            warn(
                "TC_SYNTAX_ERROR",
                f"{display_name}: SyntaxError at line {exc.lineno} — "
                f"{exc.msg}; tool-call validation skipped for this field",
            )
            continue

        call_sites = _extract_tool_calls(code)

        # Collect calls whose function name IS in the model map
        # (unknown-name calls get WARN but no ERROR)
        seen_unknown: set[str] = set()
        for fn, kwargs, has_star, lineno, call_node in call_sites:
            if fn not in tool_map:
                # Only warn once per unknown tool name per field
                if fn not in seen_unknown:
                    seen_unknown.add(fn)
                    # Skip Python builtins, common stdlib functions, and
                    # typical method names that appear in template code.
                    # Only warn on names that look like isaac_assist tools
                    # (must contain an underscore to distinguish from builtins).
                    if fn not in _PYTHON_BUILTINS and "_" in fn:
                        warn(
                            "TC_UNKNOWN_TOOL",
                            f"{display_name}:line {lineno} — "
                            f"'{fn}' not found in tool model map; "
                            "cannot validate kwargs",
                        )
                continue

            model = tool_map[fn]
            model_fields = model.model_fields  # type: ignore[attr-defined]
            required_fields = {
                k for k, v in model_fields.items() if v.is_required()
            }
            known_fields = set(model_fields.keys())

            # Skip required-field check when **kwargs unpacking is present
            if not has_star:
                missing = sorted(required_fields - kwargs)
                if missing:
                    err(
                        "TC_REQUIRED_MISSING",
                        f"{display_name}:line {lineno} — "
                        f"{fn} missing required: {missing}",
                    )

            # Unknown kwargs: always report (even with extra='allow')
            unknown = sorted(kwargs - known_fields)
            if unknown:
                # In code_template, template sentinel values become strings —
                # the KWARG itself may still be a legitimate field that just
                # receives a template-var value. Flag as WARN not ERROR.
                warn(
                    "TC_UNKNOWN_KWARG",
                    f"{display_name}:line {lineno} — "
                    f"{fn} unknown kwargs: {unknown}",
                )

            # ── JSONSchema-based deep checks ─────────────────────��────────────
            # Only run when JSONSchema is available for this tool.
            param_schemas = jsonschema_map.get(fn)
            if not param_schemas:
                continue

            kw_values = _extract_kw_ast_values(call_node)

            for param_name, value_node in kw_values.items():
                param_schema = param_schemas.get(param_name)
                if not param_schema:
                    continue

                # ── TC_BAD_ENUM_VALUE ─────────────��───────────────────────────
                # When param schema has an 'enum' list and the call passes a
                # string literal, verify the literal is in the enum.
                enum_values = param_schema.get("enum")
                if enum_values and isinstance(enum_values, list):
                    literal = _ast_string_value(value_node)
                    if literal is not None and literal not in enum_values:
                        err(
                            "TC_BAD_ENUM_VALUE",
                            f"{display_name}:line {lineno} — "
                            f"{fn}.{param_name}={literal!r} is not in the "
                            f"allowed enum: {enum_values}",
                        )

                # ── TC_BAD_NESTED_KEY ─────────────────────────��───────────────
                # When param schema has 'properties' (object type) and the call
                # passes a dict literal, verify keys are in properties.
                prop_schema = param_schema.get("properties")
                if prop_schema and isinstance(prop_schema, dict):
                    # Only applies when param_schema type is "object" or implied.
                    dict_keys = _ast_dict_keys(value_node)
                    if dict_keys is not None:
                        allowed_keys = set(prop_schema.keys())
                        bad_keys = sorted(set(dict_keys) - allowed_keys)
                        if bad_keys:
                            err(
                                "TC_BAD_NESTED_KEY",
                                f"{display_name}:line {lineno} — "
                                f"{fn}.{param_name} dict has unrecognised "
                                f"key(s) {bad_keys}; allowed: "
                                f"{sorted(allowed_keys)}",
                            )

                # ── TC_BAD_NESTED_ITEM ──────────────────────────────────���─────
                # When param schema type is "array" with items.required, and
                # the call passes a list literal of dicts, verify each dict
                # contains the required item keys.
                items_schema = param_schema.get("items")
                if items_schema and isinstance(items_schema, dict):
                    items_required = items_schema.get("required")
                    if items_required and isinstance(items_required, list):
                        list_of_key_lists = _ast_list_of_dicts_keys(value_node)
                        if list_of_key_lists is not None:
                            for idx, item_keys in enumerate(list_of_key_lists):
                                item_keys_set = set(item_keys)
                                missing_item = sorted(
                                    set(items_required) - item_keys_set
                                )
                                if missing_item:
                                    err(
                                        "TC_BAD_NESTED_ITEM",
                                        f"{display_name}:line {lineno} — "
                                        f"{fn}.{param_name}[{idx}] missing "
                                        f"required item key(s) {missing_item}; "
                                        f"required: {items_required}",
                                    )

    return issues


# ── Per-template lint logic ──────────────────────────────────────────────────

def lint_one(path: Path, data: dict) -> list[Issue]:
    """
    Validate a parsed template dict against the canonical schema.

    Returns a list of Issue objects. Does not modify `data`.
    """
    issues: list[Issue] = []

    def err(rule, msg, fixable=False):
        issues.append(Issue("ERROR", rule, msg, fixable))

    def warn(rule, msg, fixable=False):
        issues.append(Issue("WARN", rule, msg, fixable))

    def info(rule, msg):
        issues.append(Issue("INFO", rule, msg))

    task_id = data.get("task_id", "")
    is_cp = schema.is_cp_template(str(task_id))

    # ── C1: Core-6 fields must be present and non-empty ─────────────────────

    for field in schema.CORE_FIELDS:
        if field not in data:
            err("C1_MISSING_CORE_FIELD", f"Missing mandatory field: {field!r}")
        elif data[field] is None or data[field] == "" or data[field] == [] or data[field] == {}:
            err("C1_EMPTY_CORE_FIELD", f"Core field is present but empty: {field!r}")

    # ── C2: task_id must match filename stem ────────────────────────────────

    expected_tid = path.stem
    if str(task_id) != expected_tid:
        err("C2_TASK_ID_MISMATCH",
            f"task_id {task_id!r} does not match filename stem {expected_tid!r}")

    # ── C3: tools_used must be a non-empty list of non-empty strings ─────────

    tus = data.get("tools_used")
    if isinstance(tus, list):
        if not tus:
            err("C3_TOOLS_USED_EMPTY", "tools_used is an empty list; must have at least one tool")
        elif not all(isinstance(t, str) and t for t in tus):
            err("C3_TOOLS_USED_INVALID",
                "tools_used must contain only non-empty strings")
    # Missing already caught by C1.

    # ── C4: failure_modes must be a list of strings ──────────────────────────

    fms = data.get("failure_modes")
    if isinstance(fms, list):
        if not all(isinstance(f, str) for f in fms):
            err("C4_FAILURE_MODES_TYPE",
                "failure_modes must be a list of strings")
    # Missing already caught by C1.

    # ── DEP: deprecated field present ────────────────────────────────────────

    for field in data.keys():
        if schema.is_deprecated_field(field):
            # blocked is a legitimate infra-pause marker, not deprecated
            if field == "blocked":
                continue
            err("DEP_FIELD_PRESENT",
                f"Deprecated field {field!r} is present; migrate or remove it")

    # ── T1-only rules ────────────────────────────────────────────────────────

    if is_cp:

        # T1: mandatory T1 fields must be present
        for field in schema.T1_FIELDS:
            if field not in data:
                # verified_status special-case: CP-06 uses `blocked` instead
                if field == "verified_status" and "blocked" in data:
                    continue
                # diagnose_args special-case: CP-06 is blocked (same exception)
                if field == "diagnose_args" and "blocked" in data:
                    continue
                err("T1_MISSING_FIELD",
                    f"CP template is missing mandatory T1 field: {field!r}",
                    fixable=(field == "verified_status"))

        # T1_VA: verify_args structure check
        va = data.get("verify_args")
        if isinstance(va, dict):
            stages = va.get("stages")
            if stages is None:
                err("T1_VA_NO_STAGES",
                    "verify_args must have a 'stages' list (can be empty for plumbing-only templates)")
            elif isinstance(stages, list) and stages:
                for i, stage in enumerate(stages):
                    if not isinstance(stage, dict):
                        err("T1_VA_STAGE_TYPE",
                            f"verify_args.stages[{i}] must be a dict")
                        continue
                    for key in schema.VERIFY_ARGS_STAGE_KEYS:
                        if key not in stage:
                            err("T1_VA_STAGE_MISSING_KEY",
                                f"verify_args.stages[{i}] is missing required key {key!r}")

        # T1_SA: simulate_args structure check
        sa = data.get("simulate_args")
        if isinstance(sa, dict):
            for key in schema.SIMULATE_ARGS_REQUIRED_KEYS:
                if key not in sa:
                    err("T1_SA_MISSING_KEY",
                        f"simulate_args is missing required key {key!r}")
            # cube_path (single) OR cube_paths (multi) must be present
            if not any(k in sa for k in schema.SIMULATE_ARGS_CUBE_KEY_VARIANTS):
                err("T1_SA_MISSING_CUBE_KEY",
                    f"simulate_args must have 'cube_path' (string) or 'cube_paths' (list); neither found")

        # T1_EX: extends <-> extension_notes co-occurrence
        has_extends = "extends" in data and data["extends"]
        has_ext_notes = "extension_notes" in data and data["extension_notes"]
        if has_extends and not has_ext_notes:
            err("T1_EXTENDS_NO_NOTES",
                "'extends' is present but 'extension_notes' is absent; both must co-occur")
        if has_ext_notes and not has_extends:
            warn("T1_NOTES_NO_EXTENDS",
                 "'extension_notes' is present but 'extends' is absent")

        # T1_SS: settle_state recommended
        if "settle_state" not in data:
            warn("T1_MISSING_SETTLE_STATE",
                 "'settle_state' is absent; settle_after_canonical will use fragile regex fallback",
                 fixable=False)

        # T1_MC: motion_controllers compatibility tag
        # Required when the canonical performs motion planning/control; otherwise
        # INFO (encouraged but not blocking).
        mc = data.get("motion_controllers")
        uses_motion = schema.template_uses_motion_planning(data)
        if mc is None:
            if uses_motion:
                warn("T1_MC_MISSING",
                     "Template uses motion-planning tools but does not declare "
                     "'motion_controllers' compatibility; consumers can't filter by controller")
            else:
                info("T1_MC_MISSING_INFO",
                     "No 'motion_controllers' field; not visible to controller-filtered retrieval")
        elif not isinstance(mc, dict):
            err("T1_MC_TYPE", f"'motion_controllers' must be a dict, got {type(mc).__name__}")
        else:
            verified = mc.get("verified", [])
            failed = mc.get("failed", {})
            untested = mc.get("untested", [])
            if not isinstance(verified, list):
                err("T1_MC_VERIFIED_TYPE",
                    "motion_controllers.verified must be a list of controller names "
                    "(optionally with @version suffix)")
            else:
                for v in verified:
                    if not isinstance(v, str) or not v:
                        err("T1_MC_VERIFIED_ENTRY",
                            f"motion_controllers.verified entry {v!r} must be a non-empty string")
                        continue
                    name, _version = schema.parse_motion_controller_name(v)
                    if name not in schema.VALID_MOTION_CONTROLLER_NAMES:
                        warn("T1_MC_UNKNOWN_NAME",
                             f"motion_controllers.verified contains unknown controller "
                             f"{name!r}; expected one of {sorted(schema.VALID_MOTION_CONTROLLER_NAMES)}")
            if not isinstance(failed, dict):
                err("T1_MC_FAILED_TYPE",
                    "motion_controllers.failed must be a dict mapping controller name to reason")
            else:
                for k, reason in failed.items():
                    if not isinstance(k, str) or not k:
                        err("T1_MC_FAILED_KEY",
                            f"motion_controllers.failed key {k!r} must be a non-empty string")
                        continue
                    name, _ = schema.parse_motion_controller_name(k)
                    if name not in schema.VALID_MOTION_CONTROLLER_NAMES:
                        warn("T1_MC_UNKNOWN_NAME",
                             f"motion_controllers.failed contains unknown controller "
                             f"{name!r}; expected one of {sorted(schema.VALID_MOTION_CONTROLLER_NAMES)}")
                    if not isinstance(reason, str) or not reason:
                        err("T1_MC_FAILED_REASON",
                            f"motion_controllers.failed[{k!r}] must have a non-empty reason string")
            if not isinstance(untested, list):
                err("T1_MC_UNTESTED_TYPE",
                    "motion_controllers.untested must be a list of controller names")

        # ── Role-based field rules ────────────────────────────────────────────

        # R1: intent field - absence is INFO until migration completes
        intent = data.get("intent")
        if intent is None:
            info("R1_MISSING_INTENT",
                 "No 'intent' field; template not visible to structural-filter retrieval (migration pending)")
        elif isinstance(intent, dict):
            ph = intent.get("pattern_hint")
            if ph not in schema.VALID_PATTERN_HINTS:
                err("R1_BAD_PATTERN_HINT",
                    f"intent.pattern_hint {ph!r} is not valid; must be one of {sorted(schema.VALID_PATTERN_HINTS)}")
            tags = intent.get("structural_tags", [])
            if isinstance(tags, list):
                for tag in tags:
                    if not isinstance(tag, str) or not schema.STRUCTURAL_TAG_PATTERN.match(tag):
                        err("R1_BAD_STRUCTURAL_TAG",
                            f"structural_tag {tag!r} does not match pattern "
                            f"'(isaac|cad|user):segment[.subsegment]+'")
            sf = intent.get("structural_features")
            if isinstance(sf, dict):
                dk = sf.get("destination_kind")
                if dk is not None and dk not in schema.VALID_DESTINATION_KINDS:
                    err("R1_BAD_DESTINATION_KIND",
                        f"intent.structural_features.destination_kind {dk!r} is not valid; "
                        f"must be one of {sorted(schema.VALID_DESTINATION_KINDS)}")
                ra = sf.get("routing_axis")
                if ra is not None and ra not in schema.VALID_ROUTING_AXES:
                    err("R1_BAD_ROUTING_AXIS",
                        f"intent.structural_features.routing_axis {ra!r} is not valid; "
                        f"must be one of {sorted(schema.VALID_ROUTING_AXES)}")
        elif intent is not None:
            err("R1_INTENT_TYPE", f"'intent' must be a dict, got {type(intent).__name__}")

        # R2: roles / role_defaults / code_template must be all-or-nothing
        role_trio_present = [f for f in schema.ROLE_CORE_TRIO if data.get(f)]
        n_present = len(role_trio_present)
        if n_present > 0 and n_present < len(schema.ROLE_CORE_TRIO):
            missing = [f for f in schema.ROLE_CORE_TRIO if not data.get(f)]
            err("R2_PARTIAL_ROLE_FIELDS",
                f"roles/role_defaults/code_template must all be present or all absent; "
                f"present={role_trio_present}, missing={missing}")
        elif n_present == 0:
            info("R2_MISSING_ROLE_FIELDS",
                 "No role-based fields (roles/role_defaults/code_template); migration pending")

        # R3: every role declared in roles must have a matching entry in role_defaults
        roles = data.get("roles")
        rd = data.get("role_defaults")
        if isinstance(roles, dict) and isinstance(rd, dict):
            for role_name in roles:
                if role_name not in rd:
                    err("R3_ROLE_DEFAULT_MISSING",
                        f"roles declares role {role_name!r} but role_defaults has no entry for it")

    return issues


# ── --fix: safe mechanical transformations ───────────────────────────────────

def apply_fixes(path: Path, data: dict, issues: list[Issue]) -> tuple[dict, list[str]]:
    """
    Apply safe, mechanical fixes to `data`. Returns (modified_data, list_of_fix_descriptions).

    Conservative rules:
    - Only fix issues where fixable=True in the Issue.
    - Never touch 'code' field content.
    - Never remove a field (deprecation removal is human-authored).
    """
    fixes_applied = []
    task_id = data.get("task_id", "")
    is_cp = schema.is_cp_template(str(task_id))

    for issue in issues:
        if not issue.fixable:
            continue

        if issue.rule == "T1_MISSING_FIELD" and "verified_status" in issue.message:
            if "verified_status" not in data:
                data["verified_status"] = "draft"
                fixes_applied.append("Added default verified_status: 'draft'")

    return data, fixes_applied


# ── Format helpers ────────────────────────────────────────────────────────────

def format_line(path: Path, issues: list[Issue], root: Path) -> str:
    """Format a single-line or multi-line output for one template."""
    rel = path.relative_to(root) if path.is_relative_to(root) else path
    if not issues:
        return f"{rel}: OK"
    lines = []
    for issue in issues:
        lines.append(f"{rel}: {issue.level} [{issue.rule}] {issue.message}")
    return "\n".join(lines)


# ── Main ──────────────────────────────────────────────────────────────────────

def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Lint canonical templates against the canonical schema",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "templates",
        nargs="*",
        help="Specific template paths to lint. Defaults to all workspace/templates/*.json",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit 1 if any ERROR or (with --strict-warn) any WARN is found",
    )
    parser.add_argument(
        "--strict-warn",
        action="store_true",
        help="Also fail on WARN-level issues (implies --strict)",
    )
    parser.add_argument(
        "--fix",
        action="store_true",
        help="Auto-apply safe mechanical fixes and write back to file",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="json_out",
        help="Output machine-readable JSON",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress OK lines; only print files with issues",
    )
    parser.add_argument(
        "--validate-tool-calls",
        action="store_true",
        dest="validate_tool_calls",
        help=(
            "AST-parse the 'code' field and validate each tool call's kwargs "
            "against the Pydantic models in _models.py "
            "(TC_REQUIRED_MISSING, TC_UNKNOWN_KWARG rule codes)"
        ),
    )
    parser.add_argument(
        "--strict-tool-calls",
        action="store_true",
        dest="strict_tool_calls",
        help=(
            "Also validate 'code_template' in addition to 'code'. "
            "Implies --validate-tool-calls. Template vars ({{...}}) are "
            "replaced with string sentinels before parsing."
        ),
    )
    args = parser.parse_args(argv)

    # --strict-tool-calls implies --validate-tool-calls
    if args.strict_tool_calls:
        args.validate_tool_calls = True

    if args.strict_warn:
        args.strict = True

    # Collect template paths
    if args.templates:
        template_paths = [Path(p) for p in args.templates]
    else:
        template_paths = sorted(_TEMPLATES_DIR.glob("*.json"))

    if not template_paths:
        print("No templates found.", file=sys.stderr)
        sys.exit(2)

    # Root for relative path display
    root = _REPO_ROOT

    results = []  # list of (path, issues_list, parse_error)
    has_parse_error = False
    has_error = False
    has_warn = False

    for path in template_paths:
        # Parse
        try:
            raw = path.read_text(encoding="utf-8")
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            parse_issue = Issue("ERROR", "JSON_PARSE", f"Invalid JSON: {exc}")
            results.append((path, [parse_issue], True))
            has_parse_error = True
            has_error = True
            continue
        except OSError as exc:
            parse_issue = Issue("ERROR", "FILE_READ", f"Cannot read file: {exc}")
            results.append((path, [parse_issue], True))
            has_parse_error = True
            has_error = True
            continue

        # Lint
        issues = lint_one(path, data)

        # Tool-call validation (optional pass)
        if args.validate_tool_calls:
            tc_issues = lint_tool_calls(
                path,
                data,
                include_code_template=args.strict_tool_calls,
            )
            issues.extend(tc_issues)

        # Fix (if requested)
        if args.fix:
            fixable = [i for i in issues if i.fixable]
            if fixable:
                data, fix_descriptions = apply_fixes(path, data, issues)
                if fix_descriptions:
                    out = json.dumps(data, indent=2, ensure_ascii=False)
                    path.write_text(out + "\n", encoding="utf-8")
                    # Re-lint to see if issues were resolved
                    issues = lint_one(path, data)

        results.append((path, issues, False))

        for issue in issues:
            if issue.level == "ERROR":
                has_error = True
            elif issue.level == "WARN":
                has_warn = True

    # ── Output ────────────────────────────────────────────────────────────────

    if args.json_out:
        output = []
        for path, issues, is_parse_err in results:
            rel = str(path.relative_to(root)) if path.is_relative_to(root) else str(path)
            output.append({
                "file": rel,
                "ok": not issues,
                "parse_error": is_parse_err,
                "issues": [i.as_dict() for i in issues],
            })
        # Summary
        error_count = sum(1 for _, iss, _ in results for i in iss if i.level == "ERROR")
        warn_count = sum(1 for _, iss, _ in results for i in iss if i.level == "WARN")
        info_count = sum(1 for _, iss, _ in results for i in iss if i.level == "INFO")
        print(json.dumps({
            "templates_scanned": len(results),
            "error_count": error_count,
            "warn_count": warn_count,
            "info_count": info_count,
            "results": output,
        }, indent=2, ensure_ascii=False))
    else:
        for path, issues, is_parse_err in results:
            rel = path.relative_to(root) if path.is_relative_to(root) else path
            if not issues:
                if not args.quiet:
                    print(f"{rel}: OK")
                continue
            for issue in issues:
                print(f"{rel}: {issue.level} [{issue.rule}] {issue.message}")

        # Summary stats
        error_count = sum(1 for _, iss, _ in results for i in iss if i.level == "ERROR")
        warn_count = sum(1 for _, iss, _ in results for i in iss if i.level == "WARN")
        info_count = sum(1 for _, iss, _ in results for i in iss if i.level == "INFO")
        ok_count = sum(1 for _, iss, _ in results if not iss)
        print(
            f"\n{len(results)} templates scanned: "
            f"{ok_count} OK, "
            f"{error_count} ERROR, "
            f"{warn_count} WARN, "
            f"{info_count} INFO"
        )

    # ── Exit code ─────────────────────────────────────────────────────────────

    if has_parse_error:
        sys.exit(2)
    if has_error and args.strict:
        sys.exit(1)
    if has_warn and args.strict_warn:
        sys.exit(1)
    if has_error:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
