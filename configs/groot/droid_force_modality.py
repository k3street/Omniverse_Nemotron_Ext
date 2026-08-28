"""GR00T N1.7 modality config for sensor-aware DROID post-training."""
from gr00t.configs.data.embodiment_configs import register_modality_config
from gr00t.data.embodiment_tags import EmbodimentTag
from gr00t.data.types import (
    ActionConfig,
    ActionFormat,
    ActionRepresentation,
    ActionType,
    ModalityConfig,
)


DROID_FORCE_MODALITY = {
    "video": ModalityConfig(
        delta_indices=[-15, 0],
        modality_keys=["exterior_image_1_left", "wrist_image_left"],
    ),
    "state": ModalityConfig(
        delta_indices=[0],
        modality_keys=[
            "eef_9d",
            "gripper_position",
            "joint_position",
            "joint_torque_measured",
            "joint_torque_commanded",
            "joint_torque_external",
            "eef_wrench",
            "joint_contact",
            "gripper_contact_force",
            "gripper_touch",
            "sensor_validity",
        ],
    ),
    "action": ModalityConfig(
        delta_indices=list(range(40)),
        modality_keys=["eef_9d", "gripper_position", "joint_position"],
        action_configs=[
            ActionConfig(
                rep=ActionRepresentation.RELATIVE,
                type=ActionType.EEF,
                format=ActionFormat.XYZ_ROT6D,
                state_key="eef_9d",
            ),
            ActionConfig(
                rep=ActionRepresentation.ABSOLUTE,
                type=ActionType.NON_EEF,
                format=ActionFormat.DEFAULT,
                state_key="gripper_position",
            ),
            ActionConfig(
                rep=ActionRepresentation.RELATIVE,
                type=ActionType.NON_EEF,
                format=ActionFormat.DEFAULT,
                state_key="joint_position",
            ),
        ],
    ),
    "language": ModalityConfig(
        delta_indices=[0],
        modality_keys=["annotation.language.language_instruction"],
    ),
}


register_modality_config(
    DROID_FORCE_MODALITY,
    embodiment_tag=EmbodimentTag.NEW_EMBODIMENT,
)
