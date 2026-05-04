"""Time-series scene diagnosis — reads DIRECT USD physics state (not
SingleArticulation cache), samples multiple points to detect motion vs
stuck, reports controller phase/errors from ctrl:* attrs written by
the instrumented controller.

Usage:
    python scripts/qa/diagnose_scene.py [--duration SEC] [--samples N]

Default: 3 samples over 2s. Prints JSON with:
- per-sample: joints (from state:*:physics:position), cubes, ee, belt
- deltas between samples (what's moving)
- controller state (phase, tick_count, errors) from ctrl:*
- motion verdict per prim: "moving" | "stuck" | "oscillating"
"""
from __future__ import annotations
import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))

from service.isaac_assist_service.chat.tools import kit_tools  # noqa: E402


READ_SCRIPT = """
import omni.usd, omni.timeline, json
from pxr import UsdGeom

stage = omni.usd.get_context().get_stage()
tl = omni.timeline.get_timeline_interface()

out = {
    "t_sim": round(tl.get_current_time(), 3),
    "playing": tl.is_playing(),
}

# Controller state from ctrl:* attrs (written by instrumented controller)
robot = stage.GetPrimAtPath("/World/Franka")
ctrl = {}
for a_name in ["phase", "phase_duration", "target_name", "target_distance",
               "last_error", "error_count", "tick_count", "cubes_delivered",
               "belt_paused", "grip_cmd", "picked_path"]:
    attr = robot.GetAttribute(f"ctrl:{a_name}") if robot.IsValid() else None
    if attr and attr.IsDefined():
        v = attr.Get()
        if v is not None:
            ctrl[a_name] = (list(v) if hasattr(v, "__len__") and not isinstance(v, str) else v)
# Also read Float3 target_pos / ee_pos
for v_name in ["target_pos", "ee_pos"]:
    attr = robot.GetAttribute(f"ctrl:{v_name}") if robot.IsValid() else None
    if attr and attr.IsDefined():
        v = attr.Get()
        if v is not None:
            ctrl[v_name] = [round(float(x), 4) for x in v]
out["ctrl"] = ctrl

# Joint positions + velocities DIRECT from USD (NOT cached SingleArticulation)
joints = {}
joint_paths = [
    ("panda_joint1", "/World/Franka/panda_link0/panda_joint1"),
    ("panda_joint2", "/World/Franka/panda_link1/panda_joint2"),
    ("panda_joint3", "/World/Franka/panda_link2/panda_joint3"),
    ("panda_joint4", "/World/Franka/panda_link3/panda_joint4"),
    ("panda_joint5", "/World/Franka/panda_link4/panda_joint5"),
    ("panda_joint6", "/World/Franka/panda_link5/panda_joint6"),
    ("panda_joint7", "/World/Franka/panda_link6/panda_joint7"),
    ("panda_finger_joint1", "/World/Franka/panda_hand/panda_finger_joint1"),
    ("panda_finger_joint2", "/World/Franka/panda_hand/panda_finger_joint2"),
]
for name, path in joint_paths:
    p = stage.GetPrimAtPath(path)
    if not p.IsValid():
        continue
    # Try angular first (revolute joints), then linear (prismatic)
    for kind in ("angular", "linear"):
        pos_a = p.GetAttribute(f"state:{kind}:physics:position")
        vel_a = p.GetAttribute(f"state:{kind}:physics:velocity")
        if pos_a and pos_a.IsDefined() and pos_a.Get() is not None:
            joints[name] = {
                "pos": round(float(pos_a.Get()), 5),
                "vel": round(float(vel_a.Get()), 5) if vel_a and vel_a.IsDefined() and vel_a.Get() is not None else None,
                "kind": kind,
            }
            break
out["joints"] = joints

# Cube world positions
cubes = {}
for i in range(1, 5):
    p = stage.GetPrimAtPath(f"/World/Cube_{i}")
    if p.IsValid():
        t = UsdGeom.Xformable(p).ComputeLocalToWorldTransform(0).ExtractTranslation()
        cubes[f"Cube_{i}"] = [round(float(v), 3) for v in t]
out["cubes"] = cubes

# EE world pos (panda_hand)
ee = stage.GetPrimAtPath("/World/Franka/panda_hand")
if ee.IsValid():
    t = UsdGeom.Xformable(ee).ComputeLocalToWorldTransform(0).ExtractTranslation()
    out["ee_world"] = [round(float(v), 3) for v in t]

# Belt surface velocity (live read)
belt = stage.GetPrimAtPath("/World/ConveyorBelt")
if belt.IsValid():
    v = belt.GetAttribute("physxSurfaceVelocity:surfaceVelocity").Get()
    out["belt_vel"] = [round(float(x), 3) for x in v] if v else None

# Sensor trigger (live from USD)
sensor = stage.GetPrimAtPath("/World/PickSensor")
if sensor.IsValid():
    trig = sensor.GetAttribute("isaac_sensor:triggered").Get()
    last_p = sensor.GetAttribute("isaac_sensor:last_triggered_path").Get()
    out["sensor"] = {"triggered": trig, "last_path": last_p}

# Finger separation (physical reality check)
lf = stage.GetPrimAtPath("/World/Franka/panda_leftfinger")
rf = stage.GetPrimAtPath("/World/Franka/panda_rightfinger")
if lf.IsValid() and rf.IsValid():
    lt = UsdGeom.Xformable(lf).ComputeLocalToWorldTransform(0).ExtractTranslation()
    rt = UsdGeom.Xformable(rf).ComputeLocalToWorldTransform(0).ExtractTranslation()
    out["finger_gap_mm"] = round(abs(float(lt[1]) - float(rt[1])) * 1000, 2)

print(json.dumps(out))
"""


def verdict_for_series(values, epsilon):
    """Classify a scalar/vector time series."""
    if not values or any(v is None for v in values):
        return "N/A"
    if all(isinstance(v, (list, tuple)) for v in values):
        # Vector: compute pairwise L2 deltas
        import math
        deltas = []
        for i in range(1, len(values)):
            d = math.sqrt(sum((values[i][j] - values[i-1][j])**2 for j in range(len(values[0]))))
            deltas.append(d)
        max_d = max(deltas) if deltas else 0
        if max_d < epsilon:
            return "stuck"
        # Check oscillation: alternating signs in first dimension
        if len(values) >= 3:
            diffs0 = [values[i+1][0] - values[i][0] for i in range(len(values)-1)]
            if all(d == 0 for d in diffs0):
                return "stuck"
        return f"moving (max_delta={max_d:.4f})"
    else:
        # Scalar
        deltas = [values[i+1] - values[i] for i in range(len(values)-1)]
        max_d = max(abs(d) for d in deltas) if deltas else 0
        if max_d < epsilon:
            return "stuck"
        return f"moving (max_delta={max_d:.4f})"


async def sample(n_samples: int, interval: float):
    samples = []
    for i in range(n_samples):
        r = await kit_tools.exec_sync(READ_SCRIPT, timeout=10)
        out = (r.get("output") or "").strip()
        for line in reversed(out.split("\n")):
            line = line.strip()
            if line.startswith("{"):
                try:
                    samples.append(json.loads(line))
                    break
                except json.JSONDecodeError:
                    pass
        if i < n_samples - 1:
            await asyncio.sleep(interval)
    return samples


def analyze(samples):
    if len(samples) < 2:
        return {"error": "need ≥2 samples"}
    # Joints motion
    joint_names = list(samples[0].get("joints", {}).keys())
    joint_verdicts = {}
    for jn in joint_names:
        pos_series = [s["joints"].get(jn, {}).get("pos") for s in samples]
        vel_series = [s["joints"].get(jn, {}).get("vel") for s in samples]
        joint_verdicts[jn] = {
            "pos_series": pos_series,
            "vel_avg": round(sum(abs(v) for v in vel_series if v is not None) / max(1, len(vel_series)), 5) if any(v is not None for v in vel_series) else None,
            "verdict": verdict_for_series(pos_series, epsilon=0.001),
        }

    # Cube motion
    cube_verdicts = {}
    for cname in samples[0].get("cubes", {}).keys():
        pos_series = [s["cubes"].get(cname) for s in samples]
        cube_verdicts[cname] = verdict_for_series(pos_series, epsilon=0.002)

    # EE
    ee_series = [s.get("ee_world") for s in samples]
    ee_verdict = verdict_for_series(ee_series, epsilon=0.002)

    # Controller state over time
    ctrl_series = [s.get("ctrl", {}) for s in samples]
    phase_series = [c.get("phase") for c in ctrl_series]
    tick_series = [c.get("tick_count") for c in ctrl_series]
    phase_changed = len(set(phase_series)) > 1
    ticks_advanced = any(tick_series[i+1] > tick_series[i] for i in range(len(tick_series)-1) if tick_series[i] is not None and tick_series[i+1] is not None)

    # Sim time advance
    t_series = [s.get("t_sim", 0) for s in samples]
    sim_advancing = t_series[-1] > t_series[0] + 0.1

    return {
        "sim_time_range": [t_series[0], t_series[-1]],
        "sim_advancing": sim_advancing,
        "ticks_advancing": ticks_advanced,
        "phases_visited": phase_series,
        "phase_changed": phase_changed,
        "controller_error_count": ctrl_series[-1].get("error_count"),
        "controller_last_error": ctrl_series[-1].get("last_error"),
        "ee_verdict": ee_verdict,
        "cube_verdicts": cube_verdicts,
        "joint_verdicts": joint_verdicts,
        "finger_gap_mm_series": [s.get("finger_gap_mm") for s in samples],
        "sensor_trig_series": [s.get("sensor", {}).get("triggered") for s in samples],
        "belt_vel_series": [s.get("belt_vel") for s in samples],
    }


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--duration", type=float, default=2.0)
    ap.add_argument("--samples", type=int, default=3)
    args = ap.parse_args()
    interval = args.duration / max(1, args.samples - 1)
    print(f"[diagnose] {args.samples} samples, {interval:.2f}s interval, {args.duration:.1f}s total")
    samples = await sample(args.samples, interval)
    print(f"\n[samples] {len(samples)} collected")
    for i, s in enumerate(samples):
        print(f"  [{i}] t_sim={s.get('t_sim')} phase={s.get('ctrl',{}).get('phase')} tick={s.get('ctrl',{}).get('tick_count')} errs={s.get('ctrl',{}).get('error_count')}")
    verdict = analyze(samples)
    print("\n[analysis]")
    print(json.dumps(verdict, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
