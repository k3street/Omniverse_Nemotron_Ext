from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest
import torch

from scripts.robolab_groot_sensor_bridge import (
    extract_sensor_state,
    gripper_contact_force,
    gripper_touch,
    make_sensor_aware_client,
    pack_sensor_state,
    sensor_validity,
)


pytestmark = pytest.mark.l0


def test_sim_sensor_terms_label_only_available_signals():
    robot = SimpleNamespace(
        data=SimpleNamespace(
            joint_names=[*[f"panda_joint{i}" for i in range(1, 8)], "finger_joint"],
            joint_pos=torch.zeros((2, 8)),
            applied_torque=torch.tensor(
                [[1, 2, 3, 4, 5, 6, 7, 0], [8, 9, 10, 11, 12, 13, 14, 0]],
                dtype=torch.float32,
            ),
        )
    )
    contact = SimpleNamespace(
        data=SimpleNamespace(
            net_forces_w=torch.tensor(
                [[[0.0, 0.0, 2.0]], [[0.0, 3.0, 0.0]]], dtype=torch.float32
            )
        )
    )

    class Scene(dict):
        sensors = {"gripper__all_objs": contact}

    env = SimpleNamespace(scene=Scene(robot=robot))
    np.testing.assert_array_equal(
        gripper_contact_force(env).numpy(), [[0.0, 0.0, 2.0], [0.0, 3.0, 0.0]]
    )
    np.testing.assert_array_equal(
        sensor_validity(env).numpy(),
        np.tile([0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 1.0], (2, 1)),
    )


def test_live_bridge_uses_any_finger_force_for_touch():
    robot = SimpleNamespace(
        data=SimpleNamespace(
            joint_names=[*[f"panda_joint{i}" for i in range(1, 8)], "finger_joint"],
            joint_pos=torch.zeros((1, 8)),
            applied_torque=torch.zeros((1, 8)),
        )
    )
    contact = SimpleNamespace(
        data=SimpleNamespace(
            net_forces_w=torch.tensor(
                [[[2.0, 0.0, 0.0], [-2.0, 0.0, 0.0]]], dtype=torch.float32
            )
        )
    )

    class Scene(dict):
        sensors = {"gripper__all_contacts": contact}

    env = SimpleNamespace(scene=Scene(robot=robot))
    np.testing.assert_array_equal(gripper_contact_force(env).numpy(), [[0.0, 0.0, 0.0]])
    np.testing.assert_array_equal(gripper_touch(env).numpy(), [[1.0]])


def test_extract_and_pack_sensor_state_preserves_mask_semantics():
    raw = {
        "proprio_obs": {
            "joint_torque_measured": np.full((1, 7), 3.0, dtype=np.float32),
            "joint_torque_commanded": np.full((1, 7), 4.0, dtype=np.float32),
            "gripper_touch": np.ones((1, 1), dtype=np.float32),
            "sensor_validity": np.array(
                [[1.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0]], dtype=np.float32
            ),
        }
    }
    state = extract_sensor_state(raw)
    assert state["joint_torque_measured"].tolist() == [3.0] * 7
    assert state["gripper_touch"].tolist() == [0.0]
    packed = pack_sensor_state(state)
    assert packed["state.joint_torque_measured"].shape == (1, 1, 7)
    assert packed["state.sensor_validity"].shape == (1, 1, 7)
    assert all(value.dtype == np.float32 for value in packed.values())


def test_sensor_aware_client_adds_state_without_changing_base_request():
    class BaseClient:
        def _extract_observation(self, raw_obs, *, env_id=0):
            return {"base": np.array([env_id], dtype=np.float32)}

        def _pack_request(self, extracted_obs, instruction):
            return {"instruction": instruction, "base": extracted_obs["base"]}

    client = make_sensor_aware_client(BaseClient)()
    raw = {
        "sensor_obs": {
            "joint_torque_commanded": np.arange(7, dtype=np.float32)[None, :],
        }
    }
    extracted = client._extract_observation(raw, env_id=0)
    request = client._pack_request(extracted, "move the object")
    assert request["instruction"] == "move the object"
    assert request["state.joint_torque_commanded"].shape == (1, 1, 7)
    assert request["state.sensor_validity"][0, 0, 1] == 1.0
