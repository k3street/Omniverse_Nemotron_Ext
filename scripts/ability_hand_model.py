#!/usr/bin/env python3
"""The PSYONIC Ability Hand's finger actuators and API firmware, for PhysX.

What the real hand does (ability-hand-api/Documentation/ABILITY-HAND-ICD.pdf):

  * Each finger and the thumb flexor is one brushless motor driving the
    digit through a 649:1 train - a 32.45:1 planetary stage and a worm
    (ICD 3.4.3, binary setting 6). The worm is self-locking: a load on the
    finger cannot turn the motor back, so "a static force [is] maintained by
    the actuator without expending any electrical energy" (ICD 3.2.1). The
    distal joint follows the proximal one through a four-bar, q2 = 1.0585 q1
    (official URDF mimic multiplier).
  * The firmware closes a position loop on the motor's encoder and drives
    the motor with a voltage, a duty of the supply (ICD 3.4.4).
  * Halt-on-collision (ICD 3.2.1): V_stall = applied voltage - back-EMF.
    When it reaches the collision voltage (word 007, default 3.0 V) the
    digit has collided: motion in that direction is capped at the
    post-contact voltage (word 008, default 0 V: stopped) until the digit is
    commanded the other way. With word 008 at 1-2 V a small current keeps
    flowing after contact and the digit keeps closing in if the object
    shifts. "Increasing [word 007] will increase grip strength ... in API
    mode": the grip a halted digit holds is set by the threshold.
  * The thumb rotator (162.45:1, no worm) is backdrivable and is not halted
    (ICD 3.2.1); it stays on the simulator's position servo.

How each piece is realised in PhysX (replay_grasp.py applies it):

  motor    a DC motor at voltage V gives torque K (V - Ke w) at the joint:
           exactly a velocity drive with damping b = K Ke and target speed
           V / Ke, no stiffness. K = stall torque per volt, calibrated
           (config/ability_hand_calibration.json); Ke = 8.4 V / 8.068 rad/s,
           the supply over the top speed (official URDF limit = 50,000 rpm /
           649; published full close 0.2 s)
  rotor    the joint's armature, J (set so a free close takes ~0.2 s)
  worm     a ratchet: the joint limit on the side the motor is not driving
           is moved up to the digit each step, so a load cannot turn it back;
           undriven, the digit is held both ways once the rotor's momentum is
           spent (coast_s) - a load moves it neither open nor closed
  four-bar a mimic joint, q2 = c q1 (build_er_scene.py), so a load on the
           distal link reaches the motor
  pads     compliant contact on the finger links (silicone): what a halted
           digit's squeeze is stored in while the worm holds it

This module is the firmware, one call per physics step: the voltage from the
position loop, halt-on-collision and the post-contact cap, the drive's
target speed and the worm's limits. A rotor starting from rest draws a large
current too; the ICD does not say how the firmware tells that from a load,
and it plainly does (the fingers close), so a stall counts as a collision
only while the digit is not speeding up, and not within blanking_s of a
start from rest or a reversal (an assumption).

    from ability_hand_model import AbilityHandFirmware, AbilityHandParams
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
CALIBRATION = REPO / "config" / "ability_hand_calibration.json"
DIGITS = ("index", "middle", "ring", "pinky", "thumb_flexor")


def _calibrated(path: Path = CALIBRATION) -> tuple[float, ...]:
    k = json.loads(Path(path).read_text())["stall_torque_per_volt_nm"]
    return (k["finger"],) * 4 + (k["thumb_flexor"],)


@dataclass
class AbilityHandParams:
    supply_v: float = 8.4                         # ICD Table 2, typical
    gear_ratio: float = 649.0                     # ICD 3.4.3, fingers and thumb flexor
    torque_constant_nm_per_a: float = 1.49e-3     # ICD 3.4.5
    max_speed_rad_s: float = 8.0677777442         # official URDF velocity limit (50,000 rpm / 649)
    stall_torque_per_volt: tuple = field(default_factory=_calibrated)
    rotor_inertia_kg_m2: float = 1.0e-3           # J at the joint: a free 0-1.74 rad close in ~0.23 s
                                                  # (published 0.2 s; HomeHero estimated 5e-3..1e-2 from an
                                                  # assumed rotor, which would take 0.27-0.32 s)
    collision_voltage: float = 3.0                # ICD word 007 default
    postcontact_voltage: float = 0.0              # ICD word 008 default (stop after contact)
    position_gain_v_per_rad: float = 100.0        # the firmware's PID gains live on the hand (registers
                                                  # 0x1-0x3c), not published; saturates beyond ~5 deg of error
    grip_speed: float = 1.0                       # fraction of the top speed a command is slewed at
    blanking_s: float = 0.02                      # stall detection off after a start from rest or a reversal
    accel_floor_rad_s2: float = 5.0               # a digit speeding up faster than this is not stalled
    coast_s: float = 0.05                         # how long the rotor's momentum can carry a digit on once the
                                                  # drive stops (5 x J / (K Ke), the motor's time constant)
    coupling: float = 1.05851325                  # q2 = c q1 (official URDF mimic)
    hold_eps_rad: float = 1.0e-4                  # the worm's lock sits this far behind the digit

    @property
    def back_emf_v_s_per_rad(self) -> float:
        """Joint-side back-EMF constant from the top speed at full supply,
        8.4 V / 8.068 rad/s = 1.041 V s/rad, so a free digit tops out at the
        published speed and a stalled one gives the full-supply torque the
        grasp forces were calibrated at. (k_tau * N from the ICD gives
        0.967, 7 % lower: the ICD's constant is quoted per rotor amp under
        field-oriented control, the duty-cycle voltage is not the same
        quantity.)"""
        return self.supply_v / self.max_speed_rad_s

    @property
    def motor_damping(self) -> np.ndarray:
        """The velocity drive's damping, K Ke (N*m*s/rad), per digit."""
        return np.asarray(self.stall_torque_per_volt, float) * self.back_emf_v_s_per_rad

    @classmethod
    def from_spec(cls, spec: dict) -> "AbilityHandParams":
        keys = {"collision_voltage", "postcontact_voltage", "grip_speed", "position_gain_v_per_rad",
                "supply_v", "rotor_inertia_kg_m2", "blanking_s", "accel_floor_rad_s2", "coast_s"}
        kw = {k: spec[k] for k in keys if k in spec}
        if spec.get("calibration"):
            kw["stall_torque_per_volt"] = _calibrated(REPO / spec["calibration"])
        return cls(**kw)


class AbilityHandFirmware:
    """The API firmware for five worm-driven digits (index, middle, ring,
    pinky, thumb flexor), one step per physics step."""

    def __init__(self, params: AbilityHandParams, q0, lower, upper):
        self.p = params
        self.K = np.asarray(params.stall_torque_per_volt, float)
        self.Ke = params.back_emf_v_s_per_rad
        self.lower0, self.upper0 = np.asarray(lower, float), np.asarray(upper, float)
        q0 = np.asarray(q0, float)
        self.cmd = q0.copy()                              # the slewed command (a grip's motion path)
        self.lock = q0.copy()                             # the worm's position
        self.halted = np.zeros(5, int)                    # direction of the collision: +1 closing, -1 opening
        self.drive_dir = np.zeros(5, int)                 # the direction the motor last drove
        self.blank_until = np.zeros(5)
        self.prev_s = np.zeros(5, int)
        self.prev_qd = np.zeros(5)
        self.coast_until = np.zeros(5)
        self.voltage = np.zeros(5)
        self.v_stall = np.zeros(5)
        self.halts = []                                   # (t, digit, direction, speed at the halt)
        self.t = 0.0

    def step(self, dt, command, q, qd):
        """command: the five digits' API position commands (rad); q, qd: the
        digits' measured positions and speeds (rad, rad/s). Returns (target
        speed, lower limit, upper limit) for the five motor joints."""
        p = self.p
        self.t += dt
        q, qd = np.asarray(q, float), np.asarray(qd, float)
        command = np.clip(np.asarray(command, float), self.lower0, self.upper0)
        vmax = p.max_speed_rad_s * p.grip_speed
        self.cmd += np.clip(command - self.cmd, -vmax * dt, vmax * dt)
        V = np.clip(p.position_gain_v_per_rad * (self.cmd - q), -p.supply_v, p.supply_v)
        s = np.sign(V).astype(int)
        # halt-on-collision: a reversal clears it; the collided direction is capped
        clear = (self.halted != 0) & (s == -self.halted)
        self.halted[clear] = 0
        capped = (self.halted != 0) & (s == self.halted)
        V = np.where(capped, s * np.minimum(np.abs(V), p.postcontact_voltage), V)
        s = np.sign(V).astype(int)
        # a start from rest or a reversal opens the blanking window
        starting = (s != 0) & (s != self.prev_s)
        self.blank_until = np.where(starting, self.t + p.blanking_s, self.blank_until)
        # the firmware's stall measure: the voltage the winding resistance drops.
        # A rotor speeding up draws it too; a collision is a stall that is not that
        v_stall = s * (V - self.Ke * qd)
        speeding = s * (qd - self.prev_qd) > p.accel_floor_rad_s2 * dt
        new = ((self.halted == 0) & (s != 0) & (v_stall >= p.collision_voltage)
               & (self.t >= self.blank_until) & ~speeding)
        for i in np.flatnonzero(new):
            self.halts.append((round(self.t, 4), DIGITS[i], int(s[i]), round(float(qd[i]), 3)))
        self.halted[new] = s[new]
        V = np.where(new, s * np.minimum(np.abs(V), p.postcontact_voltage), V)
        s = np.sign(V).astype(int)
        # the motor: a velocity drive at V / Ke (top speed capped)
        target = np.clip(V / self.Ke, -p.max_speed_rad_s, p.max_speed_rad_s)
        # the worm: it turns only as the motor turns it. Driven one way, the
        # other side is held at the digit; undriven, it is held where the
        # digit stops, coasting on until then
        # the rotor's momentum carries a digit on, the way the motor last drove
        # it, for a moment after the drive stops and only while slowing: a
        # load never turns the worm, either way
        self.coast_until = np.where((s == 0) & (self.prev_s != 0), self.t + p.coast_s, self.coast_until)
        moving = np.where(np.abs(qd) > 1e-3, np.sign(qd), 0).astype(int)
        coasting = ((moving == self.drive_dir) & (self.t < self.coast_until)
                    & (np.abs(qd) <= np.abs(self.prev_qd) + p.accel_floor_rad_s2 * dt))
        r = np.where(s != 0, s, np.where(coasting, moving, 0))
        self.lock = np.where(r > 0, np.maximum(self.lock, q),
                             np.where(r < 0, np.minimum(self.lock, q), self.lock))
        lo = np.where(r >= 0, np.maximum(self.lower0, self.lock - p.hold_eps_rad), self.lower0)
        hi = np.where(r <= 0, np.minimum(self.upper0, self.lock + p.hold_eps_rad), self.upper0)
        # an undriven, stopped digit is held both ways where it is
        hi = np.where(r == 0, np.minimum(self.upper0, self.lock + p.hold_eps_rad), hi)
        lo = np.where(r == 0, np.maximum(self.lower0, self.lock - p.hold_eps_rad), lo)
        self.voltage, self.v_stall = V, v_stall
        self.prev_s, self.prev_qd = s, qd
        self.drive_dir = np.where(s != 0, s, self.drive_dir)
        return target, lo, hi

    def reset_lock(self, q):
        self.lock = np.asarray(q, float).copy()

    def grip_complete(self, q, qd, tol=0.02, still=0.05) -> bool:
        """Every digit has collided or reached its command and stopped (the
        hand's 'grip completion' byte)."""
        done = (self.halted != 0) | ((np.abs(self.cmd - np.asarray(q)) < tol) & (np.abs(np.asarray(qd)) < still))
        return bool(np.all(done))

    def status(self) -> dict:
        return {"voltage_v": self.voltage.round(3).tolist(), "v_stall_v": self.v_stall.round(3).tolist(),
                "halted": self.halted.tolist(), "lock_rad": self.lock.round(4).tolist(),
                "cmd_rad": self.cmd.round(4).tolist()}
