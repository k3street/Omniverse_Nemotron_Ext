import os


class Config:
    def __init__(self):
        # Load service-level .env first, then root .env as fallback
        for env_path in [
            os.path.join(os.path.dirname(__file__), ".env"),
            os.path.join(os.path.dirname(__file__), "..", "..", ".env"),  # repo root
        ]:
            env_path = os.path.normpath(env_path)
            if os.path.exists(env_path):
                with open(env_path, "r") as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith("#") and "=" in line:
                            key, val = line.split("=", 1)
                            # Don't overwrite values already set (service .env takes priority)
                            if key not in os.environ:
                                os.environ[key] = val

        # ── LLM routing ─────────────────────────────────────────────────────
        self.llm_mode = os.environ.get("LLM_MODE", "local")
        self.local_model_name = os.environ.get("LOCAL_MODEL_NAME", "qwen3.5:35b")
        self.cloud_model_name = os.environ.get("CLOUD_MODEL_NAME", "claude-sonnet-4-6")

        # ── API keys (pulled from root .env or service .env) ─────────────────
        self.api_key_gemini    = os.environ.get("API_KEY_GEMINI") or os.environ.get("GEMINI_API_KEY", "")
        self.api_key_anthropic = os.environ.get("ANTHROPIC_API_KEY", "")
        self.api_key_openai    = os.environ.get("OPENAI_API_KEY", "")
        self.api_key_grok      = os.environ.get("GROK_API_KEY") or os.environ.get("XAI_API_KEY", "")

        # ── LiveKit ──────────────────────────────────────────────────────────
        self.livekit_url        = os.environ.get("LIVEKIT_URL", "")
        self.livekit_api_key    = os.environ.get("LIVEKIT_API_KEY", "")
        self.livekit_api_secret = os.environ.get("LIVEKIT_API_SECRET", "")

        # ── MCP Server ───────────────────────────────────────────────────────
        self.mcp_host = os.environ.get("MCP_HOST", "127.0.0.1")
        self.mcp_port = int(os.environ.get("MCP_PORT", "8002"))

        # ── Misc ─────────────────────────────────────────────────────────────
        self.openai_api_base  = os.environ.get("OPENAI_API_BASE", "https://api.openai.com/v1")
        self.contribute_data  = os.environ.get("CONTRIBUTE_DATA", "false").lower() == "true"


config = Config()
