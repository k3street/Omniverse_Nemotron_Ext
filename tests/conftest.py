"""
Shared fixtures for the Isaac Assist test suite.
Minimal scaffolding for the tier-12 asset-management tools — only fixtures
needed by the test files in this branch.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

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
# Mock Kit RPC — patches kit_tools so no running Kit server is required.
# ---------------------------------------------------------------------------

@pytest.fixture()
def mock_kit_rpc(monkeypatch):
    """Patch kit_tools.queue_exec_patch + _get + _post so handlers run offline.

    Default behavior: queue_exec_patch returns a stub patch_id; tests that
    need to inspect the generated script override queue_exec_patch with their
    own monkeypatch (see test_tier12_data_handlers.py).
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
    monkeypatch.setattr(kt, "_get", fake_get, raising=False)
    monkeypatch.setattr(kt, "_post", fake_post, raising=False)
    monkeypatch.setattr(kt, "queue_exec_patch", fake_queue, raising=False)

    return defaults
