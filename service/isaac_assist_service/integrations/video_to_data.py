"""Safe adapter for NVIDIA Isaac Video to Data (V2D).

V2D remains an independently installed, GPU-enabled repository. This module
only validates requests, builds its documented CLI invocations, and optionally
runs them without a shell. Live execution is opt-in via
``ISAAC_ASSIST_V2D_EXECUTE=1``.
"""
from __future__ import annotations

import asyncio
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence


_TRUE = {"1", "true", "yes", "on"}
_MAX_OUTPUT_CHARS = 20_000


@dataclass(frozen=True)
class V2DConfig:
    root: Path
    python: Path
    execute_enabled: bool
    timeout_seconds: float = 3600.0

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "V2DConfig":
        values = os.environ if env is None else env
        root = Path(values.get("V2D_ROOT", "external/video_to_data")).expanduser().resolve()
        default_python = root / "video_ingestion_agent" / ".venv" / "bin" / "python"
        configured_python = values.get("V2D_PYTHON")
        python = Path(configured_python).expanduser().resolve() if configured_python else default_python
        if not python.exists() and not configured_python:
            python = Path(sys.executable).resolve()
        enabled = values.get("ISAAC_ASSIST_V2D_EXECUTE", "0").lower() in _TRUE
        timeout = float(values.get("V2D_TIMEOUT_SECONDS", "3600"))
        return cls(root=root, python=python, execute_enabled=enabled, timeout_seconds=timeout)


@dataclass(frozen=True)
class V2DCommand:
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


class V2DAdapter:
    """Build and optionally execute documented NVIDIA V2D commands."""

    def __init__(self, config: V2DConfig | None = None) -> None:
        self.config = config or V2DConfig.from_env()

    @staticmethod
    def _path(value: str, label: str, *, must_exist: bool = False) -> Path:
        if not value or value.startswith("-"):
            raise ValueError(f"{label} must be a non-empty filesystem path")
        path = Path(value).expanduser().resolve()
        if must_exist and not path.exists():
            raise FileNotFoundError(f"{label} does not exist: {path}")
        return path

    def status(self) -> dict[str, Any]:
        components = {
            "video_ingestion_agent": self.config.root / "video_ingestion_agent",
            "reconstruction": self.config.root / "reconstruction",
            "robotic_grounding": self.config.root / "robotic_grounding",
        }
        return {
            "available": self.config.root.is_dir() and all(path.is_dir() for path in components.values()),
            "root": str(self.config.root),
            "python": str(self.config.python),
            "python_exists": self.config.python.exists(),
            "execute_enabled": self.config.execute_enabled,
            "components": {name: path.is_dir() for name, path in components.items()},
        }

    def ingest(self, *, video_path: str, output_dir: str, config_path: str,
               verify: bool = False) -> V2DCommand:
        video = self._path(video_path, "video_path", must_exist=True)
        output = self._path(output_dir, "output_dir")
        config = self._path(config_path, "config_path", must_exist=True)
        cwd = self.config.root / "video_ingestion_agent"
        argv = [str(self.config.python), "scripts/run_ingestion.py", str(video), "-c", str(config), "-o", str(output)]
        if not verify:
            argv.append("--no-verify")
        return V2DCommand("ingest", tuple(argv), cwd, (output,))

    def retrieve(self, *, query: str, database_dir: str, config_path: str) -> V2DCommand:
        if not query.strip():
            raise ValueError("query must not be empty")
        database = self._path(database_dir, "database_dir", must_exist=True)
        config = self._path(config_path, "config_path", must_exist=True)
        cwd = self.config.root / "video_ingestion_agent"
        argv = (str(self.config.python), "scripts/run_retrieval.py", query, "-d", str(database), "-c", str(config))
        return V2DCommand("retrieve", argv, cwd)

    def reconstruct_depth(self, *, video_path: str, output_dir: str,
                          weights_path: str) -> V2DCommand:
        video = self._path(video_path, "video_path", must_exist=True)
        output = self._path(output_dir, "output_dir")
        weights = self._path(weights_path, "weights_path", must_exist=True)
        depth = output / "depth"
        intrinsics = output / "intrinsics"
        argv = (
            str(self.config.python), "-m", "v2d.moge.docker.run_video_to_depth",
            "--video_path", str(video), "--depth_folder", str(depth),
            "--intrinsics_folder", str(intrinsics), "--weights_path", str(weights),
        )
        return V2DCommand("reconstruct_depth", argv, self.config.root / "reconstruction", (depth, intrinsics))

    def ground_motion(self, *, dataset: str, hmd_root: str, mano_dir: str,
                      max_sequences: int = 2) -> V2DCommand:
        if not dataset or dataset.startswith("-"):
            raise ValueError("dataset must be a non-empty dataset name")
        if max_sequences < 1:
            raise ValueError("max_sequences must be at least 1")
        hmd = self._path(hmd_root, "hmd_root", must_exist=True)
        mano = self._path(mano_dir, "mano_dir", must_exist=True)
        argv = (
            str(self.config.python), "scripts/run_pipeline_docker.py", dataset,
            "--hmd", str(hmd), "--mano-dir", str(mano),
            "--max-sequences", str(max_sequences),
        )
        artifacts = (hmd / "example_sequences" / dataset,)
        return V2DCommand("ground_motion", argv, self.config.root / "robotic_grounding", artifacts)

    async def run(self, command: V2DCommand, *, dry_run: bool = True) -> dict[str, Any]:
        result = {**command.as_dict(), "dry_run": dry_run}
        if dry_run:
            return {**result, "status": "dry_run"}
        if not self.config.execute_enabled:
            return {
                **result,
                "status": "blocked",
                "error": "Live V2D execution requires ISAAC_ASSIST_V2D_EXECUTE=1",
            }
        if not command.cwd.is_dir():
            return {**result, "status": "unavailable", "error": f"V2D component not found: {command.cwd}"}
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
            return {**result, "status": "timeout", "error": f"V2D exceeded {self.config.timeout_seconds:g}s"}
        return {
            **result,
            "status": "completed" if process.returncode == 0 else "failed",
            "returncode": process.returncode,
            "stdout": stdout.decode(errors="replace")[-_MAX_OUTPUT_CHARS:],
            "stderr": stderr.decode(errors="replace")[-_MAX_OUTPUT_CHARS:],
        }
