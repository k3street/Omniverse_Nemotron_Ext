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
        "OPENAI_API_KEY": "api_key_openai",
        "API_KEY_GEMINI": "api_key_gemini",
    }
    _BOOL_KEYS = {"CONTRIBUTE_DATA", "AUTO_APPROVE"}

    def __init__(self):
        # We target the .env located right next to the main config.py
        self.env_path = Path(os.path.dirname(os.path.dirname(__file__))) / ".env"
    
    def get_settings(self) -> Dict[str, Any]:
        """Returns the current loaded configuration settings."""
        return {
            "LLM_MODE": config.llm_mode,
            "LOCAL_MODEL_NAME": config.local_model_name,
            "CLOUD_MODEL_NAME": config.cloud_model_name,
            "API_KEY_GEMINI": config.api_key_gemini,
            "OPENAI_API_BASE": config.openai_api_base,
            "OPENAI_API_KEY": config.api_key_openai,
            "CONTRIBUTE_DATA": str(config.contribute_data).lower(),
            "AUTO_APPROVE": str(config.auto_approve).lower()
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
                else:
                    setattr(config, attr, val)
                
            with open(self.env_path, "w", encoding="utf-8") as f:
                f.writelines(lines)
                
            return True
        except Exception as e:
            logger.error(f"Failed to update settings in .env: {e}")
            return False
