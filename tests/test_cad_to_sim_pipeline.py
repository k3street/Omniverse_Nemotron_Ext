"""Hermetic and integration tests for the CAD-to-SimReady + Isaac Sim 7.0.0a1 + OpenUSD workcell pipeline."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

from pxr import Usd

from service.isaac_assist_service.chat.tools import kit_tools
from service.isaac_assist_service.mcp_cad_ecosystem_tools import (
    handles_cad_ecosystem_tool,
    mcp_cad_ecosystem_tool_schemas,
)


def test_cad_mcp_tool_schemas_complete():
    schemas = mcp_cad_ecosystem_tool_schemas()
    names = {s["name"] for s in schemas}
    assert names == {
        "design_parametric_cad",
        "convert_cad_to_simready_usd",
        "verify_asset_dual_7a",
        "compose_omniverse_workcell_stage",
        "render_or_simulate_in_blender_ovrtx",
        "run_cad_to_sim_e2e",
        "extract_cad_creator_r2_hybrid",
        "simulate_mujoco_r2_grasp_and_mate",
        "run_full_cad_mujoco_isaac_blender_showcase",
    }
    for name in names:
        assert handles_cad_ecosystem_tool(name)
    assert not handles_cad_ecosystem_tool("nonexistent_tool")

    from service.isaac_assist_service.mcp_server import MCPServer

    server = MCPServer()
    registered = {t["name"] for t in server._mcp_tools}
    assert names.issubset(registered)


def test_cross_platform_rpc_port_resolution(monkeypatch):
    monkeypatch.delenv("KIT_RPC_PORT", raising=False)
    monkeypatch.delenv("ISAAC_ASSIST_URL", raising=False)
    port_file = Path(tempfile.gettempdir()) / "isaac_assist_rpc_port"
    backup = port_file.read_text(encoding="utf-8") if port_file.exists() else None
    try:
        port_file.write_text("8007", encoding="utf-8")
        assert kit_tools._resolve_kit_rpc_base() == "http://127.0.0.1:8007"
        from mcp import isaac_spatial_mcp

        assert isaac_spatial_mcp._service_url() == "http://127.0.0.1:8007"
    finally:
        if backup is not None:
            port_file.write_text(backup, encoding="utf-8")
        elif port_file.exists():
            port_file.unlink()


def test_parametric_cad_and_layered_usd_authoring(tmp_path: Path):
    from scripts import cad_to_sim_pipeline as pipe

    cad = pipe.design_parametric_cad(
        part_id="test_finger",
        template="parallel_gripper_finger",
        material="tpu_95a",
        output_dir=tmp_path / "cad",
    )
    assert Path(cad["step_path"]).is_file()
    assert Path(cad["stl_path"]).is_file()
    assert cad["mass_kg"] > 0.0

    simready = pipe.convert_cad_to_simready_usd(
        cad["step_path"],
        part_id="test_finger",
        material="tpu_95a",
        output_dir=tmp_path / "usd",
    )
    assert Path(simready["geometry_usdc_path"]).is_file()
    assert Path(simready["physics_usda_path"]).is_file()
    stage = Usd.Stage.Open(simready["root_usd_path"])
    default_prim = stage.GetDefaultPrim()
    assert default_prim.IsValid()
    vsets = default_prim.GetVariantSets()
    assert vsets.HasVariantSet("PhysicsFidelity")
    assert vsets.HasVariantSet("MaterialFinish")
    assert simready["validation"]["errors"] == 0


def test_clean_blender_env_strips_venv_and_sets_profile(tmp_path: Path, monkeypatch):
    from scripts import cad_to_sim_pipeline as pipe

    monkeypatch.setenv("VIRTUAL_ENV", r"H:\isaacsim7-venv")
    monkeypatch.setenv("PYTHONPATH", r"H:\isaacsim7-venv\Lib\site-packages")
    monkeypatch.setenv("OMNI_USER", "test")
    monkeypatch.setenv("OVRTX_CUSTOM", "leak")

    env = pipe._build_clean_blender_env(str(tmp_path))
    assert "VIRTUAL_ENV" not in env
    assert "PYTHONPATH" not in env
    assert "OMNI_USER" not in env
    assert "OVRTX_CUSTOM" not in env
    assert env["BLENDER_USER_EXTENSIONS"] == str(tmp_path / "extensions")
    assert (tmp_path / "extensions").is_dir()


def test_mcp_blender_ovrtx_tool_roundtrip(tmp_path: Path):
    import asyncio
    import json
    import pytest
    from scripts import cad_to_sim_pipeline as pipe
    from service.isaac_assist_service.mcp_server import MCPServer

    try:
        rt = pipe.resolve_blender_ovrtx_runtime()
    except FileNotFoundError:
        pytest.skip("Blender 5.2+ runtime not installed on this host")
    if not rt.get("profile_dir"):
        pytest.skip("Isolated ovrtx profile not configured on this host")

    fixture_usd = (
        Path(__file__).resolve().parents[1]
        / "workspace"
        / "cad_pipeline"
        / "cad_machined_shaft"
        / "simready_usd"
        / "cad_machined_shaft_simready.usda"
    )
    if not fixture_usd.is_file():
        pytest.skip("Sample SimReady CAD USD not generated yet")

    server = MCPServer()
    resp = asyncio.run(
        server.handle_request(
            {
                "jsonrpc": "2.0",
                "id": 42,
                "method": "tools/call",
                "params": {
                    "name": "render_or_simulate_in_blender_ovrtx",
                    "arguments": {
                        "usd_paths": [str(fixture_usd)],
                        "job_id": "pytest_mcp_ovrtx",
                        "simulate_frames": 20,
                        "substeps": 2,
                        "resolution": [320, 180],
                        "samples": 4,
                        "output_dir": str(tmp_path / "ovrtx_out"),
                    },
                },
            }
        )
    )
    assert resp["result"]["isError"] is False
    payload = json.loads(resp["result"]["content"][0]["text"])
    assert payload["status"] == "passed"
    assert Path(payload["ovrtx_render"]["render_png_path"]).is_file()
    assert Path(payload["blend_path"]).is_file()
    assert payload["ovphysx_simulation"]["frames_simulated"] == 20
