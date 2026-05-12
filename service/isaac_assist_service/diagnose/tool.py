"""diagnose_scene_feasibility — orchestrator handler.

This module implements the install-time constraint validator per spec
docs/specs/2026-05-09-diagnose-scene-feasibility.md.

It is a HANDLER (async function with the standard `args` signature) ready
to be registered into `tool_executor.DATA_HANDLERS` via
`register_diagnose_handlers(handlers)` — same pattern as multimodal_handlers.

The orchestrator:
1. Validates inputs (robot_path, pick_pose / drop_pose presence)
2. Resolves cache key; returns cached payload on hit
3. Runs physics queries via execute_tool_call (solve_ik, check_singularity,
   check_path_clearance, get_bounding_box, raycast, overlap_sphere)
4. Scores each axis through metrics.py
5. Builds Violation list + alternatives
6. Classifies verdict, formats messages, persists to cache, returns report

Sequence diagram (single-robot, no cache hit):
   args  ──→ validate ──→ cache miss
                              │
                              ▼
   solve_ik(pick) ─┐
   solve_ik(drop)  ├─→ batched physics queries
   bbox(obstacles)─┘
                              │
                              ▼
   metric_*() per axis ──→ Violation[] ──→ classify ──→ messages
                              │
                              ▼
   FeasibilityReport ──→ cache.put ──→ args caller
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, List, Optional, Tuple

from . import cache as dcache
from . import messages
from . import metrics
from .schema import (
    Severity, Verdict, Violation, Alternative,
    FeasibilityReport, classify_verdict, THRESHOLDS,
)


# Lazy import to avoid circular dependency (tool_executor → diagnose → tool_executor)
async def _execute_tool_call(name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    from service.isaac_assist_service.chat.tools.tool_executor import execute_tool_call
    return await execute_tool_call(name, args)


# --- Helpers ---------------------------------------------------------

async def _get_world_bbox(prim_path: str) -> Optional[Dict[str, List[float]]]:
    """Return {'min':[x,y,z], 'max':[x,y,z]} via get_bounding_box. None on miss."""
    res = await _execute_tool_call("get_bounding_box", {"prim_path": prim_path})
    out = (res.get("output") or "").strip()
    if not out:
        return None
    # get_bounding_box may print json; tolerant parse
    import json
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                d = json.loads(line)
                mn = d.get("min") or d.get("aabb_min")
                mx = d.get("max") or d.get("aabb_max")
                if mn and mx:
                    return {"min": list(mn), "max": list(mx)}
            except Exception:
                continue
    return None


async def _solve_ik(robot_path: str, pose: List[float], seed: int,
                     robot_type: str = "franka") -> Dict[str, Any]:
    """Wrap solve_ik. Returns parsed dict with at least {'success': bool}."""
    res = await _execute_tool_call("solve_ik", {
        "articulation_path": robot_path,
        "target_position": pose,
        "robot_type": robot_type,
    })
    out = (res.get("output") or "").strip()
    import json
    for line in out.splitlines()[::-1]:
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except Exception:
                continue
    return {"success": False, "error": "no_json_in_solve_ik_output"}


async def _check_singularity(robot_path: str, joint_positions: Optional[List[float]]) -> Optional[float]:
    """Return manipulability index, or None on failure."""
    args: Dict[str, Any] = {"robot_path": robot_path}
    if joint_positions is not None:
        args["joint_positions"] = joint_positions
    res = await _execute_tool_call("check_singularity", args)
    out = (res.get("output") or "").strip()
    import json
    for line in out.splitlines()[::-1]:
        line = line.strip()
        if line.startswith("{"):
            try:
                d = json.loads(line)
                m = d.get("manipulability") or d.get("manipulability_index")
                if m is not None:
                    return float(m)
            except Exception:
                continue
    return None


async def _check_path_clearance(robot_path: str,
                                 start_q: List[float],
                                 end_q: List[float],
                                 n_samples: int = 20) -> Tuple[int, int]:
    """Return (clear_count, total). Falls back to (0, total) on error."""
    res = await _execute_tool_call("check_path_clearance", {
        "robot_path": robot_path,
        "start_joint_positions": start_q,
        "end_joint_positions": end_q,
        "n_samples": n_samples,
    })
    out = (res.get("output") or "").strip()
    import json
    for line in out.splitlines()[::-1]:
        line = line.strip()
        if line.startswith("{"):
            try:
                d = json.loads(line)
                ok = d.get("clear_samples") or d.get("clear_count")
                tot = d.get("n_samples") or d.get("total") or n_samples
                if ok is not None:
                    return int(ok), int(tot)
            except Exception:
                continue
    return 0, n_samples


async def _bbox_for_obstacles(obstacle_paths: List[str]) -> Dict[str, Dict[str, List[float]]]:
    """Resolve bbox for each registered obstacle. Skips ones we can't resolve."""
    out: Dict[str, Dict[str, List[float]]] = {}
    for p in obstacle_paths:
        bb = await _get_world_bbox(p)
        if bb:
            out[p] = bb
    return out


# --- Suggestion builder ---------------------------------------------

def _build_alternative(violation: Violation) -> Optional[Alternative]:
    """Heuristic: per-axis suggestions for the most common violations."""
    axis = violation.axis
    if axis == "reach_utilization" and violation.severity == Severity.WARNING:
        return Alternative(
            axis=axis,
            suggestion=f"Move pose ~{(violation.value - 0.85)*0.3:.2f} m closer to robot base",
            expected_value=0.85,
        )
    if axis == "reach_utilization" and violation.severity == Severity.CRITICAL:
        return Alternative(
            axis=axis,
            suggestion=f"Pose at {violation.value:.0%} of max reach — relocate or use longer-reach robot",
        )
    if axis == "inside_obstacle_bbox":
        d = violation.details or {}
        delta_m = d.get("suggest_delta_m", 0.05)
        ax = d.get("suggest_axis", "z")
        return Alternative(
            axis=axis,
            suggestion=f"Move drop point {delta_m:.2f} m along +{ax} (out of obstacle bbox)",
            delta={"axis": ax, "shift_m": delta_m},
        )
    if axis == "clearance_pct" and violation.severity == Severity.ERROR:
        return Alternative(
            axis=axis,
            suggestion="Reposition transit obstacle or add way-point in middle of trajectory",
        )
    return None


# --- Main handler ---------------------------------------------------

async def _handle_diagnose_scene_feasibility(args: Dict[str, Any]) -> Dict[str, Any]:
    """Implements the tool. Args:
      robot_path: USD path of the robot prim (required, single-robot mode)
      pick_pose: world [x,y,z] OR cube_paths to auto-pick first reachable
      drop_pose: world [x,y,z] OR destination_path to use bbox center
      obstacles: list of USD paths for collision context (optional)
      ee_offset: tool-tip offset (optional, passed-through to solve_ik)
      sensor_path: optional, for sensor-zone metric
      cube_paths: list of cube paths in scene (optional, for sensor-zone)
      mutex_corridors: optional dict for multi-robot mutex check
                       {"robot_a_corridor": {min,max}, "robot_b_corridor": {min,max}, "has_mutex": bool}
      cycles: list of {"robot_path", "pick_pose", "drop_pose", "ee_offset"?,
              "robot_base"?, "max_reach"?} for multi-robot/multi-stage mode.
              Returns per_cycle[] and aggregate{worst_severity, worst_axis,
              mutex_conflicts: []}. (Opus §E option 2.)
      seed: int (default 42)
      use_cache: bool (default True)
      lang: "sv" | "en" (default "sv")
    """
    t0 = time.time()

    # Multi-robot mode: cycles list takes precedence
    cycles = args.get("cycles")
    if cycles and isinstance(cycles, list):
        return await _handle_multi_robot(cycles, args, t0)

    robot_path = (args.get("robot_path") or "").strip()
    if not robot_path:
        return {"error": "diagnose_scene_feasibility requires robot_path (or cycles[] for multi-robot mode)"}

    pick_pose = args.get("pick_pose")
    drop_pose = args.get("drop_pose")
    if pick_pose is None and drop_pose is None:
        # Tolerant fallback: when poses are computed at runtime (most CP
        # templates), we can't statically diagnose IK/reach. Return a
        # neutral verdict with a note so feasibility_baseline still has
        # data to feed phase2_triage. Better than DIAGNOSE_ERROR.
        return {
            "verdict": "feasible",
            "metrics": {"_skipped_reason": "no_pose_provided"},
            "violations": [],
            "alternatives": [],
            "seed_used": int(args.get("seed", 42)),
            "cache_hit": False,
            "elapsed_ms": int((time.time() - t0) * 1000),
            "note": "pick_pose and drop_pose both absent — static checks skipped",
        }

    obstacles = args.get("obstacles") or []
    ee_offset = args.get("ee_offset")
    seed = int(args.get("seed", 42))
    use_cache = bool(args.get("use_cache", True))
    lang = args.get("lang", "sv")

    # --- Cache lookup ---
    # Phase 49b: query stage revision explicitly so the dependency
    # is visible here. When no provider is registered, get_stage_revision
    # returns None and the key falls back to the legacy shape
    # (backwards compatible).
    cache_key = dcache.make_key(
        robot_path=robot_path,
        pick_pose=pick_pose, drop_pose=drop_pose,
        ee_offset=ee_offset,
        obstacle_bboxes=None,  # filled in below
        seed=seed,
        stage_revision=dcache.get_stage_revision(),
    )
    if use_cache:
        cached = dcache.get(cache_key)
        if cached is not None:
            payload = dcache.mark_hit(cached)
            payload["elapsed_ms"] = int((time.time() - t0) * 1000)
            return payload

    # --- Resolve obstacle bboxes ---
    obstacle_bboxes = await _bbox_for_obstacles(obstacles) if obstacles else {}

    # --- Per-pose IK + manipulability ---
    violations: List[Violation] = []
    metrics_out: Dict[str, Any] = {}

    poses_to_check: List[Tuple[str, List[float]]] = []
    if pick_pose is not None:
        poses_to_check.append(("pick", list(pick_pose)))
    if drop_pose is not None:
        poses_to_check.append(("drop", list(drop_pose)))

    ik_results: Dict[str, Dict[str, Any]] = {}
    for label, pose in poses_to_check:
        ik = await _solve_ik(robot_path, pose, seed=seed)
        ik_results[label] = ik
        ok, sev = metrics.metric_ik_feasible(ik_result=ik)
        metrics_out[f"{label}_ik_feasible"] = ok
        if not ok and sev:
            violations.append(Violation(
                axis="ik_feasible",
                severity=sev,
                value=ok,
                threshold=True,
                message=messages.format_violation(
                    "ik_feasible", sev.value,
                    pose=pose, pose_label=label, lang=lang),
                details={"pose_label": label},
            ))
            continue  # skip downstream metrics if no IK

        # Manipulability at IK solution
        joint_q = ik.get("joint_positions") or ik.get("q") or None
        manip = await _check_singularity(robot_path, joint_q)
        manip_v, manip_sev = metrics.metric_manipulability(manip=manip)
        if manip_v is not None:
            metrics_out[f"{label}_manipulability"] = manip_v
        if manip_sev:
            violations.append(Violation(
                axis="manipulability",
                severity=manip_sev,
                value=manip_v,
                threshold=THRESHOLDS["manipulability"]["warning"],
                message=messages.format_violation(
                    "manipulability", manip_sev.value,
                    value=manip_v, threshold=THRESHOLDS["manipulability"]["warning"],
                    pose_label=label, lang=lang),
                details={"pose_label": label},
            ))

        # Reach utilization (needs robot_base + max_reach)
        # We expect the caller to pass these via args; falls back to hard-coded
        # Franka defaults if not provided.
        rb = args.get("robot_base") or [0.0, 0.0, 0.0]
        max_reach = float(args.get("max_reach", 0.855))  # Franka default
        reach_v, reach_sev = metrics.metric_reach_utilization(
            pose=pose, robot_base=rb, max_reach=max_reach,
        )
        metrics_out[f"{label}_reach_utilization"] = reach_v
        if reach_sev:
            violations.append(Violation(
                axis="reach_utilization",
                severity=reach_sev,
                value=reach_v,
                threshold=THRESHOLDS["reach_utilization"][reach_sev.value.lower()],
                message=messages.format_violation(
                    "reach_utilization", reach_sev.value,
                    value=reach_v,
                    threshold=THRESHOLDS["reach_utilization"][reach_sev.value.lower()],
                    pose_label=label, lang=lang),
                details={"pose_label": label, "max_reach": max_reach},
            ))

        # Inside obstacle bbox?
        path, in_sev = metrics.metric_inside_obstacle_bbox(
            pose=pose, obstacle_bboxes=obstacle_bboxes,
        )
        metrics_out[f"{label}_inside_obstacle"] = path
        if path and in_sev:
            # Compute suggested delta to escape
            bb = obstacle_bboxes[path]
            # Push along smallest axis-aligned delta out of bbox
            deltas = [
                ("x", bb["max"][0] - pose[0] + 0.02),
                ("y", bb["max"][1] - pose[1] + 0.02),
                ("z", bb["max"][2] - pose[2] + 0.02),
            ]
            ax, dm = min(deltas, key=lambda kv: abs(kv[1]))
            violations.append(Violation(
                axis="inside_obstacle_bbox",
                severity=in_sev,
                value=path,
                threshold=None,
                message=messages.format_violation(
                    "inside_obstacle_bbox", in_sev.value,
                    path=path, delta_m=abs(dm), axis_label=ax,
                    pose_label=label, lang=lang),
                details={"pose_label": label,
                         "suggest_delta_m": abs(dm), "suggest_axis": ax},
            ))

    # --- Path-clearance metric (transit corridor) ---
    if "pick" in ik_results and "drop" in ik_results:
        pq = ik_results["pick"].get("joint_positions") or ik_results["pick"].get("q")
        dq = ik_results["drop"].get("joint_positions") or ik_results["drop"].get("q")
        if pq and dq:
            n_samples = int(args.get("path_n_samples", 20))
            ok_count, total = await _check_path_clearance(
                robot_path, pq, dq, n_samples=n_samples,
            )
            pct, sev = metrics.metric_clearance_pct(clear_count=ok_count, total=total)
            metrics_out["clearance_pct"] = pct
            if sev:
                violations.append(Violation(
                    axis="clearance_pct",
                    severity=sev,
                    value=pct,
                    threshold=THRESHOLDS["clearance_pct"]["error" if sev == Severity.ERROR else "warning"],
                    message=messages.format_violation(
                        "clearance_pct", sev.value,
                        value=pct, lang=lang),
                ))

    # --- Sensor-zone metric ---
    sensor_path = (args.get("sensor_path") or "").strip()
    cube_paths_arg = args.get("cube_paths") or []
    if sensor_path and cube_paths_arg:
        # Read sensor + cube positions via raycast/overlap_sphere — for now,
        # caller passes cube_xys directly; richer integration via execute_tool_call
        # in a follow-up.
        cube_xys = args.get("cube_xys") or []
        sensor_xy = args.get("sensor_xy") or [0, 0]
        sensor_radius = float(args.get("sensor_radius", 0.1))
        ok, sev = metrics.metric_cube_in_sensor_zone_at_settle(
            cube_xys=cube_xys, sensor_xy=sensor_xy,
            sensor_radius=sensor_radius,
        )
        metrics_out["cube_in_sensor_zone_at_settle"] = ok
        if not ok and sev:
            violations.append(Violation(
                axis="cube_in_sensor_zone_at_settle",
                severity=sev,
                value=ok,
                threshold=True,
                message=messages.format_violation(
                    "cube_in_sensor_zone_at_settle", sev.value, lang=lang),
            ))

    # --- Multi-robot mutex conflict ---
    mc = args.get("mutex_corridors")
    if mc and isinstance(mc, dict):
        v, sev = metrics.metric_mutex_conflict(
            robot_a_corridor=mc.get("robot_a_corridor") or {},
            robot_b_corridor=mc.get("robot_b_corridor") or {},
            has_mutex=bool(mc.get("has_mutex")),
        )
        metrics_out["mutex_conflict"] = v
        if v and sev:
            violations.append(Violation(
                axis="mutex_conflict",
                severity=sev,
                value=v,
                threshold=False,
                message=messages.format_violation(
                    "mutex_conflict", sev.value,
                    robot_a=mc.get("robot_a_path", "A"),
                    robot_b=mc.get("robot_b_path", "B"),
                    lang=lang),
            ))

    # --- Classify + alternatives ---
    verdict = classify_verdict(violations)
    alternatives = [a for a in (
        _build_alternative(v) for v in violations
    ) if a is not None]

    report = FeasibilityReport(
        verdict=verdict,
        metrics=metrics_out,
        violations=violations,
        alternatives=alternatives,
        seed_used=seed,
        cache_hit=False,
        elapsed_ms=int((time.time() - t0) * 1000),
    )
    payload = report.to_dict()

    # Persist to cache
    if use_cache:
        dcache.put(cache_key, payload)

    return payload


async def _handle_multi_robot(cycles: List[Dict[str, Any]],
                              top_args: Dict[str, Any],
                              t_start: float) -> Dict[str, Any]:
    """Multi-robot / multi-stage diagnose. Per Opus §E option 2.

    Loops over each cycle, calls the single-robot handler, aggregates
    per-cycle results + a top-level aggregate { worst_severity, worst_axis,
    mutex_conflicts[] }.
    """
    seed = int(top_args.get("seed", 42))
    lang = top_args.get("lang", "sv")
    use_cache = bool(top_args.get("use_cache", True))

    per_cycle: List[Dict[str, Any]] = []
    severity_rank = {"INFO": 1, "WARNING": 2, "ERROR": 3, "CRITICAL": 4}
    worst_severity = "INFO"
    worst_axis: Optional[str] = None

    for ci, cycle in enumerate(cycles):
        cycle_args = dict(cycle)
        cycle_args.setdefault("seed", seed)
        cycle_args.setdefault("use_cache", use_cache)
        cycle_args.setdefault("lang", lang)
        # carry top-level shared fields if not overridden
        for k in ("obstacles", "sensor_path", "cube_paths", "cube_xys",
                  "sensor_xy", "sensor_radius", "path_n_samples"):
            if k in top_args and k not in cycle_args:
                cycle_args[k] = top_args[k]

        rep = await _handle_diagnose_scene_feasibility(cycle_args)
        rep["cycle_index"] = ci
        per_cycle.append(rep)

        for v in rep.get("violations", []):
            sv = v.get("severity", "INFO")
            if severity_rank.get(sv, 0) > severity_rank.get(worst_severity, 0):
                worst_severity = sv
                worst_axis = v.get("axis")

    # Cross-cycle: detect mutex conflict if 2+ cycles target same workspace
    mutex_conflicts: List[Dict[str, Any]] = []
    has_mutex = bool(top_args.get("has_mutex"))
    for i in range(len(cycles)):
        for j in range(i + 1, len(cycles)):
            ci_args = cycles[i]
            cj_args = cycles[j]
            # Build axis-aligned corridor as bbox between pick and drop
            def _corridor(c):
                pp = c.get("pick_pose")
                dp = c.get("drop_pose")
                if not pp or not dp:
                    return None
                return {
                    "min": [min(pp[k], dp[k]) for k in range(3)],
                    "max": [max(pp[k], dp[k]) for k in range(3)],
                }
            a = _corridor(ci_args)
            b = _corridor(cj_args)
            if not a or not b:
                continue
            v, sev = metrics.metric_mutex_conflict(
                robot_a_corridor=a,
                robot_b_corridor=b,
                has_mutex=has_mutex,
            )
            if v and sev:
                mutex_conflicts.append({
                    "cycle_a": i, "cycle_b": j,
                    "robot_a": ci_args.get("robot_path"),
                    "robot_b": cj_args.get("robot_path"),
                    "severity": sev.value,
                })
                if severity_rank.get(sev.value, 0) > severity_rank.get(worst_severity, 0):
                    worst_severity = sev.value
                    worst_axis = "mutex_conflict"

    # Aggregate verdict from worst-severity
    if worst_severity == "CRITICAL":
        agg_verdict = "infeasible"
    elif worst_severity == "ERROR":
        agg_verdict = "overconstrained"
    elif worst_severity == "WARNING":
        agg_verdict = "tightly_feasible"
    else:
        agg_verdict = "feasible"

    return {
        "verdict": agg_verdict,
        "per_cycle": per_cycle,
        "aggregate": {
            "worst_severity": worst_severity,
            "worst_axis": worst_axis,
            "mutex_conflicts": mutex_conflicts,
            "n_cycles": len(per_cycle),
        },
        "metrics": {},
        "violations": [],
        "alternatives": [],
        "seed_used": seed,
        "cache_hit": False,
        "elapsed_ms": int((time.time() - t_start) * 1000),
    }


def register_diagnose_handlers(handlers: Dict[str, Any]) -> None:
    """Hook used by tool_executor.py to register the handler.

    Pattern mirrors `multimodal_handlers.register_multimodal_handlers`.
    Adding the handler here keeps tool_executor.py edit-free until the
    register-call line is added in Phase 1's final commit.
    """
    handlers["diagnose_scene_feasibility"] = _handle_diagnose_scene_feasibility
