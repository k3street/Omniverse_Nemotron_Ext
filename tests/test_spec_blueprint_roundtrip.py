"""Phase 27 — LayoutSpec ↔ scene blueprint."""
import pytest
pytestmark = pytest.mark.l0


def test_spec_to_blueprint_produces_blueprint():
    from service.isaac_assist_service.multimodal.spec_to_blueprint import spec_to_blueprint

    class S:
        objects = [{"object_class": "franka_panda", "position": [0, 0, 0.8]}]
    bp = spec_to_blueprint(S(), name="my_layout")
    assert bp["name"] == "my_layout"
    assert len(bp["objects"]) == 1
    assert bp["objects"][0]["asset_name"] == "franka_panda"


def test_blueprint_to_spec_stub_reverses():
    from service.isaac_assist_service.multimodal.spec_to_blueprint import (
        spec_to_blueprint, blueprint_to_spec_stub,
    )

    class S:
        objects = [{"object_class": "cube", "position": [0.1, 0.2, 0.3]}]
    bp = spec_to_blueprint(S())
    back = blueprint_to_spec_stub(bp)
    assert back["objects"][0]["object_class"] == "cube"
    assert back["objects"][0]["position"] == [0.1, 0.2, 0.3]
