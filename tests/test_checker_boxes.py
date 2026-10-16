"""Obstacle boxes taken out of a checker for a stage and put back (checker_boxes.py)."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from checker_boxes import drop_boxes, restore_boxes  # noqa: E402


class Stub:
    def __init__(self, ids):
        self.boxes = [(i, None, None, np.array([k, 0, 0.0]), np.array([k + 1, 1, 1.0])) for k, i in enumerate(ids)]
        self.box_lows = np.array([b[3] for b in self.boxes])
        self.box_highs = np.array([b[4] for b in self.boxes])


def test_cloth_cells_leave_and_return_in_place():
    c = Stub(["table:slab0", "cloth:1_2_3", "table:under0", "cloth:4_5_6"])
    assert drop_boxes(c, "cloth:") == 2
    assert [b[0] for b in c.boxes] == ["table:slab0", "table:under0"]
    assert c.box_lows.shape == (2, 3) and c.box_highs[1][0] == 3.0
    assert drop_boxes(c, "cloth:") == 0                      # idempotent
    assert restore_boxes(c) == 2
    assert sorted(b[0] for b in c.boxes) == ["cloth:1_2_3", "cloth:4_5_6", "table:slab0", "table:under0"]
    assert c.box_lows.shape == (4, 3)
    assert restore_boxes(c) == 0


def test_nothing_to_drop_leaves_the_arrays_alone():
    c = Stub(["table:slab0"])
    lows = c.box_lows.copy()
    assert drop_boxes(c, "cloth:") == 0 and np.array_equal(c.box_lows, lows)
