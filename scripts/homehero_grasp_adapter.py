#!/usr/bin/env python3
"""Plan an R5 pickup with HomeHero's grasp planner (run in HomeHero's venv).

Reads one request (JSON on stdin, written by plan_er_grasp.py): the robot's
base pose and start configuration, the target's surface points in world, the
support's box and the other obstacles. Runs HomeHero's Planner
(scripts/rl_teacher/aida_r5_grasp_planner.py) with its geometry-pickup
config - candidates from the object's hull, ranked by predicted thumb and
fingertip opposition, IK through pinocchio, every waypoint screened with coal
against the robot's own links, the support and the obstacles - the same way
HomeHero's preflight adapter calls it (aida_exploration_recovery._worker).

The planner covers raise, align and descend. HomeHero closes, lifts and
holds with a grip controller on fingertip forces; here the rest is appended
to the plan:

  close  "grip_command": the real hand's grip - every digit commanded
         closed, each stopped by the hand's firmware on contact (replay_grasp
         runs it: ability_hand_model.py); or "predicted_contact": each digit
         to the planner's predicted contact angle plus a preload, at the
         commissioned per-tick rate
  lift   the shoulder slide alone raised by lift_m (HomeHero's lift rule),
         arm and hand held
  hold   the last target, hold_s

Every tick of the result is checked against the per-tick action limits and
the joint limits, as HomeHero's adapter checks a screened path, and the
appended ticks are screened for collisions with the planner's own checker.

    <HomeHero>/.venv/bin/python scripts/homehero_grasp_adapter.py < request.json > plan.json
"""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

HH = Path("/home/kimate/Documents/Github/HomeHero")
sys.path.insert(0, str(HH / "src/homehero_core"))
sys.path.insert(0, str(HH / "scripts/rl_teacher"))

import numpy as np  # noqa: E402
from scipy.spatial.transform import Rotation  # noqa: E402


def main() -> int:
    req = json.loads(sys.stdin.read())
    from aida_r5_grasp_planner import Planner
    from aida_r5_motion import Checker
    from homehero_core.aida_r5_learning import ACTION_STEP, HAND_HI, HAND_LO

    if req.get("hover_clearance_m") is not None:
        # HomeHero's candidates hover 0.14 m over the object's top before the
        # descent; a 13.5 cm can on a 0.708 m table puts that at 0.985 m, and
        # the R5 palm tops out at 0.978 m from its reset pose (shoulder lift at
        # its stop, the upper arm then meets the chassis). An explicit,
        # recorded override of that one margin - nothing else in the planner.
        import aida_r5_grasp_planner as _gp
        from homehero_core.aida_r5_geometry_pickup import candidates as _orig
        clearance = float(req["hover_clearance_m"])

        def _candidates(points, calibration, table_top, settings=None):
            result = _orig(points, calibration, table_top, settings)
            top = float(np.asarray(points)[:, 2].max())
            for c in result:
                c["hover_z"] = max(float(c["position"][2] + .065), top + clearance)
            return result
        _gp.candidates = _candidates
    config = json.loads(Path(req["config"]).read_text())
    config = copy.deepcopy(config)
    # the calibrated wrist orientation is in the recording's world frame (its
    # robot faced world -Y); turned with this base, as HomeHero's runtime does
    # (aida_exploration_recovery.transform_config). Without it every candidate
    # put the palm beyond the can, out of reach.
    from aida_exploration_recovery import transform_config
    protocol = json.loads((Path(req["experiment"]) / "protocol.json").read_text())
    settings_before = dict(config["settings"])
    config = transform_config(config, protocol["base"], req["base_pose"])
    config["settings"] = settings_before                                   # (it also caps attempts; not ours to change)
    # HomeHero's scanned meshes are of its own objects; ours is planned
    # against the convex hull of its surface points (the planner's default)
    config["planning"].pop("object_collision", None)
    planner = Planner(Path(req["experiment"]), config)
    if planner.robot["physics"]["usd_sha256"] != req["robot_usd_sha256"]:
        raise ValueError("the scene's robot differs from the planner's pinned geometry")
    planner.protocol["table"] = dict(planner.protocol["table"], top=req["table_top"])
    planner.checker = Checker(planner.g, Path(config["planning"]["collision_cache"]), req["obstacles"],
                              clearance=config["planning"]["table_margin_m"])
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from r5_collision_exceptions import apply_to_checker
    exceptions_dropped = apply_to_checker(planner.checker)              # the recorded asset exceptions (see that module)
    if planner.contact_targets is not None:
        planner.contact_targets.checker = planner.checker
    lower = np.r_[planner.original_bounds[:, 0], HAND_LO]
    upper = np.r_[planner.original_bounds[:, 1], HAND_HI]
    measured = np.asarray(req["measured"], float)
    g = planner.g
    g.translation = np.asarray(req["base_pose"][:3], float)
    g.rotation = Rotation.from_quat(req["base_pose"][3:]).as_matrix()
    from aida_r5_motion import measured_configuration
    measured_configuration(g, req["joint_positions"])
    g.forward(measured)
    p0, r0 = g.pose()
    palm = np.r_[p0, Rotation.from_matrix(r0).as_quat()]

    # the planner starts from HomeHero's reset pose (arm out over the table);
    # from the arms-down rest, raising the palm straight up drives the upper
    # arm into the chassis (measured: every candidate failed there). So the
    # arm first goes rest -> reset by HomeHero's collision-checked RRT.
    entry, entry_info = [], None
    if req.get("via"):
        from aida_r5_motion import joint_path
        via = np.asarray(req["via"], float)
        # the reach is checked against the target too: the planner guards the
        # target only in its own stages (its object model), and a reach
        # without it swept the hand through the can (measured: tipped 14 deg)
        entry_checker = Checker(planner.g, Path(config["planning"]["collision_cache"]),
                                req["obstacles"] + [req["target_box"]], clearance=config["planning"]["table_margin_m"])
        apply_to_checker(entry_checker)
        dense, entry_info = joint_path(entry_checker, measured[:8], via[:8], measured[8:])
        # joint_path emits commands for a faster controller (0.025 rad per
        # command); resampled here to the 30 Hz action limits, along the same
        # checked segments
        step_ = np.asarray(ACTION_STEP, float)
        entry, prev = [], measured.copy()
        for q in (np.asarray(x, float) for x in dense):
            n = max(1, int(np.ceil(np.max(np.abs(q - prev) / step_))))
            for f in np.linspace(0, 1, n + 1)[1:]:
                entry.append(prev * (1 - f) + q * f)
            prev = q
        # open the hand to the reset hand at the commissioned rate
        q = entry[-1].copy() if entry else measured.copy()
        goal = np.r_[via[:8], via[8:]]
        n = int(np.ceil(np.max(np.abs(goal - q) / step_)))
        for f in np.linspace(0, 1, n + 1)[1:]:
            entry.append(q * (1 - f) + goal * f)
        # the RRT checked its own segments with the hand as it was; the
        # resampled reach and the hand opening at its end are screened again
        from aida_r5_motion import screen
        reach_screen = screen(entry_checker, entry)
        if not reach_screen["passed"]:
            hit = reach_screen["collisions"][0]
            print(json.dumps({"passed": False, "reason": f"the reach collides at tick {hit['waypoint'] + 1}: "
                              + "; ".join(f"{a.split('/')[-1]} x {b.split('/')[-1]}" for a, b in hit["pairs"][:4]),
                              "reach_screen": reach_screen}, default=float))
            return 0
        entry_info = dict(entry_info, reach_screen={k: reach_screen[k] for k in ("passed", "waypoints")})
        measured = entry[-1].copy()
        g.forward(measured)
        p0, r0 = g.pose()
        palm = np.r_[p0, Rotation.from_matrix(r0).as_quat()]

    # HomeHero passes the drive's measured gravity offset per arm joint; its
    # planner reads only the slide's (reachable_slide_bounds reserves that
    # much travel below the slide's stop). The slide's is exact from the
    # carried mass (plan_er_grasp.py); the other entries are not read.
    sg = req["slide_gravity_offset"]
    gravity_offset = [float(sg["m"])] + [0.0] * 7
    task = req.get("task") or {"kind": "pickup"}
    if task["kind"] == "loop_grip":
        # a flat tool worked by two digits through its loops (scissors by one hand)
        from aida_r5_grasp_planner import reachable_slide_bounds
        from loop_grip_planner import plan_loop_grip
        bounds = reachable_slide_bounds(planner.original_bounds,
                                        {"arm_lower": lower[:8].tolist(), "arm_upper": upper[:8].tolist(),
                                         "gravity_offset": gravity_offset}, config["planning"]["slide_headroom_m"])
        g.bounds = bounds

        def make_checker_l(extra):
            c = Checker(g, Path(config["planning"]["collision_cache"]), list(req["obstacles"]) + list(extra),
                        clearance=config["planning"]["table_margin_m"])
            apply_to_checker(c)
            return c
        res = plan_loop_grip(req, g, planner.checker, measured, lower, upper, ACTION_STEP, bounds[0], make_checker_l)
        out = {"planner": "loop_grip_planner (HomeHero geometry, checker, RRT)", "task": {k: v for k, v in task.items() if k != "bodies"},
               "passed": bool(res.get("passed")), "reason": res.get("reason"), "slide_gravity_offset": dict(sg), "entry": entry_info,
               **{k: res[k] for k in ("grip", "holes", "rrt", "tried", "candidates", "spread_now_m") if k in res}}
        if res.get("passed"):
            start = np.asarray(req["measured"], float)
            arr = np.vstack([start] + entry + list(res["targets"][1:]))
            stages = ["start"] + ["entry"] * len(entry) + list(res["stages"][1:])
            np.savez(req["out_npz"], targets=arr, stages=np.asarray(stages))
            out.update(targets=req["out_npz"], ticks=len(arr), seconds_at_30hz=round(len(arr) / 30, 2),
                       stage_ticks={k: int(sum(1 for x in stages if x == k)) for k in dict.fromkeys(stages)})
        print(json.dumps(out, default=lambda o: o.tolist() if hasattr(o, "tolist") else float(o)))
        return 0
    if task["kind"] == "button_press":
        # a push button pressed with the index (a microwave's door release): button_press_planner.py
        from aida_r5_grasp_planner import reachable_slide_bounds
        from button_press_planner import plan_button_press
        bounds = reachable_slide_bounds(planner.original_bounds,
                                        {"arm_lower": lower[:8].tolist(), "arm_upper": upper[:8].tolist(),
                                         "gravity_offset": gravity_offset}, config["planning"]["slide_headroom_m"])
        g.bounds = bounds

        def make_checker(extra):
            c = Checker(g, Path(config["planning"]["collision_cache"]), list(req["obstacles"]) + list(extra),
                        clearance=config["planning"]["table_margin_m"])
            apply_to_checker(c)
            return c
        res = plan_button_press(req, g, planner.checker, measured, lower, upper, ACTION_STEP, bounds[0], make_checker)
        out = {"planner": "button_press_planner (HomeHero geometry, checker, RRT)", "task": {k: v for k, v in task.items() if k != "button"},
               "passed": bool(res.get("passed")), "reason": res.get("reason"), "slide_gravity_offset": dict(sg), "entry": entry_info,
               "collision_exceptions_dropped": exceptions_dropped,
               **{k: res[k] for k in ("grip", "palm", "rrt", "candidates", "tried", "rejected", "screen") if k in res}}
        if res.get("passed"):
            start = np.asarray(req["measured"], float)
            arr = np.vstack([start] + entry + list(res["targets"][1:]))
            stages = ["start"] + ["entry"] * len(entry) + list(res["stages"][1:])
            np.savez(req["out_npz"], targets=arr, stages=np.asarray(stages))
            out.update(targets=req["out_npz"], ticks=len(arr), seconds_at_30hz=round(len(arr) / 30, 2),
                       stage_ticks={k: int(sum(1 for x in stages if x == k)) for k in dict.fromkeys(stages)})
        print(json.dumps(out, default=lambda o: o.tolist() if hasattr(o, "tolist") else float(o)))
        return 0
    if task["kind"] == "screw_drive":
        # a screw driven with the drill already in the hand: screw_drive_planner.py
        from aida_r5_grasp_planner import reachable_slide_bounds
        from screw_drive_planner import plan_screw_drive
        bounds = reachable_slide_bounds(planner.original_bounds,
                                        {"arm_lower": lower[:8].tolist(), "arm_upper": upper[:8].tolist(),
                                         "gravity_offset": gravity_offset}, config["planning"]["slide_headroom_m"])
        g.bounds = bounds

        def make_checker_s(extra):
            c = Checker(g, Path(config["planning"]["collision_cache"]), list(req["obstacles"]) + list(extra),
                        clearance=config["planning"]["table_margin_m"])
            apply_to_checker(c)
            return c
        res = plan_screw_drive(req, g, planner.checker, measured, lower, upper, ACTION_STEP, bounds[0], make_checker_s)
        out = {"planner": "screw_drive_planner (HomeHero geometry, checker)", "task": {k: v for k, v in task.items() if k != "screw"},
               "passed": bool(res.get("passed")), "reason": res.get("reason"), "slide_gravity_offset": dict(sg), "entry": entry_info,
               **{k: res[k] for k in ("drive",) if k in res}}
        if res.get("passed"):
            start = np.asarray(req["measured"], float)
            arr = np.vstack([start] + entry + list(res["targets"][1:]))
            stages = ["start"] + ["entry"] * len(entry) + list(res["stages"][1:])
            np.savez(req["out_npz"], targets=arr, stages=np.asarray(stages))
            out.update(targets=req["out_npz"], ticks=len(arr), seconds_at_30hz=round(len(arr) / 30, 2),
                       stage_ticks={k: int(sum(1 for x in stages if x == k)) for k in dict.fromkeys(stages)})
        print(json.dumps(out, default=lambda o: o.tolist() if hasattr(o, "tolist") else float(o)))
        return 0
    if task["kind"] == "door_open":
        # a hinged door opened by its handle (a refrigerator's): door_open_planner.py
        from aida_r5_grasp_planner import reachable_slide_bounds
        from door_open_planner import plan_door_open
        bounds = reachable_slide_bounds(planner.original_bounds,
                                        {"arm_lower": lower[:8].tolist(), "arm_upper": upper[:8].tolist(),
                                         "gravity_offset": gravity_offset}, config["planning"]["slide_headroom_m"])
        g.bounds = bounds

        def make_checker(extra):
            c = Checker(g, Path(config["planning"]["collision_cache"]), list(req["obstacles"]) + list(extra),
                        clearance=config["planning"]["table_margin_m"])
            apply_to_checker(c)
            return c
        res = plan_door_open(req, g, planner.checker, measured, lower, upper, ACTION_STEP, bounds[0], make_checker)
        out = {"planner": "door_open_planner (HomeHero geometry, checker, RRT)", "task": {k: v for k, v in task.items() if k != "door"},
               "passed": bool(res.get("passed")), "reason": res.get("reason"), "slide_gravity_offset": dict(sg), "entry": entry_info,
               "collision_exceptions_dropped": exceptions_dropped,
               **{k: res[k] for k in ("grip", "palm", "pull_deg", "door_angle_before_deg", "reposed", "approach", "backed_off_ticks", "retreat_ok", "rrt", "candidates", "tried", "search", "screen", "best") if k in res}}
        if res.get("passed"):
            start = np.asarray(req["measured"], float)
            arr = np.vstack([start] + entry + list(res["targets"][1:]))
            stages = ["start"] + ["entry"] * len(entry) + list(res["stages"][1:])
            np.savez(req["out_npz"], targets=arr, stages=np.asarray(stages))
            out.update(targets=req["out_npz"], ticks=len(arr), seconds_at_30hz=round(len(arr) / 30, 2),
                       stage_ticks={k: int(sum(1 for x in stages if x == k)) for k in dict.fromkeys(stages)})
        print(json.dumps(out, default=lambda o: o.tolist() if hasattr(o, "tolist") else float(o)))
        return 0
    if task["kind"] == "tool_grip":
        # a tool taken by its handle, a finger on its control (a drill's
        # trigger): tool_grip_planner.py, HomeHero's geometry, checker and RRT
        from aida_r5_grasp_planner import reachable_slide_bounds
        from tool_grip_planner import plan_tool_grip
        bounds = reachable_slide_bounds(planner.original_bounds,
                                        {"arm_lower": lower[:8].tolist(), "arm_upper": upper[:8].tolist(),
                                         "gravity_offset": gravity_offset}, config["planning"]["slide_headroom_m"])
        g.bounds = bounds

        def make_checker(extra):
            c = Checker(g, Path(config["planning"]["collision_cache"]), list(req["obstacles"]) + list(extra),
                        clearance=config["planning"]["table_margin_m"])
            apply_to_checker(c)
            return c
        res = plan_tool_grip(req, g, planner.checker, measured, lower, upper, ACTION_STEP, bounds[0], make_checker)
        out = {"planner": "tool_grip_planner (HomeHero geometry, checker, RRT)", "task": {k: v for k, v in task.items() if k != "tool"},
               "passed": bool(res.get("passed")), "reason": res.get("reason"), "slide_gravity_offset": dict(sg), "entry": entry_info,
               "collision_exceptions_dropped": exceptions_dropped,
               **{k: res[k] for k in ("grip", "palm", "index_pre_rad", "lifted_m", "switch", "frame", "rrt", "candidates", "tried", "screen", "best") if k in res}}
        if res.get("passed"):
            start = np.asarray(req["measured"], float)
            arr = np.vstack([start] + entry + list(res["targets"][1:]))
            stages = ["start"] + ["entry"] * len(entry) + list(res["stages"][1:])
            np.savez(req["out_npz"], targets=arr, stages=np.asarray(stages))
            out.update(targets=req["out_npz"], ticks=len(arr), seconds_at_30hz=round(len(arr) / 30, 2),
                       stage_ticks={k: int(sum(1 for x in stages if x == k)) for k in dict.fromkeys(stages)})
        print(json.dumps(out, default=lambda o: o.tolist() if hasattr(o, "tolist") else float(o)))
        return 0
    if task["kind"] in ("pinch_place", "pinch_lift", "cap_turn"):
        # a pinch pick-and-place (a chess move): planned with HomeHero's
        # geometry, checker and RRT by pinch_place_planner.py, from where the
        # reach left the arm, inside the slide's compensated travel
        from aida_r5_grasp_planner import reachable_slide_bounds
        from pinch_place_planner import plan_pinch_place
        bounds = reachable_slide_bounds(planner.original_bounds,
                                        {"arm_lower": lower[:8].tolist(), "arm_upper": upper[:8].tolist(),
                                         "gravity_offset": gravity_offset}, config["planning"]["slide_headroom_m"])
        g.bounds = bounds
        if task.get("survey"):
            from pinch_place_planner import survey

            def checker_without(body_id):
                boxes = [o for o in req["obstacles_all"] if not o["id"].startswith(body_id + ":") and o["id"] != body_id]
                c = Checker(g, Path(config["planning"]["collision_cache"]), boxes,
                            clearance=config["planning"]["table_margin_m"])
                apply_to_checker(c)
                return c
            rows = survey(req, g, checker_without, measured)
            print(json.dumps({"passed": True, "survey": rows}))
            return 0
        res = plan_pinch_place(req, g, planner.checker, measured, lower, upper, ACTION_STEP, bounds[0])
        out = {"planner": "pinch_place_planner (HomeHero geometry, checker, RRT)", "task": task,
               "passed": bool(res.get("passed")), "reason": res.get("reason"),
               "slide_gravity_offset": dict(sg), "entry": entry_info, "collision_exceptions_dropped": exceptions_dropped,
               **{k: res[k] for k in ("pinch", "grasp", "options", "tried", "rrt", "lifted_m", "screened_ticks",
                                      "rejections", "examples", "closure", "rise_range") if k in res}}
        if res.get("passed"):
            start = np.asarray(req["measured"], float)
            arr = np.vstack([start] + entry + list(res["targets"][1:]))
            stages = ["start"] + ["entry"] * len(entry) + list(res["stages"][1:])
            np.savez(req["out_npz"], targets=arr, stages=np.asarray(stages))
            out.update(targets=req["out_npz"], ticks=len(arr), seconds_at_30hz=round(len(arr) / 30, 2),
                       stage_ticks={k: int(sum(1 for x in stages if x == k)) for k in dict.fromkeys(stages)})
        print(json.dumps(out, default=lambda o: o.tolist() if hasattr(o, "tolist") else float(o)))
        return 0
    plan = planner.plan(dict(env_id=0, round=0, measured=measured.tolist(), base_pose=req["base_pose"],
                             object_pose=req["object_pose"], object_id=req["object_id"], palm_pose=palm.tolist(),
                             cloud=req["cloud"], joint_positions=req["joint_positions"],
                             arm_lower=lower[:8].tolist(), arm_upper=upper[:8].tolist(),
                             gravity_offset=gravity_offset))
    out = {"planner": "HomeHero aida_r5_grasp_planner.Planner", "config": req["config"],
           "palm_start": "URDF forward kinematics of the commanded start (no measured palm in this replay)",
           "passed": bool(plan.get("passed")), "reason": plan.get("reason"), "trials": plan.get("trials"),
           "selection": plan.get("selection"), "seconds": plan.get("seconds"),
           "slide_gravity_offset": dict(sg, source="carried link masses in the built USD x g / slide stiffness")}
    if not plan.get("passed"):
        print(json.dumps(out, default=float))
        return 0
    start = np.asarray(req["measured"], float)
    path = [start] + entry + [np.asarray(q, float) for q in plan["path"]]
    stages = ["start"] + ["reach"] * len(entry) + [("open", "raise", "align", "descend")[min(3, s + 1)] if s else "open/raise"
                                                   for s in plan["stages"]]
    cand = plan["candidate"]
    pred = cand.get("contact_prediction") or {}
    contacts = pred.get("contacts") or [None] * 5
    step = np.asarray(ACTION_STEP, float)

    def walk(goal, label):
        q = path[-1].copy()
        n = int(np.ceil(np.max(np.abs(goal - q) / step))) if np.any(goal != q) else 0
        for f in np.linspace(0, 1, n + 1)[1:]:
            path.append(q * (1 - f) + goal * f)
            stages.append(label)

    close = req.get("close") or {"mode": "predicted_contact"}
    open_hand = path[-1][8:].copy()                                  # the hand as the planner left it, open
    if close["mode"] == "grip_command":
        # the real hand's grip command: every digit driven toward closed (a
        # power grip; the thumb rotator stays where the planner put it) and
        # stopped by its own firmware on contact - halt-on-collision, run by
        # replay_grasp.py's Ability Hand model. One command, held.
        goal = path[-1].copy()
        goal[8:13] = np.asarray(HAND_HI[:5], float)
        for _ in range(int(float(close.get("hold_s", 1.0)) * 30)):
            path.append(goal.copy())
            stages.append("close")
    else:
        # each digit to its predicted contact plus a preload, at the commissioned rate
        pre = req.get("preload_rad", {"fingers": 0.08, "thumb_flex": 0.15})
        fallback = float(pred.get("closure_rad") or 0.5)
        goal = path[-1].copy()
        for k in range(4):
            a = contacts[k]["angle"] if contacts[k] else fallback
            goal[8 + k] = min(0.95, a + float(pre["fingers"]))      # q1 <= 0.95: L2 meets the palm past it
        goal[12] = min(float(HAND_HI[4]), (contacts[4]["angle"] if contacts[4] else fallback) + float(pre["thumb_flex"]))
        walk(goal, "close")
        for _ in range(int(0.5 * 30)):                               # let the squeeze build
            path.append(path[-1].copy())
            stages.append("close")
    # lift: HomeHero's rule (homehero_core/aida_r5_recovery.PickupRecovery):
    # the arm held where it closed, the vertical shoulder slide raised 0.4 mm
    # per tick, to lift_m or the slide's reachable top (the planner's bounds,
    # which reserve travel for the gravity-compensated drive). HomeHero stops
    # at 7 cm of measured rise; open loop, lift_m stands in for that. (An IK
    # lift that kept the palm straight re-posed the shoulder into the chassis:
    # coal and PhysX both found arm link 1 against the base.)
    lift_m = float(req.get("lift_m", 0.08))
    slide_top = float(g.bounds[0, 1])
    q = path[-1].copy()
    top = min(q[0] + lift_m, slide_top)
    while q[0] < top - 1e-9:
        q = q.copy()
        q[0] = min(q[0] + 0.0004, top)
        path.append(q)
        stages.append("lift")
    lift_fit = {"rule": "slide only, 0.4 mm per tick (HomeHero PickupRecovery)", "requested_m": lift_m,
                "slide_top_m": round(slide_top, 4), "lifted_m": round(float(q[0] - path[-1 - sum(1 for x in stages if x == 'lift')][0]), 4)}
    for _ in range(int(float(req.get("hold_s", 1.5)) * 30)):
        path.append(path[-1].copy())
        stages.append("hold")

    arr = np.asarray(path)
    # the appended close, lift and hold are screened with the planner's own
    # checker (arm and hand against the support, the other objects and the
    # robot's non-adjacent links; the target is not an obstacle - the hand is
    # meant to touch it). A lift that runs the arm into the chassis or the
    # table fails the plan here, not in PhysX.
    from aida_r5_motion import screen
    labels = np.asarray(stages)
    appended = np.flatnonzero(np.isin(labels, ("close", "lift", "hold")))
    screened = [arr[i].copy() for i in appended]
    if close["mode"] == "grip_command":
        # a grip command's digits stop on the object, not at the closed
        # posture they are commanded to; the arm is what moves here, screened
        # with the hand as it was before the grip
        for q in screened:
            q[8:] = open_hand
    appended_screen = screen(planner.checker, screened)
    for h in appended_screen["collisions"]:
        h["tick"] = int(appended[h.pop("waypoint")])
        h["stage"] = str(labels[h["tick"]])
    jumps = np.abs(np.diff(arr, axis=0))
    if close["mode"] == "grip_command":
        jumps[:, 8:13] = 0.0       # the hand's firmware runs a grip command's motion path, at its own speed
    if np.any(jumps > step + 1e-8):
        raise ValueError(f"path exceeds per-tick limits at {int(np.argmax(np.max(jumps - step, axis=1)))}")
    if np.any(arr < lower - 1e-8) or np.any(arr > upper + 1e-8):
        raise ValueError("path leaves the joint limits")
    if not appended_screen["passed"]:
        first = appended_screen["collisions"][0]
        out.update(passed=False, reason=f"the appended {first['stage']} collides at tick {first['tick']}: "
                   + "; ".join(f"{a.split('/')[-1]} x {b.split('/')[-1]}" for a, b in first["pairs"][:4]),
                   appended_screen=appended_screen, lift=lift_fit)
        print(json.dumps(out, default=lambda o: o.tolist() if hasattr(o, "tolist") else float(o)))
        return 0
    np.savez(req["out_npz"], targets=arr, stages=np.asarray(stages))
    out.update(targets=req["out_npz"], ticks=len(arr), seconds_at_30hz=round(len(arr) / 30, 2), entry=entry_info,
               candidate={k: v for k, v in cand.items() if k in ("position", "rotation", "yaw", "width", "score",
                                                               "hover_z", "signature", "contact_prediction",
                                                               "target_source")},
               closure_targets=arr[np.asarray(stages) == "close"][-1, 8:].round(4).tolist(),
               lift=lift_fit,
               stage_ticks={s: int(sum(1 for x in stages if x == s)) for s in dict.fromkeys(stages)},
               appended_screen={k: appended_screen[k] for k in ("passed", "waypoints", "scope")}, close=close,
               scope="every tick screened by coal (the planner's checker); PhysX decides")
    print(json.dumps(out, default=lambda o: o.tolist() if hasattr(o, "tolist") else float(o)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
