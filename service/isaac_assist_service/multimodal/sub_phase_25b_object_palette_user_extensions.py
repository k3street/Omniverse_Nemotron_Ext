"""Phase 25b — object palette user extensions.

YAML-driven extension loader that lets users register custom object classes
beyond the 60-class built-in palette.  Builtin classes have absolute precedence:
a user YAML entry whose name matches a builtin is refused with
``conflict_with_builtin``.

Per specs/IA_FULL_SPEC_2026-05-10.md Phase 25b.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from .object_palette import PALETTE, ObjectClass
from .user_object_class_registry import UserObjectClass, UserObjectClassRegistry

PHASE_ID = "25b"
PHASE_TITLE = "object palette user extensions"
PHASE_STATUS = "landed"

_DEFAULT_YAML_DIR = Path.home() / ".isaac_assist" / "object_classes"

# Required top-level keys in every user YAML entry.
_REQUIRED_KEYS = {"name"}
_VALID_CATEGORIES = {"robot", "sensor", "fixture", "prop", "environment", "user_prop"}


def get_phase_metadata() -> Dict[str, Any]:
    """Return phase identification and status for this phase.

    Returns:
        Dict[str, Any]: Keys ``phase``, ``title``, ``status``, and ``spec_ref``.
    """
    return {
        "phase": PHASE_ID,
        "title": PHASE_TITLE,
        "status": PHASE_STATUS,
        "spec_ref": "specs/IA_FULL_SPEC_2026-05-10.md Phase 25b",
    }


class PaletteExtensionLoader:
    """Load user-supplied object class YAML files and register them.

    Each ``*.yaml`` file in *yaml_dir* is expected to contain a single mapping
    at the top level with at least the key ``name``.  Optional keys mirror the
    fields of ``UserObjectClass``:

    .. code-block:: yaml

       name: my_custom_fixture
       usd_ref: "omniverse://server/MyAssets/fixture.usd"
       category: fixture
       footprint_xy_m: [0.5, 0.5]
       tags: [fixture, custom]
       added_by: alice

    Parameters
    ----------
    yaml_dir:
        Directory that is scanned for ``*.yaml`` files.  Defaults to
        ``~/.isaac_assist/object_classes/``.
    """

    def __init__(self, yaml_dir: Optional[Path] = None) -> None:
        """Initialise the loader with the YAML extension directory (defaults to ``~/.isaac_assist/object_classes/``)."""
        self.yaml_dir: Path = yaml_dir if yaml_dir is not None else _DEFAULT_YAML_DIR

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load_directory(self) -> List[UserObjectClass]:
        """Walk *yaml_dir* and parse every ``*.yaml`` into a UserObjectClass.

        Files that fail validation are silently skipped (callers get the full
        status map from :meth:`register_all`).
        """
        entries: List[UserObjectClass] = []
        if not self.yaml_dir.exists():
            return entries
        for yaml_path in sorted(self.yaml_dir.glob("*.yaml")):
            entry = self._parse_file(yaml_path)
            if entry is not None:
                entries.append(entry)
        return entries

    def register_all(
        self, registry: UserObjectClassRegistry
    ) -> Dict[str, str]:
        """Register all YAML files found in *yaml_dir* into *registry*.

        Returns a mapping of ``filename → status`` where *status* is one of:

        * ``"registered"`` — successfully added.
        * ``"conflict_with_builtin"`` — name already exists in the 60-class
          builtin palette; entry is rejected.
        * ``"conflict_with_user"`` — name already registered by a previous user
          YAML (first-file-wins); entry is rejected.
        * ``"invalid"`` — YAML could not be parsed or failed validation.
        """
        result: Dict[str, str] = {}
        if not self.yaml_dir.exists():
            return result

        for yaml_path in sorted(self.yaml_dir.glob("*.yaml")):
            fname = yaml_path.name
            data = self._load_yaml(yaml_path)
            if data is None:
                result[fname] = "invalid"
                continue

            error = self.validate_yaml_entry(data)
            if error is not None:
                result[fname] = "invalid"
                continue

            name = data["name"]

            if name in PALETTE:
                result[fname] = "conflict_with_builtin"
                continue

            if registry.get(name) is not None:
                result[fname] = "conflict_with_user"
                continue

            entry = self._build_user_class(data)
            registry.register(entry)
            result[fname] = "registered"

        return result

    def validate_yaml_entry(self, data: dict) -> Optional[str]:
        """Validate a parsed YAML dict.

        Returns ``None`` on success, or an error message string on failure.
        """
        if not isinstance(data, dict):
            return "YAML root must be a mapping"
        for key in _REQUIRED_KEYS:
            if key not in data:
                return f"missing required field: '{key}'"
        name = data["name"]
        if not isinstance(name, str) or not name.strip():
            return "'name' must be a non-empty string"
        if "category" in data and data["category"] not in _VALID_CATEGORIES:
            return (
                f"invalid category '{data['category']}'; "
                f"must be one of {sorted(_VALID_CATEGORIES)}"
            )
        if "footprint_xy_m" in data:
            fp = data["footprint_xy_m"]
            if not (isinstance(fp, (list, tuple)) and len(fp) == 2):
                return "'footprint_xy_m' must be a list of two numbers"
        return None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _load_yaml(path: Path) -> Optional[dict]:
        """Load and parse a YAML file; returns None on any parse error."""
        try:
            with path.open("r", encoding="utf-8") as fh:
                return yaml.safe_load(fh)
        except Exception:
            return None

    def _parse_file(self, path: Path) -> Optional[UserObjectClass]:
        """Parse a single YAML file into a UserObjectClass; returns None if invalid."""
        data = self._load_yaml(path)
        if data is None:
            return None
        if self.validate_yaml_entry(data) is not None:
            return None
        return self._build_user_class(data)

    @staticmethod
    def _build_user_class(data: dict) -> UserObjectClass:
        """Construct a :class:`UserObjectClass` from a validated YAML dict."""
        fp_raw = data.get("footprint_xy_m", [0.1, 0.1])
        footprint: tuple = tuple(fp_raw) if isinstance(fp_raw, (list, tuple)) else (0.1, 0.1)
        return UserObjectClass(
            name=data["name"],
            usd_ref=data.get("usd_ref", ""),
            category=data.get("category", "user_prop"),
            footprint_xy_m=footprint,
            tags=list(data.get("tags", [])),
            added_by=str(data.get("added_by", "anonymous")),
        )


def merged_palette(registry: UserObjectClassRegistry) -> Dict[str, Any]:
    """Return a combined palette dict of builtin + user entries.

    Builtin entries are always included under their canonical names.
    User entries are included only when their name does *not* collide with a
    builtin — i.e. builtins have absolute precedence.
    """
    combined: Dict[str, Any] = dict(PALETTE)
    for user_class in registry.all():
        if user_class.name not in combined:
            combined[user_class.name] = user_class
    return combined
