"""Score a policy server's predicted action chunks against oracle ground truth.

The closed-loop grasp benchmark needs Isaac and depends on the environment
restoring the arm between trials, which it does not reliably do. This harness
measures the policy directly instead: it replays recorded observations from a
LeRobot episode, asks a running policy server for its action chunk at each
sampled frame, and compares that chunk to what the oracle actually did.

Point it at an episode the policy was not trained on -- ``DROIDLeRobotDataset``
holds one out as its val split -- and the number is a generalisation measure
rather than a memorisation one.

Arm joints and the gripper are reported separately and never averaged together:
the joints are radians and the gripper is a 0/1 closure, so a single combined
figure hides which one regressed. That separation is what showed a
post-trained checkpoint cutting joint error while emitting a constant gripper.

Run against a server started with ``action_policy_server_robolab``::

    python scripts/eval_action_chunk_prediction.py \
        --root <lerobot v3.0 root> --episode 32 --port 8000 \
        --out artifacts/eval.json
"""
import argparse
import glob
import json
import sys
from pathlib import Path

import cv2
import numpy as np
import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cosmos3_edge_client import Cosmos3EdgeChunkClient  # noqa: E402

# Sub-ranges of the packed 17-D vectors written by
# convert_robolab_demo_to_lerobot_v3.py.
JOINT_SLICE = slice(10, 17)
GRIPPER_INDEX = 9
WRIST_KEY = "observation.images.wrist_image_left"
EXTERIOR_KEY = "observation.images.exterior_image_1_left"


def _episode_file(root: str, pattern: str, episode: int) -> str:
    # The exporter writes one episode per file, so file order is episode order.
    return sorted(glob.glob(f"{root}/{pattern}", recursive=True))[episode]


def read_frames(root: str, video_key: str, episode: int) -> list[np.ndarray]:
    capture = cv2.VideoCapture(
        _episode_file(root, f"videos/{video_key}/**/*.mp4", episode)
    )
    frames = []
    while True:
        ok, frame = capture.read()
        if not ok:
            break
        frames.append(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
    capture.release()
    return frames


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, help="LeRobot v3.0 dataset root")
    parser.add_argument("--episode", type=int, required=True)
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--instruction", default="pick up the banana")
    parser.add_argument("--frames", type=int, default=8)
    parser.add_argument("--horizon", type=int, default=32)
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    table = pq.read_table(_episode_file(args.root, "data/**/*.parquet", args.episode))
    action = np.array(table["action"].to_pylist(), dtype=np.float32)
    state = np.array(table["observation.state"].to_pylist(), dtype=np.float32)
    wrist = read_frames(args.root, WRIST_KEY, args.episode)
    exterior = read_frames(args.root, EXTERIOR_KEY, args.episode)

    length = min(len(wrist), len(exterior), len(action))
    print(f"episode {args.episode}: {length} frames", flush=True)

    client = Cosmos3EdgeChunkClient(host=args.host, port=args.port)
    horizon = args.horizon
    starts = np.linspace(0, max(length - horizon - 1, 1), args.frames).astype(int)

    rows = []
    for start in starts:
        chunk = client.infer_chunk(
            wrist_rgb=wrist[start],
            # A two-camera recording has no right over-shoulder view; the wrist
            # viewpoint does not read it, so reuse the exterior frame.
            left_rgb=exterior[start],
            right_rgb=exterior[start],
            joint_position_rad=state[start, JOINT_SLICE].astype(np.float64),
            gripper_position=float(state[start, GRIPPER_INDEX]),
            prompt=args.instruction,
        )
        predicted = np.asarray(chunk.actions, dtype=np.float32)[:horizon]
        truth = np.concatenate(
            [
                action[start : start + horizon, JOINT_SLICE],
                action[start : start + horizon, GRIPPER_INDEX][:, None],
            ],
            axis=-1,
        )
        overlap = min(len(predicted), len(truth))
        joint_mae = float(np.abs(predicted[:overlap, :7] - truth[:overlap, :7]).mean())
        gripper_mae = float(np.abs(predicted[:overlap, 7] - truth[:overlap, 7]).mean())
        # A constant gripper scores well whenever the truth is constant too, so
        # report its spread: zero spread means the policy never actuates it.
        gripper_spread = float(predicted[:overlap, 7].max() - predicted[:overlap, 7].min())
        rows.append(
            {
                "start": int(start),
                "joint_mae_rad": joint_mae,
                "gripper_mae": gripper_mae,
                "gripper_spread": gripper_spread,
            }
        )
        print(
            f"  t={start:4d}  joint MAE {joint_mae:.4f} rad   "
            f"gripper MAE {gripper_mae:.4f}   gripper spread {gripper_spread:.3f}",
            flush=True,
        )

    summary = {
        "episode": args.episode,
        "frames": len(rows),
        "joint_mae_rad": float(np.mean([r["joint_mae_rad"] for r in rows])),
        "gripper_mae": float(np.mean([r["gripper_mae"] for r in rows])),
        "gripper_spread": float(np.mean([r["gripper_spread"] for r in rows])),
        "per_frame": rows,
    }
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(summary, indent=2) + "\n")
    print(
        "MEAN joint MAE {joint_mae_rad:.4f} rad | gripper MAE {gripper_mae:.4f} "
        "| gripper spread {gripper_spread:.3f}".format(**summary)
    )


if __name__ == "__main__":
    main()
