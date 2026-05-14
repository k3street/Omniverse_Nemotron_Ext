"""USD/pxr import + raw-list rules (4 of 22)."""
from __future__ import annotations

from typing import List, Optional
import ast as _ast

from ..registry import PatchIssue, PatchValidatorRule, Severity, register


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
class MissingImportOmniUsdRule(PatchValidatorRule):
    """Flag patches that call omni.usd APIs without importing the module."""

    rule_id = "missing_import_omni_usd"
    severity = Severity.ERROR
    fix_hint = "Add `import omni.usd` at the top of the patch."

    def check(self, code: str, ast_tree: Optional[_ast.AST] = None) -> List[PatchIssue]:
        """Check for omni.usd usage without a corresponding import."""
        from ...patch_validator import _check_missing_import_omni_usd
        return _adapt(_check_missing_import_omni_usd)(code)


@register
class MissingPxrImportsRule(PatchValidatorRule):
    """Flag patches that use pxr symbols (Sdf, Gf, UsdGeom …) without importing them."""

    rule_id = "missing_pxr_imports"
    severity = Severity.ERROR

    def check(self, code: str, ast_tree: Optional[_ast.AST] = None) -> List[PatchIssue]:
        """Check for pxr module usage without matching from-pxr imports."""
        from ...patch_validator import _check_missing_pxr_imports
        return _adapt(_check_missing_pxr_imports)(code)


@register
class RawListForVecRule(PatchValidatorRule):
    """Flag plain Python lists passed where Gf.Vec* types are required."""

    rule_id = "raw_list_for_vec"
    severity = Severity.ERROR
    fix_hint = "Wrap with Gf.Vec3f(...) or Gf.Vec3d(...)."

    def check(self, code: str, ast_tree: Optional[_ast.AST] = None) -> List[PatchIssue]:
        """Check for raw list/tuple literals in attribute Set() calls."""
        from ...patch_validator import _check_raw_list_for_vec
        return _adapt(_check_raw_list_for_vec)(code)


@register
class UnsafeAddXformOpRule(PatchValidatorRule):
    """Detect direct AddXformOp calls that can duplicate ops on repeated runs."""

    rule_id = "unsafe_add_xform_op"
    severity = Severity.ERROR
    fix_hint = "Use _safe_set_translate / _safe_set_scale / _safe_set_rotate_xyz."

    def check(self, code: str, ast_tree: Optional[_ast.AST] = None) -> List[PatchIssue]:
        """Check for bare AddXformOp calls without an existing-op guard."""
        from ...patch_validator import _check_unsafe_add_xform_op
        return _adapt(_check_unsafe_add_xform_op)(code)
