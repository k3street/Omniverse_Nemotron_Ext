import asyncio
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.l0


class FakeResponse:
    def __init__(self, status, payload=None, text=""):
        self.status = status
        self._payload = payload or {}
        self._text = text

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def json(self):
        return self._payload

    async def text(self):
        return self._text


class FakeSession:
    def __init__(self):
        self.posted = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def get(self, url):
        self.canvas_url = url
        return FakeResponse(200, {"revision": 7})

    def post(self, url, json, timeout=None):
        self.posted = {"url": url, "json": json, "timeout": timeout}
        return FakeResponse(
            200,
            {
                "valid": True,
                "revision": 8,
                "spec": {"objects": []},
            },
        )


def load_service_client(version):
    path = Path(
        f"exts/isaac_{version}/omni.isaac.assist/omni/isaac/assist/service_client.py"
    )
    spec = importlib.util.spec_from_file_location(f"service_client_{version}", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("version", ["5.1", "6.0"])
def test_observe_viewport_canvas_posts_current_revision(monkeypatch, version):
    module = load_service_client(version)
    fake_session = FakeSession()

    monkeypatch.setattr(module, "HAS_AIOHTTP", True)
    monkeypatch.setattr(
        module,
        "aiohttp",
        SimpleNamespace(
            ClientSession=lambda: fake_session,
            ClientTimeout=lambda total: {"total": total},
        ),
        raising=False,
    )

    client = module.AssistServiceClient(
        base_url="http://service.test",
        session_id="session with spaces",
    )

    response = asyncio.run(
        client.observe_viewport_canvas(prompt="Use live viewport", max_dim=640)
    )

    assert response["ok"] is True
    assert response["revision"] == 8
    assert fake_session.canvas_url == "http://service.test/api/v1/canvas/session%20with%20spaces"
    assert fake_session.posted == {
        "url": "http://service.test/api/v1/canvas/session%20with%20spaces/cosmos/observe_viewport",
        "json": {
            "prompt": "Use live viewport",
            "max_dim": 640,
            "parent_revision": 7,
        },
        "timeout": {"total": 120},
    }
