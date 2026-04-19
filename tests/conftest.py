"""
Shared fixtures for the Isaac Assist test suite.
All fixtures that touch the filesystem use tmp_path to avoid polluting the repo.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Ensure the service package is importable
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

# Prevent config.__init__ from modifying the real environment:
# set harmless defaults BEFORE any import touches os.environ.
os.environ.setdefault("LLM_MODE", "local")
os.environ.setdefault("OPENAI_API_BASE", "https://api.openai.com/v1")
os.environ.setdefault("CONTRIBUTE_DATA", "false")
os.environ.setdefault("AUTO_APPROVE", "false")
os.environ.setdefault("MAX_TOOL_ROUNDS", "10")
os.environ.setdefault("ASSETS_ROOT_PATH", "")


# ---------------------------------------------------------------------------
# Config fixture
# ---------------------------------------------------------------------------

@pytest.fixture()
def fresh_config(monkeypatch):
    """Return a fresh Config instance with controlled env vars."""
    monkeypatch.setenv("LLM_MODE", "local")
    monkeypatch.setenv("LOCAL_MODEL_NAME", "test-model:7b")
    monkeypatch.setenv("CLOUD_MODEL_NAME", "test-cloud-model")
    monkeypatch.setenv("CONTRIBUTE_DATA", "false")
    monkeypatch.setenv("AUTO_APPROVE", "false")
    monkeypatch.setenv("MAX_TOOL_ROUNDS", "10")
    monkeypatch.setenv("ASSETS_ROOT_PATH", "")

    from service.isaac_assist_service.config import Config
    return Config()


# ---------------------------------------------------------------------------
# Knowledge Base fixture (tmp_path)
# ---------------------------------------------------------------------------

@pytest.fixture()
def knowledge_base(tmp_path):
    """KnowledgeBase instance backed by a temp directory."""
    from service.isaac_assist_service.knowledge.knowledge_base import KnowledgeBase
    return KnowledgeBase(storage_dir=str(tmp_path / "knowledge"))


# ---------------------------------------------------------------------------
# Snapshot Manager fixture (tmp_path)
# ---------------------------------------------------------------------------

@pytest.fixture()
def snapshot_manager(tmp_path, monkeypatch):
    """SnapshotManager instance with SNAPSHOT_ROOT redirected to tmp_path."""
    import service.isaac_assist_service.snapshots.manager as snap_mod
    monkeypatch.setattr(snap_mod, "SNAPSHOT_ROOT", str(tmp_path / "snapshots"))
    os.makedirs(tmp_path / "snapshots", exist_ok=True)
    return snap_mod.SnapshotManager()


# ---------------------------------------------------------------------------
# Policy Engine fixture
# ---------------------------------------------------------------------------

@pytest.fixture()
def policy_engine():
    from service.isaac_assist_service.governance.policy_engine import PolicyEngine
    from service.isaac_assist_service.governance.models import GovernanceConfig
    return PolicyEngine(GovernanceConfig())


# ---------------------------------------------------------------------------
# Mock LLM provider
# ---------------------------------------------------------------------------

class _FakeLLMResponse:
    """Mimics the response object returned by LLM providers."""

    def __init__(self, text: str = "", tool_calls: Optional[List[Dict]] = None):
        self.text = text
        self.tool_calls = tool_calls
        self.actions = tool_calls or []


class MockLLMProvider:
    """Controllable mock LLM provider.

    Set `.responses` to a list of _FakeLLMResponse objects; each call to
    complete() pops the first one.
    """

    def __init__(self):
        self.responses: List[_FakeLLMResponse] = [
            _FakeLLMResponse(text="Hello, I am the mock assistant.")
        ]
        self._call_log: List[Dict] = []
        self._system_override: Optional[str] = None

    async def complete(self, messages, options=None):
        self._call_log.append({"messages": messages, "options": options})
        if self.responses:
            return self.responses.pop(0)
        return _FakeLLMResponse(text="(no more mock responses)")


@pytest.fixture()
def mock_llm_provider():
    return MockLLMProvider()


@pytest.fixture()
def fake_llm_response():
    """Factory for building controllable LLM responses."""
    return _FakeLLMResponse


# ---------------------------------------------------------------------------
# Mock Kit RPC -- patches kit_tools so no running Kit server is required.
# ---------------------------------------------------------------------------

@pytest.fixture()
def mock_kit_rpc(monkeypatch):
    """Patch kit_tools.queue_exec_patch + _get + _post so handlers run offline.

    Default behavior: queue_exec_patch returns a stub patch_id; tests that
    need to inspect the generated script override queue_exec_patch with their
    own monkeypatch.
    """
    defaults = {
        "/health": {"ok": True},
        "/exec": {"queued": True, "patch_id": "test_patch_001"},
    }

    async def fake_get(path, params=None):
        return defaults.get(path, {"error": f"Unknown path {path}"})

    async def fake_post(path, body):
        return defaults.get(path, {"error": f"Unknown path {path}"})

    async def fake_queue(code, desc):
        return {"queued": True, "patch_id": "test_patch_001"}

    import service.isaac_assist_service.chat.tools.kit_tools as kt
    monkeypatch.setattr(kt, "_get", fake_get)
    monkeypatch.setattr(kt, "_post", fake_post)
    monkeypatch.setattr(kt, "queue_exec_patch", fake_queue, raising=False)

    return defaults


# ---------------------------------------------------------------------------
# FastAPI TestClient fixture
# ---------------------------------------------------------------------------

@pytest.fixture()
def app():
    """Import the FastAPI app object (does NOT start the server)."""
    from service.isaac_assist_service.main import app as _app
    return _app


@pytest.fixture()
async def client(app):
    """Async httpx client pointing at the FastAPI app."""
    from httpx import ASGITransport, AsyncClient
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac
