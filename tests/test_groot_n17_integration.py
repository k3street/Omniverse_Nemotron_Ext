"""Contracts for the isolated NVIDIA Isaac GR00T N1.7 adapter."""
from __future__ import annotations

from pathlib import Path

import pytest

from service.isaac_assist_service.integrations.groot_n17 import (
    GrootN17Adapter,
    GrootN17Config,
)

pytestmark = pytest.mark.l0


@pytest.fixture
def groot(tmp_path: Path) -> tuple[GrootN17Adapter, Path]:
    root = tmp_path / "Isaac-GR00T"
    for path in (
        "scripts/deployment",
        "scripts",
        "gr00t/eval",
        "gr00t/experiment",
        "demo_data/droid_sample",
        ".venv/bin",
    ):
        (root / path).mkdir(parents=True, exist_ok=True)
    (root / "scripts/deployment/standalone_inference_script.py").write_text(
        "parser.add_argument('--execution-horizon')\n"
    )
    (root / "scripts/activate_spark.sh").touch()
    (root / "gr00t/eval/run_gr00t_server.py").touch()
    (root / "gr00t/experiment/launch_finetune.py").touch()
    python = root / ".venv/bin/python"
    python.touch()
    (root / ".venv/pyvenv.cfg").write_text("version = 3.12.9\n")
    config = GrootN17Config(root=root, python=python, execute_enabled=False)
    return GrootN17Adapter(config), tmp_path


def test_status_reports_spark_ready_and_components(groot):
    adapter, _ = groot
    status = adapter.status()
    assert status["available"] is True
    assert status["spark_ready"] is True
    assert status["horizon_flag"] == "--execution-horizon"
    assert status["model_id"] == "nvidia/GR00T-N1.7-3B"


def test_config_preserves_virtualenv_python_symlink(tmp_path):
    root = tmp_path / "groot"
    target = tmp_path / "python-target"
    target.touch()
    venv_python = root / ".venv/bin/python"
    venv_python.parent.mkdir(parents=True)
    venv_python.symlink_to(target)
    config = GrootN17Config.from_env({"GROOT_ROOT": str(root), "GROOT_PYTHON": str(venv_python)})
    assert config.python == venv_python.absolute()


@pytest.mark.asyncio
async def test_droid_inference_dry_run_uses_current_cli(groot):
    adapter, _ = groot
    dataset = adapter.config.root / "demo_data/droid_sample"
    command = adapter.infer(
        dataset_path=str(dataset),
        embodiment_tag="OXE_DROID_RELATIVE_EEF_RELATIVE_JOINT",
        trajectory_ids=[1, 2],
    )
    result = await adapter.run(command)
    assert result["status"] == "dry_run"
    assert result["argv"][1] == "scripts/deployment/standalone_inference_script.py"
    assert "nvidia/GR00T-N1.7-3B" in result["argv"]
    assert "--execution-horizon" in result["argv"]


def test_ea_checkout_uses_action_horizon(groot):
    adapter, _ = groot
    script = adapter.config.root / "scripts/deployment/standalone_inference_script.py"
    script.write_text("parser.add_argument('--action-horizon')\n")
    command = adapter.infer(
        dataset_path=str(adapter.config.root / "demo_data/droid_sample"),
        embodiment_tag="OXE_DROID_RELATIVE_EEF_RELATIVE_JOINT",
    )
    assert "--action-horizon" in command.argv


def test_server_and_finetune_use_n17_entrypoints(groot):
    adapter, tmp = groot
    server = adapter.serve(embodiment_tag="LIBERO_PANDA", port=5556)
    assert server.argv[1] == "gr00t/eval/run_gr00t_server.py"
    modality = tmp / "so100_config.py"
    modality.touch()
    finetune = adapter.finetune(
        dataset_path=str(adapter.config.root / "demo_data/droid_sample"),
        modality_config_path=str(modality),
        output_dir=str(tmp / "checkpoints"),
    )
    assert finetune.argv[1] == "gr00t/experiment/launch_finetune.py"
    assert "--base-model-path" in finetune.argv


@pytest.mark.asyncio
async def test_live_execution_is_explicitly_gated(groot):
    adapter, _ = groot
    command = adapter.serve(embodiment_tag="LIBERO_PANDA")
    result = await adapter.run(command, dry_run=False)
    assert result["status"] == "blocked"
    assert "ISAAC_ASSIST_GROOT_EXECUTE=1" in result["error"]


def test_rejects_bad_ports_modes_and_missing_paths(groot):
    adapter, tmp = groot
    with pytest.raises(ValueError):
        adapter.serve(embodiment_tag="LIBERO_PANDA", port=80)
    with pytest.raises(ValueError):
        adapter.infer(
            dataset_path=str(adapter.config.root / "demo_data/droid_sample"),
            embodiment_tag="LIBERO_PANDA",
            inference_mode="unsafe-shell-mode",
        )
    with pytest.raises(FileNotFoundError):
        adapter.infer(dataset_path=str(tmp / "missing"), embodiment_tag="LIBERO_PANDA")


@pytest.mark.asyncio
async def test_status_reachable_through_production_dispatch(monkeypatch, groot):
    adapter, _ = groot
    monkeypatch.setenv("GROOT_ROOT", str(adapter.config.root))
    monkeypatch.setenv("GROOT_PYTHON", str(adapter.config.python))
    from service.isaac_assist_service.chat.tools.tool_executor import execute_tool_call
    result = await execute_tool_call("groot_n17_status", {})
    assert result["type"] == "data"
    assert result["available"] is True
