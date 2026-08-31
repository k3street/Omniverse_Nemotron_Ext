"""Dynamic discovery of tools that can realize task-neutral world effects.

Goal graphs describe only desired world state.  This registry is a separate
runtime layer: provider recipes state which semantic tool capabilities they
need, and currently advertised tools are matched without naming an embodiment,
controller, end effector, or task routine in the goal contract.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Mapping, Sequence


WORLD_EFFECT_PROVIDER_SCHEMA_VERSION = "world-effect-provider.v1"
TOOL_ACTIVATION_STATUSES = frozenset({"factory_available", "active"})
_IDENTIFIER = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")
_CAPABILITY_TAG = re.compile(r"^[a-z][a-z0-9_.:-]{0,127}$")


class WorldEffectProviderError(ValueError):
    """Raised when provider discovery evidence violates its contract."""


def _identifier(value: Any, path: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise WorldEffectProviderError(f"{path} has an invalid format")
    return value


def _capability_tags(value: Sequence[str], path: str) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)):
        raise WorldEffectProviderError(f"{path} must be an array")
    result: list[str] = []
    for index, tag in enumerate(value):
        if not isinstance(tag, str) or not _CAPABILITY_TAG.fullmatch(tag):
            raise WorldEffectProviderError(
                f"{path}[{index}] has an invalid format"
            )
        result.append(tag)
    if len(set(result)) != len(result):
        raise WorldEffectProviderError(f"{path} must not contain duplicates")
    return tuple(result)


@dataclass(frozen=True)
class RuntimeToolCapability:
    """One active tool or available tool factory and its world semantics."""

    tool_id: str
    tool_family: str
    capability_tags: tuple[str, ...]
    activation_status: str
    source: str
    tool_advertisement: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        _identifier(self.tool_id, "tool_id")
        _identifier(self.tool_family, "tool_family")
        _capability_tags(self.capability_tags, "capability_tags")
        if self.activation_status not in TOOL_ACTIVATION_STATUSES:
            raise WorldEffectProviderError(
                "activation_status must be factory_available or active"
            )
        if not isinstance(self.source, str) or not self.source.strip():
            raise WorldEffectProviderError("source must be non-empty text")
        if self.tool_advertisement is not None and not isinstance(
            self.tool_advertisement, Mapping
        ):
            raise WorldEffectProviderError("tool_advertisement must be an object")
        if self.tool_advertisement is not None:
            advertised_id = self.tool_advertisement.get(
                "executor_id", self.tool_advertisement.get("tool_id")
            )
            if advertised_id is not None and advertised_id != self.tool_id:
                raise WorldEffectProviderError(
                    "tool_advertisement id must match runtime tool_id"
                )
            advertised_family = self.tool_advertisement.get("tool_family")
            if (
                advertised_family is not None
                and advertised_family != self.tool_family
            ):
                raise WorldEffectProviderError(
                    "tool_advertisement family must match runtime tool_family"
                )

    def to_dict(self) -> dict[str, Any]:
        result = {
            "tool_id": self.tool_id,
            "tool_family": self.tool_family,
            "capability_tags": list(self.capability_tags),
            "activation_status": self.activation_status,
            "source": self.source,
            "execution_authority": False,
        }
        if self.tool_advertisement is not None:
            result.update(dict(self.tool_advertisement))
            result.update(
                {
                    "tool_id": self.tool_id,
                    "tool_family": self.tool_family,
                    "capability_tags": list(self.capability_tags),
                    "activation_status": self.activation_status,
                    "source": self.source,
                    "execution_authority": False,
                }
            )
        return result


@dataclass(frozen=True)
class WorldEffectProviderRequirement:
    requirement_id: str
    accepted_tool_families: tuple[str, ...]
    required_capability_tags: tuple[str, ...]
    description: str

    def __post_init__(self) -> None:
        _identifier(self.requirement_id, "requirement_id")
        if not self.accepted_tool_families:
            raise WorldEffectProviderError(
                "accepted_tool_families must not be empty"
            )
        for index, family in enumerate(self.accepted_tool_families):
            _identifier(family, f"accepted_tool_families[{index}]")
        if len(set(self.accepted_tool_families)) != len(
            self.accepted_tool_families
        ):
            raise WorldEffectProviderError(
                "accepted_tool_families must not contain duplicates"
            )
        tags = _capability_tags(
            self.required_capability_tags, "required_capability_tags"
        )
        if not tags:
            raise WorldEffectProviderError(
                "required_capability_tags must not be empty"
            )
        if not isinstance(self.description, str) or not self.description.strip():
            raise WorldEffectProviderError("description must be non-empty text")

    def advertisement(self) -> dict[str, Any]:
        return {
            "requirement_id": self.requirement_id,
            "accepted_tool_families": list(self.accepted_tool_families),
            "required_capability_tags": list(self.required_capability_tags),
            "description": self.description,
        }


@dataclass(frozen=True)
class WorldEffectProviderSpec:
    provider_id: str
    supported_world_capability_ids: tuple[str, ...]
    requirements: tuple[WorldEffectProviderRequirement, ...]
    description: str

    def __post_init__(self) -> None:
        _identifier(self.provider_id, "provider_id")
        if not self.supported_world_capability_ids:
            raise WorldEffectProviderError(
                "supported_world_capability_ids must not be empty"
            )
        for index, capability_id in enumerate(
            self.supported_world_capability_ids
        ):
            _identifier(
                capability_id,
                f"supported_world_capability_ids[{index}]",
            )
        if not self.requirements:
            raise WorldEffectProviderError("requirements must not be empty")
        requirement_ids = [item.requirement_id for item in self.requirements]
        if len(set(requirement_ids)) != len(requirement_ids):
            raise WorldEffectProviderError(
                "provider requirement ids must be unique"
            )
        if not isinstance(self.description, str) or not self.description.strip():
            raise WorldEffectProviderError("description must be non-empty text")

    def advertisement(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "supported_world_capability_ids": list(
                self.supported_world_capability_ids
            ),
            "requirements": [
                item.advertisement() for item in self.requirements
            ],
            "description": self.description,
            "execution_authority": False,
        }


@dataclass(frozen=True)
class WorldEffectProviderBinding:
    provider_id: str
    world_capability_id: str
    compatible: bool
    active: bool
    requirement_bindings: tuple[Mapping[str, Any], ...]
    missing_requirement_ids: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "world_capability_id": self.world_capability_id,
            "compatible": self.compatible,
            "active": self.active,
            "requirement_bindings": [dict(item) for item in self.requirement_bindings],
            "missing_requirement_ids": list(self.missing_requirement_ids),
            "execution_authority": False,
        }


@dataclass(frozen=True)
class WorldEffectProviderAssessment:
    world_capability_id: str
    bindings: tuple[WorldEffectProviderBinding, ...]

    @property
    def binding_ready(self) -> bool:
        return any(item.compatible for item in self.bindings)

    @property
    def active_binding_ready(self) -> bool:
        return any(item.active for item in self.bindings)

    def preferred_binding(self) -> WorldEffectProviderBinding | None:
        candidates = sorted(
            (item for item in self.bindings if item.compatible),
            key=lambda item: (not item.active, item.provider_id),
        )
        return candidates[0] if candidates else None

    def to_dict(self) -> dict[str, Any]:
        preferred = self.preferred_binding()
        return {
            "schema_version": WORLD_EFFECT_PROVIDER_SCHEMA_VERSION,
            "world_capability_id": self.world_capability_id,
            "binding_ready": self.binding_ready,
            "active_binding_ready": self.active_binding_ready,
            "preferred_provider_id": (
                preferred.provider_id if preferred is not None else None
            ),
            "bindings": [item.to_dict() for item in self.bindings],
            "execution_authority": False,
        }


class WorldEffectProviderRegistry:
    """Match world effects to whatever semantic tools exist at runtime."""

    def __init__(self) -> None:
        self._specs: dict[str, WorldEffectProviderSpec] = {}

    def register(self, spec: WorldEffectProviderSpec) -> None:
        if not isinstance(spec, WorldEffectProviderSpec):
            raise WorldEffectProviderError(
                "provider registration requires WorldEffectProviderSpec"
            )
        if spec.provider_id in self._specs:
            raise WorldEffectProviderError(
                f"provider {spec.provider_id!r} is already registered"
            )
        self._specs[spec.provider_id] = spec

    def advertisement(self) -> dict[str, Any]:
        return {
            "schema_version": WORLD_EFFECT_PROVIDER_SCHEMA_VERSION,
            "providers": [
                self._specs[key].advertisement() for key in sorted(self._specs)
            ],
            "execution_authority": False,
        }

    def assess(
        self,
        world_capability_id: str,
        tools: Sequence[RuntimeToolCapability],
    ) -> WorldEffectProviderAssessment:
        capability_id = _identifier(
            world_capability_id, "world_capability_id"
        )
        if isinstance(tools, (str, bytes)):
            raise WorldEffectProviderError("tools must be an array")
        normalized_tools: list[RuntimeToolCapability] = []
        seen_tool_ids: set[str] = set()
        for index, tool in enumerate(tools):
            if not isinstance(tool, RuntimeToolCapability):
                raise WorldEffectProviderError(
                    f"tools[{index}] must be RuntimeToolCapability"
                )
            if tool.tool_id in seen_tool_ids:
                raise WorldEffectProviderError(
                    f"duplicate runtime tool id {tool.tool_id!r}"
                )
            seen_tool_ids.add(tool.tool_id)
            normalized_tools.append(tool)

        bindings: list[WorldEffectProviderBinding] = []
        for provider_id in sorted(self._specs):
            spec = self._specs[provider_id]
            if capability_id not in spec.supported_world_capability_ids:
                continue
            requirement_bindings: list[Mapping[str, Any]] = []
            missing: list[str] = []
            selected_tools: list[RuntimeToolCapability] = []
            for requirement in spec.requirements:
                required_tags = set(requirement.required_capability_tags)
                matches = sorted(
                    (
                        tool
                        for tool in normalized_tools
                        if tool.tool_family in requirement.accepted_tool_families
                        and required_tags.issubset(tool.capability_tags)
                    ),
                    key=lambda tool: (
                        tool.activation_status != "active",
                        tool.tool_id,
                    ),
                )
                selected = matches[0] if matches else None
                if selected is None:
                    missing.append(requirement.requirement_id)
                else:
                    selected_tools.append(selected)
                requirement_bindings.append(
                    {
                        "requirement_id": requirement.requirement_id,
                        "tool_id": selected.tool_id if selected else None,
                        "activation_status": (
                            selected.activation_status if selected else None
                        ),
                        "required_capability_tags": list(
                            requirement.required_capability_tags
                        ),
                        "compatible_tools": [
                            {
                                "tool_id": tool.tool_id,
                                "activation_status": tool.activation_status,
                            }
                            for tool in matches
                        ],
                    }
                )
            compatible = not missing
            active = bool(
                compatible
                and selected_tools
                and all(
                    item.activation_status == "active"
                    for item in selected_tools
                )
            )
            bindings.append(
                WorldEffectProviderBinding(
                    provider_id=provider_id,
                    world_capability_id=capability_id,
                    compatible=compatible,
                    active=active,
                    requirement_bindings=tuple(requirement_bindings),
                    missing_requirement_ids=tuple(missing),
                )
            )
        return WorldEffectProviderAssessment(
            world_capability_id=capability_id,
            bindings=tuple(bindings),
        )


def default_world_effect_provider_registry() -> WorldEffectProviderRegistry:
    """Return the task-neutral provider recipes currently understood."""
    registry = WorldEffectProviderRegistry()
    registry.register(
        WorldEffectProviderSpec(
            provider_id="transport.reversible_attachment",
            supported_world_capability_ids=("world_relation.realize_inside",),
            description=(
                "Realize a spatial relation by observing the scene, acquiring "
                "and later releasing an entity, and executing observation-bound "
                "spatial targets. Tool implementations are selected at runtime."
            ),
            requirements=(
                WorldEffectProviderRequirement(
                    requirement_id="fresh_scene_geometry",
                    accepted_tool_families=("sensor",),
                    required_capability_tags=("scene.geometry.rgbd",),
                    description="Publish fresh labeled scene geometry.",
                ),
                WorldEffectProviderRequirement(
                    requirement_id="observation_bound_spatial_motion",
                    accepted_tool_families=("motion",),
                    required_capability_tags=(
                        "spatial.pose_target",
                        "motion.observation_bound",
                        "motion.invalidation_feedback",
                    ),
                    description=(
                        "Execute spatial targets under fresh-observation leases."
                    ),
                ),
                WorldEffectProviderRequirement(
                    requirement_id="reversible_entity_attachment",
                    accepted_tool_families=("actuator",),
                    required_capability_tags=(
                        "entity_attachment.acquire",
                        "entity_attachment.release",
                        "actuation.observation_bound",
                    ),
                    description=(
                        "Acquire and release an entity with feedback between commands."
                    ),
                ),
            ),
        )
    )
    return registry
