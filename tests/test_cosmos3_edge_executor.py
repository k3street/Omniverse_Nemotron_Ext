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


# ---------------------------------------------------------------------------
# Policy-owned acquisition (actuator family)
# ---------------------------------------------------------------------------

def _acquire_spec():
    from scripts.cosmos3_edge_executor import (
        COSMOS3_EDGE_CAPABILITY_TAG,
        build_cosmos3_edge_acquire_executor_spec,
    )

    return build_cosmos3_edge_acquire_executor_spec(
        capability_tags=(
            "entity_attachment.acquire",
            "entity_attachment.release",
            "actuation.observation_bound",
            COSMOS3_EDGE_CAPABILITY_TAG,
        )
    )


def test_acquire_spec_coexists_with_the_binary_clamp():
    from scripts.observation_bound_motion_tools import (
        ActuatorExecutorRegistry,
        ActuatorExecutorSpec,
    )
    from scripts.cosmos3_edge_executor import (
        COSMOS3_EDGE_ACQUIRE_EXECUTOR_ID,
        COSMOS3_EDGE_ACQUIRE_TOOL_NAME,
    )

    clamp = ActuatorExecutorSpec(
        executor_id="binary_end_effector_clamp",
        tool_name="execute_binary_end_effector_clamp",
        description="Binary clamp.",
        command_schema={
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "state": {"type": "string", "enum": ["engage", "disengage", "maintain"]}
            },
            "required": ["state"],
        },
        configuration_schema={"type": "object", "properties": {}},
        capability_tags=(
            "entity_attachment.acquire",
            "entity_attachment.release",
            "actuation.observation_bound",
        ),
        semantic_command_bindings={
            "entity_attachment.acquire": {"state": "engage"},
            "entity_attachment.release": {"state": "disengage"},
        },
    )
    registry = ActuatorExecutorRegistry()
    registry.register(clamp)
    registry.register(_acquire_spec())
    assert {spec.executor_id for spec in registry.specs()} == {
        "binary_end_effector_clamp",
        COSMOS3_EDGE_ACQUIRE_EXECUTOR_ID,
    }
    assert registry.resolve(COSMOS3_EDGE_ACQUIRE_TOOL_NAME) is not None


def test_acquire_spec_keeps_the_runtime_command_vocabulary():
    # Attachment bookkeeping keys off requested_state, and post-release
    # geometry-change classification requires a literal "disengage".
    spec = _acquire_spec()
    advertisement = spec.advertisement()
    assert advertisement["tool_family"] == "actuator"
    assert advertisement["semantic_command_bindings"] == {
        "entity_attachment.acquire": {"state": "engage"},
        "entity_attachment.release": {"state": "disengage"},
    }
    assert set(
        advertisement["command_schema"]["properties"]["state"]["enum"]
    ) == {"engage", "disengage", "maintain"}
    # The operation planner filters recovery candidates by a ".release" suffix.
    assert any(
        effect_id.endswith(".release")
        for effect_id in advertisement["semantic_command_bindings"]
    )
    for command in advertisement["semantic_command_bindings"].values():
        assert spec.validate_command(command) == command


def test_acquire_rollout_budget_is_bounded():
    from scripts.cosmos3_edge_executor import MAXIMUM_ACQUIRE_ACTION_CHUNKS
    from scripts.observation_bound_motion_tools import MotionToolValidationError

    spec = _acquire_spec()
    assert spec.validate_configuration(
        {"maximum_action_chunks": MAXIMUM_ACQUIRE_ACTION_CHUNKS}
    ) == {"maximum_action_chunks": MAXIMUM_ACQUIRE_ACTION_CHUNKS}
    with pytest.raises(MotionToolValidationError):
        spec.validate_configuration(
            {"maximum_action_chunks": MAXIMUM_ACQUIRE_ACTION_CHUNKS + 1}
        )
    # settle_steps still configures the direct disengage/maintain commands.
    assert spec.validate_configuration({"settle_steps": 35}) == {"settle_steps": 35}


# ---------------------------------------------------------------------------
# Approach folded into a policy acquisition (lane 2c)
# ---------------------------------------------------------------------------

class _Call:
    def __init__(self, call_id, tool_id, tool_family, targets, effect=None):
        self.call_id = call_id
        self.tool_id = tool_id
        self.tool_family = tool_family
        self.target_entity_ids = tuple(targets)
        self.semantic_effect_id = effect

    def __repr__(self):
        return f"<{self.call_id}>"


def _ids(calls):
    return [c.call_id for c in calls]


def test_alignment_motion_before_policy_acquisition_is_dropped():
    from scripts.cosmos3_edge_executor import (
        COSMOS3_EDGE_ACQUIRE_EXECUTOR_ID,
        drop_redundant_pre_acquisition_motions,
    )

    calls = [
        _Call("align", "cosmos3_edge_policy", "motion", ["red_block"]),
        _Call(
            "acquire",
            COSMOS3_EDGE_ACQUIRE_EXECUTOR_ID,
            "actuator",
            ["red_block"],
            "entity_attachment.acquire",
        ),
        _Call("transport", "cosmos3_edge_policy", "motion", ["grey_bin"]),
        _Call(
            "release",
            COSMOS3_EDGE_ACQUIRE_EXECUTOR_ID,
            "actuator",
            ["red_block"],
            "entity_attachment.release",
        ),
    ]
    kept = drop_redundant_pre_acquisition_motions(
        calls, {COSMOS3_EDGE_ACQUIRE_EXECUTOR_ID}
    )
    # The approach is part of the acquisition; transport is not.
    assert _ids(kept) == ["acquire", "transport", "release"]


def test_unrelated_and_post_acquisition_motions_are_preserved():
    from scripts.cosmos3_edge_executor import (
        COSMOS3_EDGE_ACQUIRE_EXECUTOR_ID,
        drop_redundant_pre_acquisition_motions,
    )

    calls = [
        _Call("clear_lid", "cosmos3_edge_policy", "motion", ["grey_bin"]),
        _Call(
            "acquire",
            COSMOS3_EDGE_ACQUIRE_EXECUTOR_ID,
            "actuator",
            ["red_block"],
            "entity_attachment.acquire",
        ),
        _Call("nudge", "cosmos3_edge_policy", "motion", ["red_block"]),
    ]
    kept = drop_redundant_pre_acquisition_motions(
        calls, {COSMOS3_EDGE_ACQUIRE_EXECUTOR_ID}
    )
    assert _ids(kept) == ["clear_lid", "acquire", "nudge"]


def test_clamp_sequences_and_empty_registries_are_untouched():
    from scripts.cosmos3_edge_executor import (
        COSMOS3_EDGE_ACQUIRE_EXECUTOR_ID,
        drop_redundant_pre_acquisition_motions,
    )

    clamp_calls = [
        _Call("align", "bounded_dls_ik", "motion", ["red_block"]),
        _Call(
            "clamp",
            "binary_end_effector_clamp",
            "actuator",
            ["red_block"],
            "entity_attachment.acquire",
        ),
    ]
    # A gripper-only clamp still needs its alignment motion.
    assert _ids(
        drop_redundant_pre_acquisition_motions(
            clamp_calls, {COSMOS3_EDGE_ACQUIRE_EXECUTOR_ID}
        )
    ) == ["align", "clamp"]
    assert _ids(drop_redundant_pre_acquisition_motions(clamp_calls, set())) == [
        "align",
        "clamp",
    ]


# ---------------------------------------------------------------------------
# A policy engage without retention must not read as a completed operation
# ---------------------------------------------------------------------------

def test_policy_engage_without_retention_is_flagged():
    from scripts.cosmos3_edge_executor import acquisition_not_retained

    base = {
        "acquisition_source": "cosmos3_edge_policy_rollout",
        "requested_state": "engage",
    }
    assert acquisition_not_retained({**base, "engaged_after": False}) is True
    assert acquisition_not_retained({**base, "engaged_after": True}) is False
    # Release and hold are direct gripper commands; they settle, they do not grasp.
    assert acquisition_not_retained(
        {**base, "requested_state": "disengage", "engaged_after": False}
    ) is False
    # The binary clamp keeps its own semantics untouched.
    assert acquisition_not_retained(
        {"executor_id": "binary_end_effector_clamp", "requested_state": "engage",
         "engaged_after": False}
    ) is False
    assert acquisition_not_retained(None) is False
