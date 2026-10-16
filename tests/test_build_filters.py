"""An articulated asset's overlapping, unjointed bodies are collision-filtered by the build (build_er_scene._filter_overlapping_unjointed)."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
pxr = pytest.importorskip("pxr")
from pxr import Gf, Sdf, Usd, UsdGeom, UsdPhysics  # noqa: E402

from build_er_scene import _filter_overlapping_unjointed  # noqa: E402


def _body(stage, path, centre, size=0.02):
    cube = UsdGeom.Cube.Define(stage, path)
    cube.CreateSizeAttr(size)
    cube.AddTranslateOp().Set(Gf.Vec3d(*centre))
    UsdPhysics.RigidBodyAPI.Apply(cube.GetPrim())
    UsdPhysics.CollisionAPI.Apply(cube.GetPrim())
    return cube.GetPrim()


def _joint(stage, path, a, b):
    j = UsdPhysics.RevoluteJoint.Define(stage, path)
    j.CreateBody0Rel().SetTargets([a.GetPath()])
    j.CreateBody1Rel().SetTargets([b.GetPath()])
    return j.GetPrim()


def test_a_pin_through_an_unjointed_half_is_filtered_and_jointed_pairs_are_not():
    stage = Usd.Stage.CreateInMemory()
    obj = UsdGeom.Xform.Define(stage, "/World/Objects/Scissors").GetPrim()
    half1 = _body(stage, "/World/Objects/Scissors/half1", (0, 0, 0), 0.04)
    half2 = _body(stage, "/World/Objects/Scissors/half2", (0.01, 0, 0), 0.04)     # overlaps half1, jointed to it
    pin = _body(stage, "/World/Objects/Scissors/pin", (0, 0, 0), 0.005)           # inside half1, jointed only to half2
    _joint(stage, "/World/Objects/Scissors/Joints/pivot", half2, half1)
    _joint(stage, "/World/Objects/Scissors/Joints/pin_on_arm", half2, pin)
    pairs = _filter_overlapping_unjointed(stage, obj)
    assert pairs == [(half1.GetPath().pathString, pin.GetPath().pathString)]
    assert "PhysicsFilteredPairsAPI" in stage.GetPrimAtPath("/World/Objects/Scissors/half1").GetAppliedSchemas()
    assert [str(t) for t in stage.GetPrimAtPath("/World/Objects/Scissors/half1").GetRelationship("physics:filteredPairs").GetTargets()] == ["/World/Objects/Scissors/pin"]


def test_bodies_apart_are_left_alone():
    stage = Usd.Stage.CreateInMemory()
    obj = UsdGeom.Xform.Define(stage, "/World/Objects/Thing").GetPrim()
    _body(stage, "/World/Objects/Thing/a", (0, 0, 0))
    _body(stage, "/World/Objects/Thing/b", (0.1, 0, 0))
    assert _filter_overlapping_unjointed(stage, obj) == []


def test_robot_overlaps_finds_a_robot_built_inside_a_prop():
    """build_er_scene.robot_overlaps: a robot link's collider box inside an
    environment collider's box by more than the margin is reported; a prop
    merely beside it is not."""
    from pxr import Gf, Usd, UsdGeom, UsdPhysics

    from build_er_scene import robot_overlaps

    st = Usd.Stage.CreateInMemory()
    UsdGeom.Xform.Define(st, "/World")

    def cube(path, centre, size):
        c = UsdGeom.Cube.Define(st, path)
        c.CreateSizeAttr(1.0)
        xf = UsdGeom.XformCommonAPI(c)
        xf.SetTranslate(Gf.Vec3d(*centre))
        xf.SetScale(Gf.Vec3f(*size))
        UsdPhysics.CollisionAPI.Apply(c.GetPrim())

    cube("/World/Robot/base", (0.0, 0.0, 0.5), (0.4, 0.4, 1.0))
    cube("/World/Environment/chair", (0.25, 0.0, 0.4), (0.4, 0.4, 0.8))      # 15 cm into the robot
    cube("/World/Environment/table", (0.0, 0.7, 0.7), (1.0, 0.95, 0.04))     # 2.5 cm off the robot's front
    hits = robot_overlaps(st)
    assert [(a.split("/")[-1], b.split("/")[-1]) for a, b, _ in hits] == [("base", "chair")]
    assert abs(hits[0][2] - 0.15) < 1e-6
    UsdGeom.Imageable(st.GetPrimAtPath("/World/Environment/chair")).GetPrim().SetActive(False)
    assert robot_overlaps(st) == []


def test_an_actuators_housing_gets_an_exact_collider():
    """asset_review_hub.exact_actuator_housings: the motor's throttle joint's
    body0 (the housing) collides through an SDF; the other bodies keep theirs."""
    from pxr import Usd, UsdGeom, UsdPhysics

    from asset_review_hub import exact_actuator_housings

    st = Usd.Stage.CreateInMemory()
    for name in ("body", "trigger", "chuck"):
        b = UsdGeom.Xform.Define(st, f"/Tool/{name}")
        UsdPhysics.RigidBodyAPI.Apply(b.GetPrim())
        m = UsdGeom.Mesh.Define(st, f"/Tool/{name}/mesh")
        UsdPhysics.CollisionAPI.Apply(m.GetPrim())
        UsdPhysics.MeshCollisionAPI.Apply(m.GetPrim()).CreateApproximationAttr().Set("convexDecomposition")
    j = UsdPhysics.PrismaticJoint.Define(st, "/Tool/trigger_joint")
    j.CreateBody0Rel().SetTargets(["/Tool/body"]); j.CreateBody1Rel().SetTargets(["/Tool/trigger"])
    made = exact_actuator_housings(st, "/Tool", [{"type": "motor", "throttle": "trigger_joint", "output": "chuck"}])
    approx = {n: UsdPhysics.MeshCollisionAPI(st.GetPrimAtPath(f"/Tool/{n}/mesh")).GetApproximationAttr().Get() for n in ("body", "trigger", "chuck")}
    assert approx == {"body": "sdf", "trigger": "convexDecomposition", "chuck": "convexDecomposition"} and len(made) == 1


def test_mesh_volume_and_centroid_of_a_closed_box_far_from_the_origin():
    from build_er_scene import mesh_volume_centroid
    lo, hi = np.array([4.70, 0.60, 0.77]), np.array([4.72, 0.63, 0.78])
    V = np.array([[lo[0] if i & 1 == 0 else hi[0], lo[1] if i & 2 == 0 else hi[1], lo[2] if i & 4 == 0 else hi[2]] for i in range(8)])
    quads = [[0, 2, 3, 1], [4, 5, 7, 6], [0, 1, 5, 4], [2, 6, 7, 3], [0, 4, 6, 2], [1, 3, 7, 5]]   # wound outward
    vol, c = mesh_volume_centroid(V, [4] * 6, [i for q in quads for i in q])
    assert abs(vol - 0.02 * 0.03 * 0.01) < 1e-12 and np.allclose(c, (lo + hi) / 2, atol=1e-9)
    vol_in, _ = mesh_volume_centroid(V, [4] * 6, [i for q in quads for i in q[::-1]])
    assert vol_in < 0                                                     # wound inward: the build refuses it


def test_mass_from_parts_puts_the_centre_of_mass_toward_the_dense_part():
    from build_er_scene import mass_from_parts
    # a scissors half: a 2.2 cm3 steel blade 9 cm from a 21.7 cm3 plastic loop
    m, c = mass_from_parts([(2.2e-6, [0.09, 0.0, 0.0], 7850.0), (21.7e-6, [0.0, 0.0, 0.0], 1050.0)])
    assert abs(m - (2.2e-6 * 7850 + 21.7e-6 * 1050)) < 1e-12
    uniform = 2.2 / (2.2 + 21.7) * 0.09                                   # one density: the loop's volume wins
    assert c[0] > 3 * uniform


def test_com_over_edge_is_signed_by_the_side_the_object_hangs():
    from build_er_scene import com_over_edge
    assert abs(com_over_edge([4.7743, 0.67], "-", "x", 4.75, 0.01) - 0.0143) < 1e-9     # hung past -x: the support is at x > edge
    assert com_over_edge([4.7429, 0.67], "-", "x", 4.75, 0.01) < 0                       # 7 mm past the edge: tips off
    assert abs(com_over_edge([1.0, 0.48], "+", "y", 0.5, 0.0) - 0.02) < 1e-9


def test_config_drift_names_what_the_build_did_not_take():
    from build_er_scene import config_drift
    cfg = {"name": "s", "grasp_plan": {"lift_m": 0.0, "lift_why": "a note", "cycles": 5}, "objects": [{"yaw_deg": -90}]}
    built = {"name": "s", "grasp_plan": {"lift_m": 0.05, "cycles": 5, "added_by_build": 1}, "objects": [{"yaw_deg": -90.0, "z": 0.77}]}
    assert config_drift(cfg, built) == ["/grasp_plan/lift_m"]
    built["grasp_plan"]["lift_m"] = 0.0
    assert config_drift(cfg, built) == []
    assert config_drift({"a": [1, 2]}, {"a": [1]}) == ["/a"]
