from .llm_ollama import OllamaProvider
from .llm_gemini import GeminiProvider
from .llm_anthropic import AnthropicProvider
from .llm_openai_compat import OpenAICompatProvider, PROVIDER_URLS
from ..config import config


def get_llm_provider():
    """
    Factory returning the configured LLM provider.

    LLM_MODE options:
      local       → Ollama (LOCAL_MODEL_NAME)
      cloud       → Gemini (CLOUD_MODEL_NAME + API_KEY_GEMINI / GEMINI_API_KEY)
      anthropic   → Claude (CLOUD_MODEL_NAME + ANTHROPIC_API_KEY)
      openai      → OpenAI (CLOUD_MODEL_NAME + OPENAI_API_KEY)
      grok        → xAI Grok (CLOUD_MODEL_NAME + GROK_API_KEY / XAI_API_KEY)
    """
    mode = config.llm_mode.lower()

    if mode == "anthropic":
        _require(config.api_key_anthropic, "ANTHROPIC_API_KEY", mode)
        return AnthropicProvider(
            api_key=config.api_key_anthropic,
            model=config.cloud_model_name,
        )

    if mode == "openai":
        _require(config.api_key_openai, "OPENAI_API_KEY", mode)
        return OpenAICompatProvider(
            api_key=config.api_key_openai,
            model=config.cloud_model_name,
            base_url=PROVIDER_URLS["openai"],
        )

    if mode == "grok":
        _require(config.api_key_grok, "GROK_API_KEY / XAI_API_KEY", mode)
        return OpenAICompatProvider(
            api_key=config.api_key_grok,
            model=config.cloud_model_name,
            base_url=PROVIDER_URLS["grok"],
        )

    if mode == "cloud":
        _require(config.api_key_gemini, "API_KEY_GEMINI / GEMINI_API_KEY", mode)
        return GeminiProvider(
            api_key=config.api_key_gemini,
            model=config.cloud_model_name,
        )

    # Default → local Ollama via OpenAI-compatible endpoint
    # Uses /v1/chat/completions instead of /api/chat to avoid
    # Qwen 3.5 tool-call JSON parsing bugs in the native Ollama API.
    # See: https://github.com/ollama/ollama/issues/14493
    ollama_base = config.openai_api_base.rstrip("/")
    if not ollama_base.endswith("/chat/completions"):
        ollama_base += "/chat/completions"
    return OpenAICompatProvider(
        api_key="ollama",  # Ollama ignores auth
        model=config.local_model_name,
        base_url=ollama_base,
    )


def _require(value: str, key_name: str, mode: str):
    if not value:
        raise ValueError(f"LLM_MODE={mode} but {key_name} is missing from .env")


def get_distiller_provider():
    """
    Return a small / fast LLM provider for context compression.

    Uses DISTILLER_MODEL_NAME (defaults to LOCAL_MODEL_NAME via Ollama).
    If the main mode is local, reuses the same Ollama instance.
    If a dedicated distiller model is configured, always uses Ollama for it.
    """
    model = config.distiller_model_name or config.local_model_name
    return OllamaProvider(model=model)
