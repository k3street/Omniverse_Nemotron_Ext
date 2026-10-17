"""A button-released door (behaviors.latch_kick): held by its latch until the
button trips it, kicked ajar, re-hooked only after it has opened."""
from __future__ import annotations

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from behaviors import latch_kick  # noqa: E402

KW = dict(trip_m=0.006, latch_torque_n_m=30.0, kick_torque_n_m=0.6, kick_end_rad=math.radians(10))


def test_the_latch_holds_the_door_until_the_button_trips_it():
    st = {}
    assert abs(latch_kick(math.radians(0.25), 0.0, st, **KW) + 15.0) < 1e-9   # pulled half way through the catch's play
    assert latch_kick(math.radians(2.0), 0.005, st, **KW) == -30.0            # pulled hard, the button short of its trip
    assert st["latched"] is True
    t = latch_kick(0.0, 0.007, st, **KW)                                      # the button past its trip: kicked
    assert st["latched"] is False and abs(t - 0.6) < 1e-12
    assert abs(latch_kick(math.radians(5), 0.007, st, **KW) - 0.3) < 1e-12    # half way to the kick's end
    assert latch_kick(math.radians(12), 0.0, st, **KW) == 0.0                 # past it: free


def test_it_latches_again_only_after_it_has_opened():
    st = {}
    latch_kick(0.0, 0.008, st, **KW)                                          # released at 0 deg
    latch_kick(math.radians(0.5), 0.0, st, **KW)                              # the finger off, the door barely moving
    assert st["latched"] is False                                             # not re-hooked on the way out
    latch_kick(math.radians(20), 0.0, st, **KW)                               # opened
    latch_kick(math.radians(0.5), 0.0, st, **KW)                              # pushed back shut
    assert st["latched"] is True
