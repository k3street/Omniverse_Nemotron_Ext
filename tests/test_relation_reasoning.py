import asyncio

import pytest


pytestmark = pytest.mark.l0


def _obj(object_id, object_class, name=None, x=0.0, y=0.0, w=0.2, h=0.2):
    return {
        "id": object_id,
        "class": object_class,
        "name": name or object_id,
        "position": {"x": x, "y": y},
        "size": {"w": w, "h": h},
    }


def test_reasoner_accepts_nested_table_bowl_fruit():
    from service.isaac_assist_service.multimodal.relation_reasoning import (
        normalize_spatial_relations,
    )

    result = normalize_spatial_relations(
        [
            _obj("table", "table_medium"),
            _obj("bowl", "bowl"),
            _obj("fruit", "fruit"),
        ],
        [
            {"subject_id": "bowl", "relation": "on", "object_id": "table"},
            {"subject_id": "fruit", "relation": "in", "object_id": "bowl"},
        ],
    )

    assert result.valid
    assert [(r.subject_id, r.relation, r.object_id) for r in result.relations] == [
        ("bowl", "on_top_of", "table"),
        ("fruit", "inside", "bowl"),
    ]
    assert result.diagnostics == []


def test_reasoner_accepts_plate_in_microwave_on_counter():
    from service.isaac_assist_service.multimodal.relation_reasoning import (
        normalize_spatial_relations,
    )

    result = normalize_spatial_relations(
        [
            _obj("counter", "kitchen_counter"),
            _obj("microwave", "microwave"),
            _obj("plate", "plate"),
            _obj("burger", "hamburger"),
        ],
        [
            {"subject_id": "microwave", "relation": "on top of", "object_id": "counter"},
            {"subject_id": "plate", "relation": "inside", "object_id": "microwave"},
            {"subject_id": "burger", "relation": "on_top_of", "object_id": "plate"},
        ],
    )

    assert result.valid
    assert [(r.subject_id, r.relation, r.object_id) for r in result.relations] == [
        ("microwave", "on_top_of", "counter"),
        ("plate", "inside", "microwave"),
        ("burger", "on_top_of", "plate"),
    ]


def test_reasoner_normalizes_robot_on_table_to_mount_with_warning():
    from service.isaac_assist_service.multimodal.relation_reasoning import (
        normalize_spatial_relations,
    )

    result = normalize_spatial_relations(
        [
            _obj("table", "table_medium"),
            _obj("franka", "franka_panda"),
        ],
        [
            {"subject_id": "franka", "relation": "on_top_of", "object_id": "table"},
        ],
    )

    assert result.valid
    assert [(r.subject_id, r.relation, r.object_id) for r in result.relations] == [
        ("franka", "mounted_to", "table"),
    ]
    assert [d.code for d in result.diagnostics] == ["relation.robot_on_support"]


def test_relation_geometry_verifier_passes_nested_predictions():
    from service.isaac_assist_service.multimodal.relation_reasoning import (
        verify_relation_geometry,
    )

    report = verify_relation_geometry(
        [
            _obj("table", "table_medium", w=1.2, h=0.8),
            _obj("bowl", "bowl", w=0.25, h=0.25),
            _obj("fruit", "fruit", w=0.07, h=0.07),
        ],
        [
            {"subject_id": "bowl", "relation": "on_top_of", "object_id": "table"},
            {"subject_id": "fruit", "relation": "inside", "object_id": "bowl"},
        ],
    )

    assert report["status"] == "pass"
    assert report["check_count"] == 2
    assert report["failed_count"] == 0


def test_relation_position_prediction_preserves_tabletop_xy_offsets():
    from service.isaac_assist_service.multimodal.relation_reasoning import (
        verify_relation_geometry,
    )

    report = verify_relation_geometry(
        [
            _obj("table", "table_medium", x=0.5, y=0.0, w=1.2, h=0.8),
            _obj("cube_a", "cube_small", x=0.32, y=0.18, w=0.05, h=0.05),
            _obj("cube_b", "cube_small", x=0.44, y=0.18, w=0.05, h=0.05),
            _obj("cube_c", "cube_small", x=0.56, y=0.18, w=0.05, h=0.05),
        ],
        [
            {"subject_id": "cube_a", "relation": "on_top_of", "object_id": "table"},
            {"subject_id": "cube_b", "relation": "on_top_of", "object_id": "table"},
            {"subject_id": "cube_c", "relation": "on_top_of", "object_id": "table"},
        ],
    )

    assert report["status"] == "pass"
    assert report["predicted_positions"]["cube_a"] == [0.32, 0.18, 0.775]
    assert report["predicted_positions"]["cube_b"] == [0.44, 0.18, 0.775]
    assert report["predicted_positions"]["cube_c"] == [0.56, 0.18, 0.775]
    assert len({
        tuple(report["predicted_positions"][object_id][:2])
        for object_id in ("cube_a", "cube_b", "cube_c")
    }) == 3


def test_relation_geometry_verifier_mounts_robot_root_on_support_surface():
    from service.isaac_assist_service.multimodal.relation_reasoning import (
        verify_relation_geometry,
    )

    report = verify_relation_geometry(
        [
            _obj("table", "table_medium", w=1.2, h=0.8),
            _obj("franka", "franka_panda", w=0.4, h=0.4),
        ],
        [
            {"subject_id": "franka", "relation": "mounted_to", "object_id": "table"},
        ],
    )

    assert report["status"] == "pass"
    assert report["checks"][0]["expected_position"] == [0.0, 0.0, 0.75]
    assert report["predicted_positions"]["franka"] == [0.0, 0.0, 0.75]


def test_relation_geometry_verifier_fails_bad_actual_position():
    from service.isaac_assist_service.multimodal.relation_reasoning import (
        verify_relation_geometry,
    )

    report = verify_relation_geometry(
        [
            _obj("table", "table_medium", w=1.2, h=0.8),
            _obj("bowl", "bowl", w=0.25, h=0.25),
        ],
        [
            {"subject_id": "bowl", "relation": "on_top_of", "object_id": "table"},
        ],
        actual_positions={
            "table": [0.0, 0.0, 0.0],
            "bowl": [2.0, 0.0, 0.0],
        },
    )

    assert report["status"] == "fail"
    assert report["failed_count"] == 1
    assert report["checks"][0]["status"] == "fail"


def test_instantiator_uses_reasoned_robot_mount_relation():
    from service.isaac_assist_service.multimodal.instantiator import instantiate

    class Spec:
        objects = [
            {
                "id": "table",
                "object_class": "table_medium",
                "name": "Table",
                "position": [0.0, 0.0, 0.0],
                "size": {"w": 1.2, "h": 0.8},
            },
            {
                "id": "franka",
                "object_class": "franka_panda",
                "name": "Franka",
                "position": [0.0, 0.0, 0.0],
                "size": {"w": 0.4, "h": 0.4},
            },
        ]
        relations = [
            {"subject_id": "franka", "relation": "on_top_of", "object_id": "table"},
        ]

    result = asyncio.run(instantiate(Spec(), dry_run=True))

    assert "# relation: franka mounted_to table" in result.generated_code
    assert "Gf.Vec3d(0.0, 0.0, 0.75)" in result.generated_code
    assert "Gf.Vec3d(0.0, 0.0, 1.25)" not in result.generated_code
    assert "# relation_diagnostic: relation.robot_on_support warning" in result.generated_code
    assert result.relation_summary[0]["relation"] == "mounted_to"
    assert result.relation_diagnostics[0]["code"] == "relation.robot_on_support"
    assert result.relation_verification["status"] == "warning"
    assert "isaac_assist:relation_verification" in result.generated_code
    assert "[Isaac Assist] relation verification:" in result.generated_code
    compile(result.generated_code, "generated_relation_scene.py", "exec")


def test_instantiator_preserves_support_relation_xy_offsets():
    from service.isaac_assist_service.multimodal.instantiator import instantiate

    class Spec:
        objects = [
            {
                "id": "table",
                "object_class": "table_medium",
                "name": "Table",
                "position": [0.5, 0.0, 0.0],
                "size": {"w": 1.2, "h": 0.8},
            },
            {
                "id": "cube_a",
                "object_class": "cube_small",
                "name": "Cube A",
                "position": [0.32, 0.18, 0.0],
                "size": {"w": 0.05, "h": 0.05},
            },
            {
                "id": "cube_b",
                "object_class": "cube_small",
                "name": "Cube B",
                "position": [0.44, 0.18, 0.0],
                "size": {"w": 0.05, "h": 0.05},
            },
        ]
        relations = [
            {"subject_id": "cube_a", "relation": "on_top_of", "object_id": "table"},
            {"subject_id": "cube_b", "relation": "on_top_of", "object_id": "table"},
        ]

    result = asyncio.run(instantiate(Spec(), dry_run=True))

    assert "Gf.Vec3d(0.32, 0.18, 0.775)" in result.generated_code
    assert "Gf.Vec3d(0.44, 0.18, 0.775)" in result.generated_code
    assert result.relation_verification["predicted_positions"]["cube_a"] == [0.32, 0.18, 0.775]
    assert result.relation_verification["predicted_positions"]["cube_b"] == [0.44, 0.18, 0.775]
    compile(result.generated_code, "generated_offset_scene.py", "exec")


def test_instantiator_authors_controller_and_ros_graph_markers():
    from service.isaac_assist_service.multimodal.instantiator import instantiate

    class Spec:
        objects = [
            {
                "id": "franka",
                "object_class": "franka_panda",
                "name": "Franka",
                "position": [0.0, 0.0, 0.0],
                "size": {"w": 0.4, "h": 0.4},
            },
        ]
        relations = []
        parameters = {
            "controller": {
                "robot_path": "/World/Franka",
                "articulation_controller": {
                    "path": "/World/IsaacAssistControllers/FrankaPickPlaceController",
                    "type": "isaacsim.core.nodes.IsaacArticulationController",
                },
                "ros2_control_graph": {
                    "path": "/World/ROS2ControlGraph",
                    "node_namespace": "isaacsim.ros2.nodes",
                    "joint_states_topic": "/isaac_joint_states",
                    "joint_commands_topic": "/isaac_joint_commands",
                    "author_omnigraph": False,
                    "omnigraph_policy": "defer_until_live_probe_passes",
                    "connect_articulation_controller": False,
                    "connection_policy": "safe_bridge_until_live_probe_passes",
                },
            },
        }

    result = asyncio.run(instantiate(Spec(), dry_run=True))

    assert "/World/IsaacAssistControllers/FrankaPickPlaceController" in result.generated_code
    assert "/World/ROS2ControlGraph" in result.generated_code
    assert "isaac_assist:kind\", \"articulation_controller" in result.generated_code
    assert "isaac_assist:kind\", \"ros2_control_omnigraph" in result.generated_code
    assert "isaacsim.core.nodes.IsaacArticulationController" in result.generated_code
    assert "isaacsim.ros2.nodes" in result.generated_code
    assert "isaacsim.ros2.bridge" in result.generated_code
    assert "ROS2Context" in result.generated_code
    assert "ROS2PublishJointState" in result.generated_code
    assert "ROS2SubscribeJointState" in result.generated_code
    assert "isaac_assist:author_ros2_omnigraph" in result.generated_code
    assert "defer_until_live_probe_passes" in result.generated_code
    assert "isaac_assist:connect_articulation_controller" in result.generated_code
    assert "deferred_live_probe" in result.generated_code
    compile(result.generated_code, "generated_controller_scene.py", "exec")
