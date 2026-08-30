import pytest

from scripts.world_entity_physical_evidence import (
    WorldEntityPhysicalEvidenceError,
    build_entity_physical_evidence,
    estimate_visible_destination_capacity,
)


def test_runtime_facts_classify_dynamic_kinematic_fixed_and_unknown_entities():
    dynamic = build_entity_physical_evidence(
        entity_id="block",
        prim_path="/World/envs/env_0/scene/block",
        rigid_body_records=[
            {
                "prim_path": "/World/envs/env_0/scene/block/body",
                "enabled": True,
                "kinematic": False,
            }
        ],
        prim_observed=True,
        mass_kg=0.125,
        mass_source="live_physx_body_mass",
    )
    kinematic = build_entity_physical_evidence(
        entity_id="animated_prop",
        prim_path="/World/envs/env_0/scene/animated_prop",
        rigid_body_records=[
            {
                "prim_path": "/World/envs/env_0/scene/animated_prop",
                "enabled": True,
                "kinematic": True,
            }
        ],
        prim_observed=True,
    )
    fixed = build_entity_physical_evidence(
        entity_id="table",
        prim_path="/World/envs/env_0/scene/table",
        prim_observed=True,
    )
    unknown = build_entity_physical_evidence(
        entity_id="occluded",
        prim_path=None,
    )

    assert dynamic["mobility"]["status"] == "dynamic"
    assert dynamic["mass"] == {
        "available": True,
        "mass_kg": 0.125,
        "source": "live_physx_body_mass",
    }
    assert kinematic["mobility"]["status"] == "kinematic"
    assert fixed["mobility"]["status"] == "fixed"
    assert unknown["mobility"]["status"] == "unknown"
    assert all(
        item["execution_authority"] is False
        for item in (dynamic, kinematic, fixed, unknown)
    )


def test_active_runtime_registry_can_prove_dynamic_when_usd_path_is_unresolved():
    evidence = build_entity_physical_evidence(
        entity_id="block",
        prim_path=None,
        registered_dynamic=True,
        mass_kg=0.1,
        mass_source="live_physx_body_mass",
    )

    assert evidence["mobility"]["status"] == "dynamic"
    assert evidence["mobility"]["source"] == (
        "active_runtime_rigid_object_registry"
    )


def test_visible_capacity_is_a_planning_upper_bound_not_execution_authority():
    result = estimate_visible_destination_capacity(
        {
            "visible_aabb_min_base_m": [0.5, 0.2, 0.02],
            "visible_aabb_max_base_m": [0.55, 0.25, 0.07],
        },
        {
            "visible_aabb_min_base_m": [0.1, 0.1, 0.0],
            "visible_aabb_max_base_m": [0.4, 0.4, 0.2],
        },
    )

    assert result["available"]
    assert result["subject_fits_observed_envelope"]
    assert result["axis_aligned_count_upper_bound"] > 0
    assert result["interior_clearance_observed"] is False
    assert result["execution_authority"] is False


def test_visible_capacity_rejects_obvious_non_fit_and_reports_missing_geometry():
    non_fit = estimate_visible_destination_capacity(
        {"visible_extent_base_m": [0.5, 0.5, 0.5]},
        {"visible_extent_base_m": [0.3, 0.3, 0.2]},
    )
    unavailable = estimate_visible_destination_capacity({}, {})

    assert non_fit["available"]
    assert not non_fit["subject_fits_observed_envelope"]
    assert non_fit["axis_aligned_count_upper_bound"] == 0
    assert not unavailable["available"]
    assert "requires visible extent" in unavailable["error"]


def test_physical_evidence_rejects_invalid_mass():
    with pytest.raises(WorldEntityPhysicalEvidenceError, match="mass_kg"):
        build_entity_physical_evidence(
            entity_id="block",
            prim_path=None,
            mass_kg=float("nan"),
        )
