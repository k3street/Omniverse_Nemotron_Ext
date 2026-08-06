"""L0 tests for the sim-ready asset augmentation tools.

Covers `make_sim_ready` / `sim_ready_audit` codegen and the
`sim_readiness` stage validator.
"""
import asyncio
import sys

import pytest

pytestmark = pytest.mark.l0

from service.isaac_assist_service.chat.tools.handlers import physics as physics_handlers
from service.isaac_assist_service.chat.tools.handlers.physics import (
    _gen_articulate_asset,
    _gen_make_sim_ready,
    _handle_sim_ready_audit,
)
from service.isaac_assist_service.analysis.validators.sim_readiness import (
    SimReadinessRule,
)


# ---------------------------------------------------------------------------
# make_sim_ready codegen


class TestMakeSimReadyCodegen:
    def test_default_profile_compiles(self):
        code = _gen_make_sim_ready({"prim_path": "/World/Mug"})
        compile(code, "<gen>", "exec")
        assert "_profile = 'manipulable'" in code
        assert "_approx = 'convexHull'" in code
        assert "RigidBodyAPI" in code
        assert "MassAPI" in code

    def test_material_resolves_database_values(self):
        code = _gen_make_sim_ready(
            {"prim_path": "/World/Mug", "material": "ceramic"}
        )
        compile(code, "<gen>", "exec")
        assert "_mat_name = 'ceramic'" in code
        # density must flow from the database into the mass estimate
        db = physics_handlers._load_physics_materials()
        density = db["materials"]["ceramic"]["density_kg_m3"]
        assert f"_density = {density!r}" in code

    def test_material_alias_normalizes(self):
        code = _gen_make_sim_ready({"prim_path": "/World/X", "material": "steel"})
        compile(code, "<gen>", "exec")
        assert "_mat_name = 'steel_" in code  # steel_mild or steel_stainless

    def test_explicit_mass_and_kinematic(self):
        code = _gen_make_sim_ready(
            {"prim_path": "/World/X", "mass_kg": 2.5, "kinematic": True}
        )
        compile(code, "<gen>", "exec")
        assert "_explicit_mass = 2.5" in code
        assert "_kinematic = True" in code

    def test_skip_patterns_lowercased(self):
        code = _gen_make_sim_ready(
            {"prim_path": "/World/X", "skip_name_patterns": ["Screw", "BOLT"]}
        )
        compile(code, "<gen>", "exec")
        assert "_skip = ['screw', 'bolt']" in code

    def test_static_profile_compiles(self):
        code = _gen_make_sim_ready(
            {"prim_path": "/World/Table", "profile": "furniture"}
        )
        compile(code, "<gen>", "exec")
        assert "_profile = 'furniture'" in code

    def test_unknown_profile_rejected(self):
        code = _gen_make_sim_ready({"prim_path": "/World/X", "profile": "bogus"})
        assert code.startswith("raise ValueError")
        compile(code, "<gen>", "exec")

    def test_unknown_approximation_rejected(self):
        code = _gen_make_sim_ready(
            {"prim_path": "/World/X", "approximation": "octree"}
        )
        assert code.startswith("raise ValueError")
        compile(code, "<gen>", "exec")

    def test_unknown_material_rejected_with_available_list(self):
        code = _gen_make_sim_ready(
            {"prim_path": "/World/X", "material": "unobtainium"}
        )
        assert code.startswith("raise ValueError")
        assert "aluminum" in code  # lists available materials
        compile(code, "<gen>", "exec")

    def test_baked_class_callout_present(self):
        code = _gen_make_sim_ready({"prim_path": "/World/OfficeChair"})
        compile(code, "<gen>", "exec")
        assert "baked asset" in code
        assert "single fused mesh" in code

    def test_fill_ratio_clamped(self):
        code = _gen_make_sim_ready({"prim_path": "/World/X", "fill_ratio": 7.0})
        assert "_fill_ratio = 1.0" in code
        code = _gen_make_sim_ready({"prim_path": "/World/X", "fill_ratio": -1})
        assert "_fill_ratio = 0.0" in code


# ---------------------------------------------------------------------------
# sim_ready_audit codegen


class TestSimReadyAudit:
    def _run(self, args, monkeypatch):
        captured = {}

        async def fake_queue(code, description="", timeout=600):
            captured["code"] = code
            captured["description"] = description
            return {"queued": True}

        from service.isaac_assist_service.chat.tools import kit_tools

        monkeypatch.setattr(kit_tools, "queue_exec_patch", fake_queue)
        asyncio.run(_handle_sim_ready_audit(args))
        return captured

    def test_audit_code_compiles(self, monkeypatch):
        captured = self._run(
            {"prim_path": "/World/Asset", "expect_dynamic": True}, monkeypatch
        )
        compile(captured["code"], "<audit>", "exec")
        assert "_root_path = '/World/Asset'" in captured["code"]
        assert "_expect_dynamic = True" in captured["code"]
        assert captured["description"] == "sim_ready_audit /World/Asset"

    def test_audit_defaults_to_world(self, monkeypatch):
        captured = self._run({}, monkeypatch)
        compile(captured["code"], "<audit>", "exec")
        assert "_root_path = '/World'" in captured["code"]
        assert "_expect_dynamic = False" in captured["code"]

    def test_audit_includes_fidelity_callouts(self, monkeypatch):
        captured = self._run({}, monkeypatch)
        assert "baked asset" in captured["code"]
        assert "articulation candidates" in captured["code"]
        assert "no physics material bound" in captured["code"]
        # fidelity caps are ERRORS (gate sim-readiness), tracked separately
        # from physics errors via the 'simulable' flag
        assert "category='fidelity'" in captured["code"]
        assert "result['simulable']" in captured["code"]


# ---------------------------------------------------------------------------
# articulate_asset codegen


def _drawer_joint(**over):
    j = {
        "name": "drawer_slide", "joint_type": "prismatic",
        "parent_prim": "Body", "child_prim": "Drawer",
        "axis": "Y", "lower_limit": 0.0, "upper_limit": 0.45,
    }
    j.update(over)
    return j


class TestArticulateAssetCodegen:
    def test_basic_config_compiles(self):
        code = _gen_articulate_asset(
            {"prim_path": "/World/Cabinet", "joints": [_drawer_joint()]}
        )
        compile(code, "<gen>", "exec")
        assert "'/World/Cabinet/Body'" in code  # relative paths resolved
        assert "_base_link = '/World/Cabinet/Body'" in code
        assert "_fixed_base = True" in code

    def test_axis_vector_snaps_to_token(self):
        code = _gen_articulate_asset({
            "prim_path": "/W/A",
            "joints": [_drawer_joint(joint_type="revolute", axis=[0, 0.1, 0.9])],
        })
        compile(code, "<gen>", "exec")
        assert "'axis': 'Z'" in code

    def test_empty_joints_rejected(self):
        code = _gen_articulate_asset({"prim_path": "/W/A", "joints": []})
        assert code.startswith("raise ValueError")

    def test_bad_joint_type_rejected(self):
        code = _gen_articulate_asset(
            {"prim_path": "/W/A", "joints": [_drawer_joint(joint_type="spherical")]}
        )
        assert code.startswith("raise ValueError")

    def test_parent_equals_child_rejected(self):
        code = _gen_articulate_asset(
            {"prim_path": "/W/A", "joints": [_drawer_joint(child_prim="Body")]}
        )
        assert code.startswith("raise ValueError")

    def test_inverted_limits_rejected(self):
        code = _gen_articulate_asset(
            {"prim_path": "/W/A", "joints": [_drawer_joint(lower_limit=1, upper_limit=0)]}
        )
        assert code.startswith("raise ValueError")

    def test_duplicate_names_rejected(self):
        code = _gen_articulate_asset({
            "prim_path": "/W/A",
            "joints": [_drawer_joint(), _drawer_joint(child_prim="Drawer2")],
        })
        assert code.startswith("raise ValueError")

    def test_nested_links_rejected(self):
        code = _gen_articulate_asset({
            "prim_path": "/W/A",
            "joints": [_drawer_joint(parent_prim="Body", child_prim="Body/Inner")],
        })
        assert code.startswith("raise ValueError")
        assert "descendant" in code

    def test_two_parents_for_one_child_rejected(self):
        code = _gen_articulate_asset({
            "prim_path": "/W/A",
            "joints": [
                _drawer_joint(),
                _drawer_joint(name="other", parent_prim="Lid"),
            ],
        })
        assert code.startswith("raise ValueError")
        assert "child of two joints" in code

    def test_kinematic_loop_rejected(self):
        code = _gen_articulate_asset({
            "prim_path": "/W/A",
            "joints": [
                _drawer_joint(name="a", parent_prim="P1", child_prim="P2"),
                _drawer_joint(name="b", parent_prim="P2", child_prim="P1"),
            ],
        })
        assert code.startswith("raise ValueError")
        assert "loop" in code

    def test_missing_limits_warns(self):
        code = _gen_articulate_asset({
            "prim_path": "/W/A",
            "joints": [_drawer_joint(lower_limit=None, upper_limit=None)],
        })
        compile(code, "<gen>", "exec")
        assert "has no limits" in code

    def test_fixed_joint_gets_no_drive(self):
        code = _gen_articulate_asset({
            "prim_path": "/W/A",
            "joints": [_drawer_joint(joint_type="fixed", axis=None)],
        })
        compile(code, "<gen>", "exec")
        assert "'drive': False" in code


# ---------------------------------------------------------------------------
# Real-stage execution (runs only where pxr is importable, e.g.
# PYTHONPATH=<openusd>/lib/python LD_LIBRARY_PATH=<openusd>/lib pytest ...)


class TestGeneratedCodeOnRealStage:
    @pytest.fixture()
    def stage_env(self, monkeypatch):
        pxr = pytest.importorskip("pxr")
        from pxr import Usd, UsdGeom, Gf
        import types as _types

        stage = Usd.Stage.CreateInMemory()
        UsdGeom.SetStageMetersPerUnit(stage, 1.0)
        omni = _types.ModuleType("omni")
        omni_usd = _types.ModuleType("omni.usd")
        ctx = type("Ctx", (), {"get_stage": lambda self: stage})()
        omni_usd.get_context = lambda: ctx
        omni.usd = omni_usd
        monkeypatch.setitem(sys.modules, "omni", omni)
        monkeypatch.setitem(sys.modules, "omni.usd", omni_usd)

        UsdGeom.Xform.Define(stage, "/World")
        UsdGeom.Xform.Define(stage, "/World/Cabinet")
        UsdGeom.Xform.Define(stage, "/World/Cabinet/Body")
        UsdGeom.Mesh.Define(stage, "/World/Cabinet/Body/Geo")
        drawer = UsdGeom.Xform.Define(stage, "/World/Cabinet/Drawer")
        UsdGeom.XformCommonAPI(drawer.GetPrim()).SetTranslate(Gf.Vec3d(0, 0.5, 0.3))
        UsdGeom.Mesh.Define(stage, "/World/Cabinet/Drawer/Geo")
        return stage

    def test_articulate_asset_authors_correct_usd(self, stage_env):
        from pxr import Gf, UsdPhysics

        stage = stage_env
        code = _gen_articulate_asset({
            "prim_path": "/World/Cabinet",
            "joints": [_drawer_joint()],
            "fixed_base": True,
        })
        exec(compile(code, "<gen>", "exec"), {"__builtins__": __builtins__})

        cab = stage.GetPrimAtPath("/World/Cabinet")
        assert cab.HasAPI(UsdPhysics.ArticulationRootAPI)
        assert not cab.HasAPI(UsdPhysics.RigidBodyAPI)
        assert stage.GetPrimAtPath("/World/Cabinet/Body").HasAPI(UsdPhysics.RigidBodyAPI)

        slide = UsdPhysics.PrismaticJoint(
            stage.GetPrimAtPath("/World/Cabinet/Joints/drawer_slide")
        )
        assert slide and slide.GetAxisAttr().Get() == "Y"
        # anchor = child origin, expressed in the parent frame
        assert Gf.IsClose(
            Gf.Vec3d(slide.GetLocalPos0Attr().Get()), Gf.Vec3d(0, 0.5, 0.3), 1e-5
        )
        assert Gf.IsClose(
            Gf.Vec3d(slide.GetLocalPos1Attr().Get()), Gf.Vec3d(0, 0, 0), 1e-5
        )
        # prismatic joints must get a LINEAR drive, not angular
        assert UsdPhysics.DriveAPI(slide.GetPrim(), "linear")
        assert not UsdPhysics.DriveAPI.Get(slide.GetPrim(), "angular")
        fixed = UsdPhysics.FixedJoint(
            stage.GetPrimAtPath("/World/Cabinet/Joints/FixedBase")
        )
        assert [str(t) for t in fixed.GetBody1Rel().GetTargets()] == ["/World/Cabinet/Body"]


# ---------------------------------------------------------------------------
# sim_readiness stage validator


def _prim(path, schemas=(), prim_type="Xform"):
    return {"path": path, "schemas": list(schemas), "type": prim_type}


class TestSimReadinessValidator:
    def test_nested_rigid_body_is_error(self):
        stage = {"prims": [
            _prim("/World/Asset", ["PhysicsRigidBodyAPI"]),
            _prim("/World/Asset/Part", ["PhysicsRigidBodyAPI", "PhysicsCollisionAPI"], "Mesh"),
        ]}
        findings = SimReadinessRule().check(stage)
        nested = [f for f in findings if f.rule_id == "sim_ready.nested_rigid_body"]
        assert len(nested) == 1
        assert nested[0].severity == "error"
        assert nested[0].prim_path == "/World/Asset/Part"

    def test_no_collision_in_subtree_is_error(self):
        stage = {"prims": [
            _prim("/World/Asset", ["PhysicsRigidBodyAPI"]),
            _prim("/World/Asset/Geo", [], "Mesh"),
        ]}
        findings = SimReadinessRule().check(stage)
        assert any(f.rule_id == "sim_ready.no_collision_in_subtree" for f in findings)

    def test_partial_collision_is_warning(self):
        stage = {"prims": [
            _prim("/World/Asset", ["PhysicsRigidBodyAPI"]),
            _prim("/World/Asset/Body", ["PhysicsCollisionAPI"], "Mesh"),
            _prim("/World/Asset/Handle", [], "Mesh"),
        ]}
        findings = SimReadinessRule().check(stage)
        partial = [f for f in findings if f.rule_id == "sim_ready.partial_collision"]
        assert len(partial) == 1
        assert partial[0].severity == "warning"
        assert "/World/Asset/Handle" in partial[0].evidence["meshes_without_collision"]

    def test_clean_asset_passes(self):
        stage = {"prims": [
            _prim("/World/Asset", ["PhysicsRigidBodyAPI", "PhysicsMassAPI"]),
            _prim("/World/Asset/Body", ["PhysicsCollisionAPI"], "Mesh"),
        ]}
        assert SimReadinessRule().check(stage) == []

    def test_single_prim_rb_left_to_schema_consistency_rule(self):
        # A bare RB prim with no children is schema_consistency's finding,
        # not ours — avoid double-reporting.
        stage = {"prims": [_prim("/World/Cube", ["PhysicsRigidBodyAPI"], "Cube")]}
        findings = SimReadinessRule().check(stage)
        assert not any(
            f.rule_id == "sim_ready.no_collision_in_subtree" for f in findings
        )

    def test_registered_in_registry(self):
        from service.isaac_assist_service.analysis.validators import (
            get_registered_validators,
        )
        assert "sim_readiness" in get_registered_validators()
