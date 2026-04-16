"""
L1 tests for FastAPI routes — uses httpx AsyncClient against the app.
Mocks the orchestrator and other heavy dependencies.
"""
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

pytestmark = [pytest.mark.l1, pytest.mark.asyncio]


class TestHealthEndpoint:

    async def test_health_returns_ok(self, client):
        resp = await client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "llm_mode" in data
        assert "model" in data


class TestSettingsEndpoints:

    async def test_get_settings(self, client):
        resp = await client.get("/api/v1/settings/")
        assert resp.status_code == 200
        data = resp.json()
        assert "settings" in data
        settings = data["settings"]
        assert "LLM_MODE" in settings
        assert "LOCAL_MODEL_NAME" in settings

    async def test_get_llm_mode(self, client):
        resp = await client.get("/api/v1/settings/llm_mode")
        assert resp.status_code == 200
        data = resp.json()
        assert "llm_mode" in data

    async def test_switch_llm_mode_invalid(self, client):
        resp = await client.put(
            "/api/v1/settings/llm_mode",
            json={"mode": "invalid_mode"},
        )
        assert resp.status_code == 400

    async def test_switch_llm_mode_valid(self, client, monkeypatch):
        # Patch the settings manager to avoid writing real .env
        from service.isaac_assist_service.settings.manager import SettingsManager
        monkeypatch.setattr(
            SettingsManager, "update_settings",
            MagicMock(return_value=True),
        )
        # Patch orchestrator.refresh_provider
        import service.isaac_assist_service.chat.routes as chat_routes
        monkeypatch.setattr(
            chat_routes.orchestrator, "refresh_provider",
            MagicMock(),
        )
        resp = await client.put(
            "/api/v1/settings/llm_mode",
            json={"mode": "local"},
        )
        assert resp.status_code == 200
        assert resp.json()["llm_mode"] == "local"


class TestGovernanceEndpoints:

    async def test_evaluate_actions(self, client):
        payload = {
            "actions": [
                {
                    "action_id": "a1",
                    "order": 1,
                    "write_surface": "usd",
                    "target_path": "/World/Cube",
                    "action_type": "set_property",
                    "confidence": 0.9,
                    "reasoning": "test",
                }
            ]
        }
        resp = await client.post("/api/v1/governance/evaluate", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "success"
        assert "evaluation" in data
        assert data["evaluation"]["overall_risk"] == "low"

    async def test_redact_text(self, client):
        resp = await client.post(
            "/api/v1/governance/redact",
            json={"text": "My key is sk-abcdefghijklmnopqrstuvwxyz123456"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "redacted_text" in data


class TestSnapshotEndpoints:

    async def test_list_snapshots(self, client):
        resp = await client.get("/api/v1/snapshots")
        assert resp.status_code == 200
        data = resp.json()
        assert "snapshots" in data

    async def test_create_snapshot(self, client, monkeypatch):
        from service.isaac_assist_service.snapshots.manager import SnapshotManager
        from service.isaac_assist_service.snapshots.models import Snapshot
        from datetime import datetime

        fake_snap = Snapshot(
            snapshot_id="test123",
            created_at=datetime.utcnow(),
            trigger="pre_patch",
            action_context="test",
            storage_path="/tmp/fake",
            size_bytes=0,
        )
        monkeypatch.setattr(
            SnapshotManager, "create_snapshot",
            MagicMock(return_value=fake_snap),
        )
        # Also mock collect_fingerprint
        monkeypatch.setattr(
            "service.isaac_assist_service.snapshots.routes.collect_fingerprint",
            MagicMock(return_value={"os": "linux"}),
        )
        resp = await client.post(
            "/api/v1/snapshots",
            json={
                "trigger": "pre_patch",
                "action_context": "test action",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["snapshot_id"] == "test123"


class TestChatEndpoints:

    async def test_send_message(self, client, monkeypatch):
        """Test /api/v1/chat/message with a mocked orchestrator."""
        import service.isaac_assist_service.chat.routes as chat_routes

        mock_result = {
            "intent": "general_query",
            "reply": "Hello from mock!",
            "tool_calls": [],
            "code_patches": [],
        }
        monkeypatch.setattr(
            chat_routes.orchestrator, "handle_message",
            AsyncMock(return_value=mock_result),
        )

        resp = await client.post(
            "/api/v1/chat/message",
            json={
                "session_id": "test_session",
                "message": "What is a USD prim?",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["intent"] == "general_query"
        assert len(data["response_messages"]) == 1
        assert data["response_messages"][0]["content"] == "Hello from mock!"

    async def test_send_message_with_code_patches(self, client, monkeypatch):
        import service.isaac_assist_service.chat.routes as chat_routes

        mock_result = {
            "intent": "patch_request",
            "reply": "I created a cube.",
            "tool_calls": [{"tool": "create_prim", "arguments": {}, "result": {}}],
            "code_patches": [{"code": "print('hello')", "description": "test"}],
        }
        monkeypatch.setattr(
            chat_routes.orchestrator, "handle_message",
            AsyncMock(return_value=mock_result),
        )

        resp = await client.post(
            "/api/v1/chat/message",
            json={"session_id": "s1", "message": "Create a cube"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["actions_to_approve"] is not None
        assert len(data["actions_to_approve"]) == 1
        assert data["actions_to_approve"][0]["type"] == "code_patch"

    async def test_reset_session(self, client, monkeypatch):
        import service.isaac_assist_service.chat.routes as chat_routes

        monkeypatch.setattr(
            chat_routes.orchestrator, "reset_session",
            MagicMock(),
        )
        # Mock MemoryManager — it's imported inside the function via ..memory
        monkeypatch.setattr(
            "service.isaac_assist_service.memory.MemoryManager",
            MagicMock,
        )

        resp = await client.post(
            "/api/v1/chat/reset",
            json={"session_id": "test_session"},
        )
        assert resp.status_code == 200
