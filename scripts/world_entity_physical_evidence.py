"""Task-neutral physical evidence for entities in a semantic scene inventory.

The contract deliberately describes observed world entities, not robots or
controllers.  Runtime adapters may populate it from USD/physics backends while
reasoning and capability layers consume the same JSON-compatible structure.
"""
from __future__ import annotations

import math
from typing import Any, Mapping, Sequence


WORLD_ENTITY_PHYSICAL_EVIDENCE_SCHEMA_VERSION = (
    "world-entity-physical-evidence.v1"
)
MOBILITY_STATUSES = frozenset(
    {"dynamic", "deformable", "kinematic", "fixed", "unknown"}
)


class WorldEntityPhysicalEvidenceError(ValueError):
    """Raised when physical or geometric evidence is malformed."""


def _finite_nonnegative(value: Any, path: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise WorldEntityPhysicalEvidenceError(
            f"{path} must be a finite non-negative number"
        )
    result = float(value)
    if not math.isfinite(result) or result < 0.0:
        raise WorldEntityPhysicalEvidenceError(
            f"{path} must be a finite non-negative number"
        )
    return result


def build_entity_physical_evidence(
    *,
    entity_id: str,
    prim_path: str | None,
    rigid_body_records: Sequence[Mapping[str, Any]] = (),
    registered_dynamic: bool = False,
    registered_deformable: bool = False,
    prim_observed: bool = False,
    mass_kg: float | None = None,
    mass_source: str | None = None,
) -> dict[str, Any]:
    """Build one conservative entity evidence record from runtime facts.

    ``rigid_body_records`` contains the authored/live USD rigid-body state.  A
    runtime physics registration may also prove mobility when a referenced USD
    subtree cannot be resolved.  Absence of either fact remains ``unknown``;
    only an observed prim with no enabled body is classified as fixed.
    """
    if not isinstance(entity_id, str) or not entity_id.strip():
        raise WorldEntityPhysicalEvidenceError("entity_id must be non-empty text")
    if prim_path is not None and (
        not isinstance(prim_path, str) or not prim_path.strip()
    ):
        raise WorldEntityPhysicalEvidenceError(
            "prim_path must be null or non-empty text"
        )
    if not isinstance(registered_dynamic, bool) or not isinstance(
        registered_deformable, bool
    ):
        raise WorldEntityPhysicalEvidenceError(
            "runtime registration flags must be boolean"
        )
    if not isinstance(prim_observed, bool):
        raise WorldEntityPhysicalEvidenceError("prim_observed must be boolean")

    normalized_bodies: list[dict[str, Any]] = []
    for index, raw in enumerate(rigid_body_records):
        if not isinstance(raw, Mapping):
            raise WorldEntityPhysicalEvidenceError(
                f"rigid_body_records[{index}] must be an object"
            )
        path = raw.get("prim_path")
        if not isinstance(path, str) or not path.strip():
            raise WorldEntityPhysicalEvidenceError(
                f"rigid_body_records[{index}].prim_path must be non-empty text"
            )
        enabled = raw.get("enabled")
        kinematic = raw.get("kinematic")
        if not isinstance(enabled, bool) or not isinstance(kinematic, bool):
            raise WorldEntityPhysicalEvidenceError(
                f"rigid_body_records[{index}] flags must be boolean"
            )
        normalized_bodies.append(
            {
                "prim_path": path.strip(),
                "enabled": enabled,
                "kinematic": kinematic,
            }
        )

    enabled_bodies = [item for item in normalized_bodies if item["enabled"]]
    if registered_deformable:
        mobility_status = "deformable"
        mobility_source = "active_runtime_deformable_registry"
    elif any(not item["kinematic"] for item in enabled_bodies):
        mobility_status = "dynamic"
        mobility_source = "live_usd_rigid_body_state"
    elif enabled_bodies:
        mobility_status = "kinematic"
        mobility_source = "live_usd_rigid_body_state"
    elif registered_dynamic:
        mobility_status = "dynamic"
        mobility_source = "active_runtime_rigid_object_registry"
    elif prim_observed:
        mobility_status = "fixed"
        mobility_source = "observed_usd_subtree_without_enabled_body"
    else:
        mobility_status = "unknown"
        mobility_source = "runtime_physics_evidence_unavailable"

    normalized_mass = None
    if mass_kg is not None:
        normalized_mass = _finite_nonnegative(mass_kg, "mass_kg")
    if mass_source is not None and (
        not isinstance(mass_source, str) or not mass_source.strip()
    ):
        raise WorldEntityPhysicalEvidenceError(
            "mass_source must be null or non-empty text"
        )

    return {
        "schema_version": WORLD_ENTITY_PHYSICAL_EVIDENCE_SCHEMA_VERSION,
        "entity_id": entity_id.strip(),
        "source": "active_simulator_physics",
        "prim_path": None if prim_path is None else prim_path.strip(),
        "mobility": {
            "available": mobility_status != "unknown",
            "status": mobility_status,
            "source": mobility_source,
            "rigid_body_records": normalized_bodies,
        },
        "mass": {
            "available": normalized_mass is not None,
            "mass_kg": normalized_mass,
            "source": mass_source.strip() if mass_source else None,
        },
        "execution_authority": False,
    }


def _visible_extent(geometry: Mapping[str, Any], path: str) -> tuple[float, ...]:
    raw_extent = geometry.get("visible_extent_base_m")
    if isinstance(raw_extent, Sequence) and not isinstance(raw_extent, (str, bytes)):
        if len(raw_extent) != 3:
            raise WorldEntityPhysicalEvidenceError(f"{path} extent must have 3 values")
        return tuple(_finite_nonnegative(item, f"{path}[{index}]") for index, item in enumerate(raw_extent))

    raw_min = geometry.get("visible_aabb_min_base_m")
    raw_max = geometry.get("visible_aabb_max_base_m")
    if not (
        isinstance(raw_min, Sequence)
        and not isinstance(raw_min, (str, bytes))
        and isinstance(raw_max, Sequence)
        and not isinstance(raw_max, (str, bytes))
        and len(raw_min) == 3
        and len(raw_max) == 3
    ):
        raise WorldEntityPhysicalEvidenceError(
            f"{path} requires visible extent or AABB bounds"
        )
    extent: list[float] = []
    for index, (minimum, maximum) in enumerate(zip(raw_min, raw_max, strict=True)):
        minimum_value = float(minimum)
        maximum_value = float(maximum)
        if not math.isfinite(minimum_value) or not math.isfinite(maximum_value):
            raise WorldEntityPhysicalEvidenceError(
                f"{path} AABB values must be finite"
            )
        if maximum_value < minimum_value:
            raise WorldEntityPhysicalEvidenceError(
                f"{path} AABB maximum precedes minimum"
            )
        extent.append(maximum_value - minimum_value)
    return tuple(extent)


def estimate_visible_destination_capacity(
    subject_geometry: Mapping[str, Any],
    reference_geometry: Mapping[str, Any],
    *,
    clearance_margin_m: float = 0.005,
) -> dict[str, Any]:
    """Estimate an axis-aligned *upper bound* on destination capacity.

    Visible AABBs describe an exterior envelope, not a receptacle's free
    interior.  The result is therefore useful for rejecting obvious non-fits
    and prioritizing planning, but it never grants execution authority.
    """
    margin = _finite_nonnegative(clearance_margin_m, "clearance_margin_m")
    try:
        subject_extent = _visible_extent(subject_geometry, "subject_geometry")
        reference_extent = _visible_extent(
            reference_geometry, "reference_geometry"
        )
    except WorldEntityPhysicalEvidenceError as error:
        return {
            "available": False,
            "source": "visible_rgbd_aabb_geometric_upper_bound",
            "error": str(error),
            "execution_authority": False,
        }

    usable_reference_extent = tuple(
        max(0.0, item - 2.0 * margin) for item in reference_extent
    )
    fit_ratios: list[float | None] = []
    axis_counts: list[int] = []
    for subject_axis, reference_axis in zip(
        subject_extent, usable_reference_extent, strict=True
    ):
        if subject_axis <= 0.0:
            fit_ratios.append(None)
            axis_counts.append(0)
        else:
            fit_ratios.append(reference_axis / subject_axis)
            axis_counts.append(int(math.floor(reference_axis / subject_axis)))
    fits = all(item >= 1 for item in axis_counts)
    count_upper_bound = math.prod(axis_counts) if fits else 0
    return {
        "available": True,
        "source": "visible_rgbd_aabb_geometric_upper_bound",
        "authority": "planning_only_upper_bound",
        "subject_extent_m": list(subject_extent),
        "reference_visible_extent_m": list(reference_extent),
        "clearance_margin_m": margin,
        "usable_reference_extent_upper_bound_m": list(
            usable_reference_extent
        ),
        "axis_fit_ratios": fit_ratios,
        "subject_fits_observed_envelope": fits,
        "axis_aligned_count_upper_bound": count_upper_bound,
        "interior_clearance_observed": False,
        "execution_authority": False,
        "limitation": (
            "visible exterior bounds do not prove free interior volume, "
            "wall thickness, occupancy, or an insertion path"
        ),
    }
