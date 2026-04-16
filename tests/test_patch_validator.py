"""
L0 tests for patch_validator.py — pre-flight code validation rules.
Tests every rule: dangerous patterns blocked, safe patterns allowed.
"""
import pytest

pytestmark = pytest.mark.l0

from service.isaac_assist_service.chat.tools.patch_validator import (
    PatchIssue,
    validate_patch,
    format_issues_for_llm,
    has_blocking_issues,
    _check_omnigraph_type_mismatch,
    _check_omnigraph_legacy_namespace,
    _check_omnigraph_diff_exec_out,
    _check_omnigraph_use_path,
    _check_omnigraph_bad_api,
    _check_omnigraph_backing_type,
    _check_carter_joint_names,
    _check_triangle_mesh_on_wheels,
    _check_missing_import_omni_usd,
    _check_raw_list_for_vec,
    _check_unsafe_add_xform_op,
    _check_create_attribute_signature,
)


# ---------------------------------------------------------------------------
# OmniGraph rules
# ---------------------------------------------------------------------------

class TestOGTypeMismatch:
    def test_direct_twist_to_diff_blocked(self):
        code = """
og.Controller.edit({}, {
    keys.CONNECT: [
        ('SubscribeTwist.outputs:linearVelocity', 'DiffController.inputs:linearVelocity'),
    ],
})
"""
        issues = _check_omnigraph_type_mismatch(code)
        assert len(issues) == 1
        assert issues[0].severity == "error"
        assert issues[0].rule == "og_double3_to_double"

    def test_with_break_vector_ok(self):
        code = """
# Wire through Break3Vector
og.Controller.edit({}, {
    keys.CONNECT: [
        ('SubscribeTwist.outputs:linearVelocity', 'Break3Vector.inputs:tuple'),
        ('Break3Vector.outputs:x', 'DiffController.inputs:linearVelocity'),
    ],
})
"""
        issues = _check_omnigraph_type_mismatch(code)
        assert len(issues) == 0

    def test_no_twist_at_all(self):
        issues = _check_omnigraph_type_mismatch("print('hello')")
        assert len(issues) == 0


class TestOGLegacyNamespace:
    def test_legacy_namespace_blocked(self):
        code = "'omni.isaac.ros2_bridge.ROS2PublishClock'"
        issues = _check_omnigraph_legacy_namespace(code)
        assert len(issues) == 1
        assert issues[0].severity == "error"
        assert issues[0].rule == "og_legacy_namespace"

    def test_legacy_core_nodes_blocked(self):
        code = "'omni.isaac.core_nodes.IsaacArticulationController'"
        issues = _check_omnigraph_legacy_namespace(code)
        assert len(issues) == 1

    def test_isaacsim_namespace_ok(self):
        code = "'isaacsim.ros2.bridge.ROS2PublishClock'"
        issues = _check_omnigraph_legacy_namespace(code)
        assert len(issues) == 0


class TestOGDiffExecOut:
    def test_diff_exec_out_blocked(self):
        code = "'DiffController.outputs:execOut'"
        issues = _check_omnigraph_diff_exec_out(code)
        assert len(issues) == 1
        assert issues[0].rule == "og_diff_no_exec_out"

    def test_normal_exec_in_ok(self):
        code = "'DiffController.inputs:execIn'"
        issues = _check_omnigraph_diff_exec_out(code)
        assert len(issues) == 0


class TestOGUsePath:
    def test_use_path_blocked(self):
        code = "ArticulationController.inputs:usePath"
        issues = _check_omnigraph_use_path(code)
        assert len(issues) == 1
        assert issues[0].rule == "og_use_path_missing"

    def test_robot_path_ok(self):
        code = "ArticulationController.inputs:robotPath"
        issues = _check_omnigraph_use_path(code)
        assert len(issues) == 0


class TestOGBadAPI:
    def test_get_node_path_blocked(self):
        code = "node.get_node_path()"
        issues = _check_omnigraph_bad_api(code)
        assert len(issues) == 1

    def test_get_attribute_count_blocked(self):
        code = "node.get_attribute_count()"
        issues = _check_omnigraph_bad_api(code)
        assert len(issues) == 1

    def test_get_prim_path_ok(self):
        code = "node.get_prim_path()"
        issues = _check_omnigraph_bad_api(code)
        assert len(issues) == 0


class TestOGBackingType:
    def test_flatcache_shared_warns(self):
        code = "GRAPH_BACKING_TYPE_FLATCACHE_SHARED"
        issues = _check_omnigraph_backing_type(code)
        assert len(issues) == 1
        assert issues[0].severity == "warning"

    def test_fabric_shared_ok(self):
        code = "GRAPH_BACKING_TYPE_FABRIC_SHARED"
        issues = _check_omnigraph_backing_type(code)
        assert len(issues) == 0


# ---------------------------------------------------------------------------
# Nova Carter rules
# ---------------------------------------------------------------------------

class TestCarterJointNames:
    def test_wrong_joint_names_blocked(self):
        code = """
# Nova Carter setup
joint_drive_fl = '/World/NovaCarter/joint_drive_fl'
"""
        issues = _check_carter_joint_names(code)
        assert len(issues) == 1
        assert issues[0].rule == "carter_wrong_joints"

    def test_correct_joint_names_ok(self):
        code = """
# Nova Carter setup
joint_wheel_left = '/World/NovaCarter/joint_wheel_left'
joint_wheel_right = '/World/NovaCarter/joint_wheel_right'
"""
        issues = _check_carter_joint_names(code)
        assert len(issues) == 0

    def test_wrong_names_without_carter_context_ok(self):
        """Wrong joint names should only fire if Carter context is present."""
        code = "joint_drive_fl = 'some_joint'"
        issues = _check_carter_joint_names(code)
        assert len(issues) == 0


# ---------------------------------------------------------------------------
# PhysX rules
# ---------------------------------------------------------------------------

class TestTriangleMeshOnWheels:
    def test_triangle_mesh_on_wheel_blocked(self):
        code = """
wheel_prim.GetAttribute('physics:approximation').Set('triangle')
"""
        issues = _check_triangle_mesh_on_wheels(code)
        assert len(issues) == 1
        assert issues[0].rule == "physx_triangle_mesh_wheel"

    def test_convex_hull_on_wheel_ok(self):
        code = """
wheel_prim.GetAttribute('physics:approximation').Set('convexHull')
"""
        issues = _check_triangle_mesh_on_wheels(code)
        assert len(issues) == 0

    def test_triangle_mesh_not_on_wheel_ok(self):
        code = """
table_prim.GetAttribute('physics:approximation').Set('triangle')
"""
        issues = _check_triangle_mesh_on_wheels(code)
        assert len(issues) == 0


# ---------------------------------------------------------------------------
# USD rules
# ---------------------------------------------------------------------------

class TestMissingImportOmniUsd:
    def test_uses_without_import_blocked(self):
        code = """
stage = omni.usd.get_context().get_stage()
"""
        issues = _check_missing_import_omni_usd(code)
        assert len(issues) == 1
        assert issues[0].rule == "missing_import_omni_usd"

    def test_with_import_ok(self):
        code = """
import omni.usd
stage = omni.usd.get_context().get_stage()
"""
        issues = _check_missing_import_omni_usd(code)
        assert len(issues) == 0

    def test_from_import_ok(self):
        code = """
from omni.usd import get_context
stage = omni.usd.get_context().get_stage()
"""
        issues = _check_missing_import_omni_usd(code)
        assert len(issues) == 0


class TestRawListForVec:
    def test_raw_list_with_xform_warns(self):
        code = """
attr = prim.GetAttribute('xformOp:translate')
attr.Set([1, 2, 3])
"""
        issues = _check_raw_list_for_vec(code)
        assert len(issues) == 1
        assert issues[0].severity == "warning"

    def test_gf_vec3d_ok(self):
        code = """
attr = prim.GetAttribute('xformOp:translate')
attr.Set(Gf.Vec3d(1, 2, 3))
"""
        issues = _check_raw_list_for_vec(code)
        assert len(issues) == 0


class TestUnsafeAddXformOp:
    def test_add_translate_op_warns(self):
        code = """
xf = UsdGeom.Xformable(prim)
xf.AddTranslateOp().Set(Gf.Vec3d(0, 0, 0))
"""
        issues = _check_unsafe_add_xform_op(code)
        assert len(issues) == 1
        assert issues[0].severity == "warning"

    def test_add_scale_op_warns(self):
        code = """
xf = UsdGeom.Xformable(prim)
xf.AddScaleOp().Set(Gf.Vec3d(1, 1, 1))
"""
        issues = _check_unsafe_add_xform_op(code)
        assert len(issues) == 1

    def test_with_safe_set_ok(self):
        code = """
_safe_set_translate(prim, (0, 0, 0))
"""
        issues = _check_unsafe_add_xform_op(code)
        assert len(issues) == 0

    def test_with_get_ordered_check_ok(self):
        code = """
for op in xf.GetOrderedXformOps():
    pass
xf.AddTranslateOp().Set(Gf.Vec3d(0, 0, 0))
"""
        issues = _check_unsafe_add_xform_op(code)
        assert len(issues) == 0


class TestCreateAttributeSignature:
    def test_python_type_blocked(self):
        code = "prim.CreateAttribute('myAttr', float)"
        issues = _check_create_attribute_signature(code)
        assert len(issues) == 1
        assert issues[0].rule == "usd_create_attr_signature"

    def test_gf_type_blocked(self):
        code = "prim.CreateAttribute('myAttr', Gf.Vec3d)"
        issues = _check_create_attribute_signature(code)
        assert len(issues) == 1

    def test_sdf_value_type_ok(self):
        code = "prim.CreateAttribute('myAttr', Sdf.ValueTypeNames.Float)"
        issues = _check_create_attribute_signature(code)
        assert len(issues) == 0


# ---------------------------------------------------------------------------
# Aggregate validate_patch
# ---------------------------------------------------------------------------

class TestValidatePatch:
    def test_clean_code_no_issues(self):
        code = """
import omni.usd
stage = omni.usd.get_context().get_stage()
prim = stage.DefinePrim('/World/Cube', 'Cube')
print('Created cube')
"""
        issues = validate_patch(code)
        assert len(issues) == 0

    def test_multiple_issues(self):
        code = """
stage = omni.usd.get_context().get_stage()
xf.AddTranslateOp().Set([1, 2, 3])
attr = prim.GetAttribute('xformOp:translate')
"""
        issues = validate_patch(code)
        # Should catch: missing import, raw list, unsafe add xform op
        assert len(issues) >= 2

    def test_validator_exception_does_not_crash(self, monkeypatch):
        """If a single validator crashes, validate_patch should still return."""
        import service.isaac_assist_service.chat.tools.patch_validator as pv

        def crasher(code):
            raise RuntimeError("Intentional test crash")

        orig = list(pv._ALL_VALIDATORS)
        monkeypatch.setattr(pv, "_ALL_VALIDATORS", [crasher] + orig)
        issues = validate_patch("safe code")
        # Should not raise; the crasher is logged and skipped
        assert isinstance(issues, list)


class TestFormatIssuesForLLM:
    def test_empty_list_returns_empty(self):
        assert format_issues_for_llm([]) == ""

    def test_formats_issues(self):
        issues = [
            PatchIssue(
                severity="error",
                rule="test_rule",
                message="Something bad",
                fix_hint="Do this instead",
            )
        ]
        text = format_issues_for_llm(issues)
        assert "PRE-FLIGHT VALIDATION FAILED" in text
        assert "test_rule" in text
        assert "Do this instead" in text


class TestHasBlockingIssues:
    def test_no_issues(self):
        assert has_blocking_issues([]) is False

    def test_warning_only(self):
        issues = [PatchIssue(severity="warning", rule="x", message="y")]
        assert has_blocking_issues(issues) is False

    def test_error_present(self):
        issues = [PatchIssue(severity="error", rule="x", message="y")]
        assert has_blocking_issues(issues) is True

    def test_mixed(self):
        issues = [
            PatchIssue(severity="warning", rule="a", message="b"),
            PatchIssue(severity="error", rule="c", message="d"),
        ]
        assert has_blocking_issues(issues) is True
