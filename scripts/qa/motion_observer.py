"""Continuous motion observer — logs joint positions, EE trajectory, target
drives, cube positions, and controller phase to a JSONL file. Runs
inside Kit via a physics-step subscription.

Use this to study WHY the robot tangles or misses a pick — post-session
analysis on the log tells you exactly where the trajectory diverged
from the intended target.

Usage:
    # Start logging:
    python -m scripts.qa.motion_observer --start [--path /tmp/motion.jsonl]
    # Stop logging:
    python -m scripts.qa.motion_observer --stop
    # Print summary of last log:
    python -m scripts.qa.motion_observer --summarize [--path /tmp/motion.jsonl]
"""
from __future__ import annotations
import argparse
import json
import sys
import time
from pathlib import Path

import httpx

KIT_RPC = "http://127.0.0.1:8001/exec_sync"
DEFAULT_PATH = "/tmp/motion_observer.jsonl"


def kit_exec(code: str, timeout: float = 30) -> dict:
    with httpx.Client(timeout=timeout + 10) as c:
        r = c.post(KIT_RPC, json={"code": code, "timeout": timeout})
        r.raise_for_status()
        return r.json()


START_CODE_TEMPLATE = r"""
import omni.usd, omni.physx, builtins, json, time
from pxr import UsdGeom

# Unsubscribe prior observer
_OLD = getattr(builtins, '_motion_observer_sub', None)
if _OLD is not None:
    try: _OLD.unsubscribe()
    except Exception: pass
    try: delattr(builtins, '_motion_observer_sub')
    except Exception: pass
_fh_old = getattr(builtins, '_motion_observer_fh', None)
if _fh_old is not None:
    try: _fh_old.close()
    except Exception: pass
    try: delattr(builtins, '_motion_observer_fh')
    except Exception: pass

stage = omni.usd.get_context().get_stage()
LOG_PATH = %s

JOINT_PATHS = [
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

# Cache prim handles
_joint_prims = {}
for nm, pth in JOINT_PATHS:
    p = stage.GetPrimAtPath(pth)
    if p.IsValid():
        _joint_prims[nm] = p
_cube_prims = {}
for i in range(1, 5):
    p = stage.GetPrimAtPath(f"/World/Cube_{i}")
    if p.IsValid():
        _cube_prims[f"Cube_{i}"] = p
_ee_prim = stage.GetPrimAtPath("/World/Franka/panda_hand")
_lf_prim = stage.GetPrimAtPath("/World/Franka/panda_leftfinger")
_rf_prim = stage.GetPrimAtPath("/World/Franka/panda_rightfinger")
_robot_prim = stage.GetPrimAtPath("/World/Franka")
_sensor_prim = stage.GetPrimAtPath("/World/PickSensor")
_belt_prim = stage.GetPrimAtPath("/World/ConveyorBelt")

_fh = open(LOG_PATH, "w")
setattr(builtins, '_motion_observer_fh', _fh)
_start_wall = time.time()
_tick = 0

def _w(obj):
    try:
        _fh.write(json.dumps(obj, separators=(',', ':')) + "\n")
        _fh.flush()
    except Exception: pass

def _wpos(prim):
    if not prim or not prim.IsValid(): return None
    t = UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(0).ExtractTranslation()
    return [round(float(t[0]), 4), round(float(t[1]), 4), round(float(t[2]), 4)]

def _getf(prim, attr):
    a = prim.GetAttribute(attr)
    if not a or not a.IsDefined(): return None
    v = a.Get()
    if v is None: return None
    return round(float(v), 5)

def _on_step(dt):
    global _tick
    _tick += 1
    # Sample every 3rd tick (20Hz at 60Hz physics) to keep log manageable
    if _tick %% 3 != 0: return
    try:
        rec = {"t": round(time.time() - _start_wall, 3), "tick": _tick}
        joints = {}
        for nm, p in _joint_prims.items():
            for kind in ("angular", "linear"):
                pos_a = p.GetAttribute(f"state:{kind}:physics:position")
                vel_a = p.GetAttribute(f"state:{kind}:physics:velocity")
                tgt_a = p.GetAttribute(f"drive:{kind}:physics:targetPosition")
                if pos_a and pos_a.IsDefined() and pos_a.Get() is not None:
                    joints[nm] = {
                        "pos": round(float(pos_a.Get()), 5),
                        "vel": round(float(vel_a.Get()), 5) if vel_a and vel_a.IsDefined() and vel_a.Get() is not None else None,
                        "tgt": round(float(tgt_a.Get()), 5) if tgt_a and tgt_a.IsDefined() and tgt_a.Get() is not None else None,
                    }
                    break
        rec["joints"] = joints
        rec["cubes"] = {nm: _wpos(p) for nm, p in _cube_prims.items()}
        rec["ee"] = _wpos(_ee_prim)
        rec["lf"] = _wpos(_lf_prim)
        rec["rf"] = _wpos(_rf_prim)
        if rec["lf"] and rec["rf"]:
            rec["finger_gap_mm"] = round(abs(rec["lf"][1] - rec["rf"][1]) * 1000, 2)
        if _robot_prim.IsValid():
            ctrl = {}
            for n in ("phase", "tick_count", "cubes_delivered", "error_count",
                      "last_error", "picked_path"):
                a = _robot_prim.GetAttribute(f"ctrl:{n}")
                if a and a.IsDefined():
                    v = a.Get()
                    if v is not None:
                        ctrl[n] = v
            rec["ctrl"] = ctrl
        if _sensor_prim.IsValid():
            st = _sensor_prim.GetAttribute("isaac_sensor:triggered")
            lp = _sensor_prim.GetAttribute("isaac_sensor:last_triggered_path")
            rec["sensor"] = {
                "triggered": st.Get() if st and st.IsDefined() else None,
                "last": lp.Get() if lp and lp.IsDefined() else None,
            }
        if _belt_prim.IsValid():
            sv = _belt_prim.GetAttribute("physxSurfaceVelocity:surfaceVelocity")
            if sv and sv.IsDefined() and sv.Get() is not None:
                rec["belt_vel"] = [round(float(x), 3) for x in sv.Get()]
        _w(rec)
    except Exception as e:
        _w({"err": f"{type(e).__name__}: {e}", "tick": _tick})

_physx = omni.physx.get_physx_interface()
_sub = _physx.subscribe_physics_step_events(_on_step)
setattr(builtins, '_motion_observer_sub', _sub)
print(json.dumps({"ok": True, "log_path": LOG_PATH, "sample_hz": 20}))
"""


STOP_CODE = r"""
import builtins, json
_sub = getattr(builtins, '_motion_observer_sub', None)
if _sub is not None:
    try: _sub.unsubscribe()
    except Exception: pass
    try: delattr(builtins, '_motion_observer_sub')
    except Exception: pass
_fh = getattr(builtins, '_motion_observer_fh', None)
if _fh is not None:
    try: _fh.close()
    except Exception: pass
    try: delattr(builtins, '_motion_observer_fh')
    except Exception: pass
print(json.dumps({"ok": True, "stopped": True}))
"""


def summarize(path: str) -> int:
    p = Path(path)
    if not p.exists():
        print(f"no log at {path}")
        return 1
    lines = p.read_text().splitlines()
    if not lines:
        print("log is empty")
        return 1
    parsed = []
    for ln in lines:
        try:
            parsed.append(json.loads(ln))
        except json.JSONDecodeError:
            pass
    print(f"[log] {len(parsed)} samples from {path}")
    if not parsed:
        return 1
    first, last = parsed[0], parsed[-1]
    t_range = [first.get("t"), last.get("t")]
    phases = sorted(set(r.get("ctrl", {}).get("phase") for r in parsed if r.get("ctrl")))
    errs = last.get("ctrl", {}).get("error_count", 0)
    cubes = last.get("ctrl", {}).get("cubes_delivered", 0)
    picked = [r.get("ctrl", {}).get("picked_path") for r in parsed if r.get("ctrl", {}).get("picked_path")]
    picked_unique = sorted(set(p for p in picked if p))
    # Joint range
    j_ranges = {}
    for r in parsed:
        for nm, d in (r.get("joints") or {}).items():
            pos = d.get("pos")
            if pos is None: continue
            j_ranges.setdefault(nm, []).append(pos)
    j_summary = {nm: {
        "min": round(min(vs), 4), "max": round(max(vs), 4),
        "range": round(max(vs) - min(vs), 4),
    } for nm, vs in j_ranges.items()}
    # EE range
    ee_pts = [r.get("ee") for r in parsed if r.get("ee")]
    if ee_pts:
        ee_sum = {
            "start": ee_pts[0], "end": ee_pts[-1],
            "x_range": [round(min(p[0] for p in ee_pts), 3), round(max(p[0] for p in ee_pts), 3)],
            "y_range": [round(min(p[1] for p in ee_pts), 3), round(max(p[1] for p in ee_pts), 3)],
            "z_range": [round(min(p[2] for p in ee_pts), 3), round(max(p[2] for p in ee_pts), 3)],
        }
    else:
        ee_sum = None
    finger_gaps = [r.get("finger_gap_mm") for r in parsed if r.get("finger_gap_mm") is not None]
    fg_sum = ({"min": round(min(finger_gaps), 2), "max": round(max(finger_gaps), 2)}
              if finger_gaps else None)
    # Cube trajectories
    cube_deltas = {}
    for i in range(1, 5):
        nm = f"Cube_{i}"
        pts = [r.get("cubes", {}).get(nm) for r in parsed if r.get("cubes", {}).get(nm)]
        if len(pts) < 2: continue
        total = 0.0
        for k in range(1, len(pts)):
            dx = pts[k][0] - pts[k-1][0]
            dy = pts[k][1] - pts[k-1][1]
            dz = pts[k][2] - pts[k-1][2]
            total += (dx*dx + dy*dy + dz*dz) ** 0.5
        cube_deltas[nm] = {"total_motion_m": round(total, 3),
                           "start": pts[0], "end": pts[-1]}
    summary = {
        "samples": len(parsed),
        "time_range_s": t_range,
        "phases_visited": phases,
        "final_error_count": errs,
        "final_cubes_delivered": cubes,
        "cubes_picked": picked_unique,
        "joint_ranges": j_summary,
        "ee_summary": ee_sum,
        "finger_gap_mm": fg_sum,
        "cube_motion": cube_deltas,
    }
    print(json.dumps(summary, indent=2, default=str))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", action="store_true")
    ap.add_argument("--stop", action="store_true")
    ap.add_argument("--summarize", action="store_true")
    ap.add_argument("--path", default=DEFAULT_PATH)
    args = ap.parse_args()
    if args.stop:
        r = kit_exec(STOP_CODE)
        print(r.get("output", "").strip())
        return 0
    if args.summarize:
        return summarize(args.path)
    if args.start:
        code = START_CODE_TEMPLATE % (repr(args.path),)
        r = kit_exec(code)
        print(r.get("output", "").strip())
        return 0
    ap.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
