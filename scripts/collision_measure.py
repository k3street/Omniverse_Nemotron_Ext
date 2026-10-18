#!/usr/bin/env python3
"""The robot's collisions in a take, for HomeHero's collision_violation_count:
from each control tick's contacts between the robot's arm and hand links and
anything that is neither the robot nor the target (a bench, a bin's wall,
another object), the ticks it was touching something and the episodes - a
partner touched after a tick it was not. PhysX's report is read once a tick:
a touch that begins and ends between two reads is not seen."""
from __future__ import annotations


def partners(pairs, robot_root: str = "/World/Robot", target_paths=()) -> list[str]:
    """The non-robot, non-target actors among (actor0, actor1) path pairs that a
    robot link is in: each partner once, sorted."""
    out = set()
    tgt = tuple(target_paths)
    for a, b in pairs:
        for robot, other in ((a, b), (b, a)):
            if robot.startswith(robot_root + "/") and not other.startswith(robot_root + "/"):
                if not any(other == t or other.startswith(t + "/") for t in tgt):
                    out.add(other)
    return sorted(out)


def tally(ticks: list[list[str]]) -> dict:
    """{touch_ticks, episodes, by_partner: {partner: episodes}} from each tick's partner list."""
    by, prev, touch = {}, set(), 0
    for now in ticks:
        now = set(now)
        if now:
            touch += 1
        for p in now - prev:
            by[p] = by.get(p, 0) + 1
        prev = now
    return {"touch_ticks": touch, "episodes": sum(by.values()), "by_partner": dict(sorted(by.items(), key=lambda kv: -kv[1]))}


def contact_torque(normal_forces, points, normals, counts, starts, friction_forces, friction_points, friction_counts,
                   friction_starts, centre, axis) -> float:
    """The torque about an axis through `centre` (N m, right-handed about `axis`) that a sensor body's contacts put on
    it, from a tensor RigidContactView's get_contact_data and get_friction_data: each normal force (magnitude x normal)
    and each friction force at its point. The forces' sense - on the sensor - is proved on the rig (rig_pill_kit
    torque_sign: a finger dragging the cap anticlockwise reads positive)."""
    import numpy as np
    c, a = np.asarray(centre, float), np.asarray(axis, float)
    tq = 0.0
    sets = ((np.asarray(normal_forces, float).reshape(-1, 1) * np.asarray(normals, float).reshape(-1, 3), np.asarray(points, float).reshape(-1, 3),
             np.asarray(counts).reshape(-1), np.asarray(starts).reshape(-1)),
            (np.asarray(friction_forces, float).reshape(-1, 3), np.asarray(friction_points, float).reshape(-1, 3),
             np.asarray(friction_counts).reshape(-1), np.asarray(friction_starts).reshape(-1)))
    for F, P, C_, S_ in sets:
        for n, s0 in zip(C_, S_):
            for i in range(int(s0), int(s0) + int(n)):
                tq += float(np.cross(P[i] - c, F[i]) @ a)
    return tq
