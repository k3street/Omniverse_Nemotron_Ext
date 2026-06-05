"""Base class for all stage validation rules."""
from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional
from ..models import ValidationFinding, FixSuggestion


class ValidationRule(ABC):
    """Abstract base for a single deterministic USD stage validation rule.

    Subclasses set ``rule_id``, ``pack``, ``severity``, ``name``, and
    ``description`` in ``__init__``, then implement ``check()``.

    Override ``auto_fixable()`` to return True and implement
    ``suggest_fix()`` when the rule can produce a structured change set.
    """

    def __init__(self):
        """Initialise default metadata fields shared by all concrete rules.

        Subclasses must overwrite ``rule_id``, ``pack``, ``severity``,
        ``name``, and ``description`` in their own ``__init__`` before
        returning control to callers.
        """
        self.rule_id = "base_rule"
        self.pack = "base_pack"
        self.severity = "info"
        self.name = "Abstract Rule"
        self.description = "Base class for Omniverse validation rules"

    @abstractmethod
    def check(self, stage_data: Dict[str, Any]) -> List[ValidationFinding]:
        """
        Receives serialized stage data directly from the UI extension
        and computes deterministic logic finding USD tree errors.
        """
        pass

    def auto_fixable(self) -> bool:
        """Return True if this rule can generate a structured auto-fix.

        Returns:
            bool: False by default; override to True in fixable subclasses.
        """
        return False

    def suggest_fix(self, finding: ValidationFinding) -> Optional[FixSuggestion]:
        """Produce a ``FixSuggestion`` for the given finding.

        Only called when ``auto_fixable()`` returns True.  The base
        implementation always returns None; subclasses override to provide
        a concrete fix.

        Args:
            finding (ValidationFinding): The finding to suggest a fix for.

        Returns:
            FixSuggestion | None: Structured fix or None if not applicable.
        """
        return None
