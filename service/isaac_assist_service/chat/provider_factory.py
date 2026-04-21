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
      google      → Gemini (GEMINI_MODEL_NAME + GEMINI_API_KEY)
      anthropic   → Claude (CLOUD_MODEL_NAME + ANTHROPIC_API_KEY)
      openai      → OpenAI (CLOUD_MODEL_NAME + OPENAI_API_KEY)
      grok        → xAI Grok (CLOUD_MODEL_NAME + GROK_API_KEY / XAI_API_KEY)
    """
    mode = config.llm_mode.lower()

    if mode == "anthropic":
        _require(config.api_key_anthropic, "ANTHROPIC_API_KEY", mode)
        _require_model(config.cloud_model_name, mode)
        return AnthropicProvider(
            api_key=config.api_key_anthropic,
            model=config.cloud_model_name,
        )

    if mode == "openai":
        _require(config.api_key_openai, "OPENAI_API_KEY", mode)
        _require_model(config.cloud_model_name, mode)
        return OpenAICompatProvider(
            api_key=config.api_key_openai,
            model=config.cloud_model_name,
            base_url=PROVIDER_URLS["openai"],
        )

    if mode == "grok":
        _require(config.api_key_grok, "GROK_API_KEY / XAI_API_KEY", mode)
        _require_model(config.cloud_model_name, mode)
        return OpenAICompatProvider(
            api_key=config.api_key_grok,
            model=config.cloud_model_name,
            base_url=PROVIDER_URLS["grok"],
        )

    if mode == "google":
        _require(config.api_key_gemini, "GEMINI_API_KEY", mode)
        _require_model(config.gemini_model_name, mode)
        return GeminiProvider(
            api_key=config.api_key_gemini,
            model=config.gemini_model_name,
        )

    # Default → local Ollama via OpenAI-compatible endpoint
    # Uses /v1/chat/completions instead of /api/chat to avoid
    # Qwen 3.5 tool-call JSON parsing bugs in the native Ollama API.
    # See: https://github.com/ollama/ollama/issues/14493
    _require_model(config.local_model_name, mode)
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


def _require_model(model: str, mode: str):
    if not model or not model.strip():
        raise ValueError(
            f"LLM_MODE={mode} but model name is empty. "
            f"Set LOCAL_MODEL_NAME (or OLLAMA_MODEL) in service/.env for local mode, "
            f"or CLOUD_MODEL_NAME for non-local modes."
        )


def get_distiller_provider():
    """
    Return a small / fast LLM provider for context compression.

    Strategy:
      1. If DISTILLER_MODEL_NAME is explicitly set → use Ollama (user wants local distiller)
      2. If LLM_MODE is local → use Ollama with LOCAL_MODEL_NAME
      3. Otherwise → reuse the main cloud provider (works even when Ollama is not running)
    """
    if config.distiller_model_name:
        # Explicit distiller model configured — always use Ollama for it
        return OllamaProvider(model=config.distiller_model_name)

    mode = config.llm_mode.lower()
    if mode == "local":
        return OllamaProvider(model=config.local_model_name)

    # Cloud modes: reuse the main provider so compression works without Ollama
    return get_llm_provider()
