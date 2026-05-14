"""Phase 75 — user-supplied object class registry.

Users can register custom object classes beyond the 60-class palette
(Phase 25). Each entry has a name + USD reference + footprint metadata.

Per specs/IA_FULL_SPEC_2026-05-10.md Phase 75.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


PHASE_ID = 75
PHASE_TITLE = "User-supplied object class registry"
PHASE_STATUS = "landed"


@dataclass
class UserObjectClass:
    """A user-supplied custom object class extending the 60-class palette."""
    name: str
    usd_ref: str = ""
    category: str = "user_prop"
    footprint_xy_m: tuple = (0.1, 0.1)
    tags: List[str] = field(default_factory=list)
    added_by: str = "anonymous"


class UserObjectClassRegistry:
    """In-memory user object class registry."""

    def __init__(self) -> None:
        """Initialise the registry with an empty entries dict."""
        self._entries: Dict[str, UserObjectClass] = {}

    def register(self, entry: UserObjectClass) -> bool:
        """Register a new class. Returns False if name conflict."""
        if entry.name in self._entries:
            return False
        self._entries[entry.name] = entry
        return True

    def get(self, name: str) -> Optional[UserObjectClass]:
        """Return the class with *name*, or ``None`` if not registered."""
        return self._entries.get(name)

    def list_by_user(self, user: str) -> List[UserObjectClass]:
        """Return all classes registered by *user*."""
        return [e for e in self._entries.values() if e.added_by == user]

    def all(self) -> List[UserObjectClass]:
        """Return all registered user object classes."""
        return list(self._entries.values())


_DEFAULT: Optional[UserObjectClassRegistry] = None


def get_default_registry() -> UserObjectClassRegistry:
    """Return the process-wide singleton ``UserObjectClassRegistry``, creating it on first call."""
    global _DEFAULT
    if _DEFAULT is None:
        _DEFAULT = UserObjectClassRegistry()
    return _DEFAULT


def get_phase_metadata() -> Dict[str, Any]:
    """Return phase identification and status for Phase 75.

    Returns:
        Dict[str, Any]: Keys ``phase``, ``title``, ``status``, and ``spec_ref``.
    """
    return {
        "phase": PHASE_ID, "title": PHASE_TITLE, "status": PHASE_STATUS,
        "spec_ref": "specs/IA_FULL_SPEC_2026-05-10.md Phase 75",
    }
