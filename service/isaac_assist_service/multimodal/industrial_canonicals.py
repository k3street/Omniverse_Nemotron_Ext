"""Phase 31 — industrial expansion: ROS2 + OPC-UA bridge canonicals.

Adds canonical templates for industrial integration scenarios:
- ros2_arm_cell: arm robot driven by external ROS2 controller
- opcua_plc_cell: PLC mimic via OPC-UA bridge
- mqtt_sparkplug_cell: MQTT-Sparkplug industrial telemetry
- modbus_io_cell: legacy Modbus TCP discrete IO

Per specs/IA_FULL_SPEC_2026-05-10.md Phase 31.
"""
from __future__ import annotations

from typing import Dict, List


INDUSTRIAL_CANONICALS: Dict[str, Dict] = {
    "ros2_arm_cell": {
        "task_id": "IND-01",
        "name": "ros2_arm_cell",
        "bridge_type": "ros2",
        "roles": {
            "robot": {"constraints": ["franka_panda", "ur5e", "ur10"], "required": True},
            "ros2_bridge": {"constraints": ["ros2_arm_bridge"], "required": True},
            "workpiece": {"constraints": ["cube_small"], "required": False},
        },
        "tools_used": ["ros2_connect", "configure_ros2_bridge", "ros2_publish"],
    },
    "opcua_plc_cell": {
        "task_id": "IND-02",
        "name": "opcua_plc_cell",
        "bridge_type": "opcua",
        "roles": {
            "robot": {"constraints": ["ur10"], "required": True},
            "opcua_server": {"constraints": ["openplc_runtime"], "required": True},
            "conveyor": {"constraints": ["conveyor_short", "conveyor_long"], "required": False},
        },
        "tools_used": ["opcua_bridge_attach", "diagnose_opcua_bridge"],
    },
    "mqtt_sparkplug_cell": {
        "task_id": "IND-03",
        "name": "mqtt_sparkplug_cell",
        "bridge_type": "mqtt_sparkplug",
        "roles": {
            "robot": {"constraints": ["ur5e", "ur10"], "required": True},
            "mqtt_broker": {"constraints": ["mqtt_sparkplug_broker"], "required": True},
            "sensors": {"constraints": ["force_torque_sensor", "barcode_reader"], "required": False},
        },
        "tools_used": ["mqtt_sparkplug_bridge_attach", "diagnose_mqtt_sparkplug_bridge"],
    },
    "modbus_io_cell": {
        "task_id": "IND-04",
        "name": "modbus_io_cell",
        "bridge_type": "modbus_tcp",
        "roles": {
            "robot": {"constraints": ["ur5e", "ur10"], "required": True},
            "modbus_io": {"constraints": ["modbus_tcp_client"], "required": True},
            "sensors": {"constraints": ["proximity_sensor"], "required": False},
        },
        "tools_used": ["modbus_tcp_bridge_attach", "diagnose_modbus_bridge"],
    },
}


def get_canonical(task_id: str) -> Dict:
    for tpl in INDUSTRIAL_CANONICALS.values():
        if tpl["task_id"] == task_id:
            return tpl
    return {}


def list_canonicals_by_bridge(bridge_type: str) -> List[Dict]:
    return [t for t in INDUSTRIAL_CANONICALS.values() if t["bridge_type"] == bridge_type]
