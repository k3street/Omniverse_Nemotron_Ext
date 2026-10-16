"""A body's world points moved from its authored pose to its settled one (plan_er_grasp._at_settled_pose): a frame change
about the body's origin that keeps the body's shape, whatever scale the authored matrix carries."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest
from scipy.spatial.transform import Rotation as R

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from plan_er_grasp import _at_settled_pose  # noqa: E402


def _authored_matrix(rotation, translation, scale):
    # row-vector convention as pxr's Gf.Matrix4d: axes in the rows, translation in the last row; np.array(Gf.Matrix4d) gives this
    M = np.eye(4)
    M[:3, :3] = (rotation.as_matrix() * scale).T
    M[3, :3] = translation
    return M


def test_points_follow_the_body_and_keep_their_shape_under_an_asset_scale():
    r_auth, p_auth = R.from_euler("x", -90, degrees=True), np.array([6.81, 0.937, 0.935])
    M = _authored_matrix(r_auth, p_auth, 0.5)                          # the asset authored at half scale
    local = np.array([[0.1, 0.0, 0.0], [0.0, 0.2, 0.0], [0.0, 0.0, 0.05], [0.1, 0.2, 0.05]])
    world = local @ (r_auth.as_matrix() * 0.5).T + p_auth               # the mesh points as USD places them
    r_set = r_auth * R.from_euler("y", -21, degrees=True)
    p_set = p_auth + np.array([0.03, 0.03, -0.013])
    q = r_set.as_quat()                                                 # xyzw
    moved_pts, moved = _at_settled_pose(world, M, {"p": p_set.tolist(), "q_wxyz": [q[3], q[0], q[1], q[2]]})
    assert moved == pytest.approx(np.linalg.norm(p_set - p_auth))
    d0 = np.linalg.norm(world[:, None] - world[None], axis=-1)
    d1 = np.linalg.norm(moved_pts[:, None] - moved_pts[None], axis=-1)
    assert np.allclose(d0, d1, atol=1e-9)                                # the shape is kept
    expected = (world - p_auth) @ (r_set.as_matrix() @ r_auth.as_matrix().T).T + p_set
    assert np.allclose(moved_pts, expected, atol=1e-9)
    assert not np.any(np.abs(moved_pts) > 10)                            # nothing scattered metres away


def test_an_unchanged_pose_is_the_identity():
    r_auth, p_auth = R.from_euler("z", 45, degrees=True), np.array([1.0, 2.0, 3.0])
    M = _authored_matrix(r_auth, p_auth, 2.0)
    world = np.random.default_rng(0).normal(size=(20, 3)) + p_auth
    q = r_auth.as_quat()
    moved_pts, moved = _at_settled_pose(world, M, {"p": p_auth.tolist(), "q_wxyz": [q[3], q[0], q[1], q[2]]})
    assert moved == 0.0 and np.allclose(moved_pts, world)
