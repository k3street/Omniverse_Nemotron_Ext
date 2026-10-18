"""behaviors.cap_thread: a seated cap holds with its removal torque (a share
of the torque it was applied with), turns loose on its thread's running
friction, leaves the thread past one turn, catches it again when set back
on the neck, and the torque it is closed with is what holds it next time."""
from __future__ import annotations

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from behaviors import cap_thread  # noqa: E402

KW = dict(turns=1.0, retention=0.5, run_n_m=0.02, pitch_m=0.00423)


def test_a_seated_cap_holds_with_half_its_application_torque():
    st = {}
    r = cap_thread(st, 0.0, 0.3, applied_n_m=1.0, **KW)
    assert r["on"] and r["locked"] and abs(r["friction_n_m"] - 0.5) < 1e-12 and r["event"] is None
    # turned a little under the hand, still within its seat, the hand short of the hold: still held
    assert cap_thread(st, math.radians(3), 0.45, **KW)["locked"]
    # the hand's torque reaching the hold breaks it loose where it is
    r = cap_thread(st, math.radians(3), 0.5, **KW)
    assert r["event"] == "broke_loose" and not r["locked"] and r["friction_n_m"] == 0.02


def test_broken_loose_it_turns_on_the_running_friction():
    st = {}
    cap_thread(st, 0.0, 0.3, applied_n_m=1.0, **KW)
    r = cap_thread(st, math.radians(6), 0.3, **KW)                    # turned past its seat however light the hand
    assert r["event"] == "broke_loose" and r["friction_n_m"] == 0.02
    assert cap_thread(st, math.radians(200), 0.05, **KW)["friction_n_m"] == 0.02


def test_past_one_turn_it_leaves_the_thread():
    st = {}
    cap_thread(st, math.radians(6), 0.6, applied_n_m=1.0, **KW)
    assert cap_thread(st, 2 * math.pi + math.radians(1), 0.05, **KW)["on"]          # at the thread's end, not yet past it
    r = cap_thread(st, 2 * math.pi + math.radians(2.5), 0.05, **KW)
    assert r == {"on": False, "locked": False, "friction_n_m": 0.0, "event": "off_thread"}
    # free, it stays free until it is set back on the neck: leaving the thread's end it does not catch it again
    assert not cap_thread(st, 0.0, 0.0, free_pose={"off_axis_m": 0.0005, "tilt_rad": 0.0, "height_m": 0.0045}, **KW)["on"]
    cap_thread(st, 0.0, 0.0, free_pose={"off_axis_m": 0.0005, "tilt_rad": 0.0, "height_m": 0.03}, **KW)      # lifted clear
    assert not cap_thread(st, 0.0, 0.0, free_pose={"off_axis_m": 0.01, "tilt_rad": 0.0, "height_m": 0.0045}, **KW)["on"]
    assert not cap_thread(st, 0.0, 0.0, free_pose={"off_axis_m": 0.0005, "tilt_rad": math.radians(12), "height_m": 0.0045}, **KW)["on"]


def test_set_back_on_the_neck_it_catches_and_closing_it_sets_its_hold():
    st = {"on": False, "clear": True}
    # caught whatever turn the free cap had counted (here past the end): the caller counts from the thread's end on
    r = cap_thread(st, 2 * math.pi + 0.05, 0.0, free_pose={"off_axis_m": 0.0005, "tilt_rad": 0.01, "height_m": 0.0050}, **KW)
    assert r["on"] and r["event"] == "on_thread" and r["friction_n_m"] == 0.02 and not r["locked"]
    assert cap_thread(st, 2 * math.pi, 0.0, **KW)["on"]                            # at the thread's end: still on it
    cap_thread(st, math.pi, -0.05, **KW)                              # screwed down
    r = cap_thread(st, 0.01, -0.4, **KW)                              # on its seat (its stop within a degree), closed with 0.4 N m
    assert r["event"] == "seated" and r["locked"]
    assert abs(st["applied_n_m"] - 0.4) < 1e-12 and abs(r["friction_n_m"] - 0.2) < 1e-12
    # tightened harder while still seated: the hold follows
    assert abs(cap_thread(st, -0.02, -0.7, **KW)["friction_n_m"] - 0.35) < 1e-12


def test_just_broken_loose_it_is_not_seated_again_until_it_has_left_its_seat():
    st = {}
    cap_thread(st, 0.0, 0.3, applied_n_m=1.0, **KW)
    assert cap_thread(st, math.radians(0.4), 0.6, **KW)["event"] == "broke_loose"
    r = cap_thread(st, math.radians(0.6), 0.6, **KW)                  # still within a degree of the seat, turning off it
    assert r["event"] is None and not r["locked"]
    cap_thread(st, math.radians(40), 0.05, **KW)                      # turned away
    r = cap_thread(st, math.radians(0.5), -0.3, **KW)                 # and back down onto it
    assert r["event"] == "seated" and r["locked"]
