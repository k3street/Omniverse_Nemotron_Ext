import os
import re
from typing import Dict, Any
from pathlib import Path
import logging

logger = logging.getLogger(__name__)

from ..config import config

class SettingsManager:
    """Manages reading and writing configuration settings to the .env file."""

    # Map env var names to config attribute names where they differ
    _ENV_TO_ATTR = {
        "LLM_MODE":          "llm_mode",
        "CLOUD_MODEL_NAME":  "cloud_model_name",
        "GEMINI_MODEL_NAME": "gemini_model_name",
        "LOCAL_MODEL_NAME":  "local_model_name",
        "DISTILLER_MODEL_NAME": "distiller_model_name",
        "GEMINI_API_KEY":    "api_key_gemini",
        "API_KEY_GEMINI":    "api_key_gemini",
        "ANTHROPIC_API_KEY": "api_key_anthropic",
        "OPENAI_API_KEY":    "api_key_openai",
        "GROK_API_KEY":      "api_key_grok",
        "XAI_API_KEY":       "api_key_grok",
        "OPENAI_API_BASE":   "openai_api_base",
        "MAX_TOOL_ROUNDS":   "max_tool_rounds",
        "ISAAC_ASSIST_RENDERING_MODE": "rendering_mode",
        "ISAAC_ASSIST_RENDER_CONTROL_FILE": "render_control_file",
        "COSMOS3_MODE": "cosmos3_mode",
        "COSMOS3_REASONER_BASE_URL": "cosmos3_reasoner_base_url",
        "COSMOS3_REASONER_MODEL": "cosmos3_reasoner_model",
        "COSMOS3_GENERATOR_BASE_URL": "cosmos3_generator_base_url",
        "COSMOS3_GENERATOR_MODEL": "cosmos3_generator_model",
        "COSMOS3_API_KEY": "cosmos3_api_key",
        "GEMINI_ROBOTICS_ER_FALLBACK": "gemini_robotics_er_fallback",
        "GEMINI_ROBOTICS_ER_MODEL": "gemini_robotics_er_model",
        "GEMINI_ROBOTICS_ER_BASE_URL": "gemini_robotics_er_base_url",
        "ISAAC_ASSIST_SCALE_PROVIDER": "scale_provider",
        "DGX_SPARK_COSMOS_BASE_URL": "dgx_spark_cosmos_base_url",
        "BREV_API_KEY": "brev_api_key",
        "BREV_PROJECT_ID": "brev_project_id",
        "BREV_TEMPLATE_ID": "brev_template_id",
        "ISAAC_AUTOMATOR_ROOT": "isaac_automator_root",
        "ISAAC_AUTOMATOR_CLOUD": "isaac_automator_cloud",
        "ISAAC_AUTOMATOR_DEPLOYMENT": "isaac_automator_deployment",
        "ISAAC_AUTOMATOR_ISAACSIM_REF": "isaac_automator_isaacsim_ref",
        "ISAAC_AUTOMATOR_ISAACLAB_REF": "isaac_automator_isaaclab_ref",
    }
    _BOOL_KEYS = {"CONTRIBUTE_DATA", "AUTO_APPROVE", "GEMINI_ROBOTICS_ER_FALLBACK"}
    _INT_KEYS  = {"MAX_TOOL_ROUNDS"}

    def __init__(self):
        # Persist machine-local UI changes to .env.local when it exists.
        # launch_service.sh loads .env.local after .env, so writing .env would
        # appear to work until the next restart and then get overridden.
        repo_root = Path(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))))
        local_env = repo_root / ".env.local"
        self.env_path = local_env if local_env.exists() else repo_root / ".env"
    
    def get_settings(self) -> Dict[str, Any]:
        """Returns the current loaded configuration settings."""
        return {
            "LLM_MODE": config.llm_mode,
            "LOCAL_MODEL_NAME": config.local_model_name,
            "DISTILLER_MODEL_NAME": config.distiller_model_name,
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
        "ISAAC_ASSIST_RENDERING_MODE": config.rendering_mode,
        "ISAAC_ASSIST_RENDER_CONTROL_FILE": config.render_control_file,
            # Cosmos 3
            "COSMOS3_MODE": config.cosmos3_mode,
            "COSMOS3_REASONER_BASE_URL": config.cosmos3_reasoner_base_url,
            "COSMOS3_REASONER_MODEL": config.cosmos3_reasoner_model,
            "COSMOS3_GENERATOR_BASE_URL": config.cosmos3_generator_base_url,
            "COSMOS3_GENERATOR_MODEL": config.cosmos3_generator_model,
            "GEMINI_ROBOTICS_ER_FALLBACK": str(config.gemini_robotics_er_fallback).lower(),
            "GEMINI_ROBOTICS_ER_MODEL": config.gemini_robotics_er_model,
            "GEMINI_ROBOTICS_ER_BASE_URL": config.gemini_robotics_er_base_url,
            # Remote scale providers
            "ISAAC_ASSIST_SCALE_PROVIDER": config.scale_provider,
            "DGX_SPARK_COSMOS_BASE_URL": config.dgx_spark_cosmos_base_url,
            "BREV_PROJECT_ID": config.brev_project_id,
            "BREV_TEMPLATE_ID": config.brev_template_id,
            "ISAAC_AUTOMATOR_ROOT": config.isaac_automator_root,
            "ISAAC_AUTOMATOR_CLOUD": config.isaac_automator_cloud,
            "ISAAC_AUTOMATOR_DEPLOYMENT": config.isaac_automator_deployment,
            "ISAAC_AUTOMATOR_ISAACSIM_REF": config.isaac_automator_isaacsim_ref,
            "ISAAC_AUTOMATOR_ISAACLAB_REF": config.isaac_automator_isaaclab_ref,
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
