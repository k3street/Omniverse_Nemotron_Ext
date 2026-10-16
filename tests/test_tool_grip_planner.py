"""The tool grip's geometry (tool_grip_planner): surfaces sampled, bodies as
slab hulls, the handle's axis, the left hand's pistol-grip frame, and how far
a finger pushes a trigger in."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from tool_grip_planner import (contact_site, face_normals, force_reach, grip_axes, handle_axis, inside,  # noqa: E402
                               left_grip_rotation, push_force, push_lever, sample_faces, settled_hand, slab_hulls,
                               trigger_travel)


def _box(lo, hi):
    lo, hi = np.asarray(lo, float), np.asarray(hi, float)
    V = np.array([[lo[0] if i & 1 == 0 else hi[0], lo[1] if i & 2 == 0 else hi[1], lo[2] if i & 4 == 0 else hi[2]] for i in range(8)])
    quads = [[0, 1, 3, 2], [4, 5, 7, 6], [0, 1, 5, 4], [2, 3, 7, 6], [0, 2, 6, 4], [1, 3, 7, 5]]
    return V, [4] * 6, [i for q in quads for i in q]


def test_sample_faces_fills_the_faces_not_only_the_corners():
    V, c, idx = _box([0, 0, 0], [0.04, 0.02, 0.10])
    P = sample_faces(V, c, idx, spacing=0.003)
    assert len(P) > 300
    band = P[(P[:, 2] > 0.05) & (P[:, 2] < 0.056)]                   # a 6 mm band through the middle: both sides, not empty
    assert band[:, 0].min() < 0.001 and band[:, 0].max() > 0.039 and band[:, 1].min() < 0.001 and band[:, 1].max() > 0.019


def test_slab_hulls_follow_an_l_shaped_body():
    """A pistol grip's shape: a handle and a barrel over it. One convex hull
    would fill the corner in front of the handle where the trigger finger goes."""
    V1, c1, i1 = _box([0.0, 0.0, 0.0], [0.03, 0.02, 0.10])          # the handle
    V2, c2, i2 = _box([-0.12, 0.0, 0.10], [0.05, 0.02, 0.14])       # the barrel
    P = np.vstack([sample_faces(V1, c1, i1, 0.003), sample_faces(V2, c2, i2, 0.003)])
    hulls = slab_hulls(P, [0, 0, 1], band=0.006)
    probe = np.array([[-0.05, 0.01, 0.06],                          # in front of the handle, under the barrel: free
                      [0.015, 0.01, 0.05],                          # inside the handle
                      [-0.08, 0.01, 0.12]])                         # inside the barrel
    assert inside(probe, hulls, 0.0005).tolist() == [False, True, True]


def test_handle_axis_and_the_grip_frame():
    rng = np.random.default_rng(0)
    t = rng.uniform(0, 0.06, 2000)
    lean = np.array([0.0, np.sin(np.radians(20)), np.cos(np.radians(20))])   # leaning 20 deg toward +y
    P = np.outer(t, lean) + rng.normal(0, 0.004, (2000, 3)) * [1, 0.3, 0]
    c, a = handle_axis(P, [0, 0, 1], band=0.006)
    assert np.degrees(np.arccos(abs(a @ lean))) < 4 and a[2] > 0
    a, f, l = grip_axes(a, [0, 1, 0])                               # forward toward +y
    assert abs(a @ f) < 1e-9 and f[1] > 0.9 and np.allclose(l, np.cross(a, f))
    R = left_grip_rotation(a, f, l)
    assert np.allclose(R.T @ R, np.eye(3), atol=1e-9) and abs(np.linalg.det(R) - 1) < 1e-9
    assert np.allclose(R[:, 0], a) and np.allclose(R[:, 1], -l)    # index up the handle, the palm facing the tool's right
    assert R[:, 2] @ f > 0.99                                       # the fingers forward


def test_trigger_travel_is_the_fingers_depth_past_the_triggers_front():
    V, c, idx = _box([-0.03, -0.005, 0.10], [-0.02, 0.005, 0.12])   # a trigger; it travels along +x (toward the handle)
    T = sample_faces(V, c, idx, 0.002)
    finger = np.array([[-0.024, 0.0, 0.11], [-0.040, 0.0, 0.11]])    # one point 6 mm past the front face (x -0.03)
    assert abs(trigger_travel(finger, T, [1, 0, 0]) - 0.006) < 0.0011
    assert trigger_travel(np.array([[-0.024, 0.03, 0.11]]), T, [1, 0, 0]) == 0.0   # beside it: not touching


def test_palm_seat_is_measured_to_the_palms_convex_collider():
    """The palm's collider is one convex hull: from its heel (2.3 cm proud
    at the wrist) a slanted face runs down to the finger bases. The handle
    meets that face - not the heel's height, not only the vertices nearby."""
    from tool_grip_planner import palm_seat

    pts = []
    for x in (-0.04, 0.035):
        for z in (0.0, 0.02):
            pts.append([x, 0.023, z])                                       # the heel's front corners
        for z in (0.0, 0.11):
            pts.append([x, -0.02, z])                                       # the back of the hand
        pts.append([x, 0.0, 0.11])                                          # the finger bases' front
    P = np.asarray(pts)
    seat = palm_seat(P, np.array([[0.0, 0.030, 0.08]]))
    face_y = 0.023 * (0.11 - 0.08) / (0.11 - 0.02)                          # the slanted face's height at z 0.08
    assert abs(seat - (0.030 - face_y)) < 1e-6
    assert palm_seat(P, np.array([[0.3, 0.03, 0.08]])) is None              # not over the palm


def test_seat_along_and_hull_entry_ray_cast_into_a_convex_collider():
    from scipy.spatial import ConvexHull

    from tool_grip_planner import hull_entry, seat_along

    cube = np.array([[x, y, z] for x in (0, 1) for y in (0, 1) for z in (0, 1)], float) * 0.02   # a 2 cm box at the origin
    eq = ConvexHull(cube).equations
    t = hull_entry(np.array([[0.01, 0.01, 0.07], [0.01, 0.01, 0.01], [0.05, 0.05, 0.07]]), [0, 0, -1], eq)
    assert abs(t[0] - 0.05) < 1e-9 and t[1] == 0.0 and np.isinf(t[2])       # 5 cm to the top face; already inside; misses
    tool = np.array([[0.01, 0.01, 0.07], [0.015, 0.005, 0.04]])
    assert abs(seat_along(cube, tool, [0, 0, -1]) - 0.02) < 1e-9           # the second point meets the top face first
    assert seat_along(cube, tool, [0, 0, 1]) is None


def test_tool_in_hulls_finds_a_face_contact_no_vertex_test_sees():
    """A finger's collider (a box here) lying across a thin handle: no
    finger vertex is inside the handle, but the handle's points are inside
    the finger."""
    from scipy.spatial import ConvexHull

    from tool_grip_planner import inside, slab_hulls, tool_in_hulls

    finger = np.array([[x, y, z] for x in (-0.03, 0.03) for y in (0.0, 0.012) for z in (0.0, 0.015)], float)   # 6 cm long across the handle
    eq = ConvexHull(finger).equations
    handle = np.array([[x, y, z] for x in np.linspace(-0.005, 0.005, 5) for y in np.linspace(0.005, 0.03, 6)
                       for z in np.linspace(-0.05, 0.05, 21)])                                                  # 1 cm wide, through the finger
    assert not inside(finger, slab_hulls(handle, [0, 0, 1], band=0.006, min_points=4), 0.0005).any()       # the vertex test misses it
    assert tool_in_hulls(handle, [(np.eye(3), np.zeros(3), eq)])                                             # the face test finds it
    assert not tool_in_hulls(handle + [0.0, 0.05, 0.0], [(np.eye(3), np.zeros(3), eq)])


def test_face_normals_point_out_of_a_closed_right_handed_mesh():
    V = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]], float)
    tris = [[0, 2, 1], [0, 1, 3], [0, 3, 2], [1, 2, 3]]                    # wound outward
    C, N, A = face_normals(V, [3] * 4, [i for t in tris for i in t])
    centre = V.mean(0)
    assert len(C) == 4 and np.all(np.sum((C - centre) * N, axis=1) > 0)
    assert np.allclose(np.linalg.norm(N, axis=1), 1.0) and abs(A.sum() - (1.5 + np.sqrt(3) / 2)) < 1e-9


def test_push_force_is_the_torque_through_the_face_it_presses():
    pull = np.array([0.0, -1.0, 0.0])
    # a face square to the travel (outward normal against it), the finger moving 5 cm/rad along it: torque / lever
    assert abs(push_force(0.84, [0.0, -0.05, 0.0], [0.0, 1.0, 0.0], pull) - 16.8) < 1e-9
    # a finger moving ACROSS the travel into a face side-on to it (normal 72 deg off the travel): it presses hard
    # and wedges the trigger along little; moving along the travel the same face passes the whole force (virtual work)
    n = np.array([np.sin(np.radians(72)), np.cos(np.radians(72)), 0.0])
    assert 0 < push_force(0.84, [-0.05, -0.01, 0.0], n, pull) < 0.4 * 16.8
    assert abs(push_force(0.84, [0.0, -0.05, 0.0], n, pull) - 16.8) < 1e-9
    # moving out of the face, or a face that does not face the travel: nothing
    assert push_force(0.84, [0.0, 0.05, 0.0], [0.0, 1.0, 0.0], pull) == 0.0
    assert push_force(0.84, [0.0, -0.05, 0.0], [0.0, -1.0, 0.0], pull) == 0.0


def test_push_lever_is_the_touching_points_motion_along_the_travel():
    P0 = np.array([[0.0, 0.0, 0.0], [0.0, 0.1, 0.0]])
    P1 = P0 + np.array([[0.0, 0.0, 0.0], [0.0, -0.0007, 0.0]])
    assert abs(push_lever(P0, P1, 0.01, [False, True], [0.0, -1.0, 0.0]) - 0.07) < 1e-9


def test_force_reach_stops_where_the_finger_stalls_on_the_spring():
    # the trigger moves 1 mm per 0.1 rad; the finger makes 8 N: a 1500 N/m spring stalls it at 5.3 mm of 10
    qs = np.arange(0.0, 1.01, 0.1)
    reach = force_reach(lambda q: q * 0.01, lambda q: 8.0, qs, 1500.0, 0.01)
    assert abs(reach - 8.0 / 1500.0) < 1e-9
    # 20 N carries it the whole way
    assert force_reach(lambda q: q * 0.01, lambda q: 20.0, qs, 1500.0, 0.01) == 0.01
    # a finger that stops pushing (no force) ends the reach where it is
    assert force_reach(lambda q: q * 0.01, lambda q: 20.0 if q < 0.45 else 0.0, qs, 1500.0, 0.01) == 0.004


def test_contact_site_is_the_touched_part_of_the_tool():
    tool = np.array([[0.0, 0.0, 0.0], [0.01, 0.0, 0.0], [0.5, 0.0, 0.0]])
    site = contact_site(np.array([[0.005, 0.002, 0.0]]), tool, near=0.008)
    assert np.allclose(site, [0.005, 0.0, 0.0])
    assert contact_site(np.array([[0.2, 0.2, 0.2]]), tool, near=0.004) is None


def test_settled_hand_moves_the_tool_in_the_palm_frame_by_the_settle():
    from scipy.spatial.transform import Rotation as Rot
    R = Rot.from_euler("xyz", [20, -35, 70], degrees=True).as_matrix()
    o = np.array([4.8, 0.6, 0.85])
    x = np.array([4.83, 0.64, 0.80])                                     # a tool point, fixed in the world
    before = R.T @ (x - o)                                               # where the palm sees it
    o2, R2 = settled_hand(o, R, [0.003, -0.002, -0.01])                  # a shift alone: o - R t
    assert np.allclose(o2, o - R @ np.array([0.003, -0.002, -0.01])) and np.allclose(R2, R)
    settle = [-0.0061, -0.007, -0.017, -1.44, -0.63, 7.24]               # the knuckle take's drill
    o3, R3 = settled_hand(o, R, settle)
    Rs = Rot.from_rotvec(np.radians(settle[3:])).as_matrix()
    assert np.allclose(R3.T @ (x - o3), Rs @ before + np.array(settle[:3]), atol=1e-12)


def test_the_incumbent_grip_is_tried_first_while_it_passes():
    import inspect

    import tool_grip_planner as tg
    src = inspect.getsource(tg.search_grips)
    assert 'opts.get("incumbent")' in src and "not _is_inc(c)" in src
