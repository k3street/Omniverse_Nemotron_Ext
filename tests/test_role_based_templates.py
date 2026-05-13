"""Phase 20 — role-based template retrieval."""
import pytest
pytestmark = pytest.mark.l0


def test_retrieve_with_roles_returns_list():
    from service.isaac_assist_service.chat.tools.role_retriever import retrieve_with_roles
    spec = type("S", (), {"intent": None, "structural_features": []})()
    result = retrieve_with_roles(spec, templates=[{"task_id": "CP-01", "roles": {}}])
    assert len(result) == 1


def test_role_based_outranks_legacy():
    from service.isaac_assist_service.chat.tools.role_retriever import retrieve_with_roles
    spec = type("S", (), {})()
    templates = [
        {"task_id": "legacy"},  # no roles
        {"task_id": "role_based", "roles": {"primary_robot": {"constraints": ["franka"]}}},
    ]
    result = retrieve_with_roles(spec, templates)
    assert result[0][0]["task_id"] == "role_based"
