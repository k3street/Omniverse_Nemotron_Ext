from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.l0


def test_local_provider_uses_ollama_base_not_openai_base(monkeypatch):
    import sys

    monkeypatch.setitem(sys.modules, "aiohttp", SimpleNamespace())

    from service.isaac_assist_service.chat import provider_factory

    monkeypatch.setattr(
        provider_factory,
        "config",
        SimpleNamespace(
            llm_mode="local",
            local_model_name="qwen3.6:latest",
            openai_api_base="https://api.openai.com/v1",
            ollama_openai_base="http://localhost:11434/v1",
        ),
    )

    provider = provider_factory.get_llm_provider()

    assert provider.model == "qwen3.6:latest"
    assert provider.base_url == "http://localhost:11434/v1/chat/completions"
