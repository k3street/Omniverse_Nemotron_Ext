"""Contracts for the optional NVIDIA Video to Data adapter."""
from __future__ import annotations

from pathlib import Path

import pytest

from service.isaac_assist_service.integrations.video_to_data import V2DAdapter, V2DConfig

pytestmark = pytest.mark.l0


@pytest.fixture
def v2d(tmp_path: Path) -> tuple[V2DAdapter, Path]:
    root = tmp_path / "video_to_data"
    for component in ("video_ingestion_agent", "reconstruction", "robotic_grounding"):
        (root / component).mkdir(parents=True)
    python = tmp_path / "python"
    python.touch()
    return V2DAdapter(V2DConfig(root=root, python=python, execute_enabled=False)), tmp_path


def test_status_detects_composable_install(v2d):
    adapter, _ = v2d
    status = adapter.status()
    assert status["available"] is True
    assert all(status["components"].values())
    assert status["execute_enabled"] is False


@pytest.mark.asyncio
async def test_ingest_dry_run_builds_argv_without_shell(v2d):
    adapter, tmp = v2d
    video = tmp / "demo video.mp4"
    config = tmp / "ingestion.yaml"
    video.touch()
    config.touch()
    command = adapter.ingest(
        video_path=str(video), output_dir=str(tmp / "run output"),
        config_path=str(config), verify=False,
    )
    result = await adapter.run(command)
    assert result["status"] == "dry_run"
    assert result["argv"][1] == "scripts/run_ingestion.py"
    assert str(video) in result["argv"]
    assert "--no-verify" in result["argv"]


@pytest.mark.asyncio
async def test_live_execution_is_double_gated(v2d):
    adapter, tmp = v2d
    video = tmp / "demo.mp4"
    config = tmp / "ingestion.yaml"
    video.touch()
    config.touch()
    command = adapter.ingest(
        video_path=str(video), output_dir=str(tmp / "out"), config_path=str(config),
    )
    result = await adapter.run(command, dry_run=False)
    assert result["status"] == "blocked"
    assert "ISAAC_ASSIST_V2D_EXECUTE=1" in result["error"]


def test_depth_and_grounding_commands_match_documented_entrypoints(v2d):
    adapter, tmp = v2d
    video = tmp / "demo.mp4"
    weights = tmp / "weights"
    hmd = tmp / "hmd"
    mano = hmd / "mano"
    video.touch()
    weights.mkdir()
    mano.mkdir(parents=True)
    depth = adapter.reconstruct_depth(
        video_path=str(video), output_dir=str(tmp / "depth-out"), weights_path=str(weights),
    )
    assert "v2d.moge.docker.run_video_to_depth" in depth.argv
    grounding = adapter.ground_motion(
        dataset="taco", hmd_root=str(hmd), mano_dir=str(mano), max_sequences=2,
    )
    assert grounding.argv[1:3] == ("scripts/run_pipeline_docker.py", "taco")


def test_rejects_option_injection_and_missing_inputs(v2d):
    adapter, tmp = v2d
    config = tmp / "config.yaml"
    config.touch()
    with pytest.raises(ValueError):
        adapter.ingest(video_path="--help", output_dir=str(tmp / "out"), config_path=str(config))
    with pytest.raises(FileNotFoundError):
        adapter.retrieve(query="pick mug", database_dir=str(tmp / "missing"), config_path=str(config))


@pytest.mark.asyncio
async def test_v2d_status_reachable_through_production_dispatch(monkeypatch, tmp_path):
    root = tmp_path / "v2d"
    for component in ("video_ingestion_agent", "reconstruction", "robotic_grounding"):
        (root / component).mkdir(parents=True)
    monkeypatch.setenv("V2D_ROOT", str(root))
    from service.isaac_assist_service.chat.tools.tool_executor import execute_tool_call
    result = await execute_tool_call("v2d_status", {})
    assert result["type"] == "data"
    assert result["available"] is True
