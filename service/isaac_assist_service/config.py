import os

class Config:
    def __init__(self):
        # Load simplistic .env (in production, use python-dotenv)
        env_path = os.path.join(os.path.dirname(__file__), '.env')
        if os.path.exists(env_path):
            with open(env_path, "r") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#'):
                        key, val = line.split("=", 1)
                        os.environ[key] = val

        self.llm_mode = os.environ.get("LLM_MODE", "local")
        self.local_model_name = os.environ.get("LOCAL_MODEL_NAME", "cosmos-reason-2:latest")
        self.cloud_model_name = os.environ.get("CLOUD_MODEL_NAME", "gemini-1.5-pro-latest")
        self.api_key_gemini = os.environ.get("API_KEY_GEMINI", "")
        self.livekit_url = os.environ.get("LIVEKIT_URL", "")
        self.livekit_api_key = os.environ.get("LIVEKIT_API_KEY", "")
        self.livekit_api_secret = os.environ.get("LIVEKIT_API_SECRET", "")
        self.contribute_data = os.environ.get("CONTRIBUTE_DATA", "False").lower() == "true"

config = Config()
