"""Contract tests for the Carbonite console-log adapter."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.l0
MODULE_PATH = Path(__file__).parents[1] / (
    "exts/isaac_6.0/omni.isaac.assist/omni/isaac/assist/context/console_log.py"
)


class FakeLogging:
    def __init__(self):
        self.callback = None
        self.removed = None

    def add_logger(self, callback):
        self.callback = callback
        return "handle"

    def remove_logger(self, handle):
        self.removed = handle


def load_module(fake_logging):
    fake_carb = SimpleNamespace(
        logging=SimpleNamespace(
            acquire_logging=lambda: fake_logging,
            LEVEL_VERBOSE=0, LEVEL_INFO=1, LEVEL_WARN=2, LEVEL_ERROR=3, LEVEL_FATAL=4,
        ),
        log_info=lambda _message: None,
        log_warn=lambda _message: None,
    )
    old = sys.modules.get("carb")
    sys.modules["carb"] = fake_carb
    try:
        spec = importlib.util.spec_from_file_location("extension_console_log", MODULE_PATH)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        if old is None:
            sys.modules.pop("carb", None)
        else:
            sys.modules["carb"] = old


def test_attach_capture_detach_uses_supported_logging_contract():
    logging = FakeLogging()
    module = load_module(logging)
    module.attach_log_listener()
    module.attach_log_listener()
    logging.callback("renderer", 3, "render.cpp", 42, " failure ")
    assert module.get_recent_logs(1, "error") == [{
        "level": "error",
        "msg": "failure",
        "source": "render.cpp:42",
        "channel": "renderer",
    }]
    module.detach_log_listener()
    assert logging.removed == "handle"
    assert module._subscription is None
