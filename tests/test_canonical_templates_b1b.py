"""Phase 28 — Block 1B canonical templates."""
import pytest
pytestmark = pytest.mark.l0


def test_5_canonical_templates_present():
    from service.isaac_assist_service.multimodal.canonical_templates_b1b import (
        B1B_CANONICAL_TEMPLATES,
    )
    expected_ids = {"CP-01", "CP-02", "CP-03", "CP-04", "CP-05"}
    assert set(B1B_CANONICAL_TEMPLATES.keys()) == expected_ids


def test_each_template_has_roles():
    from service.isaac_assist_service.multimodal.canonical_templates_b1b import (
        B1B_CANONICAL_TEMPLATES,
    )
    for tid, tpl in B1B_CANONICAL_TEMPLATES.items():
        assert "roles" in tpl, f"{tid} missing roles"
        assert len(tpl["roles"]) >= 1


def test_get_template_known():
    from service.isaac_assist_service.multimodal.canonical_templates_b1b import get_template
    cp1 = get_template("CP-01")
    assert cp1["name"] == "single_arm_pick_place"
