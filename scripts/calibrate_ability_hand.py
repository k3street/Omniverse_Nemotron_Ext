#!/usr/bin/env python3
"""Calibrate the Ability Hand motor model's stall torque per volt from the
hand's published grasp forces and the R5 hand's own geometry.

The interface document (ability-hand-api/Documentation/ABILITY-HAND-ICD.pdf)
gives the gear ratio (649:1 fingers and thumb flexor) and the motor torque
constant (1.49 mNm/A); the official URDF gives the top speed (8.068 rad/s at
the joint = 50,000 rpm at the rotor). Neither gives the winding resistance or
the gear train's efficiency, which together set how much torque a volt buys
at stall: K = N * efficiency * k_tau / R (joint N*m per motor volt). K is
taken here from the published grasp forces for the Robotics Edition (Knox
Labs: pinch 8 N, key 17 N), assumed to be full-supply (8.4 V) stalls:

    K_finger = F_pinch * lever_index / 8.4 V
    K_thumb  = F_key   * lever_thumb / 8.4 V

lever = |d(fingertip)/d(q1)| with the distal joint coupled (q2 = 1.0585 q1),
the generalised lever a fingertip force acts through on the motor. The
fingertip is the distal link's collision point farthest from its joint, from
HomeHero's R5 collision cache; kinematics by pinocchio on the R5 URDF.

    <HomeHero>/.venv/bin/python scripts/calibrate_ability_hand.py > config/ability_hand_calibration.json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pinocchio as pin

HH = Path("/home/kimate/Documents/Github/HomeHero")
ROBOT = HH / "artifacts/isaac/aida_r5_20261004/reference_learning_v1/robot.json"
CACHE = HH / "artifacts/isaac/aida_r5_20261004/physics_v7_joint_filters/planning_collision"
COUPLING = 1.05851325            # official URDF mimic multiplier, q2 = c * q1
V_SUPPLY = 8.4                   # ICD Table 2, typical
PUBLISHED = {"pinch_n": 8.0, "key_n": 17.0, "power_n": 78.0,
             "source": "Knox Labs, PSYONIC Ability Hand Robotics Edition product page (2026-10-14)"}
POSTURE_Q1 = 0.75                # the lever is read at mid-flexion; the spread over 0.3-1.2 rad is reported


def main() -> int:
    robot = json.loads(ROBOT.read_text())
    model = pin.buildModelFromUrdf(robot["physics"]["urdf"])
    data = model.createData()
    manifest = json.loads(CACHE.with_suffix(".json").read_text())
    pts = np.load(CACHE.with_suffix(".npz"))

    def tip_point(link):
        cloud = np.concatenate([pts[f"p{i}"] for i, s in enumerate(manifest["shapes"]) if s["link"] == link])
        return cloud[np.argmax(np.linalg.norm(cloud, axis=1))]

    def lever(digit, q1):
        j1 = model.joints[model.getJointId(f"psyonic_left_{digit}_q1")].idx_q
        j2 = model.joints[model.getJointId(f"psyonic_left_{digit}_q2")].idx_q
        frame = model.getFrameId(f"psyonic_left_{digit}_L2")
        tip = tip_point(f"psyonic_left_{digit}_L2")

        def p(s):
            q = pin.neutral(model)
            q[j1], q[j2] = s, COUPLING * s
            pin.framesForwardKinematics(model, data, q)
            return data.oMf[frame].act(tip)
        e = 1e-5
        return float(np.linalg.norm(p(q1 + e) - p(q1 - e)) / (2 * e))

    def thumb_lever(q2):
        j2 = model.joints[model.getJointId("psyonic_left_thumb_q2")].idx_q
        frame = model.getFrameId("psyonic_left_thumb_L2")
        tip = tip_point("psyonic_left_thumb_L2")

        def p(s):
            q = pin.neutral(model)
            q[j2] = s
            pin.framesForwardKinematics(model, data, q)
            return data.oMf[frame].act(tip)
        e = 1e-5
        return float(np.linalg.norm(p(q2 + e) - p(q2 - e)) / (2 * e))

    sweep = np.round(np.linspace(0.3, 1.2, 10), 2)
    levers = {d: [round(lever(d, s), 4) for s in sweep] for d in ("index", "middle", "ring", "pinky")}
    levers["thumb"] = [round(thumb_lever(s), 4) for s in sweep]
    li, lt = lever("index", POSTURE_Q1), thumb_lever(POSTURE_Q1)
    k_finger = PUBLISHED["pinch_n"] * li / V_SUPPLY
    k_thumb = PUBLISHED["key_n"] * lt / V_SUPPLY
    # cross-check: a power grasp with all five digits stalled at full supply,
    # each pressing at mid-finger (half the fingertip lever)
    power = sum(k_finger * V_SUPPLY / (0.5 * lever(d, POSTURE_Q1)) for d in ("index", "middle", "ring", "pinky")) \
        + k_thumb * V_SUPPLY / (0.5 * lt)
    out = {"stall_torque_per_volt_nm": {"finger": round(k_finger, 5), "thumb_flexor": round(k_thumb, 5)},
           "lever_m_at_q1": {"q1_rad": POSTURE_Q1, "index": round(li, 4), "thumb_flexor": round(lt, 4)},
           "lever_sweep_m": {"q1_rad": sweep.tolist(), **levers},
           "published_forces": PUBLISHED, "supply_v": V_SUPPLY, "coupling": COUPLING,
           "power_grasp_check_n": {"model_mid_finger_contacts": round(power, 1), "published": PUBLISHED["power_n"]},
           "fingertip": "distal-link collision point farthest from its joint (HomeHero R5 collision cache)",
           "assumption": "the published grasp forces are full-supply stalls"}
    json.dump(out, sys.stdout, indent=1)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
