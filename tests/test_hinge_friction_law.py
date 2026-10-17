"""behaviors.hinge_friction: a hinge's friction holds a door against any
smaller torque and resists its turning with its full torque once it moves."""
from __future__ import annotations

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from behaviors import hinge_friction  # noqa: E402


def test_friction_opposes_the_turning_with_its_full_torque_once_moving():
    assert hinge_friction(math.radians(30.0), 0.1) < -0.0999
    assert hinge_friction(-math.radians(30.0), 0.1) > 0.0999
    assert hinge_friction(0.0, 0.1) == 0.0


def test_a_small_torque_only_creeps_the_door():
    # the settled microwave's hinge 0.08 deg off vertical: gravity's 0.005 N m on its 1.6 kg door
    load, torque, slip = 0.005, 0.1, math.radians(0.5)
    creep = slip * math.atanh(load / torque)                                  # where friction balances the load
    assert abs(hinge_friction(creep, torque, slip) + load) < 1e-9
    assert math.degrees(creep) * 28.0 < 1.0                                   # under a degree in the 28 s it crept 76 deg on damping alone


def test_a_door_released_at_speed_coasts_as_coulomb_friction_predicts():
    # 1.6 kg door, I ~ 0.118 kg m^2, released at 30 deg/s: it stops in w^2 / (2 T / I), ~9 deg
    I, torque, w0, dt = 0.118, 0.1, math.radians(30.0), 1 / 480
    w, a = w0, 0.0
    while w > 1e-4:
        w += hinge_friction(w, torque) / I * dt
        a += w * dt
    coast = w0 * w0 / (2 * torque / I)
    assert abs(a - coast) < 0.15 * coast
