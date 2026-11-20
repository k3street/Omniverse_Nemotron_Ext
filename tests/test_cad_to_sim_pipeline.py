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
        "run_cad_to_sim_e2e",
    }
    for name in names:
        assert handles_cad_ecosystem_tool(name)
    assert not handles_cad_ecosystem_tool("nonexistent_tool")


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
