"""USD/pxr import + raw-list rules (4 of 22)."""
from __future__ import annotations

from typing import List, Optional
import ast as _ast

from ..registry import PatchIssue, PatchValidatorRule, Severity, register


def _adapt(legacy_fn):
    def _wrap(code: str) -> List[PatchIssue]:
        out = []
        for issue in legacy_fn(code):
            out.append(PatchIssue(
                severity=issue.severity, rule=issue.rule,
                message=issue.message, fix_hint=getattr(issue, "fix_hint", ""),
            ))
        return out
    return _wrap


@register
class MissingImportOmniUsdRule(PatchValidatorRule):
    rule_id = "missing_import_omni_usd"
    severity = Severity.ERROR
    fix_hint = "Add `import omni.usd` at the top of the patch."

    def check(self, code: str, ast_tree: Optional[_ast.AST] = None) -> List[PatchIssue]:
        from ...patch_validator import _check_missing_import_omni_usd
        return _adapt(_check_missing_import_omni_usd)(code)


@register
class MissingPxrImportsRule(PatchValidatorRule):
    rule_id = "missing_pxr_imports"
    severity = Severity.ERROR

    def check(self, code: str, ast_tree: Optional[_ast.AST] = None) -> List[PatchIssue]:
        from ...patch_validator import _check_missing_pxr_imports
        return _adapt(_check_missing_pxr_imports)(code)


@register
class RawListForVecRule(PatchValidatorRule):
    rule_id = "raw_list_for_vec"
    severity = Severity.ERROR
    fix_hint = "Wrap with Gf.Vec3f(...) or Gf.Vec3d(...)."

    def check(self, code: str, ast_tree: Optional[_ast.AST] = None) -> List[PatchIssue]:
        from ...patch_validator import _check_raw_list_for_vec
        return _adapt(_check_raw_list_for_vec)(code)


@register
class UnsafeAddXformOpRule(PatchValidatorRule):
    rule_id = "unsafe_add_xform_op"
    severity = Severity.ERROR
    fix_hint = "Use _safe_set_translate / _safe_set_scale / _safe_set_rotate_xyz."

    def check(self, code: str, ast_tree: Optional[_ast.AST] = None) -> List[PatchIssue]:
        from ...patch_validator import _check_unsafe_add_xform_op
        return _adapt(_check_unsafe_add_xform_op)(code)
