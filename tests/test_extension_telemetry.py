"""Extension telemetry must remain optional, opt-in, and version-aligned."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

pytestmark = pytest.mark.l0

MODULE_PATH = (
    Path(__file__).parents[1]
    / "exts/isaac_6.0/omni.isaac.assist/omni/isaac/assist/telemetry.py"
)


def load_module():
    spec = importlib.util.spec_from_file_location("isaac_assist_extension_telemetry", MODULE_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_telemetry_defaults_off_without_importing_sdk(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("IA_TELEMETRY", raising=False)
    telemetry = load_module()
    assert telemetry.is_telemetry_enabled() is False
    assert telemetry.init_telemetry() is False
    assert telemetry.tracer is None


def test_environment_choice_overrides_preference_file(monkeypatch, tmp_path):
    preference = tmp_path / ".isaac_assist" / "telemetry.txt"
    preference.parent.mkdir(parents=True)
    preference.write_text("enabled", encoding="utf-8")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("IA_TELEMETRY", "off")
    assert load_module().is_telemetry_enabled() is False


def test_trace_error_preserves_result_and_exception():
    telemetry = load_module()

    @telemetry.trace_error("double")
    def double(value):
        return value * 2

    @telemetry.trace_error("explode")
    def explode():
        raise RuntimeError("boom")

    assert double(3) == 6
    with pytest.raises(RuntimeError, match="boom"):
        explode()


def test_shutdown_flushes_owned_provider():
    telemetry = load_module()

    class Provider:
        stopped = False

        def shutdown(self):
            self.stopped = True

    provider = Provider()
    telemetry._provider = provider
    telemetry.tracer = object()
    telemetry.shutdown_telemetry()
    assert provider.stopped is True
    assert telemetry._provider is None
    assert telemetry.tracer is None
