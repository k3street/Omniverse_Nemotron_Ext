"""The Ability Hand firmware, on a bench finger, does what the ICD says the
hand does.

The bench stands in for PhysX the way replay_grasp.py sets it up: each digit
a rotor (armature J) on a velocity drive (damping K Ke, target V / Ke), its
limits the worm's ratchet, meeting an object through a compliant pad -
integrated far finer than the firmware's 480 Hz."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from ability_hand_model import AbilityHandFirmware, AbilityHandParams  # noqa: E402

DT = 1.0 / 480
PAD_K = 10.0            # joint-side pad stiffness, N*m/rad
PAD_D = 0.05


class Bench:
    def __init__(self, p: AbilityHandParams, q_c=(9.0,) * 5):
        self.p = p
        self.J = p.rotor_inertia_kg_m2
        self.b = p.motor_damping
        self.q_c = np.asarray(q_c, float)
        self.q = np.zeros(5)
        self.qd = np.zeros(5)
        self.tau_ext = np.zeros(5)
        self.contact = np.zeros(5)

    def integrate(self, v_t, lo, hi, n=40):
        h = DT / n
        for _ in range(n):
            pen = self.q - self.q_c
            tau_c = np.where(pen > 0, PAD_K * pen + PAD_D * self.qd, 0.0)
            tau_c = np.maximum(tau_c, 0.0)
            self.qd = (self.J * self.qd + h * (self.b * v_t - tau_c + self.tau_ext)) / (self.J + h * self.b)
            self.q = self.q + h * self.qd
            low, high = self.q < lo, self.q > hi
            self.q = np.clip(self.q, lo, hi)
            self.qd = np.where(low, np.maximum(self.qd, 0), np.where(high, np.minimum(self.qd, 0), self.qd))
            self.contact = tau_c


def make(**kw):
    p = AbilityHandParams(**kw)
    fw = AbilityHandFirmware(p, np.zeros(5), np.zeros(5), np.full(5, 1.74))
    return fw, p


def run(fw, bench, command, seconds):
    for _ in range(int(round(seconds / DT))):
        v_t, lo, hi = fw.step(DT, command, bench.q, bench.qd)
        bench.integrate(v_t, lo, hi)


def test_a_free_close_takes_the_published_time():
    fw, p = make()
    bench = Bench(p)
    t = 0.0
    while bench.q[0] < 1.55 and t < 1.0:
        run(fw, bench, np.full(5, 1.6), DT)
        t += DT
    assert 0.18 <= t <= 0.28        # 0-1.55 rad; PSYONIC: 0.2 s full open to full close (1.74 rad)
    run(fw, bench, np.full(5, 1.6), 0.5)
    assert not fw.halted.any()      # reaching a command in free air is not a collision
    np.testing.assert_allclose(bench.q, 1.6, atol=0.02)


def test_a_slow_grip_halts_at_the_collision_voltage_and_holds_without_power():
    fw, p = make(grip_speed=0.05)
    bench = Bench(p, q_c=(0.6,) * 5)
    run(fw, bench, np.full(5, 1.74), 3.0)
    assert (fw.halted == 1).all()
    np.testing.assert_allclose(bench.contact, fw.K * 3.0, rtol=0.15)   # the squeeze at the threshold
    q, f = bench.q.copy(), bench.contact.copy()
    run(fw, bench, np.full(5, 1.74), 2.0)                               # still commanded closed, 0 V after contact
    np.testing.assert_allclose(bench.q, q, atol=2e-4)                    # the worm holds: no creep
    np.testing.assert_allclose(bench.contact, f, rtol=0.02)             # and the force with it
    assert (fw.voltage == 0).all()                                       # with no power


def test_a_fast_grip_locks_in_at_least_the_threshold():
    fw, p = make()
    bench = Bench(p, q_c=(0.6,) * 5)
    run(fw, bench, np.full(5, 1.74), 1.0)
    assert (fw.halted == 1).all()
    assert (bench.contact >= fw.K * 3.0 * 0.9).all()                     # the rotor's momentum adds to it
    q = bench.q.copy()
    run(fw, bench, np.full(5, 1.74), 1.0)
    np.testing.assert_allclose(bench.q, q, atol=2e-4)


def test_a_load_cannot_backdrive_the_worm():
    fw, p = make(grip_speed=0.05)
    bench = Bench(p, q_c=(0.6,) * 5)
    run(fw, bench, np.full(5, 1.74), 3.0)
    q = bench.q.copy()
    bench.tau_ext[:] = -2.0                                              # pull the fingers open, 2 N*m
    run(fw, bench, np.full(5, 1.74), 1.0)
    assert (bench.q >= q - 2e-4).all()


def test_an_undriven_digit_is_not_pulled_closed_by_a_load():
    fw, p = make(grip_speed=0.05)                                       # post-contact 0 V: undriven after the halt
    bench = Bench(p, q_c=(0.6,) * 5)
    run(fw, bench, np.full(5, 1.74), 3.0)
    q = bench.q.copy()
    bench.tau_ext[:] = +2.0                                              # something drags the fingers closed
    run(fw, bench, np.full(5, 1.74), 1.0)
    assert (bench.q <= q + 2e-4).all()                                   # the worm holds that way too


def test_the_postcontact_voltage_closes_in_when_the_object_shifts():
    for vpc, closes in ((0.0, False), (1.5, True)):
        fw, p = make(postcontact_voltage=vpc, grip_speed=0.05)
        bench = Bench(p, q_c=(0.6,) * 5)
        run(fw, bench, np.full(5, 1.74), 3.0)
        q = bench.q.copy()
        bench.q_c[:] = 0.7                                               # the object slips away from the fingers
        run(fw, bench, np.full(5, 1.74), 2.0)
        if closes:
            assert (bench.q > q + 0.05).all()                            # the fingers follow it
            np.testing.assert_allclose(bench.contact, fw.K * 1.5, rtol=0.1)   # squeezing at the post-contact voltage
        else:
            np.testing.assert_allclose(bench.q, q, atol=2e-4)
            assert (bench.contact == 0).all()                            # the stored squeeze is gone


def test_a_reversal_clears_the_halt():
    fw, p = make()
    bench = Bench(p, q_c=(0.6,) * 5)
    run(fw, bench, np.full(5, 1.74), 1.0)
    run(fw, bench, np.full(5, 0.2), 1.0)
    assert not (fw.halted == 1).any()
    np.testing.assert_allclose(bench.q, 0.2, atol=0.02)


def test_without_the_halt_a_slow_stall_is_the_full_supply_torque():
    fw, p = make(collision_voltage=100.0, grip_speed=0.05)
    bench = Bench(p, q_c=(0.6,) * 5)
    run(fw, bench, np.full(5, 1.74), 4.0)
    assert not fw.halted.any()
    np.testing.assert_allclose(bench.contact, fw.K * 8.4, rtol=0.05)   # the published grasp forces' torque


def test_the_constants_come_from_the_hand():
    p = AbilityHandParams()
    assert p.stall_torque_per_volt[4] != p.stall_torque_per_volt[0]     # the thumb flexor's own
    assert p.supply_v / p.back_emf_v_s_per_rad == pytest.approx(8.0677777442)
    assert p.torque_constant_nm_per_a * p.gear_ratio == pytest.approx(0.967, rel=1e-3)
