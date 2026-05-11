"""Tests for service/isaac_assist_service/multimodal/stage_to_spec.py.

Block 3 Step 21 / IA Full Spec Phase 22 — viewport modality reads Kit
stage and produces LayoutSpec.
"""
from __future__ import annotations

from typing import Any, Dict, List

import pytest

pytestmark = pytest.mark.l0

from service.isaac_assist_service.multimodal.stage_to_spec import (
    classify_prim,
    prims_to_layout_spec,
    sync_from_stage,
)


# ── classify_prim — heuristic dispatcher ───────────────────────────────────


def test_classify_via_reference_url():
    prim = {"path": "/World/Franka", "type": "Xform",
            "reference_url": "omniverse://Library/Franka_Panda.usd"}
    assert classify_prim(prim) == "franka_panda"


def test_classify_via_prim_name_franka():
    prim = {"path": "/World/Franka1", "type": "Xform"}
    assert classify_prim(prim) == "franka_panda"


def test_classify_via_prim_name_bin():
    prim = {"path": "/World/RedBin", "type": "Cube"}
    assert classify_prim(prim) == "bin"


def test_classify_via_prim_name_conveyor():
    prim = {"path": "/World/Conv2", "type": "Cube"}
    assert classify_prim(prim) == "conveyor"


def test_classify_via_prim_name_cube():
    prim = {"path": "/World/Cube_1", "type": "Cube"}
    assert classify_prim(prim) == "cube"


def test_classify_via_usd_type_fallback():
    prim = {"path": "/World/Misc", "type": "Sphere"}
    assert classify_prim(prim) == "sphere"


def test_classify_unknown_returns_none():
    prim = {"path": "/World/Unknown", "type": "WeirdType"}
    assert classify_prim(prim) is None


def test_classify_reference_url_wins_over_name():
    """Reference URL is the most specific signal; takes precedence."""
    prim = {"path": "/World/Foo", "type": "Cube",
            "reference_url": "omniverse://library/ur5e.usd"}
    assert classify_prim(prim) == "ur5e"


# ── prims_to_layout_spec — assembly ────────────────────────────────────────


def test_to_spec_emits_viewport_modality():
    prims = [{"path": "/World/Cube_1", "type": "Cube",
              "translate": [0.0, 0.0, 0.5]}]
    spec = prims_to_layout_spec(prims)
    assert spec.source.modality == "viewport"
    assert spec.source.confidence == 1.0


def test_to_spec_translates_to_position():
    prims = [
        {"path": "/World/Cube_1", "type": "Cube",
         "translate": [1.5, -0.4, 0.8]},
    ]
    spec = prims_to_layout_spec(prims)
    assert len(spec.objects) == 1
    obj = spec.objects[0]
    assert obj.position.x == 1.5
    assert obj.position.y == -0.4


def test_to_spec_omits_unclassifiable():
    prims = [
        {"path": "/World/Cube_1", "type": "Cube",
         "translate": [0, 0, 0]},
        {"path": "/World/Unknown", "type": "WeirdType",
         "translate": [0, 0, 0]},
        {"path": "/World/Bin", "type": "Cube",
         "translate": [0, 0, 0]},
    ]
    spec = prims_to_layout_spec(prims)
    classes = [o.object_class for o in spec.objects]
    assert "cube" in classes
    assert "bin" in classes
    assert len(spec.objects) == 2


def test_to_spec_preserves_input_order():
    prims = [
        {"path": "/World/A_Cube", "type": "Cube"},
        {"path": "/World/B_Bin", "type": "Cube"},
        {"path": "/World/C_Conv", "type": "Cube"},
    ]
    spec = prims_to_layout_spec(prims)
    assert [o.name for o in spec.objects] == ["A_Cube", "B_Bin", "C_Conv"]


def test_to_spec_role_hint_becomes_binding():
    prims = [
        {"path": "/World/Franka1", "type": "Xform",
         "translate": [0, 0, 0.75],
         "metadata": {"role_hint": "primary_robot"}},
    ]
    spec = prims_to_layout_spec(prims)
    assert spec.bindings is not None
    assert "primary_robot" in spec.bindings
    assert spec.bindings["primary_robot"].source == "user_explicit"


def test_to_spec_scope_prim_in_source_metadata():
    spec = prims_to_layout_spec([], scope_prim="/World/MyLayout")
    assert spec.source.metadata.get("scope_prim") == "/World/MyLayout"


def test_to_spec_records_n_prims_read():
    prims = [{"path": "/World/Cube_1", "type": "Cube"}]
    spec = prims_to_layout_spec(prims)
    assert spec.source.metadata.get("n_prims_read") == 1


def test_to_spec_empty_prim_list_produces_empty_objects():
    spec = prims_to_layout_spec([])
    assert spec.objects == []
    assert spec.bindings is None


def test_to_spec_default_pattern_hint_pick_place():
    spec = prims_to_layout_spec([])
    assert spec.intent.pattern_hint == "pick_place"


def test_to_spec_custom_pattern_hint():
    spec = prims_to_layout_spec([], pattern_hint="sort")
    assert spec.intent.pattern_hint == "sort"


# ── name sanitization ──────────────────────────────────────────────────────


def test_to_spec_sanitizes_name_with_leading_digit():
    prims = [{"path": "/World/2Robot", "type": "Xform",
              "reference_url": "library/franka_panda.usd"}]
    spec = prims_to_layout_spec(prims)
    # Must start with letter
    assert spec.objects[0].name[0].isalpha()


def test_to_spec_sanitizes_invalid_chars():
    prims = [{"path": "/World/Foo-Bar.Baz", "type": "Cube"}]
    spec = prims_to_layout_spec(prims)
    name = spec.objects[0].name
    assert "-" not in name
    assert "." not in name


# ── sync_from_stage async wrapper ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_sync_from_stage_calls_list_prims():
    captured: Dict[str, Any] = {}

    async def stub_list_prims(scope: str) -> List[Dict[str, Any]]:
        captured["scope"] = scope
        return [{"path": "/World/Layout/Cube_1", "type": "Cube"}]

    spec = await sync_from_stage(stub_list_prims, scope_prim="/World/Layout")
    assert captured["scope"] == "/World/Layout"
    assert len(spec.objects) == 1
