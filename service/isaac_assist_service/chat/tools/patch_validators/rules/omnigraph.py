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
        """Delegate to legacy checker and convert its issues to registry type."""
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
    """Detect Double3/Double type mismatches in OmniGraph connections."""

    rule_id = "og_double3_to_double"
    severity = Severity.ERROR
    fix_hint = "Insert Break3Vector nodes between SubscribeTwist and DiffController."

    def check(self, code: str, ast_tree: Optional[_ast.AST] = None) -> List[PatchIssue]:
        """Check for vector→scalar port type mismatches."""
        from ...patch_validator import _check_omnigraph_type_mismatch
        return _adapt(_check_omnigraph_type_mismatch)(code)


@register
class OmnigraphLegacyNamespaceRule(PatchValidatorRule):
    """Reject deprecated omni.isaac.* OmniGraph node namespaces."""

    rule_id = "og_legacy_namespace"
    severity = Severity.ERROR
    fix_hint = "Use isaacsim.* namespace, not omni.isaac.* (Isaac Sim 5.1+)."

    def check(self, code: str, ast_tree: Optional[_ast.AST] = None) -> List[PatchIssue]:
        """Check for legacy omni.isaac.* node type strings."""
        from ...patch_validator import _check_omnigraph_legacy_namespace
        return _adapt(_check_omnigraph_legacy_namespace)(code)


@register
class OmnigraphDiffExecOutRule(PatchValidatorRule):
    """Detect missing execOut connections on DifferentialController nodes."""

    rule_id = "og_diff_exec_out"
    severity = Severity.ERROR

    def check(self, code: str, ast_tree: Optional[_ast.AST] = None) -> List[PatchIssue]:
        """Check that DifferentialController's execOut port is wired."""
        from ...patch_validator import _check_omnigraph_diff_exec_out
        return _adapt(_check_omnigraph_diff_exec_out)(code)


@register
class OmnigraphUsePathRule(PatchValidatorRule):
    """Ensure OmniGraph node lookups use prim paths, not node handles directly."""

    rule_id = "og_use_path"
    severity = Severity.ERROR

    def check(self, code: str, ast_tree: Optional[_ast.AST] = None) -> List[PatchIssue]:
        """Check for node-handle misuse instead of path-based lookup."""
        from ...patch_validator import _check_omnigraph_use_path
        return _adapt(_check_omnigraph_use_path)(code)


@register
class OmnigraphBadApiRule(PatchValidatorRule):
    """Reject removed or renamed OmniGraph Python API calls."""

    rule_id = "og_bad_api"
    severity = Severity.ERROR

    def check(self, code: str, ast_tree: Optional[_ast.AST] = None) -> List[PatchIssue]:
        """Check for deprecated OmniGraph API symbols."""
        from ...patch_validator import _check_omnigraph_bad_api
        return _adapt(_check_omnigraph_bad_api)(code)


@register
class OmnigraphBackingTypeRule(PatchValidatorRule):
    """Ensure OmniGraph attribute backing types are specified correctly."""

    rule_id = "og_backing_type"
    severity = Severity.ERROR

    def check(self, code: str, ast_tree: Optional[_ast.AST] = None) -> List[PatchIssue]:
        """Check for incorrect or missing backing type declarations."""
        from ...patch_validator import _check_omnigraph_backing_type
        return _adapt(_check_omnigraph_backing_type)(code)
