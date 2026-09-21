import pytest

from scripts.manipulation_scene_roles import (
    ManipulationSceneRoles,
    humanize_asset_name,
)


def test_instance_asset_name_becomes_semantic_label():
    assert humanize_asset_name("bagel_06") == "bagel"
    assert humanize_asset_name("plate_large") == "plate large"


def test_roles_build_instruction_and_provenance_without_tool_assumptions():
    roles = ManipulationSceneRoles.create(
        movable_object_asset="bagel_06",
        target_receptacle_asset="plate_large",
        target_receptacle_label="white plate",
    )
    assert roles.default_instruction() == "Pick up the bagel and put it on the white plate"
    assert roles.to_dict()["movable_object"] == {
        "asset": "bagel_06",
        "label": "bagel",
    }


def test_roles_validate_live_scene_assets():
    roles = ManipulationSceneRoles.create(
        movable_object_asset="bagel_06",
        target_receptacle_asset="plate_large",
    )
    roles.validate_scene({"bagel_06": object(), "plate_large": object()})
    with pytest.raises(KeyError, match="bagel_06"):
        roles.validate_scene({"plate_large": object()})


def test_roles_support_isaac_style_key_lookup_without_membership_protocol():
    class KeyLookupScene:
        def __init__(self):
            self.entities = {"bagel_06": object(), "plate_large": object()}

        def __getitem__(self, key):
            return self.entities[key]

    roles = ManipulationSceneRoles.create(
        movable_object_asset="bagel_06",
        target_receptacle_asset="plate_large",
    )
    roles.validate_scene(KeyLookupScene())


def test_roles_reject_same_asset_for_object_and_target():
    with pytest.raises(ValueError, match="must differ"):
        ManipulationSceneRoles.create(
            movable_object_asset="plate_large",
            target_receptacle_asset="plate_large",
        )
