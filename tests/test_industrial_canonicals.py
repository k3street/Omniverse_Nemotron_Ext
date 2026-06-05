"""Phase 31 — industrial canonicals."""
import pytest
pytestmark = pytest.mark.l0


def test_4_industrial_canonicals_present():
    from service.isaac_assist_service.multimodal.industrial_canonicals import INDUSTRIAL_CANONICALS
    assert len(INDUSTRIAL_CANONICALS) >= 4


def test_each_has_bridge_type():
    from service.isaac_assist_service.multimodal.industrial_canonicals import INDUSTRIAL_CANONICALS
    bridge_types = {t["bridge_type"] for t in INDUSTRIAL_CANONICALS.values()}
    assert "ros2" in bridge_types
    assert "opcua" in bridge_types
    assert "mqtt_sparkplug" in bridge_types
    assert "modbus_tcp" in bridge_types


def test_list_canonicals_by_bridge():
    from service.isaac_assist_service.multimodal.industrial_canonicals import list_canonicals_by_bridge
    ros2 = list_canonicals_by_bridge("ros2")
    assert len(ros2) == 1
    assert ros2[0]["name"] == "ros2_arm_cell"
