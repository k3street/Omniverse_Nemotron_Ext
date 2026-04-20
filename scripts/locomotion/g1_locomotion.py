# Copyright (c) 2022-2025, The Isaac Lab Project Developers.
# SPDX-License-Identifier: BSD-3-Clause

"""
Interactive G1 flat-terrain locomotion demo with keyboard control.

Mirrors h1_locomotion.py for the Unitree G1 (29-DOF, flat terrain).
The pre-trained checkpoint is downloaded automatically from NVIDIA Nucleus
on the first run and cached locally in .pretrained_checkpoints/.

Usage:
    ./isaaclab.sh -p scripts/demos/g1_locomotion.py

Keyboard controls (click a robot in the viewport to select it first):
    UP    — walk forward
    LEFT  — turn left
    RIGHT — turn right
    DOWN  — stop
    C     — toggle third-person / perspective camera
    ESC   — deselect robot
"""

import argparse
import os
import sys

sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "../.."))
import scripts.reinforcement_learning.rsl_rl.cli_args as cli_args  # isort: skip

from isaaclab.app import AppLauncher

parser = argparse.ArgumentParser(description="G1 flat-terrain locomotion demo with keyboard control.")
cli_args.add_rsl_rl_args(parser)
AppLauncher.add_app_launcher_args(parser)
args_cli = parser.parse_args()

app_launcher = AppLauncher(args_cli)
simulation_app = app_launcher.app

import torch

import carb
import omni
from isaacsim.core.utils.stage import get_current_stage
from omni.kit.viewport.utility import get_viewport_from_window_name
from omni.kit.viewport.utility.camera_state import ViewportCameraState
from pxr import Gf, Sdf
from rsl_rl.runners import OnPolicyRunner

from isaaclab.envs import ManagerBasedRLEnv
from isaaclab.utils.math import quat_apply
from isaaclab.utils.pretrained_checkpoint import get_published_pretrained_checkpoint

from isaaclab_rl.rsl_rl import RslRlOnPolicyRunnerCfg, RslRlVecEnvWrapper

from isaaclab_tasks.manager_based.locomotion.velocity.config.g1.flat_env_cfg import G1FlatEnvCfg_PLAY

TASK = "Isaac-Velocity-Flat-G1-v0"
RL_LIBRARY = "rsl_rl"


class G1FlatDemo:
    """Interactive G1 locomotion demo.

    Click a robot to select it, then use arrow keys to drive it.
    The policy controls only the 12 leg DOFs; arms should be held by
    PD drives (use Isaac Assist freeze_upper_body / deploy_rl_policy).
    """

    def __init__(self):
        agent_cfg: RslRlOnPolicyRunnerCfg = cli_args.parse_rsl_rl_cfg(TASK, args_cli)

        checkpoint = get_published_pretrained_checkpoint(RL_LIBRARY, TASK)
        if checkpoint is None:
            raise RuntimeError(
                f"No pre-trained checkpoint found for {TASK}. "
                "Ensure NVIDIA Nucleus is reachable from Isaac Sim, or supply "
                "--checkpoint /path/to/checkpoint.pt manually."
            )

        env_cfg = G1FlatEnvCfg_PLAY()
        env_cfg.scene.num_envs = getattr(args_cli, "num_envs", None) or 1
        env_cfg.episode_length_s = 1_000_000
        env_cfg.curriculum = None
        # Allow the full command range for interactive control
        env_cfg.commands.base_velocity.ranges.lin_vel_x = (0.0, 1.0)
        env_cfg.commands.base_velocity.ranges.lin_vel_y = (-0.5, 0.5)
        env_cfg.commands.base_velocity.ranges.heading = (-1.0, 1.0)
        # Disable domain randomisation for interactive use
        env_cfg.observations.policy.enable_corruption = False
        env_cfg.events.base_external_force_torque = None
        env_cfg.events.push_robot = None

        self.env = RslRlVecEnvWrapper(ManagerBasedRLEnv(cfg=env_cfg))
        self.device = self.env.unwrapped.device

        ppo_runner = OnPolicyRunner(self.env, agent_cfg.to_dict(), log_dir=None, device=self.device)
        ppo_runner.load(checkpoint)
        self.policy = ppo_runner.get_inference_policy(device=self.device)

        self.create_camera()
        self.commands = torch.zeros(env_cfg.scene.num_envs, 4, device=self.device)
        self.commands[:, 0:3] = self.env.unwrapped.command_manager.get_command("base_velocity")
        self.set_up_keyboard()
        self._prim_selection = omni.usd.get_context().get_selection()
        self._selected_id = None
        self._previous_selected_id = None
        self._camera_local_transform = torch.tensor([-2.5, 0.0, 0.8], device=self.device)

    def create_camera(self):
        stage = get_current_stage()
        self.viewport = get_viewport_from_window_name("Viewport")
        self.camera_path = "/World/Camera"
        self.perspective_path = "/OmniverseKit_Persp"
        camera_prim = stage.DefinePrim(self.camera_path, "Camera")
        camera_prim.GetAttribute("focalLength").Set(8.5)
        coi_prop = camera_prim.GetProperty("omni:kit:centerOfInterest")
        if not coi_prop or not coi_prop.IsValid():
            camera_prim.CreateAttribute(
                "omni:kit:centerOfInterest", Sdf.ValueTypeNames.Vector3d, True, Sdf.VariabilityUniform
            ).Set(Gf.Vec3d(0, 0, -10))
        self.viewport.set_active_camera(self.perspective_path)

    def set_up_keyboard(self):
        self._input = carb.input.acquire_input_interface()
        self._keyboard = omni.appwindow.get_default_app_window().get_keyboard()
        self._sub_keyboard = self._input.subscribe_to_keyboard_events(self._keyboard, self._on_keyboard_event)
        T, R = 1.0, 0.5
        self._key_to_control = {
            "UP":    torch.tensor([T,  0.0, 0.0,  0.0], device=self.device),
            "DOWN":  torch.tensor([0.0, 0.0, 0.0, 0.0], device=self.device),
            "LEFT":  torch.tensor([T,  0.0, 0.0, -R  ], device=self.device),
            "RIGHT": torch.tensor([T,  0.0, 0.0,  R  ], device=self.device),
            "ZEROS": torch.tensor([0.0, 0.0, 0.0, 0.0], device=self.device),
        }

    def _on_keyboard_event(self, event):
        if event.type == carb.input.KeyboardEventType.KEY_PRESS:
            if event.input.name in self._key_to_control and self._selected_id is not None:
                self.commands[self._selected_id] = self._key_to_control[event.input.name]
            elif event.input.name == "ESCAPE":
                self._prim_selection.clear_selected_prim_paths()
            elif event.input.name == "C" and self._selected_id is not None:
                cam = self.camera_path if self.viewport.get_active_camera() != self.camera_path else self.perspective_path
                self.viewport.set_active_camera(cam)
        elif event.type == carb.input.KeyboardEventType.KEY_RELEASE and self._selected_id is not None:
            self.commands[self._selected_id] = self._key_to_control["ZEROS"]

    def update_selected_object(self):
        self._previous_selected_id = self._selected_id
        selected_prim_paths = self._prim_selection.get_selected_prim_paths()
        if len(selected_prim_paths) == 0:
            self._selected_id = None
            self.viewport.set_active_camera(self.perspective_path)
        elif len(selected_prim_paths) == 1:
            parts = selected_prim_paths[0].split("/")
            if len(parts) >= 4 and parts[3].startswith("env_"):
                self._selected_id = int(parts[3][4:])
                if self._previous_selected_id != self._selected_id:
                    self.viewport.set_active_camera(self.camera_path)
                self._update_camera()

        if self._previous_selected_id is not None and self._previous_selected_id != self._selected_id:
            self.env.unwrapped.command_manager.reset([self._previous_selected_id])
            self.commands[:, 0:3] = self.env.unwrapped.command_manager.get_command("base_velocity")

    def _update_camera(self):
        base_pos = self.env.unwrapped.scene["robot"].data.root_pos_w[self._selected_id, :]
        base_quat = self.env.unwrapped.scene["robot"].data.root_quat_w[self._selected_id, :]
        camera_pos = quat_apply(base_quat, self._camera_local_transform) + base_pos
        camera_state = ViewportCameraState(self.camera_path, self.viewport)
        eye = Gf.Vec3d(camera_pos[0].item(), camera_pos[1].item(), camera_pos[2].item())
        target = Gf.Vec3d(base_pos[0].item(), base_pos[1].item(), base_pos[2].item() + 0.6)
        camera_state.set_position_world(eye, True)
        camera_state.set_target_world(target, True)


def main():
    demo = G1FlatDemo()
    obs, _ = demo.env.reset()
    while simulation_app.is_running():
        demo.update_selected_object()
        with torch.inference_mode():
            action = demo.policy(obs)
            obs, _, _, _ = demo.env.step(action)
            # G1 flat observation layout:
            #   [0:3]  base_lin_vel, [3:6] base_ang_vel, [6:9] projected_gravity,
            #   [9:12] base_velocity_cmd, [12:41] joint_pos(29), [41:70] joint_vel(29),
            #   [70:99] last_actions(29)
            obs[:, 9:12] = demo.commands[:, 0:3]


if __name__ == "__main__":
    main()
    simulation_app.close()
