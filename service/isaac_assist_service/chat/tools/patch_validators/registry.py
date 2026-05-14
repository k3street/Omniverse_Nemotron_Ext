"""Registry + pipeline runner for patch-validator rules.

Phase 11 framework. Each rule is a `PatchValidatorRule` subclass with:
- `rule_id` (str): stable identifier
- `severity` (Severity): error|warning|info
- `fix_hint` (str): suggested fix for the LLM
- `cited_failures` (list): production incidents this rule guards against
- `check(code, ast_tree=None) -> List[PatchIssue]`: rule body

Per specs/IA_FULL_SPEC_2026-05-10.md Phase 11.
"""
from __future__ import annotations

import ast as _ast
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, List, Optional, Type


class Severity(str, Enum):
    """Severity levels for patch validation issues, ordered low→high."""

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


@dataclass
class CitedFailure:
    """A production incident this rule guards against."""
    date: str
    session_id: str = ""
    error_msg: str = ""


@dataclass
class PatchIssue:
    """A single issue found by a rule."""
    severity: str
    rule: str
    message: str
    fix_hint: str = ""


@dataclass
class ValidationResult:
    """Aggregated output from running the pipeline."""
    issues: List[PatchIssue] = field(default_factory=list)

    @property
    def blocking(self) -> bool:
        """Return True if any issue has ERROR or CRITICAL severity."""
        return any(i.severity == Severity.ERROR.value
                   or i.severity == Severity.CRITICAL.value for i in self.issues)

    def format_for_llm(self) -> str:
        """Render issues as a human-readable block for LLM feedback.

        Returns an empty string when there are no issues.
        """
        if not self.issues:
            return ""
        lines = ["PRE-FLIGHT VALIDATION FAILED — do NOT retry the same pattern:"]
        for i, issue in enumerate(self.issues, 1):
            lines.append(f"  {i}. [{issue.severity.upper()}] {issue.rule}: {issue.message}")
            if issue.fix_hint:
                lines.append(f"     FIX: {issue.fix_hint}")
        return "\n".join(lines)


class PatchValidatorRule:
    """Base class for patch-validator rules.

    Subclass + register via @register decorator or `register(cls)`.
    Each rule overrides `check()`. Default attributes ensure subclasses
    are well-shaped without requiring every field to be re-declared.
    """
    rule_id: str = "unnamed"
    severity: Severity = Severity.ERROR
    fix_hint: str = ""
    cited_failures: List[CitedFailure] = []

    def check(self, code: str, ast_tree: Optional[_ast.AST] = None) -> List[PatchIssue]:
        """Return list of PatchIssue (empty = clean)."""
        raise NotImplementedError(f"{self.__class__.__name__}.check() not implemented")


# Global registry — rules add themselves via @register.
REGISTRY: List[Type[PatchValidatorRule]] = []


def register(cls: Type[PatchValidatorRule]) -> Type[PatchValidatorRule]:
    """Class decorator to add a rule to the global registry."""
    if cls not in REGISTRY:
        REGISTRY.append(cls)
    return cls


class PipelineRunner:
    """Run all registered rules against a code patch."""

    def __init__(self, rules: Optional[List[Type[PatchValidatorRule]]] = None):
        """Initialise with an explicit rule list or fall back to REGISTRY."""
        self.rules = rules if rules is not None else REGISTRY

    def run(self, code: str) -> ValidationResult:
        """Run every rule, aggregate issues."""
        ast_tree = None
        try:
            ast_tree = _ast.parse(code)
        except Exception:
            pass  # Some rules work on string only
        all_issues: List[PatchIssue] = []
        for rule_cls in self.rules:
            try:
                rule = rule_cls()
                all_issues.extend(rule.check(code, ast_tree))
            except Exception as e:
                # Rule crashed — surface as warning, don't block
                all_issues.append(PatchIssue(
                    severity=Severity.WARNING.value,
                    rule=f"{rule_cls.__name__}_crashed",
                    message=f"Rule crashed: {type(e).__name__}: {e}",
                ))
        return ValidationResult(issues=all_issues)


def run_pipeline(code: str) -> ValidationResult:
    """Run the default pipeline (all globally registered rules)."""
    return PipelineRunner().run(code)
