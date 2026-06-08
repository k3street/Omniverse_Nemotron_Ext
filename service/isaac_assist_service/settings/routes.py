import asyncio
import json
import logging
from pathlib import Path
import subprocess
from typing import Dict, Any, Optional
from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel

from .manager import SettingsManager
from ..scale_providers import cosmos_reasoner_status, scale_provider_notice

logger = logging.getLogger(__name__)

router = APIRouter()
settings_manager = SettingsManager()


def _active_model_name(config):
    mode = (config.llm_mode or "local").lower()
    if mode == "local":
        return config.local_model_name
    if mode in ("google", "gemini", "cloud"):
        return config.gemini_model_name
    return config.cloud_model_name

class SettingsUpdateRequest(BaseModel):
    settings: Dict[str, str]

class ModelPullRequest(BaseModel):
    model_name: str

@router.get("/")
async def get_settings():
    """Retrieve current LLM configuration settings."""
    from ..config import config
    return {
        "status": "success",
        "settings": settings_manager.get_settings(),
        "scale_notice": scale_provider_notice(config),
        "cosmos_reasoner": cosmos_reasoner_status(config),
    }

@router.post("/")
async def update_settings(req: SettingsUpdateRequest):
    """Updates the .env with new configuration values and persists them."""
    success = settings_manager.update_settings(req.settings)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to write new settings to .env file")

    # Reinitialize the LLM provider so model/key changes take effect immediately
    from ..chat.routes import orchestrator
    orchestrator.refresh_provider()

    return {"status": "success", "settings": settings_manager.get_settings()}

def run_ollama_pull(model_name: str):
    """Background task to pull an ollama model."""
    try:
        logger.info(f"Triggering background pull for ollama model: {model_name}")
        # We run it detached and wait for completion in background to let user keep using service
        subprocess.run(["ollama", "pull", model_name], check=True)
        logger.info(f"Successfully pulled local model: {model_name}")
    except subprocess.CalledProcessError as e:
        logger.error(f"Failed to pull model {model_name}: {e}")
    except FileNotFoundError:
        logger.error("Ollama CLI not found on host machine.")

@router.post("/pull_local")
async def pull_local_model(req: ModelPullRequest, background_tasks: BackgroundTasks):
    """
    Kicks off an ollama pull for the requested model in the background.
    Recommended model: qwen2.5:7b or similar.
    """
    if not req.model_name:
        raise HTTPException(status_code=400, detail="Missing model_name")
        
    background_tasks.add_task(run_ollama_pull, req.model_name)
    return {"status": "success", "detail": f"Pull operation started for {req.model_name} in the background."}


# ── Quick LLM mode switch ────────────────────────────────────────────────────

VALID_MODES = ("local", "google", "anthropic", "openai", "grok", "gemini")


class ModeSwitchRequest(BaseModel):
    mode: str


class RenderingModeRequest(BaseModel):
    mode: str


VALID_RENDERING_MODES = ("fast", "real")


def _parse_rendering_mode(mode: str) -> str:
    return (mode or "").strip().lower()


def _write_render_control_file(mode: str) -> Optional[str]:
    from ..config import config
    configured_path = (config.render_control_file or "").strip()
    if not configured_path:
        return None
    path = Path(configured_path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"mode": mode, "render_enabled": mode == "real"}, indent=2) + "\n",
        encoding="utf-8",
    )
    return str(path)


@router.get("/scale_notice")
async def get_scale_notice(job_kind: Optional[str] = None):
    """Return advisory notice for configured remote scale providers."""
    from ..config import config
    return {
        "status": "success",
        "notice": scale_provider_notice(config, job_kind=job_kind),
    }


@router.get("/cosmos_reasoner")
async def get_cosmos_reasoner():
    """Return the configured Cosmos 3 Reasoner endpoint without probing it."""
    from ..config import config
    return {
        "status": "success",
        "reasoner": cosmos_reasoner_status(config),
    }


@router.get("/llm_mode")
async def get_llm_mode():
    """Return the current LLM mode and active model name."""
    from ..config import config
    return {
        "llm_mode": config.llm_mode,
        "model": _active_model_name(config),
    }


@router.put("/llm_mode")
async def switch_llm_mode(req: ModeSwitchRequest):
    """
    Hot-switch the LLM provider without restarting the service.

    Accepted modes: local, google, anthropic, openai, grok
    """
    mode = req.mode.strip().lower()
    if mode not in VALID_MODES:
        raise HTTPException(status_code=400, detail=f"Invalid mode '{req.mode}'. Choose from: {', '.join(VALID_MODES)}")

    # Persist to .env + patch running config
    success = settings_manager.update_settings({"LLM_MODE": mode})
    if not success:
        raise HTTPException(status_code=500, detail="Failed to write LLM_MODE to .env")

    # Re-init the chat provider
    from ..chat.routes import orchestrator
    orchestrator.refresh_provider()

    from ..config import config
    model = _active_model_name(config)
    logger.info(f"LLM mode switched to '{mode}' (model: {model})")

    return {
        "status": "success",
        "llm_mode": mode,
        "model": model,
    }


@router.get("/rendering_mode")
async def get_rendering_mode():
    """Return fast-verification vs real-rendering mode."""
    from ..config import config
    mode = _parse_rendering_mode(config.rendering_mode)
    if mode not in VALID_RENDERING_MODES:
        mode = "real"
    return {
        "status": "success",
        "mode": mode,
        "render_enabled": mode == "real",
        "control_file": config.render_control_file,
    }


@router.put("/rendering_mode")
async def switch_rendering_mode(req: RenderingModeRequest):
    """
    Hot-switch render stepping mode.

    fast -> runtime loop should use world.step(render=False)
    real -> runtime loop should use world.step(render=True)
    """
    mode = _parse_rendering_mode(req.mode)
    if mode not in VALID_RENDERING_MODES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid rendering mode '{req.mode}'. Choose from: {', '.join(VALID_RENDERING_MODES)}",
        )

    success = settings_manager.update_settings({"ISAAC_ASSIST_RENDERING_MODE": mode})
    if not success:
        raise HTTPException(status_code=500, detail="Failed to persist rendering mode")

    control_file = None
    try:
        control_file = _write_render_control_file(mode)
    except Exception as e:
        logger.warning(f"Failed to write render control file: {e}")

    return {
        "status": "success",
        "mode": mode,
        "render_enabled": mode == "real",
        "control_file": control_file,
        "kit_applied": False,
        "kit_output": "mode is available to the runtime loop when a render control file is configured",
    }
