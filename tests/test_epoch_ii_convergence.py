"""Phase 32 — Epoch II convergence."""
import pytest
pytestmark = pytest.mark.l0


def test_build_30_object_spec_returns_30_objects():
    from service.isaac_assist_service.multimodal.convergence_test import build_30_object_spec
    spec = build_30_object_spec()
    assert len(spec["objects"]) == 30


@pytest.mark.asyncio
async def test_run_convergence_returns_ok():
    from service.isaac_assist_service.multimodal.convergence_test import run_convergence
    report = await run_convergence()
    assert report["convergence_ok"] is True
    assert report["objects_in_spec"] == 30
    assert report["instantiate_status"] == "dry_run"
