"""Pure contract tests for the Psyonic hydroelastic wrapper generator."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.l0

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))


def _hand_paths():
    paths = []
    for side in ("left", "right"):
        base = f"/Robot/psyonic_{side}_base"
        paths.append(base)
        for finger in ("index", "middle", "pinky", "ring", "thumb"):
            paths.extend((
                f"{base}/psyonic_{side}_{finger}_L1",
                f"{base}/psyonic_{side}_{finger}_L1/psyonic_{side}_{finger}_L2",
            ))
    return paths


def test_hand_body_selection_is_narrow():
    from make_newton_hydro_hands import is_hand_body_path

    assert is_hand_body_path("/Robot/psyonic_left_thumb_L2")
    assert is_hand_body_path("/Robot/psyonic_right_base")
    assert not is_hand_body_path("/Robot/amber_left_hand_mount_link")
    assert not is_hand_body_path("/Robot/psyonic_tool")


def test_only_kinematic_neighbors_are_filtered():
    from make_newton_hydro_hands import adjacent_hand_body_pairs

    pairs = adjacent_hand_body_pairs(_hand_paths())
    assert len(pairs) == 20
    assert len(set(pairs)) == 20
    assert (
        "/Robot/psyonic_left_base",
        "/Robot/psyonic_left_base/psyonic_left_index_L1",
    ) in pairs
    # Non-adjacent digits remain eligible for self-contact.
    assert not any("index" in first and "middle" in second for first, second in pairs)


def test_hull_is_deterministic_watertight_and_outward():
    pytest.importorskip("numpy")
    pytest.importorskip("scipy")
    import numpy as np

    from make_newton_hydro_hands import (
        _check_closed_triangle_mesh,
        _deterministic_convex_hull,
    )

    points = np.asarray([
        [-1.0, -1.0, -1.0], [-1.0, -1.0, 1.0],
        [-1.0, 1.0, -1.0], [-1.0, 1.0, 1.0],
        [1.0, -1.0, -1.0], [1.0, -1.0, 1.0],
        [1.0, 1.0, -1.0], [1.0, 1.0, 1.0],
        [1.0, 1.0, 1.0],  # duplicate must not perturb the result
    ])
    vertices_a, faces_a, volume_a = _deterministic_convex_hull(points)
    vertices_b, faces_b, volume_b = _deterministic_convex_hull(points[::-1])
    assert np.array_equal(vertices_a, vertices_b)
    assert np.array_equal(faces_a, faces_b)
    assert volume_a == pytest.approx(8.0)
    assert volume_b == pytest.approx(8.0)
    assert _check_closed_triangle_mesh(vertices_a, faces_a) == pytest.approx(8.0)


def test_convex_overlap_depth_distinguishes_overlap_from_separation():
    pytest.importorskip("numpy")
    pytest.importorskip("scipy")
    import numpy as np

    from make_newton_hydro_hands import _convex_overlap_depth

    cube = np.asarray([
        [-1.0, -1.0, -1.0], [-1.0, -1.0, 1.0],
        [-1.0, 1.0, -1.0], [-1.0, 1.0, 1.0],
        [1.0, -1.0, -1.0], [1.0, -1.0, 1.0],
        [1.0, 1.0, -1.0], [1.0, 1.0, 1.0],
    ])
    assert _convex_overlap_depth(cube, cube + [1.5, 0.0, 0.0]) == pytest.approx(0.25)
    assert _convex_overlap_depth(cube, cube + [3.0, 0.0, 0.0]) is None
