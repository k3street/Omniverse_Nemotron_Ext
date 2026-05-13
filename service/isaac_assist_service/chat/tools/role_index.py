"""Phase 21 — inverted role index for template retrieval.

`role_id -> List[template_id]` built at process start.

Per specs/IA_FULL_SPEC_2026-05-10.md Phase 21.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional


class RoleIndex:
    """Inverted index from role_id (and constrained class) to template_id list."""

    def __init__(self) -> None:
        self._role_to_templates: Dict[str, List[str]] = {}
        self._class_to_templates: Dict[str, List[str]] = {}

    def add_template(self, template_id: str, roles: Dict[str, Dict]) -> None:
        for role_name, role_spec in roles.items():
            self._role_to_templates.setdefault(role_name, []).append(template_id)
            for klass in role_spec.get("constraints", []):
                self._class_to_templates.setdefault(klass, []).append(template_id)

    def find_by_role(self, role_name: str) -> List[str]:
        return list(self._role_to_templates.get(role_name, []))

    def find_by_class(self, klass: str) -> List[str]:
        return list(self._class_to_templates.get(klass, []))


_DEFAULT_INDEX: Optional[RoleIndex] = None


def get_default_index() -> RoleIndex:
    global _DEFAULT_INDEX
    if _DEFAULT_INDEX is None:
        _DEFAULT_INDEX = RoleIndex()
    return _DEFAULT_INDEX
