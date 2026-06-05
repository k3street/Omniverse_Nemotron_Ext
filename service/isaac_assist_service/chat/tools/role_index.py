"""Phase 21 — inverted role index for template retrieval.

`role_id -> List[template_id]` built at process start.

Per specs/IA_FULL_SPEC_2026-05-10.md Phase 21.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional


class RoleIndex:
    """Inverted index from role_id (and constrained class) to template_id list."""

    def __init__(self) -> None:
        """Initialise empty role-id and class-name inverted indices."""
        self._role_to_templates: Dict[str, List[str]] = {}
        self._class_to_templates: Dict[str, List[str]] = {}

    def add_template(self, template_id: str, roles: Dict[str, Dict]) -> None:
        """Register a template against all of its declared roles and constraints.

        Args:
            template_id (str): Unique identifier for the template.
            roles (dict): Mapping of ``role_name -> role_spec`` dicts; each
                spec may contain a ``"constraints"`` list of robot-class strings.
        """
        for role_name, role_spec in roles.items():
            self._role_to_templates.setdefault(role_name, []).append(template_id)
            for klass in role_spec.get("constraints", []):
                self._class_to_templates.setdefault(klass, []).append(template_id)

    def find_by_role(self, role_name: str) -> List[str]:
        """Return template IDs associated with ``role_name``.

        Args:
            role_name (str): Role identifier, e.g. ``"robot_technician"``.

        Returns:
            list[str]: Template IDs in insertion order; empty list if unknown.
        """
        return list(self._role_to_templates.get(role_name, []))

    def find_by_class(self, klass: str) -> List[str]:
        """Return template IDs whose role constraints include ``klass``.

        Args:
            klass (str): Robot-class constraint string, e.g. ``"franka_panda"``.

        Returns:
            list[str]: Matching template IDs; empty list if none registered.
        """
        return list(self._class_to_templates.get(klass, []))


_DEFAULT_INDEX: Optional[RoleIndex] = None


def get_default_index() -> RoleIndex:
    """Return the module-level singleton RoleIndex, creating it on first call.

    Returns:
        RoleIndex: Shared default index instance.
    """
    global _DEFAULT_INDEX
    if _DEFAULT_INDEX is None:
        _DEFAULT_INDEX = RoleIndex()
    return _DEFAULT_INDEX
