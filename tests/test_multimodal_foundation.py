"""L0 unit tests for the multimodal foundation module.

Covers:
- types.py — LayoutSpec, Intent, structural_tags format-regex,
  TypedObject name validation
- vocabulary.py — registry add/deprecate/active discipline
- validate.py — cross-feature consistency, registry membership,
  user: namespace pass-through, object name + id uniqueness,
  binding object_id references
- persistence.py — SQLite CAS round-trip + RevisionConflictError
- ratify.py — auto-binding waterfall across all 7 scenarios
- migrations — scaffold with empty MIGRATIONS map

These functions are the LayoutSpec foundation. If types reject valid input,
modalities can't produce specs. If validate accepts inconsistent specs,
downstream consumers see corrupt data. If persistence loses a revision,
CAS fails silently. If ratify binds wrong, hard-instantiate executes against
the wrong roles.
"""
from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from typing import Iterator

import pytest

pytestmark = pytest.mark.l0


# ============================================================================
# types.py
# ============================================================================

class TestIntentTagFormat:
    def test_isaac_namespace_accepted(self):
        from service.isaac_assist_service.multimodal import Intent
        Intent(pattern_hint="pick_place",
               structural_tags=["isaac:transport.conveyor"])

    def test_cad_namespace_accepted(self):
        from service.isaac_assist_service.multimodal import Intent
        Intent(pattern_hint="pick_place",
               structural_tags=["cad:imported.fusion360"])

    def test_user_namespace_accepted(self):
        from service.isaac_assist_service.multimodal import Intent
        Intent(pattern_hint="pick_place",
               structural_tags=["user:annotation.priority_first"])

    def test_unknown_namespace_rejected(self):
        from service.isaac_assist_service.multimodal import Intent
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            Intent(pattern_hint="pick_place",
                   structural_tags=["unknown:something"])

    def test_uppercase_in_tag_rejected(self):
        from service.isaac_assist_service.multimodal import Intent
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            Intent(pattern_hint="pick_place",
                   structural_tags=["isaac:Transport.Conveyor"])

    def test_no_namespace_rejected(self):
        from service.isaac_assist_service.multimodal import Intent
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            Intent(pattern_hint="pick_place",
                   structural_tags=["just_a_tag"])

    def test_pattern_hint_custom_rejected(self):
        # spec §3.4: "custom" deliberately removed from enum
        from service.isaac_assist_service.multimodal import Intent
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            Intent(pattern_hint="custom")  # type: ignore[arg-type]


class TestTypedObjectNameValidation:
    def test_valid_usd_name_accepted(self):
        from service.isaac_assist_service.multimodal import TypedObject
        from service.isaac_assist_service.multimodal.types import Position, Size
        TypedObject(**{"class": "franka_panda"}, name="Franka_1",
                    position=Position(x=0, y=0), size=Size(w=0.12, h=0.12))

    def test_name_starting_with_digit_rejected(self):
        from service.isaac_assist_service.multimodal import TypedObject
        from service.isaac_assist_service.multimodal.types import Position, Size
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            TypedObject(**{"class": "franka_panda"}, name="1invalid",
                        position=Position(x=0, y=0), size=Size(w=0.12, h=0.12))

    def test_name_with_space_rejected(self):
        from service.isaac_assist_service.multimodal import TypedObject
        from service.isaac_assist_service.multimodal.types import Position, Size
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            TypedObject(**{"class": "franka_panda"}, name="Robot 1",
                        position=Position(x=0, y=0), size=Size(w=0.12, h=0.12))


class TestStructuralFeaturesDefaults:
    def test_all_defaults_safe(self):
        from service.isaac_assist_service.multimodal import StructuralFeatures
        f = StructuralFeatures()
        assert f.n_robot_stations == 1
        assert f.has_color_routing is False
        assert f.has_bounded_footprint is False
        assert f.footprint_xy_max_m is None
        assert f.upright_dot_threshold is None


# ============================================================================
# vocabulary.py
# ============================================================================

class TestVocabularyRegistry:
    def _registry(self, tmp_path: Path):
        from service.isaac_assist_service.multimodal.vocabulary import (
            load_default_registry,
        )
        return load_default_registry(tmp_path / "test.json")

    def test_seeds_default_tags(self, tmp_path: Path):
        reg = self._registry(tmp_path)
        assert reg.is_active("isaac:transport.conveyor")
        assert reg.is_active("isaac:robot.fixed_base.arm")

    def test_unregistered_tag_not_active(self, tmp_path: Path):
        reg = self._registry(tmp_path)
        assert not reg.is_active("isaac:nonexistent.bogus")
        assert not reg.is_registered("isaac:nonexistent.bogus")

    def test_deprecate_marks_status(self, tmp_path: Path):
        reg = self._registry(tmp_path)
        tag = "isaac:transport.belt"
        assert reg.is_active(tag)
        reg.deprecate(tag, deprecated_in_version="1.1",
                      replaced_by="isaac:transport.conveyor")
        assert not reg.is_active(tag)
        assert reg.is_registered(tag)
        entry = reg.get(tag)
        assert entry is not None
        assert entry.status == "deprecated"
        assert entry.replaced_by == "isaac:transport.conveyor"

    def test_append_only_rejects_redefinition(self, tmp_path: Path):
        from service.isaac_assist_service.multimodal.vocabulary import TagEntry
        reg = self._registry(tmp_path)
        tag = "isaac:transport.conveyor"
        # Attempting to add a different definition for an existing tag fails
        with pytest.raises(ValueError, match="append-only"):
            reg.add(TagEntry(tag=tag, status="active",
                             description="DIFFERENT description"))


# ============================================================================
# validate.py
# ============================================================================

class TestValidate:
    def _spec(self, **intent_overrides):
        from service.isaac_assist_service.multimodal import (
            LayoutSpec, Intent, Counts, StructuralFeatures, Source,
        )
        intent_kwargs = dict(
            pattern_hint="pick_place",
            counts=Counts(robots=1, conveyors=1, bins=1, cubes=2),
            structural_features=StructuralFeatures(uses_conveyor_transport=True),
            structural_tags=["isaac:transport.conveyor",
                             "isaac:robot.fixed_base.arm"],
        )
        intent_kwargs.update(intent_overrides)
        return LayoutSpec(
            intent=Intent(**intent_kwargs),
            source=Source(modality="text", confidence=0.85),
        )

    def test_valid_minimal_passes(self):
        from service.isaac_assist_service.multimodal import validate_layout_spec
        result = validate_layout_spec(self._spec())
        assert result.valid is True
        assert len(result.errors) == 0

    def test_user_namespace_tag_passes_through(self):
        from service.isaac_assist_service.multimodal import (
            Intent, Counts, StructuralFeatures, validate_layout_spec,
        )
        result = validate_layout_spec(self._spec(
            structural_tags=["user:annotation.x"],
        ))
        assert result.valid is True

    def test_unregistered_isaac_tag_rejected(self):
        from service.isaac_assist_service.multimodal import validate_layout_spec
        result = validate_layout_spec(self._spec(
            structural_tags=["isaac:bogus.never.registered"],
        ))
        assert result.valid is False
        codes = [i.code for i in result.errors]
        assert "tag.not_registered" in codes

    def test_color_routing_axis_inconsistency_rejected(self):
        from service.isaac_assist_service.multimodal import (
            StructuralFeatures, validate_layout_spec,
        )
        result = validate_layout_spec(self._spec(
            structural_features=StructuralFeatures(
                has_color_routing=True,
                routing_axis=None,  # inconsistent
            ),
        ))
        assert result.valid is False
        assert any(
            i.code == "features.color_routing_axis_mismatch"
            for i in result.errors
        )

    def test_bounded_footprint_requires_value(self):
        from service.isaac_assist_service.multimodal import (
            StructuralFeatures, validate_layout_spec,
        )
        result = validate_layout_spec(self._spec(
            structural_features=StructuralFeatures(
                has_bounded_footprint=True,
                footprint_xy_max_m=None,  # missing
            ),
        ))
        assert result.valid is False
        assert any(
            i.code == "features.bounded_footprint_missing_value"
            for i in result.errors
        )

    def test_orientation_requires_threshold(self):
        from service.isaac_assist_service.multimodal import (
            StructuralFeatures, validate_layout_spec,
        )
        result = validate_layout_spec(self._spec(
            structural_features=StructuralFeatures(
                has_orientation_requirement=True,
                upright_dot_threshold=None,
            ),
        ))
        assert result.valid is False
        assert any(
            i.code == "features.orientation_missing_threshold"
            for i in result.errors
        )

    def test_zero_robots_with_pickplace_warns(self):
        from service.isaac_assist_service.multimodal import (
            Counts, validate_layout_spec,
        )
        result = validate_layout_spec(self._spec(
            counts=Counts(robots=0, conveyors=1, bins=1, cubes=1),
        ))
        # Warning, not error — spec is still valid
        assert result.valid is True
        assert any(i.severity == "warning"
                   and i.code == "counts.zero_robots_unexpected"
                   for i in result.issues)

    def test_duplicate_object_name_rejected(self):
        from service.isaac_assist_service.multimodal import (
            LayoutSpec, Intent, Counts, StructuralFeatures, Source,
            TypedObject,
        )
        from service.isaac_assist_service.multimodal.types import Position, Size
        from service.isaac_assist_service.multimodal import validate_layout_spec
        spec = LayoutSpec(
            intent=Intent(pattern_hint="pick_place"),
            objects=[
                TypedObject(**{"class": "franka_panda"}, name="Robot",
                            position=Position(x=0, y=0),
                            size=Size(w=0.12, h=0.12)),
                TypedObject(**{"class": "ur5e"}, name="Robot",  # dup
                            position=Position(x=1, y=0),
                            size=Size(w=0.13, h=0.13)),
            ],
            source=Source(modality="drag_drop", confidence=1.0),
        )
        result = validate_layout_spec(spec)
        assert result.valid is False
        assert any(i.code == "objects.duplicate_name" for i in result.errors)


# ============================================================================
# persistence.py
# ============================================================================

class TestPersistence:
    @pytest.fixture
    def store(self, tmp_path: Path):
        from service.isaac_assist_service.multimodal.persistence import (
            MultimodalStore,
        )
        s = MultimodalStore(db_path=tmp_path / "test.db")
        yield s
        s.close()

    @pytest.fixture
    def make_spec(self):
        from service.isaac_assist_service.multimodal import (
            LayoutSpec, Intent, Counts, StructuralFeatures, Source,
        )

        def _build(robots: int = 1):
            return LayoutSpec(
                intent=Intent(
                    pattern_hint="pick_place",
                    counts=Counts(robots=robots, conveyors=1, bins=1),
                    structural_features=StructuralFeatures(
                        uses_conveyor_transport=True
                    ),
                    structural_tags=["isaac:transport.conveyor"],
                ),
                source=Source(modality="text", confidence=0.85),
            )
        return _build

    def test_empty_session_returns_none(self, store):
        assert store.get_latest("nope") is None
        assert store.get_revision("nope") == 0

    @pytest.mark.asyncio
    async def test_save_with_cas_increments_revision(self, store, make_spec):
        spec = make_spec(robots=1)
        saved = await store.save_with_cas("s1", spec, parent_revision=0)
        assert saved.revision == 1
        assert store.get_revision("s1") == 1

        spec2 = make_spec(robots=2)
        saved2 = await store.save_with_cas("s1", spec2, parent_revision=1)
        assert saved2.revision == 2

    @pytest.mark.asyncio
    async def test_cas_conflict_raises_with_current_spec(self, store, make_spec):
        from service.isaac_assist_service.multimodal.persistence import (
            RevisionConflictError,
        )
        spec = make_spec(robots=1)
        await store.save_with_cas("s1", spec, parent_revision=0)
        # Attempt save against stale parent_revision
        with pytest.raises(RevisionConflictError) as exc_info:
            await store.save_with_cas("s1", spec, parent_revision=0)
        assert exc_info.value.expected == 0
        assert exc_info.value.actual == 1
        assert exc_info.value.current_spec is not None

    @pytest.mark.asyncio
    async def test_get_at_specific_revision(self, store, make_spec):
        spec1 = make_spec(robots=1)
        spec2 = make_spec(robots=2)
        await store.save_with_cas("s1", spec1, parent_revision=0)
        await store.save_with_cas("s1", spec2, parent_revision=1)

        loaded_v1 = store.get_at_revision("s1", 1)
        loaded_v2 = store.get_at_revision("s1", 2)
        assert loaded_v1 is not None
        assert loaded_v2 is not None
        assert loaded_v1.intent.counts.robots == 1
        assert loaded_v2.intent.counts.robots == 2

    def test_build_log_progress_append(self, store):
        store.start_build("b1", "s1", revision=1)
        store.append_build_progress("b1", "robot_wizard", "ok", "franka_panda")
        store.append_build_progress("b1", "create_conveyor", "ok", "len=2.0m")
        store.finish_build("b1", "ok")
        b = store.get_build("b1")
        assert b is not None
        assert b["status"] == "ok"
        assert len(b["progress"]) == 2

    def test_event_append_and_filter(self, store):
        store.append_event("s1", "modality_invoked", {"modality": "text"})
        store.append_event("s1", "build_started", {"build_id": "b1"})
        store.append_event("s2", "modality_invoked", {"modality": "drag_drop"})

        s1_events = store.list_events(session_id="s1")
        assert len(s1_events) == 2

        modality_events = store.list_events(event_type="modality_invoked")
        assert len(modality_events) == 2


# ============================================================================
# ratify.py
# ============================================================================

class TestRatify:
    @pytest.fixture
    def make_spec(self):
        from service.isaac_assist_service.multimodal import (
            LayoutSpec, Intent, Counts, Source, TypedObject,
        )
        from service.isaac_assist_service.multimodal.types import Position, Size

        def _build(*objects):
            return LayoutSpec(
                intent=Intent(pattern_hint="pick_place",
                              counts=Counts(robots=1, conveyors=1, bins=1)),
                objects=list(objects) if objects else None,
                source=Source(modality="drag_drop", confidence=1.0),
            )
        return _build

    @pytest.fixture
    def make_obj(self):
        from service.isaac_assist_service.multimodal import TypedObject
        from service.isaac_assist_service.multimodal.types import Position, Size

        def _build(class_, name, x=0, y=0, w=0.1, h=0.1):
            return TypedObject(**{"class": class_}, name=name,
                               position=Position(x=x, y=y),
                               size=Size(w=w, h=h))
        return _build

    def test_legacy_template_no_roles_ok(self, make_spec):
        from service.isaac_assist_service.multimodal.ratify import ratify
        result = ratify({"id": "CP-X-legacy"}, make_spec())
        assert result.status == "ok"
        assert len(result.bindings) == 0

    def test_text_prompt_no_objects_ok(self, make_spec):
        from service.isaac_assist_service.multimodal.ratify import ratify
        template = {
            "roles": {
                "primary_robot": {
                    "constraints": ["franka_panda"], "expected_count": 1,
                },
            },
        }
        result = ratify(template, make_spec())
        assert result.status == "ok"

    def test_cardinality_trivial_binds(self, make_spec, make_obj):
        from service.isaac_assist_service.multimodal.ratify import ratify
        template = {
            "roles": {
                "primary_robot": {
                    "constraints": ["franka_panda"], "expected_count": 1,
                },
            },
        }
        spec = make_spec(make_obj("franka_panda", "Robot1"))
        result = ratify(template, spec)
        assert result.status == "ok"
        assert "primary_robot" in result.bindings

    def test_disambiguator_smaller_x_first(self, make_spec, make_obj):
        from service.isaac_assist_service.multimodal.ratify import ratify
        template = {
            "roles": {
                "primary": {
                    "constraints": ["franka_panda"], "expected_count": 1,
                    "disambiguator": "smaller_x_first",
                },
                "secondary": {
                    "constraints": ["franka_panda"], "expected_count": 1,
                    "disambiguator": "larger_x_first",
                },
            },
        }
        a = make_obj("franka_panda", "RobotA", x=-1.0)
        b = make_obj("franka_panda", "RobotB", x=1.0)
        spec = make_spec(a, b)
        result = ratify(template, spec)
        assert result.status == "ok"
        assert result.bindings["primary"].object_id == a.id
        assert result.bindings["secondary"].object_id == b.id

    def test_ambiguous_returns_needs_choice(self, make_spec, make_obj):
        from service.isaac_assist_service.multimodal.ratify import ratify
        template = {
            "roles": {
                "primary_robot": {
                    "constraints": ["franka_panda"], "expected_count": 1,
                    # no disambiguator
                },
            },
        }
        spec = make_spec(
            make_obj("franka_panda", "RobotA", x=-1.0),
            make_obj("franka_panda", "RobotB", x=1.0),
        )
        result = ratify(template, spec)
        assert result.status == "needs_choice"
        assert len(result.ambiguous_roles) == 1
        assert len(result.ambiguous_roles[0].candidate_object_ids) == 2

    def test_unbindable_required_role(self, make_spec, make_obj):
        from service.isaac_assist_service.multimodal.ratify import ratify
        template = {
            "roles": {
                "primary_robot": {
                    "constraints": ["franka_panda"], "expected_count": 1,
                    "required": True,
                },
            },
        }
        # No franka in spec
        spec = make_spec(make_obj("conveyor", "Conv1", w=2.0, h=0.4))
        result = ratify(template, spec)
        assert result.status == "rejected"
        assert any(e.kind == "unbindable" for e in result.errors)

    def test_user_pre_binding_wrong_class_rejected(self, make_spec, make_obj):
        from service.isaac_assist_service.multimodal.ratify import ratify
        from service.isaac_assist_service.multimodal import RoleBinding

        template = {
            "roles": {
                "primary_robot": {
                    "constraints": ["franka_panda"], "expected_count": 1,
                },
            },
        }
        cube = make_obj("cube", "Cube1", w=0.05, h=0.05)
        spec = make_spec(cube)
        spec.bindings = {
            "primary_robot": RoleBinding(
                object_id=cube.id, source="user_explicit", confidence=1.0,
            ),
        }
        result = ratify(template, spec)
        assert result.status == "rejected"
        assert any(e.kind == "wrong_class" for e in result.errors)


# ============================================================================
# migrations
# ============================================================================

class TestMigrations:
    def test_current_version_is_no_op(self):
        from service.isaac_assist_service.multimodal.migrations import (
            migrate, CURRENT_VERSION,
        )
        spec = {"version": CURRENT_VERSION, "intent": {}}
        result = migrate(spec)
        assert result is spec  # no copy needed; pure no-op

    def test_unknown_future_version_raises(self):
        from service.isaac_assist_service.multimodal.migrations import (
            migrate, MigrationError,
        )
        spec = {"version": "99.0", "intent": {}}
        with pytest.raises(MigrationError):
            migrate(spec)

    def test_needs_migration_false_for_current(self):
        from service.isaac_assist_service.multimodal.migrations import (
            needs_migration, CURRENT_VERSION,
        )
        assert needs_migration({"version": CURRENT_VERSION}) is False

    def test_quarantine_renames_file(self, tmp_path: Path):
        from service.isaac_assist_service.multimodal.migrations import (
            quarantine_broken_file,
        )
        p = tmp_path / "spec.json"
        p.write_text("{not valid")
        broken = quarantine_broken_file(p)
        assert broken.exists()
        assert ".broken-" in broken.name
        # Original is preserved (caller decides whether to delete)
        assert p.exists()
