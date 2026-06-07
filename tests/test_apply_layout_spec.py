"""Phase 19 — apply_layout_spec_to_scene Kit RPC execution.

Tests the instantiator scaffold. Full integration tests against a live
Kit instance are a daytime task; these are contract-level tests on the
scaffold shape.

Per specs/IA_FULL_SPEC_2026-05-10.md Phase 19.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.l0


class _StubSpec:
    """Minimal LayoutSpec stand-in for the scaffold contract."""
    def __init__(self, objects=None):
        self.objects = objects


class _StubObject:
    def __init__(self, object_class, position):
        self.object_class = object_class
        self.position = position


@pytest.mark.asyncio
async def test_instantiate_no_objects_returns_no_objects_status():
    """A spec with objects=None returns the 'no_objects' status."""
    from service.isaac_assist_service.multimodal.instantiator import instantiate
    spec = _StubSpec(objects=None)
    result = await instantiate(spec, dry_run=True)
    assert result.status == "no_objects"
    assert result.build_id is None


@pytest.mark.asyncio
async def test_instantiate_dry_run_returns_generated_code():
    """dry_run=True returns generated code without dispatching to Kit."""
    from service.isaac_assist_service.multimodal.instantiator import instantiate
    spec = _StubSpec(objects=[
        _StubObject("franka_panda", [0.0, 0.0, 0.8]),
        _StubObject("cube", [0.3, 0.0, 0.85]),
    ])
    result = await instantiate(spec, template_id="tabletop_pick_place", dry_run=True)
    assert result.status == "dry_run"
    assert result.generated_code is not None
    assert "import omni.usd" in result.generated_code
    assert "stage = omni.usd.get_context().get_stage()" in result.generated_code
    assert "franka_panda" in result.generated_code
    assert "cube" in result.generated_code


@pytest.mark.asyncio
async def test_instantiate_robot_palette_class_uses_lightweight_proxy_by_default():
    """Web applies should not load heavy robot references into live RTX viewports."""
    from service.isaac_assist_service.multimodal.instantiator import instantiate
    spec = _StubSpec(objects=[
        _StubObject("franka_panda", [0.0, 0.0, 0.0]),
    ])
    result = await instantiate(spec, dry_run=True)
    assert result.status == "dry_run"
    assert "source_class='franka_panda' -> prim_class='Cube'" in result.generated_code
    assert "GetReferences().AddReference" not in result.generated_code
    assert "isaac_assist:proxy', True" in result.generated_code


@pytest.mark.asyncio
async def test_instantiate_robot_explicit_asset_override_still_references_asset():
    """Explicit reviewed assets remain available for users who want full USDs."""
    from service.isaac_assist_service.multimodal.instantiator import instantiate
    spec = _StubSpec(objects=[
        {
            "object_class": "franka_panda",
            "position": [0.0, 0.0, 0.0],
            "asset_path": "omniverse://localhost/Robots/franka.usd",
        },
    ])
    result = await instantiate(spec, dry_run=True)
    assert result.status == "dry_run"
    assert "source_class='franka_panda' -> prim_class='Reference'" in result.generated_code
    assert "GetReferences().AddReference('omniverse://localhost/Robots/franka.usd')" in result.generated_code


@pytest.mark.asyncio
async def test_instantiate_handles_dict_objects_too():
    """Phase 19 scaffold accepts both dataclass-like and dict objects."""
    from service.isaac_assist_service.multimodal.instantiator import instantiate
    spec = _StubSpec(objects=[
        {"object_class": "table", "position": [0, 0, 0]},
    ])
    result = await instantiate(spec, dry_run=True)
    assert result.status == "dry_run"
    assert "table" in result.generated_code


@pytest.mark.asyncio
async def test_instantiate_plain_conveyor_generates_visible_primitive():
    """Plain conveyor objects should not fall back to invisible Xforms."""
    from service.isaac_assist_service.multimodal.instantiator import instantiate
    spec = _StubSpec(objects=[
        {"object_class": "conveyor", "name": "Conveyor_1", "position": [0, 0, 0]},
    ])
    result = await instantiate(spec, dry_run=True)
    assert result.status == "dry_run"
    assert "source_class='conveyor' -> prim_class='Cube'" in result.generated_code
    assert "UsdGeom.Cube.Define(stage, '/World/Conveyor_1')" in result.generated_code


def test_instantiate_result_from_exec_success():
    """InstantiateResult.from_exec maps success=True to status='ok'."""
    from service.isaac_assist_service.multimodal.instantiator import InstantiateResult
    res = InstantiateResult.from_exec({"success": True, "build_id": "abc123"})
    assert res.status == "ok"
    assert res.build_id == "abc123"


def test_instantiate_result_from_exec_failure():
    """InstantiateResult.from_exec maps success=False to status='error'."""
    from service.isaac_assist_service.multimodal.instantiator import InstantiateResult
    res = InstantiateResult.from_exec({"success": False, "output": "Kit died"})
    assert res.status == "error"
    assert "Kit died" in res.message


# ---------------------------------------------------------------------------
# Phase 19 CODE-GENERATOR tests (new, ≥12)
# ---------------------------------------------------------------------------

class TestLayoutSpecCodeGeneratorPrimClasses:
    """Per-class branching produces correct Define() calls."""

    def _gen(self):
        from service.isaac_assist_service.multimodal.instantiator import LayoutSpecCodeGenerator
        return LayoutSpecCodeGenerator(use_get_context=True)

    def test_cube_uses_usdgeom_define(self):
        """generate_for_prim('Cube', ...) contains UsdGeom.Cube.Define(stage, ...)."""
        code = self._gen().generate_for_prim("Cube", "/World/A")
        assert "UsdGeom.Cube.Define(stage, '/World/A')" in code

    def test_sphere_uses_usdgeom_define(self):
        code = self._gen().generate_for_prim("Sphere", "/World/Ball")
        assert "UsdGeom.Sphere.Define(stage, '/World/Ball')" in code

    def test_cylinder_uses_usdgeom_define(self):
        code = self._gen().generate_for_prim("Cylinder", "/World/Post")
        assert "UsdGeom.Cylinder.Define(stage, '/World/Post')" in code

    def test_camera_uses_usdgeom_define(self):
        code = self._gen().generate_for_prim("Camera", "/World/Cam1")
        assert "UsdGeom.Camera.Define(stage, '/World/Cam1')" in code

    def test_distant_light_uses_usdlux(self):
        """Light variants route through UsdLux, not UsdGeom."""
        code = self._gen().generate_for_prim("DistantLight", "/World/Sun")
        assert "UsdLux.DistantLight.Define(stage," in code
        assert "UsdGeom.DistantLight" not in code

    def test_sphere_light_uses_usdlux(self):
        code = self._gen().generate_for_prim("SphereLight", "/World/Bulb")
        assert "UsdLux.SphereLight.Define(stage," in code

    def test_dome_light_uses_usdlux(self):
        code = self._gen().generate_for_prim("DomeLight", "/World/Sky")
        assert "UsdLux.DomeLight.Define(stage," in code

    def test_reference_uses_add_reference(self):
        """Reference prim class emits GetReferences().AddReference()."""
        code = self._gen().generate_for_prim(
            "Reference", "/World/Robot",
            extra_attrs={"asset_path": "omniverse://localhost/assets/robot.usd"},
        )
        assert "GetReferences().ClearReferences()" in code
        assert "GetReferences().AddReference(" in code
        assert "robot.usd" in code

    def test_unknown_class_falls_back_to_xform(self):
        """Unrecognised prim class falls back to UsdGeom.Xform.Define."""
        code = self._gen().generate_for_prim("GizmoWidget", "/World/Gizmo")
        assert "UsdGeom.Xform.Define(stage," in code
        assert "Unknown prim class" in code or "falling back to Xform" in code


class TestLayoutSpecCodeGeneratorXformArgs:
    """Positional arguments are reflected in the generated Xform ops."""

    def _gen(self):
        from service.isaac_assist_service.multimodal.instantiator import LayoutSpecCodeGenerator
        return LayoutSpecCodeGenerator(use_get_context=True)

    def test_position_reflected_as_gf_vec3d(self):
        """position=(1, 2, 3) produces Gf.Vec3d(1, 2, 3) in SetTranslate."""
        code = self._gen().generate_for_prim("Cube", "/W/C", position=(1.0, 2.0, 3.0))
        assert "Gf.Vec3d(1.0, 2.0, 3.0)" in code

    def test_default_position_emitted(self):
        """Default position=(0,0,0) still produces a SetTranslate call."""
        code = self._gen().generate_for_prim("Sphere", "/W/S")
        assert "SetTranslate(" in code
        assert "Gf.Vec3d(0" in code

    def test_non_default_scale_emitted(self):
        """Non-unit scale is emitted through the version-tolerant helper."""
        code = self._gen().generate_for_prim("Cube", "/W/C", scale=(2.0, 2.0, 2.0))
        assert "_set_xform_scale(prim, 2.0, 2.0, 2.0)" in code

    def test_default_scale_omitted(self):
        """Unit scale (1,1,1) is NOT emitted to keep snippets compact."""
        code = self._gen().generate_for_prim("Cube", "/W/C", scale=(1.0, 1.0, 1.0))
        assert "SetScale" not in code


class TestLayoutSpecCodeGeneratorFullScript:
    """generate_full_script produces valid, complete scripts."""

    def _gen(self):
        from service.isaac_assist_service.multimodal.instantiator import LayoutSpecCodeGenerator
        return LayoutSpecCodeGenerator(use_get_context=True)

    def test_header_includes_get_context(self):
        """Full script header contains omni.usd.get_context().get_stage()."""
        code = self._gen().generate_full_script([
            {"prim_class": "Cube", "prim_path": "/World/Box"},
        ])
        assert "omni.usd.get_context().get_stage()" in code

    def test_header_includes_imports(self):
        code = self._gen().generate_full_script([])
        assert "import omni.usd" in code
        assert "from pxr import UsdGeom" in code

    def test_empty_list_produces_valid_script(self):
        """generate_full_script([]) returns a non-empty header-only script."""
        code = self._gen().generate_full_script([])
        assert "omni.usd.get_context().get_stage()" in code
        # No object sections → no Define calls expected, but no crash
        assert isinstance(code, str) and len(code) > 10

    def test_multi_prim_all_defined(self):
        """Each prim descriptor in the list produces its own Define call."""
        code = self._gen().generate_full_script([
            {"prim_class": "Cube", "prim_path": "/World/C1"},
            {"prim_class": "Sphere", "prim_path": "/World/S1"},
            {"prim_class": "DistantLight", "prim_path": "/World/L1"},
        ])
        assert "UsdGeom.Cube.Define" in code
        assert "UsdGeom.Sphere.Define" in code
        assert "UsdLux.DistantLight.Define" in code


class TestLayoutSpecCodeGeneratorValidation:
    """validate_generated_code enforces safety and correctness rules."""

    def _gen(self):
        from service.isaac_assist_service.multimodal.instantiator import LayoutSpecCodeGenerator
        return LayoutSpecCodeGenerator(use_get_context=True)

    def test_clean_code_returns_empty_issues(self):
        """Well-formed generated code produces no issues."""
        code = self._gen().generate_full_script([
            {"prim_class": "Cube", "prim_path": "/World/Box"},
        ])
        issues = self._gen().validate_generated_code(code)
        assert issues == []

    def test_missing_get_context_reported(self):
        """Code without get_context().get_stage() is flagged."""
        bad = "from pxr import UsdGeom\nUsdGeom.Cube.Define(stage, '/W/C')"
        issues = self._gen().validate_generated_code(bad)
        assert any("get_context" in i for i in issues)

    def test_exec_call_reported(self):
        """Code containing exec() is flagged."""
        bad = (
            "import omni.usd\n"
            "from pxr import UsdGeom, UsdLux, Gf, Sdf\n"
            "stage = omni.usd.get_context().get_stage()\n"
            "exec('import os')\n"
            "UsdGeom.Cube.Define(stage, '/W/C')"
        )
        issues = self._gen().validate_generated_code(bad)
        assert any("exec" in i for i in issues)

    def test_no_define_call_reported(self):
        """Code without any Define(stage, ...) call is flagged."""
        bad = (
            "import omni.usd\n"
            "stage = omni.usd.get_context().get_stage()\n"
            "print('hello')"
        )
        issues = self._gen().validate_generated_code(bad)
        assert any("Define" in i for i in issues)


class TestSupportedPrimClasses:
    """SUPPORTED_PRIM_CLASSES has the expected members."""

    def test_at_least_ten_entries(self):
        from service.isaac_assist_service.multimodal.instantiator import SUPPORTED_PRIM_CLASSES
        assert len(SUPPORTED_PRIM_CLASSES) >= 10

    def test_canonical_classes_present(self):
        from service.isaac_assist_service.multimodal.instantiator import SUPPORTED_PRIM_CLASSES
        for cls in ("Cube", "Sphere", "Cylinder", "Camera", "DistantLight", "Reference"):
            assert cls in SUPPORTED_PRIM_CLASSES


class TestPhaseMetadata:
    """get_phase_metadata() exposes status='landed'."""

    def test_status_is_landed(self):
        from service.isaac_assist_service.multimodal.instantiator import get_phase_metadata
        meta = get_phase_metadata()
        assert meta["status"] == "landed"

    def test_phase_number(self):
        from service.isaac_assist_service.multimodal.instantiator import get_phase_metadata
        meta = get_phase_metadata()
        assert meta["phase"] == 19
