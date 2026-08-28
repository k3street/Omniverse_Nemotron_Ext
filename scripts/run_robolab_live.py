"""Run RoboLab with sensor-aware GR00T observations and no OpenCV preview.

The local runner deliberately owns task registration so force/contact terms can
be installed after Isaac AppLauncher starts but before RoboLab builds the scene.
Base DROID checkpoints ignore the additive state keys; sensor-aware checkpoints
consume them through the same GR00T sim-policy wrapper.
"""
from __future__ import annotations

import argparse
import os
import sys
import traceback
from pathlib import Path

import cv2  # Must precede Isaac Lab imports.


root = Path(
    os.environ.get("ROBOLAB_ROOT", "/home/kimate/Documents/Github/RoboLab")
).expanduser().resolve()
if not (root / "robolab").is_dir():
    raise FileNotFoundError(f"RoboLab checkout not found: {root}")
sys.path.insert(0, str(root))

from isaaclab.app import AppLauncher


parser = argparse.ArgumentParser(description="Evaluate sensor-aware GR00T in RoboLab.")
parser.add_argument(
    "--remote-host",
    "--remote_host",
    type=str,
    default="localhost",
    help="Remote GR00T server host.",
)
parser.add_argument(
    "--remote-port",
    "--remote_port",
    type=int,
    default=5555,
    help="Remote GR00T server port.",
)
parser.add_argument(
    "--open-loop-horizon",
    "--open_loop_horizon",
    type=int,
    default=None,
    help="Actions consumed from each predicted chunk before replanning.",
)
parser.add_argument("--enable-verbose", "--enable_verbose", action="store_true")
parser.add_argument("--enable-debug", "--enable_debug", action="store_true")
parser.add_argument("--record-image-data", "--record_image_data", action="store_true")
parser.add_argument("--randomize-background", "--randomize_background", action="store_true")
parser.add_argument("--background-seed", "--background_seed", type=int, default=None)

from robolab.eval.runner import add_common_eval_args

add_common_eval_args(parser)
AppLauncher.add_app_launcher_args(parser)
args_cli, _ = parser.parse_known_args()
args_cli.enable_cameras = True

app_launcher = AppLauncher(args_cli)
simulation_app = app_launcher.app

import robolab.constants
from policies.gr00t.client import GR00TDroidJointposClient
from robolab.eval.runner import run_evaluation
from robolab.registrations.droid.auto_env_registrations_jointpos import (
    auto_register_droid_envs,
)
from robolab_groot_sensor_bridge import (
    install_sensor_observations,
    make_sensor_aware_client,
)


# RoboLab's OpenCV build is headless. Isaac remains graphical when --headless
# is absent; only the redundant cv2 preview window is disabled.
cv2.imshow = lambda *_args, **_kwargs: None
cv2.waitKey = lambda *_args, **_kwargs: -1
cv2.destroyAllWindows = lambda: None

install_sensor_observations()
SensorAwareGR00TClient = make_sensor_aware_client(GR00TDroidJointposClient)

robolab.constants.ENABLE_SUBTASK_PROGRESS_CHECKING = args_cli.enable_subtask
robolab.constants.RECORD_IMAGE_DATA = args_cli.record_image_data
robolab.constants.VERBOSE = args_cli.enable_verbose
robolab.constants.DEBUG = args_cli.enable_debug

auto_register_droid_envs(
    task_dirs=args_cli.task_dirs,
    task=args_cli.task,
    randomize_background=args_cli.randomize_background,
    background_seed=args_cli.background_seed,
)


def make_client(args: argparse.Namespace):
    kwargs = {
        "remote_host": args.remote_host,
        "remote_port": args.remote_port,
        "open_loop_horizon": args.open_loop_horizon,
    }
    return SensorAwareGR00TClient(
        **{key: value for key, value in kwargs.items() if value is not None}
    )


def main() -> None:
    run_evaluation(args_cli, policy="gr00t", client_factory=make_client)
    simulation_app.close()


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"\033[96m[RoboLab] Terminated with error: {error}\033[0m")
        traceback.print_exc()
        simulation_app.close()
        sys.exit(1)
