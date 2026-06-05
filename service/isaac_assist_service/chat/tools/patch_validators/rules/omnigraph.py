"""OmniGraph rules (6 of the 22 _check_* functions).

Each rule subclasses PatchValidatorRule and delegates the actual
detection to the existing patch_validator._check_X function for behavior
parity with the 78 existing tests.
"""
from __future__ import annotations

from typing import List, Optional
import ast as _ast

from ..registry import PatchIssue, PatchValidatorRule, Severity, register


def _adapt(legacy_fn):
    """Convert legacy List[patch_validator.PatchIssue] to registry's PatchIssue."""
    def _wrap(code: str) -> List[PatchIssue]:
        out = []
        for issue in legacy_fn(code):
            out.append(PatchIssue(
                severity=issue.severity,
                rule=issue.rule,
                message=issue.message,
                fix_hint=getattr(issue, "fix_hint", ""),
            ))
        return out
    return _wrap


@register
class OmnigraphTypeMismatchRule(PatchValidatorRule):
    rule_id = "og_double3_to_double"
    severity = Severity.ERROR
    fix_hint = "Insert Break3Vector nodes between SubscribeTwist and DiffController."

    def check(self, code: str, ast_tree: Optional[_ast.AST] = None) -> List[PatchIssue]:
        from ...patch_validator import _check_omnigraph_type_mismatch
        return _adapt(_check_omnigraph_type_mismatch)(code)


@register
class OmnigraphLegacyNamespaceRule(PatchValidatorRule):
    rule_id = "og_legacy_namespace"
    severity = Severity.ERROR
    fix_hint = "Use isaacsim.* namespace, not omni.isaac.* (Isaac Sim 5.1+)."

    def check(self, code: str, ast_tree: Optional[_ast.AST] = None) -> List[PatchIssue]:
        from ...patch_validator import _check_omnigraph_legacy_namespace
        return _adapt(_check_omnigraph_legacy_namespace)(code)


@register
class OmnigraphDiffExecOutRule(PatchValidatorRule):
    rule_id = "og_diff_exec_out"
    severity = Severity.ERROR

    def check(self, code: str, ast_tree: Optional[_ast.AST] = None) -> List[PatchIssue]:
        from ...patch_validator import _check_omnigraph_diff_exec_out
        return _adapt(_check_omnigraph_diff_exec_out)(code)


@register
class OmnigraphUsePathRule(PatchValidatorRule):
    rule_id = "og_use_path"
    severity = Severity.ERROR

    def check(self, code: str, ast_tree: Optional[_ast.AST] = None) -> List[PatchIssue]:
        from ...patch_validator import _check_omnigraph_use_path
        return _adapt(_check_omnigraph_use_path)(code)


@register
class OmnigraphBadApiRule(PatchValidatorRule):
    rule_id = "og_bad_api"
    severity = Severity.ERROR

    def check(self, code: str, ast_tree: Optional[_ast.AST] = None) -> List[PatchIssue]:
        from ...patch_validator import _check_omnigraph_bad_api
        return _adapt(_check_omnigraph_bad_api)(code)


@register
class OmnigraphBackingTypeRule(PatchValidatorRule):
    rule_id = "og_backing_type"
    severity = Severity.ERROR

    def check(self, code: str, ast_tree: Optional[_ast.AST] = None) -> List[PatchIssue]:
        from ...patch_validator import _check_omnigraph_backing_type
        return _adapt(_check_omnigraph_backing_type)(code)
