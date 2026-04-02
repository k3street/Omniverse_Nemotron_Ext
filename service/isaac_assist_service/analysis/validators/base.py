from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional
from ..models import ValidationFinding, FixSuggestion

class ValidationRule(ABC):
    def __init__(self):
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
        return False

    def suggest_fix(self, finding: ValidationFinding) -> Optional[FixSuggestion]:
        return None
