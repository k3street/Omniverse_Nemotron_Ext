#!/usr/bin/env python3
"""Did the hand open the door? From a replay's push rows (the door's angle
and the hand's contact force each tick): the door at least its least angle,
turned by at least half the planned push, and turned only while the hand was
on it. A force floor is the wrong test for a light door: a microwave's door
on 0.1 N m of hinge friction takes ~0.24 N at the hand, and its push read
0.14 N mean (0.19 N in contact) while every tick it turned had the hand on
it (2026-10-16) - and a door that crept open by itself on a tilted hinge
turned with no contact at all."""
from __future__ import annotations

import numpy as np


def hand_drove_door(degs, forces_n, least_deg, planned_push_deg, contact_n=0.02, turn_deg=0.005, with_hand=0.9):
    """degs, forces_n: per tick of the push. Returns the measures and 'opened_by_hand'."""
    d = np.asarray(degs, float)
    f = np.asarray(forces_n, float)
    if len(d) < 2:
        return {"opened_by_hand": False, "pushed_by_hand_deg": 0.0, "turning_with_hand_fraction": None}
    turning = np.r_[False, np.diff(d) > turn_deg]
    frac = float((f[turning] > contact_n).mean()) if turning.any() else 0.0
    pushed = float(d[-1] - d[0])
    ok = bool(float(d.max()) >= least_deg and pushed >= 0.5 * planned_push_deg and frac >= with_hand)
    return {"opened_by_hand": ok, "pushed_by_hand_deg": round(pushed, 1), "turning_with_hand_fraction": round(frac, 3),
            "hand_n_while_turning": round(float(f[turning].mean()), 3) if turning.any() else None}
