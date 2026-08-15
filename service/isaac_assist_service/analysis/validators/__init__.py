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
from .sim_readiness import SimReadinessRule
from .nvidia_usd_validation import NvidiaUsdValidationRule

logger = logging.getLogger(__name__)

# ── Validator registry ────────────────────────────────────────────────────
# Maps pack name → validator class.  Use `register_validator()` to add
# custom validators at runtime, or rely on auto-registration below.

_REGISTRY: Dict[str, Type[ValidationRule]] = {}
_DEFAULT_ENABLED: set[str] = set()


def register_validator(
    pack: str,
    cls: Type[ValidationRule],
    *,
    default_enabled: bool = True,
) -> None:
    """Register a validator class under a pack name.

    External or comparatively expensive validators can register with
    ``default_enabled=False``.  They remain discoverable and can be selected
    explicitly without changing the deterministic built-in analysis path.
    """
    _REGISTRY[pack] = cls
    if default_enabled:
        _DEFAULT_ENABLED.add(pack)
    else:
        _DEFAULT_ENABLED.discard(pack)
    logger.debug(f"Registered validator pack: {pack}")


def get_registered_validators() -> Dict[str, Type[ValidationRule]]:
    """Return a copy of the registry."""
    return dict(_REGISTRY)


def get_default_enabled_validators() -> set[str]:
    """Return pack names included when no explicit pack list is supplied."""
    return set(_DEFAULT_ENABLED)


def create_all_validators(
    enabled_packs: List[str] | None = None,
) -> List[ValidationRule]:
    """
    Instantiate validators from the registry.
    If `enabled_packs` is None, all default-enabled packs are enabled.
    """
    selected = _DEFAULT_ENABLED if enabled_packs is None else set(enabled_packs)
    instances = []
    for pack, cls in _REGISTRY.items():
        if pack in selected:
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
register_validator("sim_readiness", SimReadinessRule)
register_validator(
    "nvidia_usd_validation",
    NvidiaUsdValidationRule,
    default_enabled=False,
)


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
    "SimReadinessRule",
    "NvidiaUsdValidationRule",
    "register_validator",
    "get_registered_validators",
    "get_default_enabled_validators",
    "create_all_validators",
]
