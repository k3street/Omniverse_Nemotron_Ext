"""L0 tests for the scene-role failure message.

The default roles belong to the banana task, so every other task fails this
check — after the simulator has booted. A bare list of missing names sends the
reader looking for a broken scene, so the message has to carry what the scene
does hold and which flags rebind it.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.l0

from scripts.manipulation_scene_roles import (
    missing_roles_message,
    scene_asset_names,
)


class _Scene:
    def __init__(self, names):
        self.rigid_objects = {n: object() for n in names}


def test_message_names_the_flags_that_rebind_the_roles():
    text = missing_roles_message(["banana"], ("grey_bin", "red_block"))
    assert "--movable-object-asset" in text
    assert "--target-receptacle-asset" in text


def test_message_lists_what_the_scene_actually_holds():
    text = missing_roles_message(["banana", "plate_large"], ("grey_bin", "red_block"))
    assert "grey_bin" in text and "red_block" in text
    assert "banana" in text and "plate_large" in text


def test_message_survives_a_scene_that_lists_nothing():
    text = missing_roles_message(["banana"], ())
    assert "banana" in text
    assert "--movable-object-asset" in text


def test_asset_names_are_read_from_rigid_objects():
    assert scene_asset_names(_Scene(["red_block", "grey_bin"])) == ("grey_bin", "red_block")


def test_asset_names_fall_back_to_a_plain_mapping():
    assert scene_asset_names({"b": 1, "a": 2}) == ("a", "b")


def test_asset_names_never_raise_while_building_an_error():
    class Hostile:
        @property
        def rigid_objects(self):
            raise RuntimeError("scene is half torn down")
    # This only runs on the error path; it must not mask the real failure.
    assert scene_asset_names(Hostile()) == ()
