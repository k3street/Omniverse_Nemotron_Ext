"""Measure Cosmos 3 Edge grasp behaviour per scene, without any planner calls.

The composed-execution campaign showed policy acquisitions closing the gripper
on empty space. That points at perception -- the policy never reaches the
object -- but the evidence came from a handful of rollouts inside the planner
loop. This harness isolates the policy: it runs repeated "pick up the X"
rollouts against a scene and reports, per trial, how close the finger pads
ever got to the target and whether the closure held anything.

Closure is read geometrically rather than from contact sensors: a two-finger
gripper that closes on nothing reaches full closure, while one closing on a
few-centimetre object stops partway. Combined with the object's own
displacement and lift, that separates a real grasp from a closure on air.

Run under the launcher environment, e.g.

  $ISAAC_SIM_ROOT/python.sh scripts/bench_cosmos3_edge_grasp.py \
      --headless --task BananaOnPlateTask --object banana \
      --instruction "pick up the banana" --trials 10
"""

import argparse
import json

import cv2  # noqa: F401  # Must import before isaaclab. Do not remove.
from isaaclab.app import AppLauncher

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--task", default="BlocksInBinTask")
parser.add_argument("--object", default="red_block")
parser.add_argument("--instruction", default="pick up the red block")
parser.add_argument("--trials", type=int, default=10)
parser.add_argument("--chunks", type=int, default=4)
parser.add_argument("--settle-steps", type=int, default=30)
parser.add_argument("--host", default="localhost")
parser.add_argument("--port", type=int, default=8000)
parser.add_argument("--out", default=None, help="Write per-trial JSON here.")
AppLauncher.add_app_launcher_args(parser)
args_cli = parser.parse_args()
args_cli.enable_cameras = True
app_launcher = AppLauncher(args_cli)
simulation_app = app_launcher.app

import numpy as np  # noqa: E402
import torch  # noqa: E402
from robolab.core.environments.runtime import create_env  # noqa: E402
from robolab.core.environments.config import parse_env_cfg  # noqa: E402
from robolab.registrations.droid.auto_env_registrations_abs_ik import (  # noqa: E402
    auto_register_droid_abs_ik_envs,
)
from robolab.registrations.droid.camera_presets import (  # noqa: E402
    WRIST_LEFT_RIGHT,
)
from robolab.robots.droid import DroidJointPositionActionCfg  # noqa: E402
from cosmos3_edge_client import Cosmos3EdgeChunkClient  # noqa: E402
from sim6_camera_offsets import convert_sim5_camera_offsets  # noqa: E402
from cosmos3_edge_executor import SELF_CLOSURE_FRACTION  # noqa: E402

CAMERA_NAMES = (
    "wrist_cam",
    "over_shoulder_left_camera",
    "over_shoulder_right_camera",
)
# A closure that holds a few-centimetre object cannot also be fully shut.
MINIMUM_ENGAGED_FRACTION = 0.05
# Lift that a settled object does not produce on its own.
HELD_LIFT_M = 0.01


def frame(obs, name):
    tensor = obs["image_obs"][name][0]
    array = (
        tensor.detach().cpu().numpy()
        if hasattr(tensor, "detach")
        else np.asarray(tensor)
    )
    return array[..., :3].astype(np.uint8)


def torch_view(value):
    return getattr(value, "torch", value)


def pad_center_m(env):
    """Finger-pad midpoint in robot-root coordinates."""
    robot = env.scene["robot"]
    names = list(robot.data.body_names)
    body_pos_w = torch_view(robot.data.body_pos_w)
    root_pos_w = torch_view(robot.data.root_pos_w)
    pads = torch.stack(
        [
            body_pos_w[0, names.index(name)]
            for name in ("left_inner_finger", "right_inner_finger")
        ]
    ).mean(dim=0)
    return (pads - root_pos_w[0]).detach().float().cpu().numpy()


def object_xyz_m(env, name):
    asset = env.scene[name]
    root_pos_w = torch_view(robot_root(env))
    positions = torch_view(asset.data.root_pos_w)
    return (positions[0] - root_pos_w).detach().float().cpu().numpy()


def robot_root(env):
    return torch_view(env.scene["robot"].data.root_pos_w)[0]


def closed_fraction(env):
    robot = env.scene["robot"]
    joint_pos = torch_view(robot.data.joint_pos)
    index = robot.data.joint_names.index("finger_joint")
    return float(
        min(
            1.0,
            max(0.0, float(joint_pos[0, index].detach().cpu()) / (np.pi / 4.0)),
        )
    )


def arm_joints(env):
    robot = env.scene["robot"]
    joint_pos = torch_view(robot.data.joint_pos)
    ids = [
        robot.data.joint_names.index(f"panda_joint{i}") for i in range(1, 8)
    ]
    return joint_pos[0, ids].detach().cpu().numpy()


def classify(max_closed, final_closed, lift_m, displacement_m):
    """Name what the closure did, using only measured geometry."""
    if max_closed < MINIMUM_ENGAGED_FRACTION:
        return "never_closed"
    if lift_m >= HELD_LIFT_M:
        return "grasp_held"
    if final_closed >= SELF_CLOSURE_FRACTION:
        return "closed_on_air"
    if displacement_m >= 0.02:
        return "moved_not_lifted"
    return "closed_without_effect"


def reset_scene(env, object_names):
    """Restore the robot and the objects to their spawn state.

    ``env.reset()`` alone did not restore articulation state in this Sim 6
    build: every trial after the first inherited the previous rollout's arm
    pose, so five consecutive trials reported an identical frozen distance.
    Write the default states explicitly instead.
    """
    obs, _ = env.reset()
    robot = env.scene["robot"]
    default_joint_pos = torch_view(robot.data.default_joint_pos).clone()
    default_joint_vel = torch_view(robot.data.default_joint_vel).clone()
    robot.write_joint_state_to_sim(default_joint_pos, default_joint_vel)
    robot.reset()
    for name in object_names:
        try:
            asset = env.scene[name]
        except (KeyError, TypeError):
            continue
        default_root = getattr(asset.data, "default_root_state", None)
        if default_root is None:
            continue
        state = torch_view(default_root).clone()
        state[:, :3] += env.scene.env_origins
        asset.write_root_pose_to_sim(state[:, :7])
        asset.write_root_velocity_to_sim(state[:, 7:])
        asset.reset()
    return obs


def main():
    auto_register_droid_abs_ik_envs(
        task=args_cli.task, contact_sensors=False, cameras=WRIST_LEFT_RIGHT
    )
    env_cfg = parse_env_cfg(
        args_cli.task, device="cuda:0", seed=0, num_envs=1, use_fabric=True
    )
    env_cfg.scene.robot.init_state.rot = (0.0, 0.0, 0.0, 1.0)
    fixture_rot = env_cfg.scene.table_fixture.init_state.rot
    env_cfg.scene.table_fixture.init_state.rot = (
        fixture_rot[1], fixture_rot[2], fixture_rot[3], fixture_rot[0]
    )
    for asset_name in env_cfg.contact_object_list:
        asset_cfg = getattr(env_cfg.scene, asset_name)
        w, x, y, z = asset_cfg.init_state.rot
        asset_cfg.init_state.rot = (x, y, z, w)
    convert_sim5_camera_offsets(env_cfg)
    env_cfg.actions = DroidJointPositionActionCfg()
    env_cfg.terminations = None
    env_cfg.subtasks = None
    env_cfg.recorders = None
    env, _ = create_env(env_cfg, use_fabric=True, policy="cosmos3-edge-bench")
    print("[bench] env ready; connecting to policy server", flush=True)
    client = Cosmos3EdgeChunkClient(host=args_cli.host, port=args_cli.port)
    print("[bench] policy client connected", flush=True)

    object_names = tuple(getattr(env_cfg, "contact_object_list", ()) or ())
    trials = []
    for trial in range(args_cli.trials):
        obs = reset_scene(env, object_names)
        hold = torch.zeros((1, 8), dtype=torch.float32, device=env.device)
        hold[0, :7] = torch.as_tensor(
            arm_joints(env), dtype=torch.float32, device=env.device
        )
        for _ in range(args_cli.settle_steps):
            obs, *_ = env.step(hold)

        print(f"[bench] trial {trial} reset done", flush=True)
        object_start = object_xyz_m(env, args_cli.object)
        start_distance = float(
            np.linalg.norm(pad_center_m(env) - object_start)
        )
        command = hold.clone()
        minimum_distance = start_distance
        maximum_closed = closed_fraction(env)
        terminal = False
        for chunk_index in range(args_cli.chunks):
            chunk = client.infer_chunk(
                wrist_rgb=frame(obs, "wrist_cam"),
                left_rgb=frame(obs, "over_shoulder_left_camera"),
                right_rgb=frame(obs, "over_shoulder_right_camera"),
                joint_position_rad=arm_joints(env),
                gripper_position=[closed_fraction(env)],
                prompt=args_cli.instruction,
            )
            print(f"[bench]   chunk {chunk_index} inferred in "
                  f"{chunk.inference_seconds:.1f}s", flush=True)
            for step in range(len(chunk.actions)):
                command = command.clone()
                command[0, :8] = torch.as_tensor(
                    chunk.actions[step], dtype=torch.float32, device=env.device
                )
                obs, _, terminated, truncated, _ = env.step(command)
                minimum_distance = min(
                    minimum_distance,
                    float(
                        np.linalg.norm(
                            pad_center_m(env) - object_xyz_m(env, args_cli.object)
                        )
                    ),
                )
                maximum_closed = max(maximum_closed, closed_fraction(env))
                terminal = bool(torch.as_tensor(terminated).any()) or bool(
                    torch.as_tensor(truncated).any()
                )
                if terminal:
                    break
            if terminal:
                break

        object_end = object_xyz_m(env, args_cli.object)
        final_closed = closed_fraction(env)
        lift = float(object_end[2] - object_start[2])
        displacement = float(np.linalg.norm(object_end - object_start))
        record = {
            "trial": trial,
            "outcome": classify(
                maximum_closed, final_closed, lift, displacement
            ),
            "start_pad_to_object_m": round(start_distance, 4),
            "closest_pad_to_object_m": round(minimum_distance, 4),
            "final_pad_to_object_m": round(
                float(np.linalg.norm(pad_center_m(env) - object_end)), 4
            ),
            "max_closed_fraction": round(maximum_closed, 3),
            "final_closed_fraction": round(final_closed, 3),
            "object_lift_m": round(lift, 4),
            "object_displacement_m": round(displacement, 4),
            "terminal": terminal,
        }
        trials.append(record)
        print(
            f"[bench] trial {trial:2d}: {record['outcome']:21s} "
            f"closest_pad={record['closest_pad_to_object_m']:.3f} m  "
            f"max_closed={record['max_closed_fraction']:.2f}  "
            f"lift={record['object_lift_m']:+.3f} m  "
            f"moved={record['object_displacement_m']:.3f} m",
            flush=True,
        )

    counts: dict[str, int] = {}
    for item in trials:
        counts[item["outcome"]] = counts.get(item["outcome"], 0) + 1
    closest = [item["closest_pad_to_object_m"] for item in trials]
    print(
        f"\n[bench] SUMMARY task={args_cli.task} object={args_cli.object} "
        f"instruction={args_cli.instruction!r} trials={len(trials)}"
    )
    for outcome, count in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f"  {outcome:21s} {count:2d}/{len(trials)}")
    if closest:
        print(
            f"  closest pad-to-object: min {min(closest):.3f} m  "
            f"median {sorted(closest)[len(closest) // 2]:.3f} m  "
            f"max {max(closest):.3f} m"
        )
    if args_cli.out:
        with open(args_cli.out, "w") as handle:
            json.dump(
                {
                    "task": args_cli.task,
                    "object": args_cli.object,
                    "instruction": args_cli.instruction,
                    "chunks": args_cli.chunks,
                    "trials": trials,
                    "counts": counts,
                },
                handle,
                indent=2,
            )
        print(f"  wrote {args_cli.out}")
    env.close()
    simulation_app.close()


if __name__ == "__main__":
    main()
