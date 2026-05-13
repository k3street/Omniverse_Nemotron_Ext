"""Stage-mutation rules: create_prim, delete+create, kit commands, etc (6 of 22)."""
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
class CreatePrimDefaultPathRule(PatchValidatorRule):
    rule_id = "create_prim_default_path"
    severity = Severity.ERROR
    fix_hint = "Pass explicit prim_path; don't rely on Kit's default."

    def check(self, code: str, ast_tree: Optional[_ast.AST] = None) -> List[PatchIssue]:
        from ...patch_validator import _check_create_prim_default_path
        return _adapt(_check_create_prim_default_path)(code)


@register
class DeleteThenCreateSamePathRule(PatchValidatorRule):
    rule_id = "delete_then_create_same_path"
    severity = Severity.ERROR
    fix_hint = "USD path reuse needs a Sdf.ChangeBlock or stage refresh between delete and create."

    def check(self, code: str, ast_tree: Optional[_ast.AST] = None) -> List[PatchIssue]:
        from ...patch_validator import _check_delete_then_create_same_path
        return _adapt(_check_delete_then_create_same_path)(code)


@register
class KitCommandNameRule(PatchValidatorRule):
    rule_id = "kit_command_name"
    severity = Severity.ERROR

    def check(self, code: str, ast_tree: Optional[_ast.AST] = None) -> List[PatchIssue]:
        from ...patch_validator import _check_kit_command_name
        return _adapt(_check_kit_command_name)(code)


@register
class PipelineStageEnumMismatchRule(PatchValidatorRule):
    rule_id = "pipeline_stage_enum_mismatch"
    severity = Severity.ERROR

    def check(self, code: str, ast_tree: Optional[_ast.AST] = None) -> List[PatchIssue]:
        from ...patch_validator import _check_pipeline_stage_enum_mismatch
        return _adapt(_check_pipeline_stage_enum_mismatch)(code)


@register
class ClearXformOpOrderRule(PatchValidatorRule):
    rule_id = "clear_xform_op_order"
    severity = Severity.ERROR

    def check(self, code: str, ast_tree: Optional[_ast.AST] = None) -> List[PatchIssue]:
        from ...patch_validator import _check_clear_xform_op_order
        return _adapt(_check_clear_xform_op_order)(code)


@register
class NewStageMidPatchRule(PatchValidatorRule):
    rule_id = "new_stage_mid_patch"
    severity = Severity.ERROR
    fix_hint = "Patches must not call omni.usd.get_context().new_stage()."

    def check(self, code: str, ast_tree: Optional[_ast.AST] = None) -> List[PatchIssue]:
        from ...patch_validator import _check_new_stage_mid_patch
        return _adapt(_check_new_stage_mid_patch)(code)
