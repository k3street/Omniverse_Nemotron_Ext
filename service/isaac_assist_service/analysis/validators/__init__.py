import logging
from typing import Dict, Type, List

from .base import ValidationRule
from .schema_consistency import SchemaConsistencyRule
from .import_health import ImportHealthValidator
from .material_physics import MaterialPhysicsMismatchValidator
from .articulation_integrity import ArticulationIntegrityValidator
from .sensor_completeness import SensorCompletenessValidator
from .ros_bridge_readiness import ROSBridgeReadinessValidator
from .performance_warnings import PerformanceWarningsValidator
from .isaaclab_sanity import IsaacLabSanityValidator

logger = logging.getLogger(__name__)

# ── Validator registry ────────────────────────────────────────────────────
# Maps pack name → validator class.  Use `register_validator()` to add
# custom validators at runtime, or rely on auto-registration below.

_REGISTRY: Dict[str, Type[ValidationRule]] = {}


def register_validator(pack: str, cls: Type[ValidationRule]) -> None:
    """Register a validator class under a pack name."""
    _REGISTRY[pack] = cls
    logger.debug(f"Registered validator pack: {pack}")


def get_registered_validators() -> Dict[str, Type[ValidationRule]]:
    """Return a copy of the registry."""
    return dict(_REGISTRY)


def create_all_validators(
    enabled_packs: List[str] | None = None,
) -> List[ValidationRule]:
    """
    Instantiate validators from the registry.
    If `enabled_packs` is None, all registered packs are enabled.
    """
    instances = []
    for pack, cls in _REGISTRY.items():
        if enabled_packs is None or pack in enabled_packs:
            instances.append(cls())
    return instances


# ── Auto-register built-in validators ─────────────────────────────────────
register_validator("schema_consistency", SchemaConsistencyRule)
register_validator("import_health", ImportHealthValidator)
register_validator("material_physics", MaterialPhysicsMismatchValidator)
register_validator("articulation_integrity", ArticulationIntegrityValidator)
register_validator("sensor_completeness", SensorCompletenessValidator)
register_validator("ros_bridge_readiness", ROSBridgeReadinessValidator)
register_validator("performance_warnings", PerformanceWarningsValidator)
register_validator("isaaclab_sanity", IsaacLabSanityValidator)


__all__ = [
    "ValidationRule",
    "SchemaConsistencyRule",
    "ImportHealthValidator",
    "MaterialPhysicsMismatchValidator",
    "ArticulationIntegrityValidator",
    "SensorCompletenessValidator",
    "ROSBridgeReadinessValidator",
    "PerformanceWarningsValidator",
    "IsaacLabSanityValidator",
    "register_validator",
    "get_registered_validators",
    "create_all_validators",
]
