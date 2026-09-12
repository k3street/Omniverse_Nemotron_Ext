"""
L0 tests for the Cosmos 3 Edge motion executor: advertised contract
(spec/schemas/config validation) and transport-side observation packing.
Execution against a live policy server is exercised by the runner, not here.
"""
import numpy as np
import pytest

pytestmark = pytest.mark.l0

from scripts.cosmos3_edge_executor import (
    COSMOS3_EDGE_CAPABILITY_TAG,
    COSMOS3_EDGE_EXECUTOR_ID,
    COSMOS3_EDGE_TOOL_NAME,
    build_cosmos3_edge_motion_executor_spec,
)
from scripts.observation_bound_motion_tools import (
    MotionExecutorRegistry,
    MotionToolValidationError,
)


def _spec():
    return build_cosmos3_edge_motion_executor_spec(
        capability_tags=("spatial.direct_pose", COSMOS3_EDGE_CAPABILITY_TAG),
        minimum_reachable_radius_m=0.25,
        maximum_reachable_radius_m=0.85,
        maximum_displacement_m=0.40,
    )


# ---------------------------------------------------------------------------
# Advertised contract
# ---------------------------------------------------------------------------

def test_spec_registers_in_motion_registry():
    registry = MotionExecutorRegistry()
    registry.register(_spec())
    resolved = registry.resolve(COSMOS3_EDGE_TOOL_NAME)
    assert resolved is not None
    assert resolved.executor_id == COSMOS3_EDGE_EXECUTOR_ID
    (advertisement,) = registry.advertisement()
    assert advertisement["tool_family"] == "motion"
    assert advertisement["tool_name"] == COSMOS3_EDGE_TOOL_NAME
    assert COSMOS3_EDGE_CAPABILITY_TAG in advertisement["capability_tags"]


def test_invocation_schema_is_pose_grounded():
    spec = _spec()
    schema = spec.invocation_schema
    assert set(schema["required"]) == {
        "target_position_m",
        "target_quaternion_wxyz",
    }
    constraints = schema["x-runtime-constraints"]
    assert constraints["coordinate_frame"] == "robot_root"
    assert constraints["minimum_reachable_radius_m"] == 0.25
    assert constraints["maximum_reachable_radius_m"] == 0.85
    assert constraints["maximum_displacement_m"] == 0.40


def test_tool_schema_exposes_executor_config():
    schema = _spec().tool_schema("obs-1")
    function = schema["function"]
    assert function["name"] == COSMOS3_EDGE_TOOL_NAME
    properties = function["parameters"]["properties"]
    assert "executor_config" in properties
    assert "maximum_action_chunks" in (
        properties["executor_config"]["properties"]
    )


def test_configuration_validation_bounds():
    spec = _spec()
    validated = spec.validate_configuration(
        {"maximum_action_chunks": 2, "chunk_execution_steps": 16}
    )
    assert validated == {
        "maximum_action_chunks": 2,
        "chunk_execution_steps": 16,
    }
    with pytest.raises(MotionToolValidationError):
        spec.validate_configuration({"maximum_action_chunks": 0})
    with pytest.raises(MotionToolValidationError):
        spec.validate_configuration({"chunk_execution_steps": 4})
    # A pose-servo-grade tolerance is not a contract a learned policy can honour:
    # it stops at the object, ~0.10-0.15 m from a standoff pre-grasp pose.
    for too_tight in (0.003, 0.05):
        with pytest.raises(MotionToolValidationError):
            spec.validate_configuration({"position_tolerance_m": too_tight})
    assert spec.validate_configuration({"position_tolerance_m": 0.12}) == {
        "position_tolerance_m": 0.12
    }
    with pytest.raises(MotionToolValidationError):
        spec.validate_configuration({"unknown_setting": True})


# ---------------------------------------------------------------------------
# Transport-side observation packing
# ---------------------------------------------------------------------------

def _openpi_available() -> bool:
    try:
        from scripts.cosmos3_edge_client import _ensure_openpi_client

        _ensure_openpi_client()
        return True
    except Exception:
        return False


@pytest.mark.skipif(
    not _openpi_available(), reason="vendored openpi_client unavailable"
)
def test_composite_frame_matches_server_contract():
    from scripts.cosmos3_edge_client import (
        COMPOSITE_HEIGHT,
        COMPOSITE_WIDTH,
        pack_composite_frame,
    )

    frame = pack_composite_frame(
        wrist_rgb=np.zeros((720, 1280, 3), dtype=np.uint8),
        left_rgb=np.full((720, 1280, 3), 255, dtype=np.uint8),
        right_rgb=np.zeros((480, 640, 3), dtype=np.uint8),
    )
    assert frame.shape == (COMPOSITE_HEIGHT, COMPOSITE_WIDTH, 3)
    assert frame.dtype == np.uint8
    # Wrist occupies the top row; the two half-scale views share the bottom.
    assert frame[:360].max() == 0
    assert frame[360:, :320].max() == 255


@pytest.mark.skipif(
    not _openpi_available(), reason="vendored openpi_client unavailable"
)
def test_non_image_input_fails_loud():
    from scripts.cosmos3_edge_client import (
        Cosmos3EdgeClientError,
        pack_composite_frame,
    )

    with pytest.raises(Cosmos3EdgeClientError):
        pack_composite_frame(
            wrist_rgb=np.zeros((360, 640), dtype=np.uint8),
            left_rgb=np.zeros((360, 640, 3), dtype=np.uint8),
            right_rgb=np.zeros((360, 640, 3), dtype=np.uint8),
        )


# ---------------------------------------------------------------------------
# Per-operation policy instruction (lane 2b)
# ---------------------------------------------------------------------------

def test_policy_instruction_realize_effect_with_receptacle():
    from scripts.cosmos3_edge_executor import policy_instruction_for_operation

    instruction, source = policy_instruction_for_operation(
        purpose="realize_effect",
        target_entity_ids=("grey_bin", "blue_block"),
        receptacle_entity_id="grey_bin",
        fallback_instruction="Clean the table",
    )
    assert instruction == "put the blue block in the grey bin"
    assert source == "runtime_operation_template"


def test_policy_instruction_precondition_and_single_object():
    from scripts.cosmos3_edge_executor import policy_instruction_for_operation

    move, _ = policy_instruction_for_operation(
        purpose="establish_precondition",
        target_entity_ids=("lizard_figurine_01",),
        receptacle_entity_id="grey_bin",
        fallback_instruction="Clean the table",
    )
    assert move == "move to the lizard figurine 01"
    pick, _ = policy_instruction_for_operation(
        purpose="realize_effect",
        target_entity_ids=("red_block",),
        receptacle_entity_id="grey_bin",
        fallback_instruction="Clean the table",
    )
    assert pick == "pick up the red block"


def test_policy_instruction_falls_back_to_session_instruction():
    from scripts.cosmos3_edge_executor import policy_instruction_for_operation

    for purpose, targets in (
        ("observe", ("blue_block",)),
        ("realize_effect", ()),
        ("realize_effect", ("grey_bin",)),
    ):
        instruction, source = policy_instruction_for_operation(
            purpose=purpose,
            target_entity_ids=targets,
            receptacle_entity_id="grey_bin",
            fallback_instruction="Clean the table",
        )
        assert instruction == "Clean the table"
        assert source == "session_instruction"


def test_sim5_camera_offsets_reordered_once_for_present_cameras():
    from types import SimpleNamespace

    from scripts.sim6_camera_offsets import (
        POLICY_CAMERA_NAMES,
        convert_sim5_camera_offsets,
    )

    def camera(rot):
        return SimpleNamespace(offset=SimpleNamespace(rot=rot))

    env_cfg = SimpleNamespace(
        scene=SimpleNamespace(
            wrist_cam=camera((-0.420, 0.570, 0.576, -0.409)),
            over_shoulder_left_camera=camera((-0.393, -0.195, 0.399, 0.805)),
        )
    )
    converted = convert_sim5_camera_offsets(env_cfg)
    assert converted == ["wrist_cam", "over_shoulder_left_camera"]
    assert set(converted) < set(POLICY_CAMERA_NAMES)
    # (w, x, y, z) -> (x, y, z, w)
    assert env_cfg.scene.wrist_cam.offset.rot == (0.570, 0.576, -0.409, -0.420)
    assert env_cfg.scene.over_shoulder_left_camera.offset.rot == (
        -0.195, 0.399, 0.805, -0.393
    )


def test_gripper_contact_classification():
    from scripts.cosmos3_edge_executor import classify_gripper_contact

    def classify(**kwargs):
        base = dict(
            touch=True, closed_fraction=0.3, retained_force_n=0.0,
            target_distance_m=None,
        )
        base.update(kwargs)
        return classify_gripper_contact(**base)

    assert classify(touch=False)["contact_class"] == "none"
    # A fully closed empty gripper is the pads pressing each other.
    assert classify(closed_fraction=0.98)["contact_class"] == "self_closure"
    assert classify(closed_fraction=0.98, retained_force_n=2.0)[
        "contact_class"
    ] == "self_closure"
    # An opposing pinch with the gripper stopped by something is a grasp.
    assert classify(closed_fraction=0.6, retained_force_n=1.5)[
        "contact_class"
    ] == "retained_object"
    external = classify(closed_fraction=0.0)
    assert external["contact_class"] == "external"
    assert external["attributed_to_target"] is False
    # Touching the entity the rollout was sent to is the intended interaction.
    near = classify(closed_fraction=0.0, target_distance_m=0.08)
    assert near["attributed_to_target"] is True
    far = classify(closed_fraction=0.0, target_distance_m=0.40)
    assert far["attributed_to_target"] is False
    # Self-closure is never attributed as contact, even next to a target.
    assert classify(closed_fraction=1.0, target_distance_m=0.02)[
        "attributed_to_target"
    ] is False


def test_rollout_budget_bounds():
    from scripts.cosmos3_edge_executor import MAXIMUM_ACTION_CHUNKS

    spec = _spec()
    assert spec.validate_configuration(
        {"maximum_action_chunks": MAXIMUM_ACTION_CHUNKS}
    ) == {"maximum_action_chunks": MAXIMUM_ACTION_CHUNKS}
    with pytest.raises(MotionToolValidationError):
        spec.validate_configuration(
            {"maximum_action_chunks": MAXIMUM_ACTION_CHUNKS + 1}
        )
