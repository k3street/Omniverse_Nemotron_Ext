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


def test_an_object_built_against_a_wall_is_caught_by_its_clearance():
    from build_er_scene import object_clearances
    st = Usd.Stage.CreateInMemory()
    UsdGeom.SetStageMetersPerUnit(st, 1.0)

    def box(path, lo, hi):
        c = UsdGeom.Cube.Define(st, path)
        c.CreateSizeAttr(1.0)
        xf = UsdGeom.XformCommonAPI(c)
        xf.SetTranslate(Gf.Vec3d(*[(a + b) / 2 for a, b in zip(lo, hi)]))
        xf.SetScale(Gf.Vec3f(*[b - a for a, b in zip(lo, hi)]))
        UsdPhysics.CollisionAPI.Apply(c.GetPrim())
    box("/World/Environment/wall", (-1, -1.61, 0), (1, -1.50, 2.5))
    box("/World/Environment/floor", (-3, -3, -0.01), (3, 3, 0.0))
    box("/World/Objects/Fridge/body", (-0.3, -1.497, 0.001), (0.3, -0.87, 1.75))
    near = object_clearances(st, "/World/Objects/Fridge", exclude=["/World/Environment/floor"])
    assert [(b, g) for _, b, g in near] == [("/World/Environment/wall", 0.003)]
    box("/World/Objects/Fridge2/body", (-0.3, -1.46, 0.001), (0.3, -0.83, 1.75))
    assert object_clearances(st, "/World/Objects/Fridge2", exclude=["/World/Environment/floor"]) == []


def test_a_camera_eye_inside_furniture_is_refused_but_not_one_inside_the_room():
    from pxr import Usd, UsdGeom

    from build_er_scene import camera_eyes_inside
    st = Usd.Stage.CreateInMemory()
    UsdGeom.Xform.Define(st, "/World/Environment")
    wardrobe = UsdGeom.Cube.Define(st, "/World/Environment/wardrobe")               # a 2 x 0.8 x 2.6 m box
    wardrobe.CreateSizeAttr(1.0)
    UsdGeom.XformCommonAPI(wardrobe).SetScale((2.0, 0.8, 2.6))
    UsdGeom.XformCommonAPI(wardrobe).SetTranslate((0.3, 1.9, 1.3))
    m = UsdGeom.Mesh.Define(st, "/World/Environment/wardrobe_mesh")
    m.CreatePointsAttr([(-0.7, 1.5, 0.0), (1.3, 1.5, 0.0), (1.3, 2.3, 2.6), (-0.7, 2.3, 2.6)])
    m.CreateFaceVertexCountsAttr([4]); m.CreateFaceVertexIndicesAttr([0, 1, 2, 3])
    shell = UsdGeom.Mesh.Define(st, "/World/Environment/room_shell")                # spans the whole room
    shell.CreatePointsAttr([(-5, -5, 0), (5, -5, 0), (5, 5, 3), (-5, 5, 3)])
    shell.CreateFaceVertexCountsAttr([4]); shell.CreateFaceVertexIndicesAttr([0, 1, 2, 3])
    hits = camera_eyes_inside(st, {"Overview": (0.25, 1.85, 1.65), "Close": (0.46, 0.56, 1.56)})
    assert hits == [("Overview", "/World/Environment/wardrobe_mesh")]


def test_a_scene_replays_only_its_own_plan(tmp_path):
    from build_er_scene import foreign_reference
    own = tmp_path / "scenes" / "aida_r5_dining_drill_screw"
    base = tmp_path / "scenes" / "aida_r5_dining_drill"
    ok = {"grasp_replay": {"reference": str(own / "grasp_plan" / "targets.npz")}}
    copied = {"grasp_replay": {"reference": str(base / "grasp_plan" / "targets.npz")}}
    assert foreign_reference(ok, own) is None
    assert "not this scene's own plan" in foreign_reference(copied, own)
    assert foreign_reference({"grasp_replay": {}}, own) is None


def test_another_scenes_recording_is_allowed_only_when_the_config_says_why(tmp_path):
    from build_er_scene import foreign_reference
    own = tmp_path / "scenes" / "aida_r5_dining_mug_grasp"
    rec = {"grasp_replay": {"reference": "/x/HomeHero/reference.npz", "reference_foreign_why": "HomeHero's audited recording"}}
    assert foreign_reference(rec, own) is None


def test_an_attachment_records_its_tip_in_its_bodys_frame():
    import json as _json

    from pxr import Gf, Usd, UsdGeom, UsdPhysics

    from build_er_scene import _attach
    st = Usd.Stage.CreateInMemory()
    root = UsdGeom.Xform.Define(st, "/Drill")
    UsdGeom.XformCommonAPI(root).SetTranslate((1.0, 2.0, 0.5))
    UsdGeom.XformCommonAPI(root).SetRotate((0.0, 0.0, -90.0))
    house = UsdGeom.Xform.Define(st, "/Drill/house"); UsdPhysics.RigidBodyAPI.Apply(house.GetPrim())
    chuck = UsdGeom.Xform.Define(st, "/Drill/chuck"); UsdPhysics.RigidBodyAPI.Apply(chuck.GetPrim())
    nose = UsdGeom.Cube.Define(st, "/Drill/chuck/nose"); nose.CreateSizeAttr(0.01)
    UsdGeom.XformCommonAPI(nose).SetTranslate((-0.12, 0.0, 0.15))             # the nose along the drill's -x, 15 cm up
    j = UsdPhysics.RevoluteJoint.Define(st, "/Drill/chuck_joint")
    j.CreateBody0Rel().SetTargets(["/Drill/house"]); j.CreateBody1Rel().SetTargets(["/Drill/chuck"])
    j.CreateAxisAttr("X"); j.CreateLocalPos1Attr(Gf.Vec3f(-0.10, 0.0, 0.15)); j.CreateLocalRot1Attr(Gf.Quatf(1, 0, 0, 0))
    path = _attach(st, root.GetPrim(), "Drill", {"name": "Bit", "body": "chuck", "along_joint": "chuck_joint", "from_body": "nose",
                                                 "radius": 0.003, "length": 0.05, "insert_m": 0.005})
    rec = _json.loads(st.GetPrimAtPath(path).GetCustomDataByKey("er:attachment"))
    M = np.array(UsdGeom.XformCache().GetLocalToWorldTransform(st.GetPrimAtPath("/Drill/chuck"))).T
    R = M[:3, :3] / np.linalg.norm(M[:3, :3], axis=0)
    assert np.allclose(M[:3, 3] + R @ np.asarray(rec["tip_body"]), rec["tip_world_at_build"], atol=1e-9)
    assert np.allclose(R @ np.asarray(rec["axis_body"]), rec["axis_world_at_build"], atol=1e-9)
    # the drill's -x turned -90 deg about z is world +y: the bit points out of the nose, 45 mm beyond it
    assert np.allclose(rec["axis_world_at_build"], [0.0, 1.0, 0.0], atol=1e-9)
    assert abs(rec["tip_world_at_build"][1] - (2.0 + 0.125 + 0.045)) < 1e-6


def test_joint_states_use_the_schema_name_physx_registers():
    import glob
    import json as _json

    from build_er_scene import joint_state_schema
    assert joint_state_schema("angular") == "PhysicsJointStateAPI:angular"
    assert joint_state_schema("linear") == "PhysicsJointStateAPI:linear"
    with pytest.raises(ValueError):
        joint_state_schema("prismatic")
    infos = glob.glob(str(Path.home() / "Documents/Github/isaacsim/_build/linux-aarch64/release/extscache/omni.usd.schema.physx-*"
                          / Path("plugins/PhysxSchema/resources/plugInfo.json")))
    if infos:                                                       # against PhysX's own registry, where it is installed
        text = Path(infos[0]).read_text()
        assert '"schemaIdentifier": "PhysicsJointStateAPI"' in text
        assert "PhysxJointStateAPI" not in text


def test_a_scene_built_by_other_builder_code_is_stale(tmp_path):
    import json as _json

    from build_er_scene import build_code_sha, stale_build
    (tmp_path / "build.json").write_text(_json.dumps({"build_code_sha256": build_code_sha()}))
    assert stale_build(tmp_path) is None
    (tmp_path / "build.json").write_text(_json.dumps({"build_code_sha256": "0" * 64}))
    assert "other builder code" in stale_build(tmp_path)
    (tmp_path / "build.json").write_text(_json.dumps({}))
    assert "before builds were stamped" in stale_build(tmp_path)


def test_a_drilled_hole_is_built_as_the_boards_colliders_and_a_drillable_board_passes_only_the_bit():
    from build_er_scene import _drillable_twin, _filter_bit, _holed_board
    stage = Usd.Stage.CreateInMemory()
    fx = {"name": "Board", "size": [0.2, 0.018, 0.3], "position": [4.8, 0.88175, 0.858]}
    hole = {"entry": [4.8, 0.87275, 0.949], "r": 0.0016, "depth_m": 0.018, "out": [0.0, -1.0, 0.0], "sides": 16}
    _holed_board(stage, fx, [hole])
    pieces = stage.GetPrimAtPath("/World/Fixtures/Board").GetChildren()
    assert len(pieces) == 16                                            # through: the ring alone, no board behind
    assert all(UsdPhysics.MeshCollisionAPI(q).GetApproximationAttr().Get() == "convexHull" for q in pieces)
    pts = np.asarray(UsdGeom.Mesh(pieces[0]).GetPointsAttr().Get(), float)
    assert abs(pts[:, 1].min() - 0.87275) < 1e-6 and abs(pts[:, 1].max() - 0.89075) < 1e-6
    stage2 = Usd.Stage.CreateInMemory()
    cube = UsdGeom.Cube.Define(stage2, "/World/Fixtures/Board")
    UsdPhysics.CollisionAPI.Apply(cube.GetPrim())
    UsdGeom.Xform.Define(stage2, "/World/Objects/Drill/Chuck")
    UsdGeom.Cylinder.Define(stage2, "/World/Objects/Drill/Chuck/Bit")
    dfx = dict(fx, drillable={"object": "Drill", "attachments": ["Bit"], "sides": 16})
    _drillable_twin(stage2, dfx)
    _filter_bit(stage2, dfx)
    board = stage2.GetPrimAtPath("/World/Fixtures/Board")
    assert [str(p) for p in board.GetRelationship("physics:filteredPairs").GetTargets()] == ["/World/Objects/Drill/Chuck/Bit"]
    assert UsdGeom.Imageable(board).ComputeVisibility() == UsdGeom.Tokens.invisible
    twin = stage2.GetPrimAtPath("/World/FixtureVisuals/Board")
    assert len(twin.GetChildren()) == 18 and not any(q.HasAPI(UsdPhysics.CollisionAPI) for q in twin.GetChildren())


def test_a_scene_without_the_films_close_up_is_refused_at_the_build():
    from build_er_scene import film_cameras_missing
    assert film_cameras_missing({"extra_cameras": []}) == ["GraspCloseup"]
    assert film_cameras_missing({"extra_cameras": [{"name": "GraspCloseup"}]}) == []


def test_every_station_part_is_an_obstacle_for_the_planner():
    from plan_er_grasp import station_obstacles
    stage = Usd.Stage.CreateInMemory()
    st = UsdGeom.Xform.Define(stage, "/World/Environment/Stations/Bench")
    st.GetPrim().SetCustomDataByKey("er:station", '{"role": "inspection_bench"}')
    for name, c, sz in (("Top", (0.0, 0.0, 0.7), (0.7, 0.75, 0.025)), ("Leg0", (0.3, 0.3, 0.35), (0.04, 0.04, 0.68))):
        cube = UsdGeom.Cube.Define(stage, f"/World/Environment/Stations/Bench/{name}")
        cube.CreateSizeAttr(1.0)
        cube.AddTranslateOp().Set(Gf.Vec3d(*c))
        cube.AddScaleOp().Set(Gf.Vec3f(*sz))
        UsdPhysics.CollisionAPI.Apply(cube.GetPrim())
    UsdGeom.Cube.Define(stage, "/World/Environment/Decor")                     # not a station: not an obstacle
    bc = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.default_, UsdGeom.Tokens.render])
    obs = {o["id"]: o for o in station_obstacles(stage, bc)}
    assert sorted(obs) == ["Bench:Leg0", "Bench:Top"]
    assert np.allclose(obs["Bench:Leg0"]["min"], [0.28, 0.28, 0.01]) and np.allclose(obs["Bench:Top"]["max"], [0.35, 0.375, 0.7125])


def test_a_target_cloud_takes_cubes_and_cylinders_too():
    from plan_er_grasp import gprim_points
    stage = Usd.Stage.CreateInMemory()
    cube = UsdGeom.Cube.Define(stage, "/C")
    cube.CreateSizeAttr(1.0)
    cube.AddTranslateOp().Set(Gf.Vec3d(1.0, 0.0, 0.0))
    cube.AddScaleOp().Set(Gf.Vec3f(0.1, 0.02, 0.004))
    cyl = UsdGeom.Cylinder.Define(stage, "/T")
    cyl.CreateAxisAttr("Z"); cyl.CreateRadiusAttr(0.005); cyl.CreateHeightAttr(0.004)
    xc = UsdGeom.XformCache()
    P = np.asarray(gprim_points(cube.GetPrim(), xc.GetLocalToWorldTransform(cube.GetPrim())))
    assert P.shape == (8, 3) and np.allclose(P.min(0), [0.95, -0.01, -0.002]) and np.allclose(P.max(0), [1.05, 0.01, 0.002])
    T = np.asarray(gprim_points(cyl.GetPrim(), xc.GetLocalToWorldTransform(cyl.GetPrim())))
    assert T.shape == (32, 3) and np.allclose(np.hypot(T[:, 0], T[:, 1]), 0.005) and np.allclose(sorted(set(np.round(T[:, 2], 4))), [-0.002, 0.002])


def test_the_scenes_fixtures_are_the_planners_obstacles():
    from plan_er_grasp import fixture_obstacles
    stage = Usd.Stage.CreateInMemory()
    UsdGeom.Xform.Define(stage, "/World/Fixtures/Cradle")
    post = UsdGeom.Cube.Define(stage, "/World/Fixtures/Cradle/Post")
    post.CreateSizeAttr(1.0)
    post.AddTranslateOp().Set(Gf.Vec3d(1.0, 0.0, 0.0125))
    post.AddScaleOp().Set(Gf.Vec3f(0.006, 0.02, 0.025))
    UsdPhysics.CollisionAPI.Apply(post.GetPrim())
    UsdGeom.Cube.Define(stage, "/World/Fixtures/Cradle/Label")                  # no collider: not an obstacle
    bc = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.default_, UsdGeom.Tokens.render])
    obs = fixture_obstacles(stage, bc)
    assert [o["id"] for o in obs] == ["fixture:Cradle:Post"] and np.allclose(obs[0]["max"], [1.003, 0.01, 0.025])


def test_a_tool_pick_holds_the_pinch_axis_to_the_tweezers_opening_and_the_finger_row_along_them(tmp_path):
    from make_pill_kit import make_tweezers
    from plan_er_grasp import _tool_pick_task
    make_tweezers(tmp_path / "tw.usda")
    stage = Usd.Stage.CreateInMemory()
    obj = UsdGeom.Xform.Define(stage, "/World/Objects/Tweezers")
    obj.GetPrim().GetReferences().AddReference(str(tmp_path / "tw.usda"))
    UsdGeom.XformCommonAPI(obj).SetTranslate(Gf.Vec3d(4.455, 0.74, 0.758))
    UsdGeom.XformCommonAPI(obj).SetRotate(Gf.Vec3f(90.0, 0.0, 0.0))            # on edge: the arms open along world y
    task = _tool_pick_task(stage, obj.GetPrim(), UsdGeom.XformCache(), {"grip_from_root_m": 0.055}, np.zeros(3))
    assert task["kind"] == "pinch_lift" and task["tripod"]
    assert abs(abs(task["cloth_normal"][1]) - 1.0) < 1e-6                       # the opening axis: world y
    assert np.allclose(task["finger_row_axis"], [1.0, 0.0, 0.0], atol=1e-6)    # the arms' length: world x
    assert abs(task["grip_point"][0] - (4.455 + 0.015 + 0.055)) < 1e-3 and 0.002 < task["grip_width_m"] < 0.014
