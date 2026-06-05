"""Phase 25 — object palette."""
import pytest
pytestmark = pytest.mark.l0


def test_palette_has_at_least_50_classes():
    from service.isaac_assist_service.multimodal.object_palette import PALETTE
    assert len(PALETTE) >= 50, f"Expected ≥50 palette entries, got {len(PALETTE)}"


def test_palette_categories_present():
    from service.isaac_assist_service.multimodal.object_palette import PALETTE
    categories = {c.category for c in PALETTE.values()}
    assert "robot" in categories
    assert "sensor" in categories
    assert "fixture" in categories
    assert "environment" in categories


def test_get_class_known():
    from service.isaac_assist_service.multimodal.object_palette import get_class
    franka = get_class("franka_panda")
    assert franka is not None
    assert franka.category == "robot"


def test_list_classes_by_category():
    from service.isaac_assist_service.multimodal.object_palette import list_classes
    robots = list_classes("robot")
    assert len(robots) >= 8
    assert all(r.category == "robot" for r in robots)
