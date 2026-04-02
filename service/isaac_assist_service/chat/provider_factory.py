from .llm_ollama import OllamaProvider
from .llm_gemini import GeminiProvider
from ..config import config

def get_llm_provider():
    """
    Factory method to return the configured LLM Provider.
    """
    if config.llm_mode.lower() == "cloud":
        if not config.api_key_gemini:
            raise ValueError("LLM_MODE is cloud but API_KEY_GEMINI is missing from .env")
        return GeminiProvider(api_key=config.api_key_gemini, model=config.cloud_model_name)
    else:
        # Default to local Ollama
        return OllamaProvider(model=config.local_model_name)
