"""Phase 86 — Settings exposure: every runtime knob via MCP.

Real implementation: SettingsRegistry with type validation, defaults, and
custom validators.

Per specs/IA_FULL_SPEC_2026-05-10.md Phase 86.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional


PHASE_ID = 86
PHASE_TITLE = "Settings exposure: every runtime knob via MCP"
PHASE_STATUS = "landed"

logger = logging.getLogger(__name__)


def get_phase_metadata() -> Dict[str, Any]:
    """Return phase identification and status for Phase 86.

    Returns:
        Dict[str, Any]: Keys ``phase``, ``title``, ``status``, and ``spec_ref``.
    """
    return {
        "phase": PHASE_ID,
        "title": PHASE_TITLE,
        "status": PHASE_STATUS,
        "spec_ref": "specs/IA_FULL_SPEC_2026-05-10.md Phase 86",
    }


@dataclass
class Setting:
    """Descriptor for a single runtime knob."""

    name: str
    type: type
    default: Any
    description: str
    validator: Optional[Callable[[Any], None]] = field(default=None, repr=False)


class SettingsRegistry:
    """Registry that holds and validates all runtime settings."""

    def __init__(self) -> None:
        """Initialise the registry with empty settings and values dicts."""
        self._settings: Dict[str, Setting] = {}
        self._values: Dict[str, Any] = {}

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(self, setting: Setting) -> None:
        """Register a new setting.  Replaces any existing entry with the same name."""
        if not isinstance(setting, Setting):
            raise TypeError(f"Expected Setting instance, got {type(setting)}")
        self._settings[setting.name] = setting
        self._values[setting.name] = setting.default

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def list_all(self) -> List[Dict[str, Any]]:
        """Return all registered settings as a list of dicts.

        Each dict has keys ``name``, ``type``, ``current_value``, ``default``,
        and ``description``.
        """
        return [
            {
                "name": s.name,
                "type": s.type.__name__,
                "current_value": self._values[s.name],
                "default": s.default,
                "description": s.description,
            }
            for s in self._settings.values()
        ]

    def get(self, key: str) -> Any:
        """Return current value for *key*.

        Raises
        ------
        KeyError
            When *key* is not registered.
        """
        if key not in self._settings:
            raise KeyError(f"Unknown setting: {key!r}")
        return self._values[key]

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def set(self, key: str, value: Any) -> None:
        """Set *key* to *value*.

        Raises
        ------
        KeyError
            When *key* is not registered.
        TypeError
            When *value* has the wrong type.
        ValueError
            When the registered validator rejects *value*.
        """
        if key not in self._settings:
            raise KeyError(f"Unknown setting: {key!r}")

        setting = self._settings[key]

        # Type coercion attempt for numeric compatibility (e.g. int→float)
        if not isinstance(value, setting.type):
            # Allow int when float is expected
            if setting.type is float and isinstance(value, int):
                value = float(value)
            else:
                raise TypeError(
                    f"Setting {key!r} expects {setting.type.__name__}, "
                    f"got {type(value).__name__}"
                )

        if setting.validator is not None:
            setting.validator(value)

        self._values[key] = value
        logger.debug("settings: %s = %r", key, value)

    def reset(self, key: str) -> None:
        """Reset *key* to its registered default.

        Raises
        ------
        KeyError
            When *key* is not registered.
        """
        if key not in self._settings:
            raise KeyError(f"Unknown setting: {key!r}")
        self._values[key] = self._settings[key].default


# ---------------------------------------------------------------------------
# Module-level singleton with default knobs
# ---------------------------------------------------------------------------

_registry = SettingsRegistry()

_DEFAULT_SETTINGS: List[Setting] = [
    Setting(
        name="result_cap_default_chars",
        type=int,
        default=50_000,
        description="Maximum number of characters returned per tool result.",
    ),
    Setting(
        name="telemetry_enabled",
        type=bool,
        default=False,
        description="Enable anonymous usage telemetry.",
    ),
    Setting(
        name="log_level",
        type=str,
        default="INFO",
        description="Root log level for the service (DEBUG, INFO, WARNING, ERROR).",
        validator=lambda v: (_ for _ in ()).throw(
            ValueError(f"Invalid log level: {v!r}")
        )
        if v not in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
        else None,
    ),
    Setting(
        name="dispatch_timeout_s",
        type=float,
        default=30.0,
        description="Seconds before an async dispatch is considered timed out.",
    ),
]

for _s in _DEFAULT_SETTINGS:
    _registry.register(_s)


def get_registry() -> SettingsRegistry:
    """Return the module-level singleton registry."""
    return _registry
