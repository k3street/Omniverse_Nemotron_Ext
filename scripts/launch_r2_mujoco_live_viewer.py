"""Interactive real-time MuJoCo 3.11 GUI viewer continuously looping the CAD Creator R2
3-Finger Tactile Gripper closed-loop grasp, aerial lift, transport, and chamfered dock insertion.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import time

import mujoco
import mujoco.viewer

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MJCF = (
    REPO_ROOT
    / "workspace"
    / "r2_hybrid_showcase"
    / "02_mujoco_tactile_sim"
    / "r2_hybrid_mujoco_scene.xml"
)


def smoothstep(t: float, t0: float, t1: float) -> float:
    if t <= t0:
        return 0.0
    if t >= t1:
        return 1.0
    u = (t - t0) / (t1 - t0)
    return u * u * (3.0 - 2.0 * u)


def lerp(a: float, b: float, u: float) -> float:
    return a + (b - a) * u


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mjcf", type=Path, default=DEFAULT_MJCF)
    args = parser.parse_args()

    model = mujoco.MjModel.from_xml_path(str(args.mjcf.resolve()))
    data = mujoco.MjData(model)

    act_ids = {
        name: mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_ACTUATOR, name)
        for name in (
            "act_wrist_x",
            "act_wrist_z",
            "act_spread",
            "act_thumb_base",
            "act_thumb_knuckle",
            "act_fa_base",
            "act_fa_knuckle",
            "act_fb_base",
            "act_fb_knuckle",
        )
    }

    open_base, open_knuckle = -0.26, 0.04
    close_base_thumb, close_knuckle_thumb = 0.28, 0.48
    close_base_pair, close_knuckle_pair = 0.26, 0.42

    dt = float(model.opt.timestep)
    cycle_duration = 5.4

    with mujoco.viewer.launch_passive(model, data) as viewer:
        viewer.cam.lookat[:] = [0.070, 0.0, 0.105]
        viewer.cam.distance = 0.56
        viewer.cam.azimuth = 132.0
        viewer.cam.elevation = -24.0

        mujoco.mj_resetData(model, data)
        mujoco.mj_forward(model, data)
        cycle_t = 0.0

        while viewer.is_running():
            step_start = time.perf_counter()

            # Advance 8 physics substeps per viewer sync (~60 Hz render sync at dt=0.002s)
            for _ in range(8):
                t = cycle_t
                if t < 0.7:
                    wx, wz = 0.0, lerp(0.0, -0.042, smoothstep(t, 0.05, 0.65))
                    tb, tk, pb, pk = open_base, open_knuckle, open_base, open_knuckle
                elif t < 1.5:
                    wx, wz = 0.0, -0.042
                    u = smoothstep(t, 0.75, 1.35)
                    tb, tk = lerp(open_base, close_base_thumb, u), lerp(open_knuckle, close_knuckle_thumb, u)
                    pb, pk = lerp(open_base, close_base_pair, u), lerp(open_knuckle, close_knuckle_pair, u)
                elif t < 2.3:
                    wx, wz = 0.0, lerp(-0.042, 0.028, smoothstep(t, 1.55, 2.20))
                    tb, tk, pb, pk = close_base_thumb, close_knuckle_thumb, close_base_pair, close_knuckle_pair
                elif t < 3.3:
                    wx, wz = lerp(0.0, 0.140, smoothstep(t, 2.35, 3.20)), 0.028
                    tb, tk, pb, pk = close_base_thumb, close_knuckle_thumb, close_base_pair, close_knuckle_pair
                elif t < 4.0:
                    wx, wz = 0.140, lerp(0.028, -0.036, smoothstep(t, 3.35, 3.92))
                    tb, tk, pb, pk = close_base_thumb, close_knuckle_thumb, close_base_pair, close_knuckle_pair
                elif t < 4.4:
                    wx, wz = 0.140, -0.036
                    u = smoothstep(t, 4.02, 4.32)
                    tb, tk = lerp(close_base_thumb, open_base, u), lerp(close_knuckle_thumb, open_knuckle, u)
                    pb, pk = lerp(close_base_pair, open_base, u), lerp(close_knuckle_pair, open_knuckle, u)
                else:
                    wx, wz = 0.140, lerp(-0.036, 0.022, smoothstep(t, 4.42, 4.85))
                    tb, tk, pb, pk = open_base, open_knuckle, open_base, open_knuckle

                data.ctrl[act_ids["act_wrist_x"]] = wx
                data.ctrl[act_ids["act_wrist_z"]] = wz
                data.ctrl[act_ids["act_spread"]] = 0.0
                data.ctrl[act_ids["act_thumb_base"]] = tb
                data.ctrl[act_ids["act_thumb_knuckle"]] = tk
                data.ctrl[act_ids["act_fa_base"]] = pb
                data.ctrl[act_ids["act_fa_knuckle"]] = pk
                data.ctrl[act_ids["act_fb_base"]] = pb
                data.ctrl[act_ids["act_fb_knuckle"]] = pk

                mujoco.mj_step(model, data)
                cycle_t += dt
                if cycle_t >= cycle_duration:
                    mujoco.mj_resetData(model, data)
                    mujoco.mj_forward(model, data)
                    cycle_t = 0.0

            viewer.sync()
            sleep_s = (8 * dt) - (time.perf_counter() - step_start)
            if sleep_s > 0:
                time.sleep(sleep_s)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
