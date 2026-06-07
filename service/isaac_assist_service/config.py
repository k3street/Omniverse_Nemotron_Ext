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

        # ── Cosmos 3 scene proposal runtime ─────────────────────────────────
        self.cosmos3_mode = os.environ.get("COSMOS3_MODE", "disabled")
        self.cosmos3_reasoner_base_url = os.environ.get("COSMOS3_REASONER_BASE_URL", "")
        self.cosmos3_reasoner_model = os.environ.get("COSMOS3_REASONER_MODEL", "Cosmos3-Nano")
        self.cosmos3_generator_base_url = os.environ.get("COSMOS3_GENERATOR_BASE_URL", "")
        self.cosmos3_generator_model = os.environ.get("COSMOS3_GENERATOR_MODEL", "Cosmos3-Super-Text2Image")
        self.cosmos3_api_key = (
            os.environ.get("COSMOS3_API_KEY")
            or os.environ.get("NGC_API_KEY")
            or ""
        )

        # ── API keys (pulled from root .env or service .env) ─────────────────
        self.api_key_gemini    = os.environ.get("GEMINI_API_KEY") or os.environ.get("API_KEY_GEMINI", "")
        self.api_key_anthropic = os.environ.get("ANTHROPIC_API_KEY", "")
        self.api_key_openai    = os.environ.get("OPENAI_API_KEY", "")
        self.api_key_grok      = os.environ.get("GROK_API_KEY") or os.environ.get("XAI_API_KEY", "")
        self.api_key_moonshot  = os.environ.get("MOONSHOT_API_KEY", "")

        # ── Cloud reasoner fallback ──────────────────────────────────────────
        # Gemini Robotics-ER is used as a cloud backup for Cosmos 3 Reasoner
        # scene observation, not as a direct Isaac scene mutator.
        self.gemini_robotics_er_fallback = (
            os.environ.get("GEMINI_ROBOTICS_ER_FALLBACK", "false").lower() == "true"
        )
        self.gemini_robotics_er_model = os.environ.get(
            "GEMINI_ROBOTICS_ER_MODEL",
            self.vision_model_name or "gemini-robotics-er-1.6-preview",
        )
        self.gemini_robotics_er_base_url = os.environ.get(
            "GEMINI_ROBOTICS_ER_BASE_URL",
            "https://generativelanguage.googleapis.com/v1beta/models",
        )

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
        self.ollama_openai_base = os.environ.get("OLLAMA_OPENAI_BASE", "http://localhost:11434/v1")
        self.contribute_data       = os.environ.get("CONTRIBUTE_DATA", "false").lower() == "true"
        self.auto_approve          = os.environ.get("AUTO_APPROVE", "false").lower() == "true"
        self.auto_inject_viewport  = os.environ.get("AUTO_INJECT_VIEWPORT", "false").lower() == "true"
        self.max_tool_rounds = int(os.environ.get("MAX_TOOL_ROUNDS", "10"))
        # ── Assets ───────────────────────────────────────────────────────────
        self.assets_root_path = os.environ.get("ASSETS_ROOT_PATH", "")
        self.assets_robots_subdir = os.environ.get("ASSETS_ROBOTS_SUBDIR", "Collected_Robots")
        # ── External tools ───────────────────────────────────────────────────
        self.isaaclab_path = os.environ.get("ISAACLAB_PATH", "")

        # ── Remote scale providers ───────────────────────────────────────────
        self.scale_provider = os.environ.get("ISAAC_ASSIST_SCALE_PROVIDER", "local")
        self.dgx_spark_cosmos_base_url = os.environ.get("DGX_SPARK_COSMOS_BASE_URL", "")
        self.brev_api_key = os.environ.get("BREV_API_KEY", "")
        self.brev_project_id = os.environ.get("BREV_PROJECT_ID", "")
        self.brev_template_id = os.environ.get("BREV_TEMPLATE_ID", "")
        self.isaac_automator_root = os.environ.get("ISAAC_AUTOMATOR_ROOT", "")
        self.isaac_automator_cloud = os.environ.get("ISAAC_AUTOMATOR_CLOUD", "")
        self.isaac_automator_deployment = os.environ.get("ISAAC_AUTOMATOR_DEPLOYMENT", "")
        self.isaac_automator_isaacsim_ref = os.environ.get("ISAAC_AUTOMATOR_ISAACSIM_REF", "")
        self.isaac_automator_isaaclab_ref = os.environ.get("ISAAC_AUTOMATOR_ISAACLAB_REF", "")

        # ── Manipulation stack (RL+VLA pipeline) ─────────────────────────────
        self.continuity_manager_url  = os.environ.get("CONTINUITY_MANAGER_URL", "http://localhost:7100")
        self.policy_bank_url         = os.environ.get("POLICY_BANK_URL",        "http://localhost:7101")
        self.manipulation_embodiment_id = os.environ.get("EMBODIMENT_ID", "tenthings_v1_open_arm_bimanual")


config = Config()
