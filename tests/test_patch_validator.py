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
    _check_create_prim_default_path,
    _check_isa_on_api_schema,
    _check_kit_command_name,
    _check_clear_xform_op_order,
    _check_delete_then_create_same_path,
    _check_pipeline_stage_enum_mismatch,
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


class TestCreatePrimDefaultPath:
    """Pins the 2026-04-19 regression: agent emitted
       omni.kit.commands.execute('CreateMeshPrimWithDefaultXform', prim_type='Cube')
    with no prim_path — Kit defaulted to /World/Cube at origin and the xform
    code aimed at /World/Cube_3 never landed. Validator must block this class."""

    def test_create_mesh_prim_with_default_xform_no_path_blocks(self):
        code = "omni.kit.commands.execute('CreateMeshPrimWithDefaultXform', prim_type='Cube')"
        issues = _check_create_prim_default_path(code)
        assert len(issues) == 1
        assert issues[0].severity == "error"
        assert issues[0].rule == "usd_create_prim_no_path"

    def test_create_prim_no_path_blocks(self):
        code = "omni.kit.commands.execute('CreatePrim', prim_type='Xform')"
        issues = _check_create_prim_default_path(code)
        assert len(issues) == 1

    def test_create_mesh_prim_no_path_blocks(self):
        code = "omni.kit.commands.execute('CreateMeshPrim', prim_type='Cube')"
        issues = _check_create_prim_default_path(code)
        assert len(issues) == 1

    def test_create_prim_with_path_passes(self):
        """Regular CreatePrim (non-MeshDefault) with prim_path is fine."""
        code = (
            "omni.kit.commands.execute('CreatePrim', prim_type='Cube', "
            "prim_path='/World/Cube_3')"
        )
        issues = _check_create_prim_default_path(code)
        assert len(issues) == 0

    def test_create_mesh_prim_with_default_xform_blocks_even_with_path(self):
        """Upgrade 2026-04-19: CreateMeshPrimWithDefaultXform is ALWAYS broken
        because it authors mesh-geometry attrs on top of the TypeName. Observed
        in session: Cube 3 rendered as warped mesh even though the path was
        explicit. Use UsdGeom.Cube.Define instead."""
        code = (
            "omni.kit.commands.execute('CreateMeshPrimWithDefaultXform', "
            "prim_type='Cube', prim_path='/World/Cube_3')"
        )
        issues = _check_create_prim_default_path(code)
        assert len(issues) == 1
        assert issues[0].rule == "usd_create_mesh_prim_with_default_xform"
        assert issues[0].severity == "error"

    def test_define_prim_path_passes(self):
        code = "prim = stage.DefinePrim('/World/Cube_3', 'Cube')"
        issues = _check_create_prim_default_path(code)
        assert len(issues) == 0

    def test_usdgeom_cube_define_passes(self):
        code = "cube = UsdGeom.Cube.Define(stage, '/World/Cube_3')"
        issues = _check_create_prim_default_path(code)
        assert len(issues) == 0

    def test_two_bad_calls_both_flagged(self):
        """Two successive bad creates — observed in the real trace."""
        code = """
import omni.kit.commands
omni.kit.commands.execute('CreateMeshPrimWithDefaultXform', prim_type='Cube')
omni.kit.commands.execute('MovePrim', path_from='/World/Cube', path_to='/World/Cube_3')
omni.kit.commands.execute('CreateMeshPrimWithDefaultXform', prim_type='Cube')
omni.kit.commands.execute('MovePrim', path_from='/World/Cube', path_to='/World/Cube_4')
"""
        issues = _check_create_prim_default_path(code)
        assert len(issues) == 2, f"Expected 2 issues (both creates), got {len(issues)}"
        assert all(i.severity == "error" for i in issues)

    def test_blocks_in_full_validate_patch(self):
        """Aggregate validate_patch must treat this as blocking."""
        code = """
import omni.kit.commands
omni.kit.commands.execute('CreateMeshPrimWithDefaultXform', prim_type='Cube')
"""
        issues = validate_patch(code)
        assert has_blocking_issues(issues) is True


class TestDeleteThenRecreateSamePath:
    """Pins the 2026-04-19 failure: DeletePrims('/World/Cube3') +
    CreatePrim(prim_path='/World/Cube3') left session-layer attribute specs
    from the old prim, which composed back onto the new prim. Viewport
    rendered the ghost mesh geometry instead of the fresh Cube parametric
    shape. Fix: use a different path name, or explicitly wipe session
    layer specs first."""

    def test_delete_then_create_same_path_warns(self):
        code = """
import omni.kit.commands
omni.kit.commands.execute('DeletePrims', paths=['/World/Cube3'])
omni.kit.commands.execute('CreatePrim', prim_path='/World/Cube3', prim_type='Cube')
"""
        issues = _check_delete_then_create_same_path(code)
        assert len(issues) == 1
        assert issues[0].rule == "usd_delete_then_recreate_same_path"
        assert issues[0].severity == "warning"

    def test_delete_then_define_prim_same_path_warns(self):
        code = """
import omni.kit.commands
from pxr import UsdGeom
omni.kit.commands.execute('DeletePrims', paths=['/World/X'])
UsdGeom.Cube.Define(stage, '/World/X')
"""
        issues = _check_delete_then_create_same_path(code)
        assert len(issues) == 1

    def test_delete_then_create_different_path_ok(self):
        code = """
import omni.kit.commands
omni.kit.commands.execute('DeletePrims', paths=['/World/Cube3'])
omni.kit.commands.execute('CreatePrim', prim_path='/World/CubeThree', prim_type='Cube')
"""
        issues = _check_delete_then_create_same_path(code)
        assert len(issues) == 0

    def test_delete_alone_ok(self):
        code = "omni.kit.commands.execute('DeletePrims', paths=['/World/X'])"
        issues = _check_delete_then_create_same_path(code)
        assert len(issues) == 0

    def test_create_alone_ok(self):
        code = "omni.kit.commands.execute('CreatePrim', prim_path='/World/X', prim_type='Cube')"
        issues = _check_delete_then_create_same_path(code)
        assert len(issues) == 0

    def test_not_blocking(self):
        """Warning only — legitimate uses exist (e.g. after explicit layer
        cleanup). Don't break flows that do it correctly."""
        code = """
omni.kit.commands.execute('DeletePrims', paths=['/World/X'])
omni.kit.commands.execute('CreatePrim', prim_path='/World/X', prim_type='Cube')
"""
        from service.isaac_assist_service.chat.tools.patch_validator import has_blocking_issues, validate_patch
        assert has_blocking_issues(validate_patch(code)) is False


class TestKitCommandName:
    """Pins the 2026-04-19 regression: agent called
       omni.kit.commands.execute('TransformPrimCommand', ...)
    which silently returned success=false at RPC because that command name
    doesn't exist (real name: TransformPrimSRT). Agent kept going and the
    user saw no viewport change."""

    def test_transform_prim_command_blocks(self):
        code = 'omni.kit.commands.execute("TransformPrimCommand", path="/X", new_translation=Gf.Vec3d(0,0,0))'
        issues = _check_kit_command_name(code)
        assert len(issues) == 1
        assert issues[0].rule == "kit_unknown_command"
        assert "TransformPrimSRT" in issues[0].message

    def test_transform_prim_srt_passes(self):
        code = 'omni.kit.commands.execute("TransformPrimSRT", path="/X", new_translation=Gf.Vec3d(0,0,0))'
        assert _check_kit_command_name(code) == []

    def test_create_prim_passes(self):
        code = "omni.kit.commands.execute('CreatePrim', prim_type='Cube')"
        assert _check_kit_command_name(code) == []

    def test_bogus_command_blocks(self):
        code = "omni.kit.commands.execute('FancifyStage')"
        issues = _check_kit_command_name(code)
        assert len(issues) == 1

    def test_command_suffix_hint(self):
        """A name like 'DeletePrimsCommand' should hint at the real 'DeletePrims'."""
        code = "omni.kit.commands.execute('DeletePrimsCommand', paths=['/X'])"
        issues = _check_kit_command_name(code)
        assert len(issues) == 1
        assert "DeletePrims" in issues[0].message


class TestClearXformOpOrder:
    """ClearXformOpOrder() is almost always misuse — warn so the agent
    self-corrects toward the reuse-existing-op pattern."""

    def test_clear_xform_op_order_warns(self):
        code = "xform.ClearXformOpOrder()\nxform.AddTranslateOp().Set(pos)"
        issues = _check_clear_xform_op_order(code)
        assert len(issues) == 1
        assert issues[0].severity == "warning"

    def test_safe_pattern_no_warn(self):
        code = """
for op in xformable.GetOrderedXformOps():
    if op.GetOpType() == UsdGeom.XformOp.TypeTranslate:
        op.Set(pos)
"""
        assert _check_clear_xform_op_order(code) == []

    def test_clear_does_not_block(self):
        """Warning only, not blocking — some flows legitimately need it."""
        code = "x.ClearXformOpOrder()"
        issues = validate_patch(code)
        assert has_blocking_issues(issues) is False


class TestPipelineStageEnumMismatch:
    """Pins the 2026-04-19 regression: both the LLM agent AND a human debugger
    hit this API gotcha. og.Controller.edit(pipeline_stage=...) takes
    GraphPipelineStage, not GraphBackingType. Runtime error is opaque
    ('incompatible function arguments'). Validator catches at authoring time."""

    def test_direct_kwarg_blocks(self):
        code = "og.Controller.edit(pipeline_stage=og.GraphBackingType.GRAPH_BACKING_TYPE_FABRIC_SHARED)"
        issues = _check_pipeline_stage_enum_mismatch(code)
        assert len(issues) == 1
        assert issues[0].rule == "og_pipeline_stage_enum"

    def test_dict_literal_blocks(self):
        code = 'og.Controller.edit({"pipeline_stage": og.GraphBackingType.GRAPH_BACKING_TYPE_FLATCACHE_SHARED}, {})'
        issues = _check_pipeline_stage_enum_mismatch(code)
        assert len(issues) == 1
        assert issues[0].rule == "og_pipeline_stage_enum"

    def test_indirect_variable_blocks(self):
        code = """
_backing = og.GraphBackingType.GRAPH_BACKING_TYPE_FABRIC_SHARED
og.Controller.edit({'pipeline_stage': _backing}, {})
"""
        issues = _check_pipeline_stage_enum_mismatch(code)
        assert len(issues) == 1
        assert issues[0].rule == "og_pipeline_stage_enum_indirect"

    def test_correct_pipeline_stage_passes(self):
        code = 'og.Controller.edit({"pipeline_stage": og.GraphPipelineStage.GRAPH_PIPELINE_STAGE_SIMULATION}, {})'
        assert _check_pipeline_stage_enum_mismatch(code) == []

    def test_no_pipeline_stage_passes(self):
        code = "og.Controller.edit({}, {keys.CREATE_NODES: [('n', 'type.Name')]})"
        assert _check_pipeline_stage_enum_mismatch(code) == []

    def test_blocks_in_full_validate_patch(self):
        code = 'og.Controller.edit(pipeline_stage=og.GraphBackingType.GRAPH_BACKING_TYPE_FABRIC_SHARED)'
        assert has_blocking_issues(validate_patch(code)) is True


class TestIsAOnApiSchema:
    """Pins the 2026-04-19 regression: agent emitted
        lights = [p for p in stage.Traverse() if p.IsA(UsdLux.LightAPI)]
    got 0 results (LightAPI is applied-schema, not prim-type), then fabricated
    'Isaac Sim auto-enables a headlight' to rationalize the bad data."""

    def test_isa_usdlux_lightapi_blocks(self):
        code = "lights = [p for p in stage.Traverse() if p.IsA(UsdLux.LightAPI)]"
        issues = _check_isa_on_api_schema(code)
        assert len(issues) == 1
        assert issues[0].severity == "error"
        assert issues[0].rule == "usd_isa_on_api_schema"
        assert "HasAPI" in issues[0].fix_hint

    def test_isa_usdphysics_collisionapi_blocks(self):
        code = "collision = prim.IsA(UsdPhysics.CollisionAPI)"
        issues = _check_isa_on_api_schema(code)
        assert len(issues) == 1

    def test_isa_physxschema_api_blocks(self):
        code = "ok = prim.IsA(PhysxSchema.PhysxArticulationAPI)"
        issues = _check_isa_on_api_schema(code)
        assert len(issues) == 1

    def test_isa_on_prim_type_ok(self):
        """DomeLight IS a prim type — IsA is correct here."""
        code = "lights = [p for p in stage.Traverse() if p.IsA(UsdLux.DomeLight)]"
        issues = _check_isa_on_api_schema(code)
        assert len(issues) == 0

    def test_isa_on_cube_ok(self):
        code = "cubes = [p for p in stage.Traverse() if p.IsA(UsdGeom.Cube)]"
        issues = _check_isa_on_api_schema(code)
        assert len(issues) == 0

    def test_has_api_ok(self):
        code = "lights = [p for p in stage.Traverse() if p.HasAPI(UsdLux.LightAPI)]"
        issues = _check_isa_on_api_schema(code)
        assert len(issues) == 0

    def test_blocks_in_full_validate_patch(self):
        code = """
import omni.usd
from pxr import Usd, UsdLux
stage = omni.usd.get_context().get_stage()
lights = [p for p in stage.Traverse() if p.IsA(UsdLux.LightAPI)]
"""
        issues = validate_patch(code)
        assert has_blocking_issues(issues) is True


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
