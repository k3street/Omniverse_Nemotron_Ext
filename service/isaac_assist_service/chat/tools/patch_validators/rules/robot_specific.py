"""Robot-specific rules: carter joints, wheel meshes, franka URL (3 of 22)."""
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
class CarterJointNamesRule(PatchValidatorRule):
    rule_id = "carter_joint_names"
    severity = Severity.ERROR

    def check(self, code: str, ast_tree: Optional[_ast.AST] = None) -> List[PatchIssue]:
        from ...patch_validator import _check_carter_joint_names
        return _adapt(_check_carter_joint_names)(code)


@register
class TriangleMeshOnWheelsRule(PatchValidatorRule):
    rule_id = "triangle_mesh_on_wheels"
    severity = Severity.WARNING
    fix_hint = "Use convexHull/convexDecomposition for wheel collision meshes."

    def check(self, code: str, ast_tree: Optional[_ast.AST] = None) -> List[PatchIssue]:
        from ...patch_validator import _check_triangle_mesh_on_wheels
        return _adapt(_check_triangle_mesh_on_wheels)(code)


@register
class WrongFrankaUrlRule(PatchValidatorRule):
    rule_id = "wrong_franka_url"
    severity = Severity.ERROR
    fix_hint = "Use the FrankaRobotics/FrankaPanda/franka.usd path."

    def check(self, code: str, ast_tree: Optional[_ast.AST] = None) -> List[PatchIssue]:
        from ...patch_validator import _check_wrong_franka_url
        return _adapt(_check_wrong_franka_url)(code)
