"""probe_ctrl_telemetry.py — runtime diagnostic for Phase 2d controller-bug fixes.

Reads ctrl:* attributes during a sim run to tell WHERE a controller gets stuck.
Distinct from diagnose_scene_feasibility (install-time geometric) — this is
RUNTIME state inspection.

Counters tracked (per the controller-runtime in tool_executor.py):
  ctrl:phase            — current phase ("wait_sensor", "approach_pick", "grip", ...)
  ctrl:phase_duration   — how long current phase has been active
  ctrl:cubes_delivered  — how many cubes successfully delivered
  ctrl:cycles_attempted — how many full cycles started
  ctrl:tick_count       — total ticks
  ctrl:plan_calls       — cuRobo plan_pose / plan_pose_batch invocations
  ctrl:plan_fails       — plan_pose returned None / fail
  ctrl:last_fail_goal   — last goal pose that failed planning
  ctrl:last_error       — last error string

Sampling: builds the canonical, plays the timeline, samples ctrl:* attrs
every 1s for the requested duration. Outputs:
  - Phase histogram (% time spent in each phase)
  - Stuck-phase detection (any phase > 30% of total wall-time)
  - Plan-call success rate
  - Final state summary

Usage:
  python scripts/qa/probe_ctrl_telemetry.py CP-37
  python scripts/qa/probe_ctrl_telemetry.py CP-65 --duration 60
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))


_PROBE_CODE = r"""
import omni.usd, omni.timeline, omni.kit.app, json, time as _t
from pxr import UsdGeom, Usd
stage = omni.usd.get_context().get_stage()
tl = omni.timeline.get_timeline_interface()
app = omni.kit.app.get_app()

duration_s = {duration_s}
sample_dt_s = {sample_dt_s}
seed_cube_paths = {seed_cube_paths_json}  # from template.simulate_args
target_attrs = ["ctrl:phase", "ctrl:phase_duration", "ctrl:cubes_delivered",
                "ctrl:cycles_attempted", "ctrl:tick_count",
                "ctrl:plan_calls", "ctrl:plan_fails", "ctrl:last_fail_goal",
                "ctrl:last_error",
                "builtin_pp:phase", "builtin_pp:tick_count", "builtin_pp:cubes_delivered"]

def _wp(path):
    p = stage.GetPrimAtPath(path)
    if not p or not p.IsValid(): return None
    fresh = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.default_])
    b = fresh.ComputeWorldBound(p).ComputeAlignedRange()
    if b.IsEmpty(): return None
    c = b.GetMidpoint()
    return [round(float(c[0]), 3), round(float(c[1]), 3), round(float(c[2]), 3)]

def _find_robots():
    out = []
    for prim in stage.Traverse():
        for a in prim.GetAttributes():
            if a.GetName() in target_attrs:
                out.append(prim)
                break
    return out

def _find_cubes():
    out = []
    seen = set()
    # Seed with explicit paths from simulate_args (e.g. /World/CubeHeap/Item_1, /World/Peg_3).
    # Probe these even if they don't exist (will just have no positions in trajectory).
    for sp in seed_cube_paths:
        if sp and sp not in seen:
            p = stage.GetPrimAtPath(sp)
            if p and p.IsValid():
                out.append(sp); seen.add(sp)
    # Then heuristic prefix scan for any other cube_* (multi-cube canonicals).
    for prim in stage.Traverse():
        n = str(prim.GetPath())
        if n in seen: continue
        tail = n.rsplit("/", 1)[-1].lower()
        if tail == "cube" or tail.startswith("cube_") or tail.startswith("cube "):
            out.append(n); seen.add(n)
    return out[:8]  # cap at 8 cubes

robots = _find_robots()
cube_paths = _find_cubes()
if not robots:
    print(json.dumps({{"error": "no robots with ctrl:* attrs found",
                       "samples": [], "summary": {{}}}}))
else:
    tl.stop()
    tl.set_current_time(0.0)
    tl.set_end_time(max(tl.get_end_time(), duration_s + 5.0))
    tl.play()

    samples = []
    cube_traj = []
    real_start = _t.time()
    last_sample = -1.0
    while True:
        app.update()
        cur_t = float(tl.get_current_time())
        if cur_t - last_sample >= sample_dt_s:
            snap = {{}}
            for r in robots:
                rpath = str(r.GetPath())
                vals = {{}}
                for an in target_attrs:
                    a = r.GetAttribute(an)
                    if a and a.IsValid():
                        v = a.Get()
                        if v is not None:
                            vals[an] = v
                if vals:
                    snap[rpath] = vals
            samples.append({{"sim_t": cur_t, "vals": snap}})
            cube_snap = {{"sim_t": round(cur_t, 1)}}
            for cp in cube_paths:
                cube_snap[cp] = _wp(cp)
            cube_traj.append(cube_snap)
            last_sample = cur_t
        if cur_t >= duration_s:
            break
        if _t.time() - real_start > duration_s + 60:
            break
    tl.stop()

    # Aggregate phase histogram
    phase_time = {{}}
    plan_calls = 0
    plan_fails = 0
    cubes_final = 0
    cycles_final = 0
    last_phases = {{}}
    last_errors = []
    for s in samples:
        for rpath, v in s["vals"].items():
            phase = v.get("ctrl:phase") or v.get("builtin_pp:phase") or "?"
            key = (rpath, phase)
            phase_time[key] = phase_time.get(key, 0) + sample_dt_s
            last_phases[rpath] = phase
            if "ctrl:plan_calls" in v:
                plan_calls = max(plan_calls, int(v["ctrl:plan_calls"]))
            if "ctrl:plan_fails" in v:
                plan_fails = max(plan_fails, int(v["ctrl:plan_fails"]))
            if "ctrl:cubes_delivered" in v:
                cubes_final = max(cubes_final, int(v["ctrl:cubes_delivered"]))
            if "ctrl:cycles_attempted" in v:
                cycles_final = max(cycles_final, int(v["ctrl:cycles_attempted"]))
            if "ctrl:last_error" in v:
                e = v["ctrl:last_error"]
                if e and e not in last_errors:
                    last_errors.append(e)

    summary = {{
        "n_samples": len(samples),
        "duration_s": duration_s,
        "sample_dt_s": sample_dt_s,
        "robots": [str(r.GetPath()) for r in robots],
        "cube_paths": cube_paths,
        "phase_histogram": [
            {{"robot": k[0], "phase": k[1], "seconds": v}}
            for k, v in sorted(phase_time.items(), key=lambda kv: -kv[1])
        ],
        "last_phase": last_phases,
        "plan_calls": plan_calls,
        "plan_fails": plan_fails,
        "plan_fail_rate": (plan_fails / plan_calls) if plan_calls else None,
        "cubes_delivered_final": cubes_final,
        "cycles_attempted_final": cycles_final,
        "last_errors": last_errors[:5],
    }}
    print(json.dumps({{"samples": samples, "cube_trajectories": cube_traj, "summary": summary}}))
"""


async def _probe(label: str, duration_s: int = 60, sample_dt_s: float = 1.0) -> Dict[str, Any]:
    from service.isaac_assist_service.chat.canonical_instantiator import (
        execute_template_canonical, settle_after_canonical,
    )
    from service.isaac_assist_service.chat.tools import kit_tools

    template_path = REPO_ROOT / f"workspace/templates/{label}.json"
    if not template_path.exists():
        return {"error": f"template not found: {label}"}
    template = json.loads(template_path.read_text())

    # Reset stage
    code = (
        "import omni.usd\n"
        "ctx = omni.usd.get_context()\n"
        "ctx.new_stage()\n"
        "stage = ctx.get_stage()\n"
        "from pxr import UsdGeom\n"
        "UsdGeom.Xform.Define(stage, '/World')\n"
    )
    await kit_tools.exec_sync(code, timeout=20)

    build = await execute_template_canonical(template)
    if not build.get("instantiated"):
        return {"error": "build_failed", "errors": build.get("errors")}
    try:
        await settle_after_canonical(template)
    except Exception:
        pass

    sa = template.get("simulate_args") or {}
    seed = []
    if sa.get("cube_path"): seed.append(sa["cube_path"])
    if sa.get("cube_paths"): seed.extend(sa["cube_paths"])
    probe_code = _PROBE_CODE.format(
        duration_s=duration_s, sample_dt_s=sample_dt_s,
        seed_cube_paths_json=json.dumps(seed),
    )
    res = await kit_tools.exec_sync(probe_code, timeout=duration_s + 60)
    out = (res.get("output") or "").strip()
    for line in out.splitlines()[::-1]:
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except Exception:
                continue
    return {"error": "no_json_output", "tail": out[-300:]}


def _diagnose_stuck(summary: Dict[str, Any], cube_traj: Optional[List[Dict]] = None) -> List[str]:
    """Heuristic interpretation of summary → human-readable diagnoses."""
    diagnoses: List[str] = []
    duration = summary.get("duration_s", 0) or 1
    plan_fail_rate = summary.get("plan_fail_rate")

    # Cube-trajectory analysis — detect cubes-stuck or cubes-fallen-off-belt
    if cube_traj and len(cube_traj) >= 3:
        cube_paths = summary.get("cube_paths") or []
        for cp in cube_paths:
            positions = [s.get(cp) for s in cube_traj if s.get(cp) is not None]
            if len(positions) < 3:
                continue
            initial = positions[0]
            final = positions[-1]
            mid = positions[len(positions) // 2]

            dx = final[0] - initial[0]
            dy = final[1] - initial[1]
            dz = final[2] - initial[2]
            dist = (dx**2 + dy**2 + dz**2) ** 0.5

            # Stuck: total displacement < 5cm
            if dist < 0.05:
                diagnoses.append(
                    f"{cp}: stuck (Δ<5cm). spawn={initial} final={final} — "
                    f"belt may be inactive or cube blocked."
                )
            # Fallen below table (typical table z = 0.75)
            if final[2] < 0.5:
                diagnoses.append(
                    f"{cp}: fell below z=0.5 (current z={final[2]:.2f}) — "
                    f"likely fell off belt edge or through floor."
                )
            # Movement past sensor without trigger (heuristic: x movement >0.5m
            # but cube ends far past initial)
            if abs(dx) > 1.0 and dist > 1.0 and summary.get("plan_calls", 0) == 0:
                diagnoses.append(
                    f"{cp}: travelled {dx:+.2f}m in x but controller never planned — "
                    f"sensor may not have triggered."
                )

    # Check for stuck phases (>30% of time in one phase)
    histogram = summary.get("phase_histogram") or []
    by_robot: Dict[str, List[Dict]] = defaultdict(list)
    for row in histogram:
        by_robot[row["robot"]].append(row)
    for robot, phases in by_robot.items():
        for ph in phases:
            pct = (ph["seconds"] / duration) * 100
            if pct > 30 and ph["phase"] not in ("delivered", "cycle_complete", "?"):
                diagnoses.append(
                    f"{robot}: stuck in '{ph['phase']}' ({pct:.0f}% of run)"
                )

    if plan_fail_rate is not None and plan_fail_rate > 0.5:
        diagnoses.append(
            f"cuRobo planning failing {plan_fail_rate*100:.0f}% — "
            f"check pose feasibility, scene_cfg obstacles, sensor-gate"
        )

    if summary.get("plan_calls", 0) == 0 and summary.get("cubes_delivered_final", 0) == 0:
        diagnoses.append(
            "0 plan calls, 0 deliveries — controller never engaged. "
            "Check setup_pick_place_controller wired? sensor never triggered?"
        )

    if summary.get("cycles_attempted_final", 0) > 0 and summary.get("cubes_delivered_final", 0) == 0:
        cycles = summary["cycles_attempted_final"]
        diagnoses.append(
            f"{cycles} cycles attempted, 0 deliveries — gripper-release issue? "
            f"drop precision issue? Mode B FJ never created?"
        )

    # Multi-robot handoff phantom-success detection.
    # Pattern: one robot incremented cubes_delivered (handed off to handoff station)
    # but another robot is stuck in wait_sensor → relay never picked up cube.
    last_phase = summary.get("last_phase") or {}
    delivered = summary.get("cubes_delivered_final", 0) or 0
    n_robots = len(last_phase) if isinstance(last_phase, dict) else 0
    if n_robots >= 2 and delivered >= 1:
        wait_sensor_count = sum(
            1 for v in last_phase.values()
            if isinstance(v, str) and "wait_sensor" in v
        )
        # ≥1 cube counted but >50% of robots still waiting on sensor → handoff stalled
        if wait_sensor_count > n_robots // 2:
            diagnoses.append(
                f"phantom_handoff: {delivered} cube(s) delivered upstream but "
                f"{wait_sensor_count}/{n_robots} robots still in wait_sensor — "
                f"handoff sensor likely never triggered for downstream robot. "
                f"Check sensor placement at handoff station."
            )

    # Detect "drop-precision" pattern: cubes delivered to bin xy but fall below
    # bin floor (z < bin_z - 0.20m). Indicates bin too small or drop too close
    # to edge.
    if cube_traj and len(cube_traj) >= 3:
        cube_paths_list = summary.get("cube_paths") or []
        for cp in cube_paths_list:
            positions = [s.get(cp) for s in cube_traj if s.get(cp) is not None]
            if len(positions) < 2: continue
            final = positions[-1]
            initial = positions[0]
            # Heuristic: cube moved significantly toward target xy
            # but final z is well below initial spawn (large drop = fall through)
            dz = final[2] - initial[2]
            if dz < -0.25:
                diagnoses.append(
                    f"{cp}: large vertical drop (Δz={dz:.2f}m) — likely "
                    f"fell through bin floor or off bin edge. "
                    f"Check bin size/walls or drop_target precision."
                )

    last_errors = summary.get("last_errors", [])
    for e in last_errors[:3]:
        diagnoses.append(f"runtime error: {e[:120]}")

    if not diagnoses:
        diagnoses.append("no obvious stuck-phase / plan-fail / error pattern detected")

    return diagnoses


async def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("canonical")
    p.add_argument("--duration", type=int, default=60)
    p.add_argument("--sample-dt", type=float, default=1.0)
    p.add_argument("--json", action="store_true", help="Emit raw JSON instead of pretty.")
    args = p.parse_args()

    from service.isaac_assist_service.chat.tools import kit_tools
    if not await kit_tools.is_kit_rpc_alive():
        print("[FAIL] Kit RPC not alive at 127.0.0.1:8001", file=sys.stderr, flush=True)
        return 2

    res = await _probe(args.canonical, args.duration, args.sample_dt)
    if args.json:
        print(json.dumps(res, indent=2))
        return 0

    if "error" in res:
        print(f"[FAIL] {res['error']}", file=sys.stderr)
        if "tail" in res:
            print(f"tail: {res['tail']}", file=sys.stderr)
        return 1

    summary = res.get("summary") or {}
    cube_traj = res.get("cube_trajectories") or []
    print(f"=== probe_ctrl_telemetry({args.canonical}) ===")
    print(f"duration_s={summary.get('duration_s')}  samples={summary.get('n_samples')}  "
          f"robots={len(summary.get('robots') or [])}")
    print(f"plan_calls={summary.get('plan_calls')}  plan_fails={summary.get('plan_fails')}  "
          f"fail_rate={summary.get('plan_fail_rate')}")
    print(f"cubes_delivered={summary.get('cubes_delivered_final')}  "
          f"cycles_attempted={summary.get('cycles_attempted_final')}")
    print(f"last_phase: {summary.get('last_phase')}")
    print()
    print("Phase histogram (top 10):")
    for row in (summary.get("phase_histogram") or [])[:10]:
        pct = (row["seconds"] / max(summary.get("duration_s", 1), 1)) * 100
        print(f"  {row['robot']:20s} {row['phase']:25s} {row['seconds']:5.1f}s  ({pct:.0f}%)")
    if cube_traj:
        print()
        print(f"Cube trajectories ({len(cube_traj)} samples):")
        cube_paths = summary.get("cube_paths") or []
        for cp in cube_paths[:4]:
            positions = [s.get(cp) for s in cube_traj if s.get(cp) is not None]
            if not positions:
                continue
            init = positions[0]
            final = positions[-1]
            print(f"  {cp.split('/')[-1]}: {init} → {final}  "
                  f"(Δ={(final[0]-init[0]):+.2f},{(final[1]-init[1]):+.2f},{(final[2]-init[2]):+.2f})")
    print()
    print("Diagnoses:")
    for d in _diagnose_stuck(summary, cube_traj):
        print(f"  • {d}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
