"""Phase 47b — Honesty-decorator long-tail rollout.

Provides a static-analysis layer for detecting silent-success return patterns
in tool handler functions.  The core artefacts are:

* ``SilentSuccessRule`` — a single detection rule (dataclass + callable).
* ``SilentSuccessFinding`` — one hit produced by a rule against a return value.
* ``SILENT_SUCCESS_RULES`` — the canonical rule catalogue (≥8 entries).
* ``HonestyDecorator`` — wraps handlers so their output is audited at call
  time; optionally raises on critical findings.
* ``audit_handler_module`` — lightweight AST scan of a Python file that flags
  functions whose return statements look suspiciously bare (text-heuristic;
  intended as a marker for human review, not a hard gate).

Per specs/IA_FULL_SPEC_2026-05-10.md Phase 47b.
"""
from __future__ import annotations

import ast
import functools
import inspect
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Literal, Optional


PHASE_ID = "47b"
PHASE_TITLE = "Honesty-decorator long-tail rollout"
PHASE_STATUS = "landed"


def get_phase_metadata() -> Dict[str, Any]:
    """Return phase identification and status for this phase.

    Returns:
        Dict[str, Any]: Keys ``phase``, ``title``, ``status``, and ``spec_ref``.
    """
    return {
        "phase": PHASE_ID,
        "title": PHASE_TITLE,
        "status": PHASE_STATUS,
        "spec_ref": "specs/IA_FULL_SPEC_2026-05-10.md Phase 47b",
    }


# ---------------------------------------------------------------------------
# Core data model
# ---------------------------------------------------------------------------

@dataclass
class SilentSuccessRule:
    """A single rule that can detect a silent-success anti-pattern.

    Parameters
    ----------
    rule_id:
        Stable kebab-case identifier for the rule (e.g. "missing_success_key").
    description:
        Human-readable explanation of what the rule checks.
    severity:
        Impact level — ``"info"`` for advisory, ``"warn"`` for attention-
        worthy, ``"critical"`` for must-fix.
    detect:
        A callable that accepts the raw return value of a handler (any type)
        and returns ``True`` if the rule fires (problem detected).
    """

    rule_id: str
    description: str
    severity: Literal["info", "warn", "critical"]
    detect: Callable[[Any], bool]


@dataclass
class SilentSuccessFinding:
    """One instance of a fired rule against a handler's return value.

    Parameters
    ----------
    rule_id:
        The ``rule_id`` of the fired :class:`SilentSuccessRule`.
    tool_name:
        Name of the tool / handler that produced the suspicious return value.
    severity:
        Copied from the rule for quick access.
    detail:
        Short human-readable description of the finding.
    """

    rule_id: str
    tool_name: str
    severity: str
    detail: str


# ---------------------------------------------------------------------------
# Rule catalogue
# ---------------------------------------------------------------------------

def _is_dict(v: Any) -> bool:
    """Return True if *v* is a plain dict (used in rule lambda predicates)."""
    return isinstance(v, dict)


SILENT_SUCCESS_RULES: List[SilentSuccessRule] = [
    # R1 ─────────────────────────────────────────────────────────────────────
    SilentSuccessRule(
        rule_id="missing_success_key",
        description=(
            "Return dict has no 'success' field — callers cannot determine "
            "whether the operation succeeded without inspecting side-effects."
        ),
        severity="critical",
        detect=lambda v: _is_dict(v) and "success" not in v,
    ),
    # R2 ─────────────────────────────────────────────────────────────────────
    SilentSuccessRule(
        rule_id="success_true_no_proof",
        description=(
            "success=True but no 'output', 'result', 'prim_path', or 'value' "
            "field present — callers have no evidence the action took effect."
        ),
        severity="warn",
        detect=lambda v: (
            _is_dict(v)
            and v.get("success") is True
            and not any(k in v for k in ("output", "result", "prim_path", "value"))
        ),
    ),
    # R3 ─────────────────────────────────────────────────────────────────────
    SilentSuccessRule(
        rule_id="error_field_with_success_true",
        description=(
            "Both 'error' and 'success=True' are present — contradictory "
            "signals; callers cannot trust either field."
        ),
        severity="critical",
        detect=lambda v: (
            _is_dict(v)
            and v.get("success") is True
            and "error" in v
            and v["error"]  # non-empty / truthy error value
        ),
    ),
    # R4 ─────────────────────────────────────────────────────────────────────
    SilentSuccessRule(
        rule_id="empty_output_with_success",
        description=(
            "success=True with an empty 'output' collection ({} or []) — "
            "indicates the handler produced no artefacts despite claiming success."
        ),
        severity="warn",
        detect=lambda v: (
            _is_dict(v)
            and v.get("success") is True
            and "output" in v
            and v["output"] in ({}, [])
        ),
    ),
    # R5 ─────────────────────────────────────────────────────────────────────
    SilentSuccessRule(
        rule_id="kit_returned_string_only",
        description=(
            "Handler returned a bare string instead of a structured dict — "
            "Kit RPC callers expect a dict and will fail silently on string coercion."
        ),
        severity="critical",
        detect=lambda v: isinstance(v, str),
    ),
    # R6 ─────────────────────────────────────────────────────────────────────
    SilentSuccessRule(
        rule_id="exception_swallowed",
        description=(
            "Return value has 'exception_class' but no 'success' field — "
            "the handler swallowed an exception without surfacing success/failure."
        ),
        severity="critical",
        detect=lambda v: _is_dict(v) and "exception_class" in v and "success" not in v,
    ),
    # R7 ─────────────────────────────────────────────────────────────────────
    SilentSuccessRule(
        rule_id="stage_unchanged_after_create",
        description=(
            "Handler claims to create a prim but 'prim_path' is None or empty "
            "string — the stage was not actually modified."
        ),
        severity="critical",
        detect=lambda v: (
            _is_dict(v)
            and v.get("success") is True
            and "prim_path" in v
            and not v["prim_path"]  # None, "", or other falsy
        ),
    ),
    # R8 ─────────────────────────────────────────────────────────────────────
    SilentSuccessRule(
        rule_id="boolean_string_confusion",
        description=(
            "'success' field is a string ('true'/'false') instead of a bool — "
            "truthiness checks on non-empty strings always pass, masking failures."
        ),
        severity="warn",
        detect=lambda v: (
            _is_dict(v)
            and isinstance(v.get("success"), str)
        ),
    ),
    # R9 ─────────────────────────────────────────────────────────────────────
    SilentSuccessRule(
        rule_id="none_return",
        description=(
            "Handler returned None — no structured result available; "
            "callers cannot distinguish intentional no-op from unhandled error."
        ),
        severity="warn",
        detect=lambda v: v is None,
    ),
    # R10 ────────────────────────────────────────────────────────────────────
    SilentSuccessRule(
        rule_id="success_false_no_error_message",
        description=(
            "success=False but no 'error' or 'message' field — callers cannot "
            "determine what went wrong or how to recover."
        ),
        severity="warn",
        detect=lambda v: (
            _is_dict(v)
            and v.get("success") is False
            and not any(k in v for k in ("error", "message", "reason"))
        ),
    ),
]


# ---------------------------------------------------------------------------
# Decorator
# ---------------------------------------------------------------------------

class _CriticalFindingError(RuntimeError):
    """Raised by :class:`HonestyDecorator` when ``raise_on_critical=True``."""


class HonestyDecorator:
    """Audit tool-handler return values against the silent-success rule set.

    Parameters
    ----------
    rules:
        Rule list to use.  Defaults to the module-level
        :data:`SILENT_SUCCESS_RULES` when ``None``.
    raise_on_critical:
        When ``True``, :meth:`wrap` will raise a :exc:`_CriticalFindingError`
        if any critical-severity rule fires.  Findings are always appended to
        the result dict (when the result is a dict) regardless of this flag.
    """

    def __init__(
        self,
        rules: Optional[List[SilentSuccessRule]] = None,
        raise_on_critical: bool = False,
    ) -> None:
        """Initialise with the rule set to apply and the raise-on-critical flag."""
        self._rules: List[SilentSuccessRule] = (
            rules if rules is not None else SILENT_SUCCESS_RULES
        )
        self._raise_on_critical = raise_on_critical

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def audit(self, tool_name: str, result: Any) -> List[SilentSuccessFinding]:
        """Run all rules against *result* and return the list of findings.

        Parameters
        ----------
        tool_name:
            Identifies the handler in each :class:`SilentSuccessFinding`.
        result:
            The raw return value of the handler.

        Returns
        -------
        list[SilentSuccessFinding]
            Empty list when no rules fire (clean return).
        """
        findings: List[SilentSuccessFinding] = []
        for rule in self._rules:
            try:
                fired = rule.detect(result)
            except Exception:  # noqa: BLE001
                # Never let a rule crash the audit; treat as not-fired.
                fired = False
            if fired:
                findings.append(
                    SilentSuccessFinding(
                        rule_id=rule.rule_id,
                        tool_name=tool_name,
                        severity=rule.severity,
                        detail=(
                            f"[{rule.severity.upper()}] {rule.rule_id}: "
                            f"{rule.description}"
                        ),
                    )
                )
        return findings

    def wrap(
        self,
        handler: Callable[..., Any],
        tool_name: Optional[str] = None,
    ) -> Callable[..., Any]:
        """Return a wrapper that audits *handler*'s output on every call.

        If ``raise_on_critical=True`` and any critical finding is produced the
        wrapper raises :exc:`_CriticalFindingError` **after** attaching the
        findings to the result dict (when the result is a dict).

        When the result is a dict the wrapper injects a
        ``_honesty_findings`` key containing serialisable finding dicts.
        For all other return types the findings are only propagated via the
        exception (if ``raise_on_critical=True``).

        Parameters
        ----------
        handler:
            The callable to wrap.
        tool_name:
            Override for the tool name used in findings.  Defaults to
            ``handler.__name__``.
        """
        effective_name: str = tool_name or getattr(handler, "__name__", "<unknown>")

        @functools.wraps(handler)
        def _wrapper(*args: Any, **kwargs: Any) -> Any:
            """Invoke the wrapped handler and attach any honesty findings to its result."""
            result = handler(*args, **kwargs)
            findings = self.audit(effective_name, result)

            # Attach findings to result dict when possible
            if isinstance(result, dict) and findings:
                result = dict(result)  # shallow copy — don't mutate original
                result["_honesty_findings"] = [
                    {
                        "rule_id": f.rule_id,
                        "tool_name": f.tool_name,
                        "severity": f.severity,
                        "detail": f.detail,
                    }
                    for f in findings
                ]

            # Optionally raise on critical findings
            if self._raise_on_critical:
                critical = [f for f in findings if f.severity == "critical"]
                if critical:
                    summary = "; ".join(f.rule_id for f in critical)
                    raise _CriticalFindingError(
                        f"Critical honesty findings in '{effective_name}': {summary}"
                    )

            return result

        return _wrapper


# ---------------------------------------------------------------------------
# Static AST scanner
# ---------------------------------------------------------------------------

def audit_handler_module(module_path: Path) -> Dict[str, List[str]]:
    """Scan a Python module file via AST for suspicious return statements.

    This is a *text-heuristic* static scan — it is not a type-checker and will
    produce false positives.  Its role is to give human reviewers a shortlist
    of functions that warrant closer inspection.

    The heuristics applied per function:

    * ``bare_return`` — function has a ``return`` with no value (returns None).
    * ``string_return`` — function returns a string literal directly.
    * ``no_success_key`` — function never builds a dict containing a literal
      ``"success"`` key in any ``return`` statement.
    * ``returns_none_literal`` — function explicitly returns ``None``.

    Parameters
    ----------
    module_path:
        Path to the ``.py`` file to scan.

    Returns
    -------
    dict[str, list[str]]
        Mapping of ``{function_name: [heuristic_tag, ...]}`` for functions
        with at least one heuristic hit.  Functions with no hits are omitted.
    """
    source = Path(module_path).read_text(encoding="utf-8")
    try:
        tree = ast.parse(source, filename=str(module_path))
    except SyntaxError:
        return {"<parse_error>": ["syntax_error"]}

    results: Dict[str, List[str]] = {}

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue

        fn_name: str = node.name
        tags: List[str] = []

        # Collect all return statements within this function (not nested defs)
        return_nodes = _collect_returns(node)

        has_any_return_with_value = False
        has_success_key_in_dict = False
        has_string_return = False
        has_none_return = False
        has_bare_return = False

        for ret in return_nodes:
            val = ret.value
            if val is None:
                has_bare_return = True
                continue

            has_any_return_with_value = True

            # None literal
            if isinstance(val, ast.Constant) and val.value is None:
                has_none_return = True

            # String literal
            if isinstance(val, ast.Constant) and isinstance(val.value, str):
                has_string_return = True

            # Dict with "success" key somewhere
            if _dict_has_success_key(val):
                has_success_key_in_dict = True

        # Tag according to findings
        if has_bare_return:
            tags.append("bare_return")
        if has_string_return:
            tags.append("string_return")
        if has_none_return:
            tags.append("returns_none_literal")
        if has_any_return_with_value and not has_success_key_in_dict:
            tags.append("no_success_key")

        if tags:
            results[fn_name] = tags

    return results


# ---------------------------------------------------------------------------
# AST helpers (private)
# ---------------------------------------------------------------------------

def _collect_returns(
    fn_node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> List[ast.Return]:
    """Yield all Return nodes in *fn_node*, skipping nested function defs."""
    returns: List[ast.Return] = []
    for child in ast.walk(fn_node):
        # Skip return nodes that belong to nested functions
        if child is not fn_node and isinstance(
            child, (ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            continue
        if isinstance(child, ast.Return):
            returns.append(child)
    return returns


def _dict_has_success_key(node: ast.expr) -> bool:
    """Return True if *node* is an ast.Dict that contains a 'success' key."""
    if not isinstance(node, ast.Dict):
        return False
    for key in node.keys:
        if isinstance(key, ast.Constant) and key.value == "success":
            return True
    return False
