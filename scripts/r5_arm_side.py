#!/usr/bin/env python3
"""HomeHero's planner geometry for either arm of the R5. prepare_aida_r5_transfer
.Geometry is the LEFT arm (its ARM/HAND/PALM are module constants and pose()'s
default link is the left palm); the right arm is its mirror in the same URDF.
This builds the same object for a chosen side: the joint indices from that
side's names, the rest from navigation_rest.json, pose() at that side's palm.

    from r5_arm_side import side_geometry
    g = side_geometry("right", urdf, base_pose)      # the same interface the pinch planner uses: forward/pose/solve/bounds/rest
"""
from __future__ import annotations

import sys
from pathlib import Path

HOMEHERO = Path("~/Documents/Github/HomeHero").expanduser()
for sub in ("src/homehero_core", "scripts/rl_teacher", "scripts", ""):      # homehero_core, the planner scripts
    p = str(HOMEHERO / sub)
    if p not in sys.path:
        sys.path.insert(0, p)


def side_names(side: str):
    from homehero_core.aida_can_baseline import ARM_NAMES
    from homehero_core.psyonic_hand_coupling import sim_joint_names
    arm = tuple(n.replace("right", side).replace("left", side) for n in ARM_NAMES)
    return arm, tuple(sim_joint_names(side)), f"psyonic_{side}_base"


def side_geometry(side: str, urdf, base, posture="free"):
    import prepare_aida_r5_transfer as pat

    arm, hand, palm = side_names(side)

    class SideGeometry(pat.Geometry):
        def __init__(self, urdf, base, posture):
            saved = (pat.ARM, pat.HAND, pat.PALM)
            pat.ARM, pat.HAND, pat.PALM = arm, hand, palm      # the constructor reads the module constants
            try:
                super().__init__(urdf, base, posture)
            finally:
                pat.ARM, pat.HAND, pat.PALM = saved
            self.side, self.arm_names, self.hand_names, self.palm = side, arm, hand, palm

        def pose(self, link=None):
            return super().pose(link or self.palm)

    return SideGeometry(urdf, base, posture)


def both_arms_geometry(urdf, base, posture="free"):
    """HomeHero's Geometry over BOTH arms: forward(q28) sets the left arm and
    hand (q[0:14]) and the right arm and hand (q[14:28]); pose(link) any
    frame; solve_right fits the right arm's eight joints to put a point of a
    right-hand link (a fingertip) at a target, the left arm held as given."""
    import numpy as np
    import pinocchio as pin
    from scipy.optimize import least_squares
    from scipy.spatial.transform import Rotation
    import prepare_aida_r5_transfer as pat
    from homehero_core.psyonic_hand_coupling import expand_actuated_to_sim

    left = side_geometry("left", urdf, base, posture)
    right = side_geometry("right", urdf, base, posture)

    class BothArms:
        def __init__(self):
            self.left, self.right = left, right
            self.model, self.data = left.model, left.data
            self.q0 = left.q0.copy()
            for n, v in zip(right.arm_names, right.rest[:8]):             # the right arm at its rest too
                self.q0[self.model.joints[self.model.getJointId(n)].idx_q] = v
            self.rotation, self.translation = left.rotation, left.translation
            self.bounds = np.vstack([left.bounds, right.bounds])             # 16 x 2
            self.rest = np.r_[left.rest, right.rest]                         # 28

        def forward(self, q):
            q = np.asarray(q, float)
            values = self.q0.copy()
            values[left.indices] = np.r_[q[:8], expand_actuated_to_sim(q[8:14])]
            values[right.indices] = np.r_[q[14:22], expand_actuated_to_sim(q[22:28])]
            pin.framesForwardKinematics(self.model, self.data, values)

        def pose(self, link):
            tf = self.data.oMf[self.model.getFrameId(link)]
            return self.rotation @ tf.translation + self.translation, self.rotation @ tf.rotation

        def point(self, link, offset):
            """A point fixed in a link (its tip, in the link's frame), in the world."""
            p, R = self.pose(link)
            return p + R @ np.asarray(offset, float)

        def solve_right(self, seed, target, link, offset, R_pref=None, w_rot=0.3):
            """The right arm's joints that put `offset` of `link` at `target`;
            R_pref, if given, is a soft orientation preference for the link."""
            lo, hi = self.bounds[8:, 0] + 1e-8, self.bounds[8:, 1] - 1e-8
            seed = np.asarray(seed, float).copy()
            seed[14:22] = np.clip(seed[14:22], lo, hi)

            def residual(arm):
                q = seed.copy(); q[14:22] = arm
                self.forward(q)
                p, R = self.pose(link)
                r = [(p + R @ np.asarray(offset, float) - target) * 10]
                if R_pref is not None:
                    r.append(Rotation.from_matrix(R_pref.T @ R).as_rotvec() * w_rot)
                r.append((arm - seed[14:22]) * 1e-5)
                return np.concatenate(r)

            res = least_squares(residual, seed[14:22], bounds=(lo, hi), max_nfev=250, xtol=1e-10, ftol=1e-10, gtol=1e-10)
            q = seed.copy(); q[14:22] = res.x
            self.forward(q)
            p, R = self.pose(link)
            err = float(np.linalg.norm(p + R @ np.asarray(offset, float) - target))
            return q, {"position_error_m": err, "passed": err < 0.002}

    return BothArms()


RIGHT_LINKS = ("amber_right_arm_link_", "amber_right_shoulder_lift_link", "psyonic_right_")
LEFT_LINKS = ("amber_left_arm_link_", "amber_left_shoulder_lift_link", "psyonic_left_")


def set_active(checker, prefixes=RIGHT_LINKS) -> int:
    """Make the links named by `prefixes` the Checker's active set and rebuild
    its self-collision pairs for them, by the Checker's own rule (every
    active shape against every shape of a non-adjacent link group, fixed
    joints merged). HomeHero's Checker builds `pairs` ONCE in its constructor
    for the left arm; assigning `checker.active` afterwards checked the right
    arm against the room's obstacles only, never against the robot's own
    body, and the push plan sent the right upper arm through the torso's
    casing (cad_part_collision_049), where PhysX stopped the shoulder roll at
    0 rad (2026-10-15). Returns the number of pairs."""
    import xml.etree.ElementTree as ET

    import numpy as np

    tree = ET.parse(checker.manifest["physics"]["urdf"]).getroot()
    parents = {x.get("name"): x.get("name") for x in tree.findall("link")}

    def group(n):
        while parents[n] != n:
            n = parents[n]
        return n

    for j in tree.findall("joint"):
        if j.get("type") == "fixed":
            parents[group(j.find("child").get("link"))] = group(j.find("parent").get("link"))
    adjacent = {frozenset((group(j.find("child").get("link")), group(j.find("parent").get("link")))) for j in tree.findall("joint")}
    links = [x["link"] for x in checker.shapes]
    active = [i for i, n in enumerate(links) if n.startswith(tuple(prefixes))]
    active_set = set(active)
    pairs = []
    for i in active:
        for j in range(len(links)):
            if j in active_set and j <= i:
                continue
            if group(links[i]) == group(links[j]) or frozenset((group(links[i]), group(links[j]))) in adjacent:
                continue
            pairs.append((i, j))
    checker.active = np.array(active, dtype=int)
    checker.pairs = np.array(pairs, dtype=int).reshape(-1, 2)
    return len(pairs)
