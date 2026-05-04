import os
import re
from typing import Dict, Any
from pathlib import Path
import logging

logger = logging.getLogger(__name__)

from service.isaac_assist_service.config import config

class SettingsManager:
    """Manages reading and writing configuration settings to the .env file."""

    # Map env var names to config attribute names where they differ
    _ENV_TO_ATTR = {
        "LLM_MODE":          "llm_mode",
        "CLOUD_MODEL_NAME":  "cloud_model_name",
        "GEMINI_MODEL_NAME": "gemini_model_name",
        "LOCAL_MODEL_NAME":  "local_model_name",
        "GEMINI_API_KEY":    "api_key_gemini",
        "API_KEY_GEMINI":    "api_key_gemini",
        "ANTHROPIC_API_KEY": "api_key_anthropic",
        "OPENAI_API_KEY":    "api_key_openai",
        "GROK_API_KEY":      "api_key_grok",
        "XAI_API_KEY":       "api_key_grok",
        "OPENAI_API_BASE":   "openai_api_base",
        "MAX_TOOL_ROUNDS":   "max_tool_rounds",
    }
    _BOOL_KEYS = {"CONTRIBUTE_DATA", "AUTO_APPROVE"}
    _INT_KEYS  = {"MAX_TOOL_ROUNDS"}

    def __init__(self):
        # Target the root repo .env (where all API keys live)
        self.env_path = Path(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))) / ".env"
    
    def get_settings(self) -> Dict[str, Any]:
        """Returns the current loaded configuration settings."""
        return {
            "LLM_MODE": config.llm_mode,
            "LOCAL_MODEL_NAME": config.local_model_name,
            "CLOUD_MODEL_NAME": config.cloud_model_name,
            "GEMINI_MODEL_NAME": config.gemini_model_name,
            "VISION_MODEL_NAME": config.vision_model_name,
            "VISION_PROVIDER": config.vision_provider,
            "OLLAMA_VISION_MODEL": config.ollama_vision_model,
            "OLLAMA_HOST": config.ollama_host,
            "OLLAMA_VISION_PORT": str(config.ollama_vision_port),
            "GEMINI_API_KEY": config.api_key_gemini,
            "OPENAI_API_BASE": config.openai_api_base,
            "OPENAI_API_KEY": config.api_key_openai,
            "CONTRIBUTE_DATA": str(config.contribute_data).lower(),
            "AUTO_APPROVE": str(config.auto_approve).lower(),
            "AUTO_INJECT_VIEWPORT": str(config.auto_inject_viewport).lower(),
            "MAX_TOOL_ROUNDS": str(config.max_tool_rounds),
            # Manipulation stack
            "CONTINUITY_MANAGER_URL": config.continuity_manager_url,
            "POLICY_BANK_URL": config.policy_bank_url,
            "EMBODIMENT_ID": config.manipulation_embodiment_id,
        }

    def update_settings(self, new_settings: Dict[str, str]) -> bool:
        """
        Updates the .env file with the given dictionary of settings.
        Also patches the running os.environ and config module.
        """
        try:
            if not self.env_path.exists():
                self.env_path.touch()
                
            with open(self.env_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
            
            # Update existing keys
            for key, val in new_settings.items():
                pattern = re.compile(rf"^{key}\s*=.*")
                found = False
                for i, line in enumerate(lines):
                    if pattern.match(line.strip()):
                        lines[i] = f"{key}={val}\n"
                        found = True
                        break
                
                # Append if not found
                if not found:
                    lines.append(f"{key}={val}\n")
                    
                # Dynamically patch live settings
                os.environ[key] = val
                attr = self._ENV_TO_ATTR.get(key, key.lower())
                if key in self._BOOL_KEYS:
                    setattr(config, attr, val.lower() == "true")
                elif key in self._INT_KEYS:
                    setattr(config, attr, int(val))
                else:
                    setattr(config, attr, val)
                
            with open(self.env_path, "w", encoding="utf-8") as f:
                f.writelines(lines)
                
            return True
        except Exception as e:
            logger.error(f"Failed to update settings in .env: {e}")
            return False
