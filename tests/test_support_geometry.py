"""The support as boxes (support_geometry.py): the slab, and a box per leg panel."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from support_geometry import densify_top, support_boxes  # noqa: E402

TOP = 0.708


def _table():
    # a slab top y 0.549..1.374 at z 0.708 (underside 0.687); leg columns at x 4.52-4.60 and 5.34-5.42 that run
    # flush to the top and stand proud of the slab's edge, out to y 0.523 (the dining table)
    ys, xs = np.linspace(0.549, 1.374, 30), np.linspace(4.14, 5.81, 40)
    slab = [[x, y, z] for x in xs for y in ys for z in (0.687, TOP)]
    legs = [[x, y, z] for x0 in (4.52, 5.34) for x in np.linspace(x0, x0 + 0.08, 4)
            for y in np.linspace(0.523, 1.374, 20) for z in list(np.linspace(0.0, 0.687, 8)) + [TOP]]
    return slab + legs


def test_slab_bands_follow_the_top_and_the_legs_stop_at_its_underside():
    boxes = support_boxes(_table(), TOP, band_m=0.1)
    slabs = [b for b in boxes if b["id"].startswith("table:slab")]
    legs = [b for b in boxes if b["id"].startswith("table:under")]
    assert len(slabs) >= 15 and len(legs) == 2
    assert all(b["min"][2] == pytest.approx(0.687) and b["max"][2] == pytest.approx(TOP) for b in slabs)
    at = lambda x: next(b for b in slabs if b["min"][0] - 1e-9 <= x <= b["max"][0] + 1e-9)
    assert at(4.95)["min"][1] == pytest.approx(0.549)                # the slab's edge at the towel
    assert at(4.55)["min"][1] == pytest.approx(0.523)                # the leg column's top, proud of it
    assert legs[0]["min"][0] == pytest.approx(4.52) and legs[0]["max"][0] == pytest.approx(4.60)
    assert legs[1]["min"][0] == pytest.approx(5.34) and legs[1]["max"][0] == pytest.approx(5.42)
    assert all(b["max"][2] < 0.687 for b in legs)                     # the legs stop at the slab's underside
    assert all(b["min"][1] == pytest.approx(0.523) for b in legs)     # and protrude past its edge


def test_the_space_under_the_slab_between_the_legs_is_free():
    boxes = support_boxes(_table(), TOP)
    x, y, z = 4.95, 0.56, 0.65                                        # under the slab edge, between the panels
    inside = [b for b in boxes if all(b["min"][i] <= (x, y, z)[i] <= b["max"][i] for i in range(3))]
    assert inside == []


def test_a_slab_that_is_one_quad_has_no_holes_between_its_vertices():
    pts = [[x, y, z] for x in (0.0, 1.0) for y in (0.0, 1.0) for z in (0.68, TOP)]
    boxes = support_boxes(pts, TOP)
    assert [b["id"] for b in boxes] == [f"table:slab{k}" for k in range(10)]
    assert all(b["min"][1] == pytest.approx(0.0) and b["max"][1] == pytest.approx(1.0) for b in boxes)


def test_a_top_face_is_sampled_between_its_vertices():
    pts = [[0, 0, TOP], [1, 0, TOP], [1, 1, TOP], [0, 1, TOP], [0, 0, 0.0], [1, 0, 0.0]]
    dense = densify_top(pts, [4, 3], [0, 1, 2, 3, 0, 1, 5], TOP, spacing=0.1)
    inside = dense[(dense[:, 0] > 0.4) & (dense[:, 0] < 0.6)]
    assert len(inside) > 20 and np.all(inside[:, 2] == TOP)          # the top quad is sampled through its middle
    assert not np.any((dense[:, 2] > 0.0) & (dense[:, 2] < TOP))     # the side face is not


def test_bands_on_a_sampled_quad_have_no_holes():
    pts = [[0, 0, TOP], [1, 0, TOP], [1, 1, TOP], [0, 1, TOP], [0, 0, 0.68], [1, 0, 0.68], [1, 1, 0.68], [0, 1, 0.68]]
    dense = densify_top(pts, [4], [0, 1, 2, 3], TOP, spacing=0.02)
    boxes = support_boxes(dense, TOP)
    assert len(boxes) == 10 and all(b["min"][1] == pytest.approx(0.0) and b["max"][1] == pytest.approx(1.0) for b in boxes)


def test_a_box_fixture_is_sampled_over_its_top():
    from support_geometry import box_samples
    pts = box_samples([6.27, 0.5, 0.95], [6.33, 1.4, 0.98], spacing=0.02)
    top = pts[pts[:, 2] == 0.98]
    assert len(top) > 100 and top[:, 1].min() == 0.5 and top[:, 1].max() == 1.4
    boxes = support_boxes(pts, 0.98)
    assert [b["id"] for b in boxes][0] == "table:slab0" and all(b["id"].startswith("table:slab") for b in boxes)
    assert boxes[0]["min"][2] == pytest.approx(0.95)


def test_slab_boxes_follow_a_diagonal_rod_tighter_than_one_box():
    import numpy as np

    from support_geometry import slab_boxes

    s = np.linspace(0, 1, 400)
    rod = np.c_[s, s, 0.5 + 0.01 * np.sin(40 * s)]                     # a thin rod along the x=y diagonal
    boxes = slab_boxes(rod, n=5, margin=0.005, prefix="rod")
    assert len(boxes) == 5 and boxes[0]["id"] == "rod:0"
    whole = np.prod(rod.max(0) - rod.min(0) + 0.01)
    assert sum(np.prod(np.asarray(b["max"]) - np.asarray(b["min"])) for b in boxes) < 0.3 * whole
    inside = np.zeros(len(rod), bool)
    for b in boxes:
        inside |= np.all((rod >= np.asarray(b["min"])) & (rod <= np.asarray(b["max"])), axis=1)
    assert inside.all()
