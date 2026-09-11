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
        spec.validate_configuration({"maximum_action_chunks": 5})
    with pytest.raises(MotionToolValidationError):
        spec.validate_configuration({"chunk_execution_steps": 4})
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
