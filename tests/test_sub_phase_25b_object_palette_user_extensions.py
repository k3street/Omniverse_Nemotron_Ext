"""Phase 25b contract tests — palette extension loader."""
from __future__ import annotations

import pytest
import yaml

pytestmark = pytest.mark.l0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_yaml(tmp_path, filename, data):
    p = tmp_path / filename
    with p.open("w") as fh:
        yaml.dump(data, fh)
    return p


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_phase_25b_metadata():
    from service.isaac_assist_service.multimodal.sub_phase_25b_object_palette_user_extensions import (
        get_phase_metadata,
        PHASE_STATUS,
    )
    md = get_phase_metadata()
    assert md["phase"] == "25b"
    assert md["status"] == "landed"
    assert PHASE_STATUS == "landed"


def test_load_directory_parses_valid_yaml(tmp_path):
    _write_yaml(tmp_path, "my_fixture.yaml", {
        "name": "my_custom_fixture",
        "usd_ref": "omniverse://server/fixture.usd",
        "category": "fixture",
        "footprint_xy_m": [0.5, 0.4],
        "tags": ["custom"],
        "added_by": "alice",
    })

    from service.isaac_assist_service.multimodal.sub_phase_25b_object_palette_user_extensions import (
        PaletteExtensionLoader,
    )
    loader = PaletteExtensionLoader(yaml_dir=tmp_path)
    entries = loader.load_directory()

    assert len(entries) == 1
    obj = entries[0]
    assert obj.name == "my_custom_fixture"
    assert obj.usd_ref == "omniverse://server/fixture.usd"
    assert obj.category == "fixture"
    assert obj.footprint_xy_m == (0.5, 0.4)
    assert obj.tags == ["custom"]
    assert obj.added_by == "alice"


def test_validate_yaml_entry_rejects_missing_name():
    from service.isaac_assist_service.multimodal.sub_phase_25b_object_palette_user_extensions import (
        PaletteExtensionLoader,
    )
    loader = PaletteExtensionLoader()
    error = loader.validate_yaml_entry({"usd_ref": "some.usd", "category": "prop"})
    assert error is not None
    assert "name" in error


def test_validate_yaml_entry_accepts_minimal_valid():
    from service.isaac_assist_service.multimodal.sub_phase_25b_object_palette_user_extensions import (
        PaletteExtensionLoader,
    )
    loader = PaletteExtensionLoader()
    assert loader.validate_yaml_entry({"name": "my_thing"}) is None


def test_register_all_conflict_with_builtin(tmp_path):
    # "franka_panda" is a builtin — should be rejected.
    _write_yaml(tmp_path, "franka_panda.yaml", {
        "name": "franka_panda",
        "usd_ref": "custom/franka.usd",
    })

    from service.isaac_assist_service.multimodal.sub_phase_25b_object_palette_user_extensions import (
        PaletteExtensionLoader,
    )
    from service.isaac_assist_service.multimodal.user_object_class_registry import (
        UserObjectClassRegistry,
    )
    loader = PaletteExtensionLoader(yaml_dir=tmp_path)
    registry = UserObjectClassRegistry()
    result = loader.register_all(registry)

    assert result["franka_panda.yaml"] == "conflict_with_builtin"
    # Must not have been added to registry.
    assert registry.get("franka_panda") is None


def test_register_all_conflict_with_user(tmp_path):
    # Two different files registering the same custom name.
    _write_yaml(tmp_path, "a_my_tool.yaml", {"name": "my_shared_tool"})
    _write_yaml(tmp_path, "b_my_tool.yaml", {"name": "my_shared_tool"})

    from service.isaac_assist_service.multimodal.sub_phase_25b_object_palette_user_extensions import (
        PaletteExtensionLoader,
    )
    from service.isaac_assist_service.multimodal.user_object_class_registry import (
        UserObjectClassRegistry,
    )
    loader = PaletteExtensionLoader(yaml_dir=tmp_path)
    registry = UserObjectClassRegistry()
    result = loader.register_all(registry)

    statuses = set(result.values())
    assert "registered" in statuses
    assert "conflict_with_user" in statuses
    # Exactly one success, one conflict.
    assert sum(1 for s in result.values() if s == "registered") == 1
    assert sum(1 for s in result.values() if s == "conflict_with_user") == 1


def test_merged_palette_includes_builtin_and_user(tmp_path):
    _write_yaml(tmp_path, "my_novel_robot.yaml", {
        "name": "my_novel_robot",
        "category": "robot",
    })

    from service.isaac_assist_service.multimodal.sub_phase_25b_object_palette_user_extensions import (
        PaletteExtensionLoader,
        merged_palette,
    )
    from service.isaac_assist_service.multimodal.object_palette import PALETTE
    from service.isaac_assist_service.multimodal.user_object_class_registry import (
        UserObjectClassRegistry,
    )

    loader = PaletteExtensionLoader(yaml_dir=tmp_path)
    registry = UserObjectClassRegistry()
    loader.register_all(registry)

    combined = merged_palette(registry)

    # All builtins present.
    for key in PALETTE:
        assert key in combined

    # User entry present.
    assert "my_novel_robot" in combined


def test_merged_palette_builtin_precedence(tmp_path):
    # User tries to override a builtin — builtin must win.
    _write_yaml(tmp_path, "franka_panda.yaml", {
        "name": "franka_panda",
        "usd_ref": "overridden/path.usd",
    })

    from service.isaac_assist_service.multimodal.sub_phase_25b_object_palette_user_extensions import (
        PaletteExtensionLoader,
        merged_palette,
    )
    from service.isaac_assist_service.multimodal.object_palette import PALETTE, ObjectClass
    from service.isaac_assist_service.multimodal.user_object_class_registry import (
        UserObjectClassRegistry,
    )

    loader = PaletteExtensionLoader(yaml_dir=tmp_path)
    registry = UserObjectClassRegistry()
    # register_all would reject it, but even if someone manually forced the
    # entry into the registry the merged_palette must still keep the builtin.
    from service.isaac_assist_service.multimodal.user_object_class_registry import UserObjectClass
    registry.register(UserObjectClass(name="franka_panda", usd_ref="overridden/path.usd"))

    combined = merged_palette(registry)

    # The value for "franka_panda" must be the builtin ObjectClass, not the
    # UserObjectClass sneaked in above.
    assert isinstance(combined["franka_panda"], ObjectClass)
    assert combined["franka_panda"].usd_ref == PALETTE["franka_panda"].usd_ref


def test_load_directory_empty_dir(tmp_path):
    from service.isaac_assist_service.multimodal.sub_phase_25b_object_palette_user_extensions import (
        PaletteExtensionLoader,
    )
    loader = PaletteExtensionLoader(yaml_dir=tmp_path)
    assert loader.load_directory() == []


def test_register_all_invalid_yaml(tmp_path):
    # Write a file that is not valid YAML.
    bad = tmp_path / "bad.yaml"
    bad.write_text(": : : broken{{{{", encoding="utf-8")

    from service.isaac_assist_service.multimodal.sub_phase_25b_object_palette_user_extensions import (
        PaletteExtensionLoader,
    )
    from service.isaac_assist_service.multimodal.user_object_class_registry import (
        UserObjectClassRegistry,
    )
    loader = PaletteExtensionLoader(yaml_dir=tmp_path)
    registry = UserObjectClassRegistry()
    result = loader.register_all(registry)

    assert result["bad.yaml"] == "invalid"
