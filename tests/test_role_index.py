"""Phase 21 — role index."""
import pytest
pytestmark = pytest.mark.l0


def test_role_index_adds_and_finds():
    from service.isaac_assist_service.chat.tools.role_index import RoleIndex
    idx = RoleIndex()
    idx.add_template("CP-01", {
        "primary_robot": {"constraints": ["franka_panda", "ur5e"]},
        "workpiece": {"constraints": ["cube"]},
    })
    assert "CP-01" in idx.find_by_role("primary_robot")
    assert "CP-01" in idx.find_by_class("franka_panda")
    assert "CP-01" in idx.find_by_class("ur5e")


def test_role_index_empty_returns_empty_list():
    from service.isaac_assist_service.chat.tools.role_index import RoleIndex
    idx = RoleIndex()
    assert idx.find_by_role("nonexistent") == []
