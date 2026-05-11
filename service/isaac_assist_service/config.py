import os


class Config:
    def __init__(self):
        # Load env files in priority order (last loaded wins)
        # .env (root) → service .env → .env.local (highest priority, overrides all)
        for env_path in [
            os.path.join(os.path.dirname(__file__), "..", "..", ".env"),  # repo root
            os.path.join(os.path.dirname(__file__), ".env"),             # service-level
            os.path.join(os.path.dirname(__file__), "..", "..", ".env.local"),  # user local overrides
        ]:
            env_path = os.path.normpath(env_path)
            if os.path.exists(env_path):
                with open(env_path, "r") as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith("#") and "=" in line:
                            key, val = line.split("=", 1)
                            # Do NOT clobber env vars set by the caller (tests rely on this).
                            os.environ.setdefault(key, val)

        # ── LLM routing ─────────────────────────────────────────────────────
        self.llm_mode = os.environ.get("LLM_MODE", "local")
        # LOCAL_MODEL_NAME → OLLAMA_MODEL → hardcoded default (in priority order)
        self.local_model_name = (
            os.environ.get("LOCAL_MODEL_NAME")
            or os.environ.get("OLLAMA_MODEL")
            or "qwen3.5:35b"
        )
        self.cloud_model_name = os.environ.get("CLOUD_MODEL_NAME", "claude-sonnet-4-6")
        self.gemini_model_name = os.environ.get("GEMINI_MODEL_NAME", "gemini-3.1-pro-preview")
        self.distiller_model_name = os.environ.get("DISTILLER_MODEL_NAME", "")  # small LLM for context compression; blank = use local_model_name
        self.vision_model_name = os.environ.get("VISION_MODEL_NAME", "gemini-robotics-er-1.6-preview")
        # Vision provider routing: "ollama" | "gemini" | "auto" (default = auto: Ollama→Gemini)
        self.vision_provider   = os.environ.get("VISION_PROVIDER", "auto").lower()
        self.ollama_vision_model = os.environ.get("OLLAMA_VISION_MODEL", "nemotron3:33b")
        self.ollama_host       = os.environ.get("OLLAMA_HOST", "127.0.0.1")
        self.ollama_vision_port = int(os.environ.get("OLLAMA_VISION_PORT", "11434"))

        # ── API keys (pulled from root .env or service .env) ─────────────────
        self.api_key_gemini    = os.environ.get("GEMINI_API_KEY") or os.environ.get("API_KEY_GEMINI", "")
        self.api_key_anthropic = os.environ.get("ANTHROPIC_API_KEY", "")
        self.api_key_openai    = os.environ.get("OPENAI_API_KEY", "")
        self.api_key_grok      = os.environ.get("GROK_API_KEY") or os.environ.get("XAI_API_KEY", "")
        self.api_key_moonshot  = os.environ.get("MOONSHOT_API_KEY", "")

        # ── LiveKit ──────────────────────────────────────────────────────────
        self.livekit_url        = os.environ.get("LIVEKIT_URL", "")
        self.livekit_api_key    = os.environ.get("LIVEKIT_API_KEY", "")
        self.livekit_api_secret = os.environ.get("LIVEKIT_API_SECRET", "")

        # ── MCP Server ───────────────────────────────────────────────────────
        self.mcp_host = os.environ.get("MCP_HOST", "127.0.0.1")
        self.mcp_port = int(os.environ.get("MCP_PORT", "8002"))

        # ── ROS Bridge (ros-mcp integration) ─────────────────────────────────
        self.rosbridge_host = os.environ.get("ROSBRIDGE_HOST", "127.0.0.1")
        self.rosbridge_port = int(os.environ.get("ROSBRIDGE_PORT", "9090"))

        # ── Misc ─────────────────────────────────────────────────────────────
        self.openai_api_base  = os.environ.get("OPENAI_API_BASE", "https://api.openai.com/v1")
        self.contribute_data       = os.environ.get("CONTRIBUTE_DATA", "false").lower() == "true"
        self.auto_approve          = os.environ.get("AUTO_APPROVE", "false").lower() == "true"
        self.auto_inject_viewport  = os.environ.get("AUTO_INJECT_VIEWPORT", "false").lower() == "true"
        self.max_tool_rounds = int(os.environ.get("MAX_TOOL_ROUNDS", "10"))
        # ── Assets ───────────────────────────────────────────────────────────
        self.assets_root_path = os.environ.get("ASSETS_ROOT_PATH", "")
        self.assets_robots_subdir = os.environ.get("ASSETS_ROBOTS_SUBDIR", "Collected_Robots")
        # ── External tools ───────────────────────────────────────────────────
        self.isaaclab_path = os.environ.get("ISAACLAB_PATH", "")

        # ── Manipulation stack (RL+VLA pipeline) ─────────────────────────────
        self.continuity_manager_url  = os.environ.get("CONTINUITY_MANAGER_URL", "http://localhost:7100")
        self.policy_bank_url         = os.environ.get("POLICY_BANK_URL",        "http://localhost:7101")
        self.manipulation_embodiment_id = os.environ.get("EMBODIMENT_ID", "tenthings_v1_open_arm_bimanual")


config = Config()
