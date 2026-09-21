"""Cosmos 3 Edge action-chunk transport for composed-execution executors.

Speaks the OpenPI msgpack-over-websocket protocol served by
``cosmos_framework.scripts.action_policy_server_robolab`` and packs
observations exactly the way RoboLab's ``Cosmos3Client`` does, so a policy
checkpoint evaluated under RoboLab behaves identically when invoked as a
composed-execution motion executor:

- one composite uint8 frame: the wrist view resized to 360x640 on top, the
  left and right over-shoulder views half-scaled to 180x320 side by side
  below (total 540x640x3);
- ``observation/joint_position`` (7,) float32 arm joints;
- ``observation/gripper_position`` (1,) float32;
- ``prompt`` language instruction.

The response is a ``(32, 8)`` float32 action chunk of absolute joint
positions plus a gripper command in the last column, which is binarized at
0.5 to match RoboLab's client-side postprocessing.

``openpi_client`` is not assumed to be installed: when the import fails the
module falls back to the vendored checkout under ``lehome_solution`` (or the
``COSMOS3_OPENPI_CLIENT_SRC`` override) and fails loudly when neither is
usable.
"""

from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass
from typing import Any

import numpy as np

_VENDORED_OPENPI_CLIENT_SRC = (
    "/home/kimate/lehome_solution/openpi/packages/openpi-client/src"
)

COMPOSITE_HEIGHT = 540
COMPOSITE_WIDTH = 640
VIEW_HEIGHT = 360
VIEW_WIDTH = 640
ACTION_HORIZON = 32
ACTION_DIM = 8
ARM_JOINT_COUNT = 7


class Cosmos3EdgeClientError(RuntimeError):
    """Raised when the transport or the response cannot be trusted."""


def _ensure_openpi_client() -> Any:
    """Import openpi_client, falling back to the vendored source tree."""
    try:
        import openpi_client  # noqa: F401
    except ModuleNotFoundError:
        vendored = os.environ.get(
            "COSMOS3_OPENPI_CLIENT_SRC", _VENDORED_OPENPI_CLIENT_SRC
        )
        if not os.path.isdir(vendored):
            raise Cosmos3EdgeClientError(
                "openpi_client is not installed and the vendored source tree "
                f"{vendored!r} does not exist; set COSMOS3_OPENPI_CLIENT_SRC"
            ) from None
        if vendored not in sys.path:
            sys.path.insert(0, vendored)
        try:
            import openpi_client  # noqa: F401
        except ModuleNotFoundError as error:
            raise Cosmos3EdgeClientError(
                "openpi_client (or one of its dependencies: numpy, PIL, "
                f"msgpack, websockets) failed to import from {vendored!r}: "
                f"{error}"
            ) from error
    from openpi_client import image_tools, websocket_client_policy

    return image_tools, websocket_client_policy


def _as_uint8_hwc(image: Any, name: str) -> np.ndarray:
    array = np.asarray(image)
    if array.ndim != 3 or array.shape[-1] != 3:
        raise Cosmos3EdgeClientError(
            f"{name} must be an HWC RGB image, got shape {array.shape}"
        )
    if np.issubdtype(array.dtype, np.floating):
        array = (np.clip(array, 0.0, 1.0) * 255).astype(np.uint8)
    return array.astype(np.uint8, copy=False)


def pack_composite_frame(
    wrist_rgb: Any, left_rgb: Any, right_rgb: Any
) -> np.ndarray:
    """Compose the three views into the 540x640 frame the server expects."""
    image_tools, _ = _ensure_openpi_client()
    wrist = image_tools.resize_with_pad(
        _as_uint8_hwc(wrist_rgb, "wrist_rgb"), VIEW_HEIGHT, VIEW_WIDTH
    )
    half_h, half_w = VIEW_HEIGHT // 2, VIEW_WIDTH // 2
    left = image_tools.resize_with_pad(
        image_tools.resize_with_pad(
            _as_uint8_hwc(left_rgb, "left_rgb"), VIEW_HEIGHT, VIEW_WIDTH
        ),
        half_h,
        half_w,
    )
    right = image_tools.resize_with_pad(
        image_tools.resize_with_pad(
            _as_uint8_hwc(right_rgb, "right_rgb"), VIEW_HEIGHT, VIEW_WIDTH
        ),
        half_h,
        half_w,
    )
    composite = np.concatenate(
        (wrist, np.concatenate((left, right), axis=1))
    )
    if composite.shape != (COMPOSITE_HEIGHT, COMPOSITE_WIDTH, 3):
        raise Cosmos3EdgeClientError(
            f"composite frame has shape {composite.shape}, expected "
            f"({COMPOSITE_HEIGHT}, {COMPOSITE_WIDTH}, 3)"
        )
    return composite


@dataclass(frozen=True)
class Cosmos3EdgeChunk:
    """One validated action chunk plus transport telemetry."""

    actions: np.ndarray
    inference_seconds: float

    @property
    def arm_joint_targets(self) -> np.ndarray:
        return self.actions[:, :ARM_JOINT_COUNT]

    @property
    def gripper_commands(self) -> np.ndarray:
        return self.actions[:, ARM_JOINT_COUNT]


class Cosmos3EdgeChunkClient:
    """Blocking chunk client with the same reconnect semantics as RoboLab's."""

    def __init__(
        self,
        host: str = "localhost",
        port: int = 8000,
        connect_retries: int = 3,
    ) -> None:
        self._host = host
        self._port = port
        self._connect_retries = max(1, int(connect_retries))
        _, websocket_client_policy = _ensure_openpi_client()
        self._policy_module = websocket_client_policy
        self._client = websocket_client_policy.WebsocketClientPolicy(host, port)

    def _reconnect(self) -> None:
        self._client = self._policy_module.WebsocketClientPolicy(
            self._host, self._port
        )

    def infer_chunk(
        self,
        wrist_rgb: Any,
        left_rgb: Any,
        right_rgb: Any,
        joint_position_rad: Any,
        gripper_position: Any,
        prompt: str,
    ) -> Cosmos3EdgeChunk:
        joints = np.asarray(joint_position_rad, dtype=np.float32).reshape(-1)
        if joints.shape != (ARM_JOINT_COUNT,):
            raise Cosmos3EdgeClientError(
                f"joint_position_rad must have {ARM_JOINT_COUNT} entries, "
                f"got shape {joints.shape}"
            )
        gripper = np.asarray(gripper_position, dtype=np.float32).reshape(-1)[:1]
        if not prompt or not isinstance(prompt, str):
            raise Cosmos3EdgeClientError("prompt must be a non-empty string")
        request = {
            "observation/image": pack_composite_frame(
                wrist_rgb, left_rgb, right_rgb
            ),
            "observation/joint_position": joints,
            "observation/gripper_position": gripper,
            "prompt": prompt,
        }
        import websockets.exceptions

        last_error: Exception | None = None
        for attempt in range(self._connect_retries):
            started = time.monotonic()
            try:
                response = self._client.infer(request)
                break
            except (
                websockets.exceptions.ConnectionClosedError,
                websockets.exceptions.ConnectionClosedOK,
                OSError,
            ) as error:
                last_error = error
                if attempt + 1 >= self._connect_retries:
                    raise Cosmos3EdgeClientError(
                        f"policy server {self._host}:{self._port} unreachable "
                        f"after {self._connect_retries} attempts: {error}"
                    ) from error
                self._reconnect()
        else:  # pragma: no cover - loop always breaks or raises
            raise Cosmos3EdgeClientError(str(last_error))
        elapsed = time.monotonic() - started
        actions = np.asarray(response.get("action"), dtype=np.float32)
        if actions.shape != (ACTION_HORIZON, ACTION_DIM):
            raise Cosmos3EdgeClientError(
                f"server returned action shape {actions.shape}, expected "
                f"({ACTION_HORIZON}, {ACTION_DIM})"
            )
        if not np.isfinite(actions).all():
            raise Cosmos3EdgeClientError("server returned non-finite actions")
        actions = actions.copy()
        actions[:, -1] = (actions[:, -1] > 0.5).astype(actions.dtype)
        return Cosmos3EdgeChunk(actions=actions, inference_seconds=elapsed)


def _smoke() -> None:
    rng = np.random.default_rng(0)
    client = Cosmos3EdgeChunkClient()
    chunk = client.infer_chunk(
        wrist_rgb=rng.uniform(60, 200, (360, 640, 3)).astype(np.uint8),
        left_rgb=rng.uniform(60, 200, (360, 640, 3)).astype(np.uint8),
        right_rgb=rng.uniform(60, 200, (360, 640, 3)).astype(np.uint8),
        joint_position_rad=[0.0, -0.2, 0.0, -2.0, 0.0, 1.8, 0.8],
        gripper_position=[0.0],
        prompt="pick up the banana and put it on the plate",
    )
    print(
        f"chunk OK: {chunk.actions.shape} in {chunk.inference_seconds:.2f}s, "
        f"arm range [{chunk.arm_joint_targets.min():+.3f}, "
        f"{chunk.arm_joint_targets.max():+.3f}], "
        f"gripper values {sorted(set(chunk.gripper_commands.tolist()))}"
    )


if __name__ == "__main__":
    _smoke()
