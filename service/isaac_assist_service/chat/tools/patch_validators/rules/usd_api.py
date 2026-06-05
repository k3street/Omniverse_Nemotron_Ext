"""USD API rules: IsA/HasAPI usage, CreateAttribute signature, deprecated utils (3 of 22)."""
from __future__ import annotations

from typing import List, Optional
import ast as _ast

from ..registry import CitedFailure, PatchIssue, PatchValidatorRule, Severity, register


def _adapt(legacy_fn):
    """Convert legacy checker output to registry PatchIssue instances."""
    def _wrap(code: str) -> List[PatchIssue]:
        """Delegate to legacy checker and adapt its issues."""
        out = []
        for issue in legacy_fn(code):
            out.append(PatchIssue(
                severity=issue.severity, rule=issue.rule,
                message=issue.message, fix_hint=getattr(issue, "fix_hint", ""),
            ))
        return out
    return _wrap


@register
class DeprecatedCoreUtilsRule(PatchValidatorRule):
    """Warn when patches import deprecated omni.isaac.core utilities."""

    rule_id = "deprecated_core_utils"
    severity = Severity.WARNING
    fix_hint = "omni.isaac.core utilities are deprecated in Isaac Sim 5.1+ — use isaacsim.core instead."

    def check(self, code: str, ast_tree: Optional[_ast.AST] = None) -> List[PatchIssue]:
        """Check for omni.isaac.core import paths that moved to isaacsim.core."""
        from ...patch_validator import _check_deprecated_core_utils
        return _adapt(_check_deprecated_core_utils)(code)


@register
class IsAOnAPISchemaRule(PatchValidatorRule):
    """Reject IsA() calls on API schemas that must use HasAPI() instead."""

    rule_id = "usd_isa_on_api_schema"
    severity = Severity.ERROR
    fix_hint = (
        "Use prim.HasAPI({module}.{api_name}) to test for an applied API. "
        "For lights, use IsA(UsdLux.DomeLight) etc. or HasAPI(UsdLux.LightAPI)."
    )
    cited_failures = [
        CitedFailure(
            date="2026-04-19",
            session_id="<known agent run>",
            error_msg=(
                "agent ran `if p.IsA(UsdLux.LightAPI)`, got 0 lights "
                "even though /World/DomeLight existed, then fabricated "
                "'Isaac Sim must be using an automatic headlight'"
            ),
        ),
    ]

    def check(self, code: str, ast_tree: Optional[_ast.AST] = None) -> List[PatchIssue]:
        """Check for prim.IsA(<ApiSchema>) patterns that should be prim.HasAPI(...)."""
        from ...patch_validator import _check_isa_on_api_schema
        return _adapt(_check_isa_on_api_schema)(code)


@register
class CreateAttributeSignatureRule(PatchValidatorRule):
    """Ensure CreateAttribute() uses Sdf.ValueTypeNames constants, not raw strings."""

    rule_id = "usd_create_attr_signature"
    severity = Severity.ERROR
    fix_hint = "Use Sdf.ValueTypeNames.Float, Sdf.ValueTypeNames.Double3, etc."

    def check(self, code: str, ast_tree: Optional[_ast.AST] = None) -> List[PatchIssue]:
        """Check for string literals where Sdf.ValueTypeNames.* is required."""
        from ...patch_validator import _check_create_attribute_signature
        return _adapt(_check_create_attribute_signature)(code)
