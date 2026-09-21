"""Safe external-process adapter for NVIDIA Isaac GR00T N1.7.

GR00T owns a CUDA/PyTorch environment that must remain separate from Isaac
Sim's bundled Python and the Isaac Assist sidecar. This adapter builds the
upstream CLI invocations and optionally executes bounded jobs without a shell.
"""
from __future__ import annotations

import asyncio
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


_TRUE = {"1", "true", "yes", "on"}
_MAX_OUTPUT_CHARS = 20_000


@dataclass(frozen=True)
class GrootN17Config:
    root: Path
    python: Path
    execute_enabled: bool
    timeout_seconds: float = 3600.0

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "GrootN17Config":
        values = os.environ if env is None else env
        root = Path(
            values.get("GROOT_ROOT", "/home/kimate/Documents/Github/Isaac-GR00T")
        ).expanduser().resolve()
        configured_python = values.get("GROOT_PYTHON")
        python = (
            # Do not resolve venv Python symlinks: invoking the symlink path is
            # what gives Python the correct sys.prefix and site-packages.
            Path(configured_python).expanduser().absolute()
            if configured_python
            else root / ".venv" / "bin" / "python"
        )
        if not python.exists() and not configured_python:
            python = Path(sys.executable).resolve()
        return cls(
            root=root,
            python=python,
            execute_enabled=values.get("ISAAC_ASSIST_GROOT_EXECUTE", "0").lower() in _TRUE,
            timeout_seconds=float(values.get("GROOT_TIMEOUT_SECONDS", "3600")),
        )


@dataclass(frozen=True)
class GrootCommand:
    operation: str
    argv: tuple[str, ...]
    cwd: Path
    artifacts: tuple[Path, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "operation": self.operation,
            "argv": list(self.argv),
            "cwd": str(self.cwd),
            "artifacts": [str(path) for path in self.artifacts],
        }


class GrootN17Adapter:
    """Build and run current GR00T N1.7 inference/training commands."""

    def __init__(self, config: GrootN17Config | None = None) -> None:
        self.config = config or GrootN17Config.from_env()

    @staticmethod
    def _path(value: str, label: str, *, must_exist: bool = False) -> Path:
        if not value or value.startswith("-"):
            raise ValueError(f"{label} must be a non-empty filesystem path")
        path = Path(value).expanduser().resolve()
        if must_exist and not path.exists():
            raise FileNotFoundError(f"{label} does not exist: {path}")
        return path

    def _horizon_flag(self) -> str:
        script = self.config.root / "scripts" / "deployment" / "standalone_inference_script.py"
        try:
            source = script.read_text(encoding="utf-8")
        except OSError:
            return "--execution-horizon"
        return "--execution-horizon" if "--execution-horizon" in source else "--action-horizon"

    def status(self) -> dict[str, Any]:
        required = {
            "inference": self.config.root / "scripts/deployment/standalone_inference_script.py",
            "server": self.config.root / "gr00t/eval/run_gr00t_server.py",
            "finetune": self.config.root / "gr00t/experiment/launch_finetune.py",
            "spark_activation": self.config.root / "scripts/activate_spark.sh",
            "droid_sample": self.config.root / "demo_data/droid_sample",
        }
        python_version = None
        pyvenv = self.config.python.parent.parent / "pyvenv.cfg"
        if pyvenv.exists():
            for line in pyvenv.read_text(encoding="utf-8", errors="replace").splitlines():
                if line.lower().startswith("version"):
                    python_version = line.partition("=")[2].strip()
                    break
        return {
            "available": self.config.root.is_dir()
            and self.config.python.exists()
            and all(path.exists() for name, path in required.items() if name != "droid_sample"),
            "root": str(self.config.root),
            "python": str(self.config.python),
            "python_version": python_version,
            "execute_enabled": self.config.execute_enabled,
            "horizon_flag": self._horizon_flag(),
            "components": {name: path.exists() for name, path in required.items()},
            "model_id": "nvidia/GR00T-N1.7-3B",
            "spark_ready": bool(python_version and python_version.startswith("3.12")),
        }

    def infer(
        self,
        *,
        dataset_path: str,
        embodiment_tag: str,
        model_path: str = "nvidia/GR00T-N1.7-3B",
        trajectory_ids: list[int] | None = None,
        inference_mode: str = "pytorch",
        execution_horizon: int = 8,
    ) -> GrootCommand:
        dataset = self._path(dataset_path, "dataset_path", must_exist=True)
        if not embodiment_tag or embodiment_tag.startswith("-"):
            raise ValueError("embodiment_tag must not be empty")
        if execution_horizon < 1:
            raise ValueError("execution_horizon must be at least 1")
        if inference_mode not in {"pytorch", "tensorrt", "trt_full_pipeline"}:
            raise ValueError("inference_mode must be pytorch, tensorrt, or trt_full_pipeline")
        ids = trajectory_ids or [1, 2]
        if not ids or any(item < 0 for item in ids):
            raise ValueError("trajectory_ids must contain non-negative integers")
        argv = [
            str(self.config.python),
            "scripts/deployment/standalone_inference_script.py",
            "--model-path", model_path,
            "--dataset-path", str(dataset),
            "--embodiment-tag", embodiment_tag,
            "--traj-ids", *[str(item) for item in ids],
            "--inference-mode", inference_mode,
            self._horizon_flag(), str(execution_horizon),
        ]
        return GrootCommand("infer", tuple(argv), self.config.root)

    def serve(
        self,
        *,
        embodiment_tag: str,
        model_path: str = "nvidia/GR00T-N1.7-3B",
        port: int = 5555,
        device: str = "cuda:0",
    ) -> GrootCommand:
        if not embodiment_tag or embodiment_tag.startswith("-"):
            raise ValueError("embodiment_tag must not be empty")
        if not (1024 <= port <= 65535):
            raise ValueError("port must be between 1024 and 65535")
        argv = (
            str(self.config.python), "gr00t/eval/run_gr00t_server.py",
            "--model-path", model_path, "--embodiment-tag", embodiment_tag,
            "--device", device, "--port", str(port),
        )
        return GrootCommand("serve", argv, self.config.root)

    def finetune(
        self,
        *,
        dataset_path: str,
        modality_config_path: str,
        output_dir: str,
        embodiment_tag: str = "NEW_EMBODIMENT",
        model_path: str = "nvidia/GR00T-N1.7-3B",
        max_steps: int = 2000,
        global_batch_size: int = 32,
    ) -> GrootCommand:
        dataset = self._path(dataset_path, "dataset_path", must_exist=True)
        modality = self._path(modality_config_path, "modality_config_path", must_exist=True)
        output = self._path(output_dir, "output_dir")
        if max_steps < 1 or global_batch_size < 1:
            raise ValueError("max_steps and global_batch_size must be at least 1")
        argv = (
            str(self.config.python), "gr00t/experiment/launch_finetune.py",
            "--base-model-path", model_path, "--dataset-path", str(dataset),
            "--embodiment-tag", embodiment_tag,
            "--modality-config-path", str(modality), "--num-gpus", "1",
            "--output-dir", str(output), "--max-steps", str(max_steps),
            "--global-batch-size", str(global_batch_size),
            "--dataloader-num-workers", "4",
        )
        return GrootCommand("finetune", argv, self.config.root, (output,))

    async def run(self, command: GrootCommand, *, dry_run: bool = True) -> dict[str, Any]:
        result = {**command.as_dict(), "dry_run": dry_run}
        if dry_run:
            return {**result, "status": "dry_run"}
        if not self.config.execute_enabled:
            return {
                **result,
                "status": "blocked",
                "error": "Live GR00T execution requires ISAAC_ASSIST_GROOT_EXECUTE=1",
            }
        if not command.cwd.is_dir() or not self.config.python.exists():
            return {**result, "status": "unavailable", "error": "GR00T checkout or Python is unavailable"}
        process = await asyncio.create_subprocess_exec(
            *command.argv,
            cwd=str(command.cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), self.config.timeout_seconds)
        except asyncio.TimeoutError:
            process.kill()
            await process.communicate()
            return {**result, "status": "timeout", "error": f"GR00T exceeded {self.config.timeout_seconds:g}s"}
        return {
            **result,
            "status": "completed" if process.returncode == 0 else "failed",
            "returncode": process.returncode,
            "stdout": stdout.decode(errors="replace")[-_MAX_OUTPUT_CHARS:],
            "stderr": stderr.decode(errors="replace")[-_MAX_OUTPUT_CHARS:],
        }
