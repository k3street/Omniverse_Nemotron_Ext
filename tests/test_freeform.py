"""Phase 30 — freeform path."""
import pytest
pytestmark = pytest.mark.l0


def test_force_freeform_marks_spec():
    from service.isaac_assist_service.multimodal.freeform import force_freeform
    result = force_freeform({"objects": []}, reason="custom_pattern")
    assert result["freeform"] is True
    assert result["reason"] == "custom_pattern"
    assert result["template_id"] is None
    assert "warning" in result
