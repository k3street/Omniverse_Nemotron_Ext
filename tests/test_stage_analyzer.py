"""
L0 unit tests for the Stage Analyzer validator packs.

One test class per validator pack.  All tests run purely on synthetic
stage_data dicts — no Kit dependency, no filesystem I/O, no Isaac Sim.

Covered packs:
  • schema_consistency      (SchemaConsistencyRule)
  • import_health           (ImportHealthValidator)
  • material_physics        (MaterialPhysicsMismatchValidator)
  • articulation_integrity  (ArticulationIntegrityValidator)
  • sensor_completeness     (SensorCompletenessValidator)
  • performance_warnings    (PerformanceWarningsValidator)
  • isaaclab_sanity         (IsaacLabSanityValidator)
  • robot_motion            (RobotMotionValidator)
  • AnalysisOrchestrator    (integration — runs all packs on one stage_data)
"""
import pytest

pytestmark = pytest.mark.l0

from service.isaac_assist_service.analysis.validators.schema_consistency import SchemaConsistencyRule
from service.isaac_assist_service.analysis.validators.import_health import ImportHealthValidator
from service.isaac_assist_service.analysis.validators.material_physics import MaterialPhysicsMismatchValidator
from service.isaac_assist_service.analysis.validators.articulation_integrity import ArticulationIntegrityValidator
from service.isaac_assist_service.analysis.validators.sensor_completeness import SensorCompletenessValidator
from service.isaac_assist_service.analysis.validators.performance_warnings import PerformanceWarningsValidator
from service.isaac_assist_service.analysis.validators.isaaclab_sanity import IsaacLabSanityValidator
from service.isaac_assist_service.analysis.validators.robot_motion import RobotMotionValidator
from service.isaac_assist_service.analysis.orchestrator import AnalysisOrchestrator


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _prim(path, prim_type="Xform", schemas=None, attrs=None, **kwargs):
    """Build a minimal prim dict for stage_data."""
    p = {
        "path": path,
        "type": prim_type,
        "schemas": schemas or [],
        "attributes": attrs or {},
    }
    p.update(kwargs)
    return p


def _stage(prims=None, **kwargs):
    """Wrap prims in a minimal stage_data dict."""
    return {"prims": prims or [], **kwargs}


# ---------------------------------------------------------------------------
# SchemaConsistencyRule
# ---------------------------------------------------------------------------

class TestSchemaConsistencyRule:
    def setup_method(self):
        self.rule = SchemaConsistencyRule()

    def test_rigid_body_missing_collision_is_flagged(self):
        stage = _stage([
            _prim("/World/Box", schemas=["PhysicsRigidBodyAPI"]),
        ])
        findings = self.rule.check(stage)
        assert len(findings) == 1
        assert findings[0].rule_id == "schema.missing_collision"
        assert findings[0].severity == "warning"
        assert findings[0].prim_path == "/World/Box"

    def test_rigid_body_with_collision_ok(self):
        stage = _stage([
            _prim("/World/Box", schemas=["PhysicsRigidBodyAPI", "PhysicsCollisionAPI"]),
        ])
        findings = self.rule.check(stage)
        assert findings == []

    def test_no_physics_prims_no_findings(self):
        stage = _stage([
            _prim("/World/Mesh", prim_type="Mesh"),
        ])
        assert self.rule.check(stage) == []

    def test_empty_stage_no_findings(self):
        assert self.rule.check(_stage()) == []

    def test_multiple_rigid_bodies_flags_each(self):
        stage = _stage([
            _prim("/World/A", schemas=["PhysicsRigidBodyAPI"]),
            _prim("/World/B", schemas=["PhysicsRigidBodyAPI"]),
            _prim("/World/C", schemas=["PhysicsRigidBodyAPI", "PhysicsCollisionAPI"]),
        ])
        findings = self.rule.check(stage)
        assert len(findings) == 2
        paths = {f.prim_path for f in findings}
        assert "/World/A" in paths and "/World/B" in paths


# ---------------------------------------------------------------------------
# ImportHealthValidator
# ---------------------------------------------------------------------------

class TestImportHealthValidator:
    def setup_method(self):
        self.rule = ImportHealthValidator()

    def test_broken_local_reference_flagged(self, tmp_path):
        missing = str(tmp_path / "missing.usd")
        stage = _stage([
            _prim("/World/Robot", references=[missing]),
        ], stage_root_dir=str(tmp_path))
        findings = self.rule.check(stage)
        assert any(f.rule_id == "import.broken_reference" for f in findings)

    def test_nucleus_reference_skipped(self):
        stage = _stage([
            _prim("/World/Robot", references=["omniverse://nucleus.server/Assets/robot.usd"]),
        ])
        findings = self.rule.check(stage)
        assert not any(f.rule_id == "import.broken_reference" for f in findings)

    def test_existing_file_reference_ok(self, tmp_path):
        asset = tmp_path / "robot.usd"
        asset.write_text("fake usd")
        stage = _stage([
            _prim("/World/Robot", references=[str(asset)]),
        ])
        findings = self.rule.check(stage)
        assert not any(f.rule_id == "import.broken_reference" for f in findings)

    def test_orphan_xform_flagged(self):
        stage = _stage([
            _prim("/World/Orphan", prim_type="Xform",
                  references=[], payloads=[], has_geometry=False, children=[]),
        ])
        findings = self.rule.check(stage)
        assert any(f.rule_id == "import.orphan_xform" for f in findings)

    def test_xform_with_children_not_orphan(self):
        stage = _stage([
            _prim("/World/Parent", prim_type="Xform",
                  references=[], payloads=[], has_geometry=False, children=[]),
            _prim("/World/Parent/Child", prim_type="Mesh"),
        ])
        findings = self.rule.check(stage)
        # /World/Parent has a child in the prim list
        assert not any(
            f.rule_id == "import.orphan_xform" and f.prim_path == "/World/Parent"
            for f in findings
        )

    def test_unresolved_payload_flagged(self, tmp_path):
        missing = str(tmp_path / "payload.usd")
        stage = _stage([
            _prim("/World/Heavy", payloads=[missing]),
        ], stage_root_dir=str(tmp_path))
        findings = self.rule.check(stage)
        assert any(f.rule_id == "import.unresolved_payload" for f in findings)

    def test_http_reference_skipped(self):
        stage = _stage([
            _prim("/World/Ext", references=["https://example.com/asset.usd"]),
        ])
        findings = self.rule.check(stage)
        assert not any(f.rule_id == "import.broken_reference" for f in findings)


# ---------------------------------------------------------------------------
# MaterialPhysicsMismatchValidator
# ---------------------------------------------------------------------------

class TestMaterialPhysicsMismatchValidator:
    def setup_method(self):
        self.rule = MaterialPhysicsMismatchValidator()

    def test_no_collision_approx_on_rigid_flagged(self):
        stage = _stage([
            _prim("/World/Box",
                  schemas=["PhysicsRigidBodyAPI", "PhysicsCollisionAPI"],
                  attrs={"physics:approximation": "none"}),
        ])
        findings = self.rule.check(stage)
        assert any(f.rule_id == "material_physics.no_collision_approx" for f in findings)

    def test_convex_hull_approx_ok(self):
        stage = _stage([
            _prim("/World/Box",
                  schemas=["PhysicsRigidBodyAPI", "PhysicsCollisionAPI"],
                  attrs={"physics:approximation": "convexHull"}),
        ])
        findings = self.rule.check(stage)
        assert not any(f.rule_id == "material_physics.no_collision_approx" for f in findings)

    def test_deformable_with_collision_conflict(self):
        stage = _stage([
            _prim("/World/Cloth",
                  schemas=["PhysxDeformableBodyAPI", "PhysicsCollisionAPI"]),
        ])
        findings = self.rule.check(stage)
        assert any(f.rule_id == "material_physics.deformable_collision_conflict" for f in findings)
        assert findings[0].severity == "error"

    def test_visual_only_mesh_in_physics_scene(self):
        stage = _stage([
            _prim("/World/Scene", prim_type="PhysicsScene"),
            _prim("/World/Prop", prim_type="Mesh", has_material=True,
                  schemas=[]),
        ])
        findings = self.rule.check(stage)
        assert any(f.rule_id == "material_physics.visual_only_mesh" for f in findings)

    def test_ground_plane_excluded_from_visual_only_check(self):
        stage = _stage([
            _prim("/World/Scene", prim_type="PhysicsScene"),
            _prim("/World/GroundPlane", prim_type="Mesh", has_material=True,
                  schemas=[]),
        ])
        findings = self.rule.check(stage)
        assert not any(f.rule_id == "material_physics.visual_only_mesh" for f in findings)

    def test_triangle_mesh_on_dynamic_body_flagged(self):
        stage = _stage([
            _prim("/World/Box",
                  schemas=["PhysicsRigidBodyAPI", "PhysicsCollisionAPI"],
                  attrs={
                      "physics:approximation": "triangleMesh",
                      "physics:kinematicEnabled": False,
                  }),
        ])
        findings = self.rule.check(stage)
        assert any(f.rule_id == "material_physics.triangle_mesh_dynamic" for f in findings)

    def test_triangle_mesh_ok_on_kinematic_body(self):
        stage = _stage([
            _prim("/World/Box",
                  schemas=["PhysicsRigidBodyAPI", "PhysicsCollisionAPI"],
                  attrs={
                      "physics:approximation": "triangleMesh",
                      "physics:kinematicEnabled": True,
                  }),
        ])
        findings = self.rule.check(stage)
        assert not any(f.rule_id == "material_physics.triangle_mesh_dynamic" for f in findings)


# ---------------------------------------------------------------------------
# ArticulationIntegrityValidator
# ---------------------------------------------------------------------------

class TestArticulationIntegrityValidator:
    def setup_method(self):
        self.rule = ArticulationIntegrityValidator()

    def test_articulation_root_no_rigid_body_flagged(self):
        stage = _stage([
            _prim("/World/Robot", schemas=["PhysicsArticulationRootAPI"]),
        ])
        findings = self.rule.check(stage)
        assert any(f.rule_id == "articulation.no_rigid_body" for f in findings)

    def test_articulation_root_with_descendant_rigid_ok(self):
        stage = _stage([
            _prim("/World/Robot", schemas=["PhysicsArticulationRootAPI"]),
            _prim("/World/Robot/Base", schemas=["PhysicsRigidBodyAPI"]),
        ])
        findings = self.rule.check(stage)
        assert not any(f.rule_id == "articulation.no_rigid_body" for f in findings)

    def test_joint_zero_drive_flagged(self):
        stage = _stage([
            _prim("/World/Robot/joint1",
                  prim_type="PhysicsRevoluteJoint",
                  attrs={
                      "drive:angular:physics:stiffness": 0,
                      "drive:angular:physics:damping": 0,
                  }),
        ])
        findings = self.rule.check(stage)
        assert any(f.rule_id == "articulation.zero_drive" for f in findings)

    def test_joint_with_nonzero_drive_ok(self):
        stage = _stage([
            _prim("/World/Robot/joint1",
                  prim_type="PhysicsRevoluteJoint",
                  attrs={
                      "drive:angular:physics:stiffness": 400,
                      "drive:angular:physics:damping": 40,
                  }),
        ])
        findings = self.rule.check(stage)
        assert not any(f.rule_id == "articulation.zero_drive" for f in findings)

    def test_revolute_joint_no_limits_flagged(self):
        stage = _stage([
            _prim("/World/Robot/shoulder",
                  prim_type="PhysicsRevoluteJoint",
                  attrs={}),
        ])
        findings = self.rule.check(stage)
        assert any(f.rule_id == "articulation.no_joint_limits" for f in findings)

    def test_revolute_joint_with_limits_ok(self):
        stage = _stage([
            _prim("/World/Robot/shoulder",
                  prim_type="PhysicsRevoluteJoint",
                  attrs={
                      "physics:lowerLimit": -3.14,
                      "physics:upperLimit": 3.14,
                  }),
        ])
        findings = self.rule.check(stage)
        assert not any(f.rule_id == "articulation.no_joint_limits" for f in findings)

    def test_dangling_joint_target_flagged(self):
        stage = _stage([
            _prim("/World/Robot/joint1",
                  prim_type="PhysicsRevoluteJoint",
                  attrs={"physics:body0": "/World/Robot/MissingLink"}),
        ])
        findings = self.rule.check(stage)
        assert any(f.rule_id == "articulation.dangling_joint_target" for f in findings)

    def test_joint_target_exists_ok(self):
        stage = _stage([
            _prim("/World/Robot/Base", schemas=["PhysicsRigidBodyAPI"]),
            _prim("/World/Robot/joint1",
                  prim_type="PhysicsRevoluteJoint",
                  attrs={"physics:body0": "/World/Robot/Base"}),
        ])
        findings = self.rule.check(stage)
        assert not any(f.rule_id == "articulation.dangling_joint_target" for f in findings)


# ---------------------------------------------------------------------------
# SensorCompletenessValidator
# ---------------------------------------------------------------------------

class TestSensorCompletenessValidator:
    def setup_method(self):
        self.rule = SensorCompletenessValidator()

    def test_camera_without_render_product_flagged(self):
        stage = _stage([
            _prim("/World/Camera", prim_type="Camera"),
        ])
        findings = self.rule.check(stage)
        assert any(f.rule_id == "sensor.camera_no_render_product" for f in findings)

    def test_camera_with_render_product_ok(self):
        stage = _stage([
            _prim("/World/Camera", prim_type="Camera"),
            _prim("/World/RenderProduct", prim_type="RenderProduct",
                  attrs={"cameraPrim": "/World/Camera"}),
        ])
        findings = self.rule.check(stage)
        assert not any(f.rule_id == "sensor.camera_no_render_product" for f in findings)

    def test_imu_sensor_not_on_physics_body_flagged(self):
        stage = _stage([
            _prim("/World/Robot", prim_type="Xform"),  # parent — no RigidBody
            _prim("/World/Robot/IMU", prim_type="IMUSensor"),
        ])
        findings = self.rule.check(stage)
        assert any(f.rule_id == "sensor.physics_sensor_no_body" for f in findings)

    def test_imu_on_rigid_body_ok(self):
        stage = _stage([
            _prim("/World/Robot/Link",
                  prim_type="Xform",
                  schemas=["PhysicsRigidBodyAPI"]),
            _prim("/World/Robot/Link/IMU", prim_type="IMUSensor"),
        ])
        findings = self.rule.check(stage)
        assert not any(f.rule_id == "sensor.physics_sensor_no_body" for f in findings)

    def test_lidar_without_omnigraph_flagged(self):
        # Only warn if og_nodes is non-empty (we have some OG data)
        stage = _stage(
            [_prim("/World/Lidar", prim_type="IsaacSensorRtxLidar")],
            omnigraph_nodes=[{"type": "SomeDifferentNode", "inputs": {}}],
        )
        findings = self.rule.check(stage)
        assert any(f.rule_id == "sensor.lidar_no_omnigraph" for f in findings)

    def test_lidar_no_og_data_no_warning(self):
        # Without any OG data we can't say it's missing
        stage = _stage(
            [_prim("/World/Lidar", prim_type="IsaacSensorRtxLidar")],
            omnigraph_nodes=[],
        )
        findings = self.rule.check(stage)
        assert not any(f.rule_id == "sensor.lidar_no_omnigraph" for f in findings)


# ---------------------------------------------------------------------------
# PerformanceWarningsValidator
# ---------------------------------------------------------------------------

class TestPerformanceWarningsValidator:
    def setup_method(self):
        self.rule = PerformanceWarningsValidator()

    def test_high_poly_mesh_flagged(self):
        stage = _stage([
            _prim("/World/HeavyMesh", prim_type="Mesh",
                  attrs={"vertex_count": 200_000}),
        ])
        findings = self.rule.check(stage)
        assert any(f.rule_id == "performance.high_poly_mesh" for f in findings)

    def test_low_poly_mesh_ok(self):
        stage = _stage([
            _prim("/World/SimpleMesh", prim_type="Mesh",
                  attrs={"vertex_count": 500}),
        ])
        findings = self.rule.check(stage)
        assert not any(f.rule_id == "performance.high_poly_mesh" for f in findings)

    def test_excessive_sublayers_flagged(self):
        stage = _stage([], sublayer_count=15)
        findings = self.rule.check(stage)
        assert any(f.rule_id == "performance.sublayer_count" for f in findings)

    def test_too_many_rigid_bodies_without_gpu_flagged(self):
        prims = [
            _prim(f"/World/Body{i}", schemas=["PhysicsRigidBodyAPI"])
            for i in range(250)
        ]
        stage = _stage(prims, gpu_dynamics=False)
        findings = self.rule.check(stage)
        assert any(f.rule_id == "performance.rigid_body_count" for f in findings)

    def test_many_rigid_bodies_with_gpu_ok(self):
        prims = [
            _prim(f"/World/Body{i}", schemas=["PhysicsRigidBodyAPI"])
            for i in range(250)
        ]
        stage = _stage(prims, gpu_dynamics=True)
        findings = self.rule.check(stage)
        assert not any(f.rule_id == "performance.rigid_body_count" for f in findings)

    def test_too_many_lights_flagged(self):
        prims = [
            _prim(f"/World/Light{i}", prim_type="SphereLight",
                  attrs={"visibility": "inherited"})
            for i in range(20)
        ]
        stage = _stage(prims)
        findings = self.rule.check(stage)
        assert any(f.rule_id == "performance.light_count" for f in findings)

    def test_invisible_lights_not_counted(self):
        prims = [
            _prim(f"/World/Light{i}", prim_type="SphereLight",
                  attrs={"visibility": "invisible"})
            for i in range(20)
        ]
        stage = _stage(prims)
        findings = self.rule.check(stage)
        assert not any(f.rule_id == "performance.light_count" for f in findings)

    def test_total_triangles_warning(self):
        stage = _stage([
            _prim("/World/Mega", prim_type="Mesh",
                  attrs={"face_count": 3_000_000}),
        ])
        findings = self.rule.check(stage)
        assert any(f.rule_id == "performance.total_triangles" for f in findings)


# ---------------------------------------------------------------------------
# IsaacLabSanityValidator
# ---------------------------------------------------------------------------

class TestIsaacLabSanityValidator:
    def setup_method(self):
        self.rule = IsaacLabSanityValidator()

    def test_no_physics_scene_flagged(self):
        stage = _stage([
            _prim("/World/Robot", schemas=["PhysicsArticulationRootAPI"]),
        ])
        findings = self.rule.check(stage)
        assert any(f.rule_id == "isaaclab.no_physics_scene" for f in findings)

    def test_no_ground_plane_flagged(self):
        stage = _stage([
            _prim("/World/physicsScene", prim_type="PhysicsScene"),
            _prim("/World/Robot", schemas=["PhysicsArticulationRootAPI"]),
        ])
        findings = self.rule.check(stage)
        assert any(f.rule_id == "isaaclab.no_ground_plane" for f in findings)

    def test_ground_plane_present_ok(self):
        stage = _stage([
            _prim("/World/physicsScene", prim_type="PhysicsScene"),
            _prim("/World/GroundPlane", prim_type="Plane"),
            _prim("/World/Robot", schemas=["PhysicsArticulationRootAPI",
                                           "PhysicsRigidBodyAPI"]),
        ])
        findings = self.rule.check(stage)
        assert not any(f.rule_id == "isaaclab.no_ground_plane" for f in findings)

    def test_rigid_bodies_no_articulation_root_flagged(self):
        stage = _stage([
            _prim("/World/physicsScene", prim_type="PhysicsScene"),
            _prim("/World/GroundPlane", prim_type="Plane"),
            _prim("/World/Robot/Link", schemas=["PhysicsRigidBodyAPI"]),
        ])
        findings = self.rule.check(stage)
        assert any(f.rule_id == "isaaclab.no_articulation_root" for f in findings)

    def test_env_spacing_too_small_flagged(self):
        stage = _stage([
            _prim("/World/physicsScene", prim_type="PhysicsScene"),
            _prim("/World/GroundPlane", prim_type="Plane"),
            _prim("/World/envs/env_0",
                  attrs={"xformOp:translate": [0, 0, 0]}),
            _prim("/World/envs/env_1",
                  attrs={"xformOp:translate": [0.5, 0, 0]}),
        ])
        findings = self.rule.check(stage)
        assert any(f.rule_id == "isaaclab.env_spacing_too_small" for f in findings)

    def test_env_spacing_ok(self):
        stage = _stage([
            _prim("/World/physicsScene", prim_type="PhysicsScene"),
            _prim("/World/GroundPlane", prim_type="Plane"),
            _prim("/World/envs/env_0",
                  attrs={"xformOp:translate": [0, 0, 0]}),
            _prim("/World/envs/env_1",
                  attrs={"xformOp:translate": [4, 0, 0]}),
        ])
        findings = self.rule.check(stage)
        assert not any(f.rule_id == "isaaclab.env_spacing_too_small" for f in findings)

    def test_articulation_root_no_joints_flagged(self):
        stage = _stage([
            _prim("/World/physicsScene", prim_type="PhysicsScene"),
            _prim("/World/GroundPlane", prim_type="Plane"),
            _prim("/World/Robot", schemas=["PhysicsArticulationRootAPI",
                                           "PhysicsRigidBodyAPI"]),
            # No joints anywhere
        ])
        findings = self.rule.check(stage)
        assert any(f.rule_id == "isaaclab.articulation_no_joints" for f in findings)

    def test_articulation_with_joints_ok(self):
        stage = _stage([
            _prim("/World/physicsScene", prim_type="PhysicsScene"),
            _prim("/World/GroundPlane", prim_type="Plane"),
            _prim("/World/Robot", schemas=["PhysicsArticulationRootAPI",
                                           "PhysicsRigidBodyAPI"]),
            _prim("/World/Robot/joint0", prim_type="PhysicsRevoluteJoint"),
        ])
        findings = self.rule.check(stage)
        assert not any(f.rule_id == "isaaclab.articulation_no_joints" for f in findings)


# ---------------------------------------------------------------------------
# RobotMotionValidator
# ---------------------------------------------------------------------------

class TestRobotMotionValidator:
    def setup_method(self):
        self.rule = RobotMotionValidator()

    def test_caster_joint_with_stiffness_flagged(self):
        stage = _stage([
            _prim("/World/Robot/caster_joint",
                  prim_type="PhysicsRevoluteJoint",
                  attrs={"drive:angular:physics:stiffness": 200.0}),
        ])
        findings = self.rule.check(stage)
        assert any(f.rule_id == "robot_motion.caster_stiffness" for f in findings)
        assert findings[0].severity == "error"

    def test_caster_joint_zero_stiffness_ok(self):
        stage = _stage([
            _prim("/World/Robot/caster_joint",
                  prim_type="PhysicsRevoluteJoint",
                  attrs={"drive:angular:physics:stiffness": 0.0}),
        ])
        findings = self.rule.check(stage)
        assert not any(f.rule_id == "robot_motion.caster_stiffness" for f in findings)

    def test_floor_without_physics_material_flagged(self):
        stage = _stage([
            _prim("/World/Floor",
                  prim_type="Mesh",
                  schemas=["PhysicsCollisionAPI"],
                  attrs={}),
        ])
        findings = self.rule.check(stage)
        assert any(f.rule_id == "robot_motion.floor_no_friction" for f in findings)

    def test_floor_with_physics_material_binding_ok(self):
        stage = _stage([
            _prim("/World/Floor",
                  prim_type="Mesh",
                  schemas=["PhysicsCollisionAPI"],
                  attrs={"material:binding:physics": "/World/FloorMat"}),
        ])
        findings = self.rule.check(stage)
        assert not any(f.rule_id == "robot_motion.floor_no_friction" for f in findings)

    def test_zero_gravity_direction_flagged(self):
        stage = _stage([
            _prim("/World/physicsScene",
                  prim_type="PhysicsScene",
                  attrs={"physics:gravityDirection": [0, 0, 0]}),
        ])
        findings = self.rule.check(stage)
        assert any(f.rule_id == "robot_motion.zero_gravity" for f in findings)

    def test_valid_gravity_ok(self):
        stage = _stage([
            _prim("/World/physicsScene",
                  prim_type="PhysicsScene",
                  attrs={"physics:gravityDirection": [0, 0, -1],
                         "physics:gravityMagnitude": 9.81}),
        ])
        findings = self.rule.check(stage)
        assert not any(f.rule_id == "robot_motion.zero_gravity" for f in findings)

    def test_negative_gravity_magnitude_flagged(self):
        stage = _stage([
            _prim("/World/physicsScene",
                  prim_type="PhysicsScene",
                  attrs={"physics:gravityMagnitude": -9.81}),
        ])
        findings = self.rule.check(stage)
        assert any(f.rule_id == "robot_motion.negative_gravity_mag" for f in findings)

    def test_conflicting_articulation_controllers_flagged(self):
        og_nodes = [
            {
                "type": "isaacsim.core.nodes.ArticulationController",
                "path": "/ActionGraph/ctrl_a",
                "graph_path": "/ActionGraph",
                "inputs": {
                    "robotPath": "/World/Robot",
                    "jointNames": ["joint0", "joint1"],
                },
            },
            {
                "type": "isaacsim.core.nodes.ArticulationController",
                "path": "/ActionGraph2/ctrl_b",
                "graph_path": "/ActionGraph2",
                "inputs": {
                    "robotPath": "/World/Robot",
                    "jointNames": ["joint0", "joint1"],
                },
            },
        ]
        stage = _stage([], omnigraph_nodes=og_nodes)
        findings = self.rule.check(stage)
        assert any(f.rule_id == "robot_motion.conflicting_controllers" for f in findings)

    def test_non_overlapping_controllers_ok(self):
        og_nodes = [
            {
                "type": "isaacsim.core.nodes.ArticulationController",
                "path": "/ActionGraph/ctrl_a",
                "graph_path": "/ActionGraph",
                "inputs": {
                    "robotPath": "/World/Robot",
                    "jointNames": ["joint0"],
                },
            },
            {
                "type": "isaacsim.core.nodes.ArticulationController",
                "path": "/ActionGraph2/ctrl_b",
                "graph_path": "/ActionGraph2",
                "inputs": {
                    "robotPath": "/World/Robot",
                    "jointNames": ["joint1"],
                },
            },
        ]
        stage = _stage([], omnigraph_nodes=og_nodes)
        findings = self.rule.check(stage)
        assert not any(f.rule_id == "robot_motion.conflicting_controllers" for f in findings)


# ---------------------------------------------------------------------------
# AnalysisOrchestrator (integration)
# ---------------------------------------------------------------------------

class TestAnalysisOrchestrator:
    def test_orchestrator_loads_all_packs(self):
        orch = AnalysisOrchestrator()
        # Should have at least 9 registered packs
        assert len(orch.rules) >= 9

    def test_orchestrator_returns_result_with_findings(self):
        orch = AnalysisOrchestrator()
        stage = _stage([
            # Rigid body without collision → schema_consistency + material_physics
            _prim("/World/Box", schemas=["PhysicsRigidBodyAPI"]),
            # Camera without render product → sensor_completeness
            _prim("/World/Cam", prim_type="Camera"),
        ])
        result = orch.run_analysis(stage)
        assert result.total_prims == 2
        assert len(result.findings) > 0
        assert isinstance(result.findings_by_severity, dict)
        assert result.duration_seconds >= 0

    def test_orchestrator_empty_scene_no_physics_errors(self):
        # Use only non-isaaclab packs so the PhysicsScene-missing rule doesn't fire.
        orch = AnalysisOrchestrator(enabled_packs=[
            "schema_consistency", "import_health", "material_physics",
            "articulation_integrity", "sensor_completeness",
            "performance_warnings", "robot_motion",
        ])
        result = orch.run_analysis(_stage())
        errors = [f for f in result.findings if f.severity == "error"]
        assert len(errors) == 0

    def test_orchestrator_selective_packs(self):
        orch = AnalysisOrchestrator(enabled_packs=["schema_consistency"])
        assert len(orch.rules) == 1

    def test_orchestrator_result_model_valid(self):
        from service.isaac_assist_service.analysis.models import StageAnalysisResult
        orch = AnalysisOrchestrator()
        result = orch.run_analysis(_stage([
            _prim("/World/A", schemas=["PhysicsRigidBodyAPI"]),
        ]))
        # Should be a valid Pydantic model
        assert isinstance(result, StageAnalysisResult)
        assert result.analysis_id  # non-empty hex string
        assert "error" in result.findings_by_severity
