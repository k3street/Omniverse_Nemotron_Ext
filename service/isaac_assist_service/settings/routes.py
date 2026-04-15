import asyncio
import logging
import subprocess
from typing import Dict, Any
from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel

from .manager import SettingsManager

logger = logging.getLogger(__name__)

router = APIRouter()
settings_manager = SettingsManager()

class SettingsUpdateRequest(BaseModel):
    settings: Dict[str, str]

class ModelPullRequest(BaseModel):
    model_name: str

@router.get("/")
async def get_settings():
    """Retrieve current LLM configuration settings."""
    return {"status": "success", "settings": settings_manager.get_settings()}

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

VALID_MODES = ("local", "cloud", "anthropic", "openai", "grok")


class ModeSwitchRequest(BaseModel):
    mode: str


@router.get("/llm_mode")
async def get_llm_mode():
    """Return the current LLM mode and active model name."""
    from ..config import config
    return {
        "llm_mode": config.llm_mode,
        "model": config.cloud_model_name if config.llm_mode != "local" else config.local_model_name,
    }


@router.put("/llm_mode")
async def switch_llm_mode(req: ModeSwitchRequest):
    """
    Hot-switch the LLM provider without restarting the service.

    Accepted modes: local, cloud, anthropic, openai, grok
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
    model = config.cloud_model_name if mode != "local" else config.local_model_name
    logger.info(f"LLM mode switched to '{mode}' (model: {model})")

    return {
        "status": "success",
        "llm_mode": mode,
        "model": model,
    }
