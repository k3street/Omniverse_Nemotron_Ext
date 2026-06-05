"""Phase 72c — BlueprintValidator tests.

Gate: pytest — blueprint validator catches:
  - 3 missing fields (name, objects, physics_settings)
  - 2 invalid object_class refs
  - AABB overlap
  - out-of-room violation

Per specs/IA_FULL_SPEC_2026-05-10.md Phase 72c.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.l0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_blueprint(**overrides):
    """Minimal valid blueprint; override any key to test edge cases."""
    bp = {
        "name": "test_scene",
        "objects": [
            {
                "name": "franka_panda_1",
                "asset_name": "franka_panda",
                "position": [0.0, 0.0, 0.0],
                "prim_path": "/World/franka_panda_1",
            }
        ],
        "physics_settings": {"gravity": -9.81, "time_step": 1.0 / 60.0},
    }
    bp.update(overrides)
    return bp


# ---------------------------------------------------------------------------
# Test 1 — metadata
# ---------------------------------------------------------------------------

def test_phase_72c_metadata():
    from service.isaac_assist_service.multimodal.sub_phase_72c_blueprint_validator import (
        get_phase_metadata,
    )
    md = get_phase_metadata()
    assert md["phase"] == "72c"
    assert md["status"] == "landed"
    assert "blueprint validator" in md["title"].lower()


# ---------------------------------------------------------------------------
# Test 2 — clean blueprint passes
# ---------------------------------------------------------------------------

def test_clean_blueprint_passes():
    from service.isaac_assist_service.multimodal.sub_phase_72c_blueprint_validator import (
        BlueprintValidator,
    )
    bv = BlueprintValidator()
    result = bv.validate(_make_blueprint())
    assert result.valid is True
    assert result.n_hard == 0
    assert result.n_soft == 0
    assert result.violations == []


# ---------------------------------------------------------------------------
# Test 3 — missing "name" fires hard ERROR
# ---------------------------------------------------------------------------

def test_missing_field_name():
    from service.isaac_assist_service.multimodal.sub_phase_72c_blueprint_validator import (
        BlueprintValidator,
    )
    bp = _make_blueprint()
    del bp["name"]
    bv = BlueprintValidator()
    result = bv.validate(bp)
    assert result.valid is False
    assert result.n_hard >= 1
    ids = [v.constraint_id for v in result.violations]
    assert "blueprint.missing_required_field" in ids
    fields = [v.diagnostics["field"] for v in result.violations
              if v.constraint_id == "blueprint.missing_required_field"]
    assert "name" in fields


# ---------------------------------------------------------------------------
# Test 4 — missing "objects" fires hard ERROR
# ---------------------------------------------------------------------------

def test_missing_field_objects():
    from service.isaac_assist_service.multimodal.sub_phase_72c_blueprint_validator import (
        BlueprintValidator,
    )
    bp = _make_blueprint()
    del bp["objects"]
    bv = BlueprintValidator()
    result = bv.validate(bp)
    assert result.valid is False
    fields = [v.diagnostics["field"] for v in result.violations
              if v.constraint_id == "blueprint.missing_required_field"]
    assert "objects" in fields


# ---------------------------------------------------------------------------
# Test 5 — missing "physics_settings" fires hard ERROR
# ---------------------------------------------------------------------------

def test_missing_field_physics_settings():
    from service.isaac_assist_service.multimodal.sub_phase_72c_blueprint_validator import (
        BlueprintValidator,
    )
    bp = _make_blueprint()
    del bp["physics_settings"]
    bv = BlueprintValidator()
    result = bv.validate(bp)
    assert result.valid is False
    fields = [v.diagnostics["field"] for v in result.violations
              if v.constraint_id == "blueprint.missing_required_field"]
    assert "physics_settings" in fields


# ---------------------------------------------------------------------------
# Test 6 — unknown asset_name fires hard ERROR (two objects)
# ---------------------------------------------------------------------------

def test_unknown_asset_name_fires_two_errors():
    from service.isaac_assist_service.multimodal.sub_phase_72c_blueprint_validator import (
        BlueprintValidator,
    )
    bp = _make_blueprint(
        objects=[
            {
                "name": "mystery_bot_1",
                "asset_name": "mystery_bot",
                "position": [0.0, 0.0, 0.0],
                "prim_path": "/World/mystery_bot_1",
            },
            {
                "name": "widget_42",
                "asset_name": "widget_42",
                "position": [1.0, 1.0, 0.0],
                "prim_path": "/World/widget_42",
            },
        ]
    )
    bv = BlueprintValidator()
    result = bv.validate(bp)
    assert result.valid is False
    unknown_vs = [v for v in result.violations
                  if v.constraint_id == "blueprint.unknown_object_class"]
    assert len(unknown_vs) == 2
    bad_names = {v.diagnostics["asset_name"] for v in unknown_vs}
    assert bad_names == {"mystery_bot", "widget_42"}


# ---------------------------------------------------------------------------
# Test 7 — AABB overlap fires soft WARNING
# ---------------------------------------------------------------------------

def test_aabb_overlap_fires_warning():
    from service.isaac_assist_service.multimodal.sub_phase_72c_blueprint_validator import (
        BlueprintValidator,
    )
    # franka_panda footprint = (0.4, 0.4) → centre±0.2 m
    # Place two Franka arms at the same location → guaranteed overlap.
    bp = _make_blueprint(
        objects=[
            {
                "name": "panda_1",
                "asset_name": "franka_panda",
                "position": [0.0, 0.0, 0.0],
                "prim_path": "/World/panda_1",
            },
            {
                "name": "panda_2",
                "asset_name": "franka_panda",
                "position": [0.1, 0.0, 0.0],  # 10 cm offset — still overlapping
                "prim_path": "/World/panda_2",
            },
        ]
    )
    bv = BlueprintValidator()
    result = bv.validate(bp)
    # AABB overlap is soft — should still be valid
    assert result.valid is True
    assert result.n_soft >= 1
    ids = [v.constraint_id for v in result.violations]
    assert "blueprint.aabb_overlap" in ids
    overlap_vs = [v for v in result.violations
                  if v.constraint_id == "blueprint.aabb_overlap"]
    # Both prim paths must be in affected_paths of the violation.
    assert "/World/panda_1" in overlap_vs[0].affected_paths
    assert "/World/panda_2" in overlap_vs[0].affected_paths


# ---------------------------------------------------------------------------
# Test 8 — object outside room dims fires soft WARNING
# ---------------------------------------------------------------------------

def test_object_out_of_room_fires_warning():
    from service.isaac_assist_service.multimodal.sub_phase_72c_blueprint_validator import (
        BlueprintValidator,
    )
    bp = _make_blueprint(
        room_dims=[2.0, 2.0, 3.0],
        objects=[
            {
                "name": "franka_panda_1",
                "asset_name": "franka_panda",
                "position": [5.0, 0.5, 0.0],  # x=5 > room_x=2 → out of bounds
                "prim_path": "/World/franka_panda_1",
            }
        ],
    )
    bv = BlueprintValidator()
    result = bv.validate(bp)
    assert result.valid is True  # soft only
    assert result.n_soft >= 1
    ids = [v.constraint_id for v in result.violations]
    assert "blueprint.object_out_of_room" in ids


# ---------------------------------------------------------------------------
# Test 9 — multiple violations aggregate correctly
# ---------------------------------------------------------------------------

def test_multiple_violations_aggregate():
    from service.isaac_assist_service.multimodal.sub_phase_72c_blueprint_validator import (
        BlueprintValidator,
    )
    # Missing name (hard) + unknown asset (hard) + out-of-room (soft, if room_dims present)
    bp = {
        # "name" is missing → hard
        "objects": [
            {
                "name": "phantom_arm",
                "asset_name": "phantom_arm",  # unknown → hard
                "position": [99.0, 99.0, 0.0],
                "prim_path": "/World/phantom_arm",
            }
        ],
        "physics_settings": {"gravity": -9.81},
        "room_dims": [4.0, 4.0, 3.0],
    }
    bv = BlueprintValidator()
    result = bv.validate(bp)
    assert result.valid is False
    assert result.n_hard >= 2  # missing "name" + unknown asset_name
    # n_hard + n_soft == total violations
    assert result.n_hard + result.n_soft == len(result.violations)


# ---------------------------------------------------------------------------
# Test 10 — n_hard and n_soft counts match category fields
# ---------------------------------------------------------------------------

def test_n_hard_n_soft_counts_correct():
    from service.isaac_assist_service.multimodal.sub_phase_72c_blueprint_validator import (
        BlueprintValidator,
    )
    # Produce a known mix: 1 hard (unknown asset_name) + 1 soft (out-of-room)
    bp = _make_blueprint(
        room_dims=[1.0, 1.0, 2.0],
        objects=[
            {
                "name": "unknown_obj",
                "asset_name": "totally_unknown_xyz",
                "position": [5.0, 5.0, 0.0],  # out of room too, but unknown skips AABB
                "prim_path": "/World/unknown_obj",
            }
        ],
    )
    bv = BlueprintValidator()
    result = bv.validate(bp)
    hard_from_list = sum(1 for v in result.violations if v.category == "hard")
    soft_from_list = sum(1 for v in result.violations if v.category == "soft")
    assert result.n_hard == hard_from_list
    assert result.n_soft == soft_from_list
    assert result.n_hard >= 1  # unknown asset
    assert result.n_soft >= 1  # out of room
