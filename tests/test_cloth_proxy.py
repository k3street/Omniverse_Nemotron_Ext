"""Cloth simulation proxies (scripts/cloth_proxy.py): needs numpy, trimesh,
fast_simplification (the Newton venv has them)."""
import sys
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("trimesh")
pytest.importorskip("fast_simplification")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))


def _grid(n, x0=0.0, flip_every=0):
    xs = np.linspace(0, 1, n)
    pts = np.array([(x0 + x, y, 0.0) for y in xs for x in xs])
    tris = []
    for j in range(n - 1):
        for i in range(n - 1):
            a, b, c, d = j * n + i, j * n + i + 1, (j + 1) * n + i, (j + 1) * n + i + 1
            tris += [(a, b, d), (a, d, c)]
    t = np.array(tris)
    if flip_every:
        t[::flip_every] = t[::flip_every][:, ::-1]   # some faces wound backwards
    return pts, t


def test_a_panel_hanging_off_one_vertex_is_dropped_and_the_winding_made_consistent():
    import cloth_proxy as cp

    big, tb = _grid(21, flip_every=7)
    small, ts = _grid(6)
    small = small * 0.5 + np.array([1.0, -0.5, 0.0])   # a half-size panel meeting the big one
                                                       # only at its corner (1, 0, 0): a bowtie
    pts = np.concatenate([big, small])
    tris = np.concatenate([tb, ts + len(big)])
    p, t = cp.make_proxy(pts, tris, target_vertices=200)
    q = cp.quality(p, t)
    assert q["winding_consistent"] and q["non_manifold_edges"] == 0 and q["folded_edges"] == 0
    # only the big panel: everything within its square
    assert p[:, 1].min() >= -1e-6 and p[:, 0].max() <= 1.0 + 1e-6
    assert 100 < q["vertices"] < 600 and q["min_angle_deg_p1"] > 15
