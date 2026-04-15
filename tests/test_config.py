"""
L0 tests for service.isaac_assist_service.config.Config
"""
import os
import pytest

pytestmark = pytest.mark.l0


class TestConfigDefaults:
    """Config should load sane defaults even when no .env file exists."""

    def test_llm_mode_default(self, fresh_config):
        assert fresh_config.llm_mode == "local"

    def test_local_model_name(self, fresh_config):
        assert fresh_config.local_model_name == "test-model:7b"

    def test_contribute_data_default_false(self, fresh_config):
        assert fresh_config.contribute_data is False

    def test_auto_approve_default_false(self, fresh_config):
        assert fresh_config.auto_approve is False

    def test_max_tool_rounds_is_int(self, fresh_config):
        assert isinstance(fresh_config.max_tool_rounds, int)
        assert fresh_config.max_tool_rounds > 0

    def test_mcp_port_default(self, fresh_config):
        assert isinstance(fresh_config.mcp_port, int)

    def test_rosbridge_defaults(self, fresh_config):
        assert fresh_config.rosbridge_host == "127.0.0.1"
        assert fresh_config.rosbridge_port == 9090


class TestConfigEnvOverrides:
    """Env vars override the defaults."""

    def test_llm_mode_override(self, monkeypatch):
        monkeypatch.setenv("LLM_MODE", "anthropic")
        from service.isaac_assist_service.config import Config
        cfg = Config()
        assert cfg.llm_mode == "anthropic"

    def test_contribute_data_true(self, monkeypatch):
        monkeypatch.setenv("CONTRIBUTE_DATA", "true")
        from service.isaac_assist_service.config import Config
        cfg = Config()
        assert cfg.contribute_data is True

    def test_auto_approve_true(self, monkeypatch):
        monkeypatch.setenv("AUTO_APPROVE", "true")
        from service.isaac_assist_service.config import Config
        cfg = Config()
        assert cfg.auto_approve is True

    def test_max_tool_rounds_override(self, monkeypatch):
        monkeypatch.setenv("MAX_TOOL_ROUNDS", "5")
        from service.isaac_assist_service.config import Config
        cfg = Config()
        assert cfg.max_tool_rounds == 5

    def test_assets_root_path_override(self, monkeypatch):
        monkeypatch.setenv("ASSETS_ROOT_PATH", "/my/assets")
        from service.isaac_assist_service.config import Config
        cfg = Config()
        assert cfg.assets_root_path == "/my/assets"


class TestConfigLLMModes:
    """All documented LLM_MODE values produce a valid Config."""

    @pytest.mark.parametrize("mode", ["local", "cloud", "anthropic", "openai", "grok"])
    def test_mode_creates_valid_config(self, monkeypatch, mode):
        monkeypatch.setenv("LLM_MODE", mode)
        from service.isaac_assist_service.config import Config
        cfg = Config()
        assert cfg.llm_mode == mode
        # Config should have all essential attributes regardless of mode
        assert hasattr(cfg, "cloud_model_name")
        assert hasattr(cfg, "local_model_name")
        assert hasattr(cfg, "api_key_openai")
        assert hasattr(cfg, "api_key_anthropic")
        assert hasattr(cfg, "api_key_gemini")
        assert hasattr(cfg, "api_key_grok")
