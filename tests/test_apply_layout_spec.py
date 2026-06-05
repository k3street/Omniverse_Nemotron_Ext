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
async def test_instantiate_handles_dict_objects_too():
    """Phase 19 scaffold accepts both dataclass-like and dict objects."""
    from service.isaac_assist_service.multimodal.instantiator import instantiate
    spec = _StubSpec(objects=[
        {"object_class": "table", "position": [0, 0, 0]},
    ])
    result = await instantiate(spec, dry_run=True)
    assert result.status == "dry_run"
    assert "table" in result.generated_code


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
