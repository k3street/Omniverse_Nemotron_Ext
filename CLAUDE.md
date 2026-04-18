# Omniverse Nemotron Extension (Isaac Assist)

FastAPI service + NVIDIA Isaac Sim extension providing an AI-powered assistant for building, diagnosing, and controlling robotics simulations via natural language.

## Running Tests

```bash
# L0 only (unit tests, no external deps) -- default
pytest

# L0 + L1 (adds FastAPI route tests with mocked providers)
pytest -m "l0 or l1"

# All except Kit integration
pytest -m "not l3"

# With coverage
pytest --cov=service --cov-report=html

# Specific test file
pytest tests/test_patch_validator.py -v
```

Install test dependencies: `pip install -r requirements-test.txt`

## Architecture

- **service/isaac_assist_service/main.py** -- FastAPI app, all route registrations
- **chat/orchestrator.py** -- Multi-turn chat session manager with tool-calling loop
- **chat/intent_router.py** -- Classifies user messages into 8 intents (general_query, patch_request, etc.)
- **chat/tools/tool_schemas.py** -- 50+ tool definitions (OpenAI function-calling format)
- **chat/tools/tool_executor.py** -- Dispatch: CODE_GEN_HANDLERS (generate Python code strings) and DATA_HANDLERS (return data dicts)
- **chat/tools/patch_validator.py** -- Pre-flight validation of generated code (OmniGraph, PhysX, USD rules)
- **chat/tools/kit_tools.py** -- Async HTTP calls to Kit RPC server (port 8001)
- **governance/policy_engine.py** -- Risk classification for code patches (low/medium/high)
- **governance/models.py** -- GovernanceConfig, AuditEntry, ApprovalDecision
- **knowledge/knowledge_base.py** -- JSONL-backed experiential memory with dedup and compaction
- **snapshots/manager.py** -- Pre-execution state snapshots (max 50, auto-pruned)
- **settings/manager.py** -- Read/write .env configuration at runtime
- **mcp_server.py** -- MCP protocol server (SSE + stdio) exposing tools to external agents
- **config.py** -- Singleton Config class reading from .env files

## Test Conventions

- **Markers**: `@pytest.mark.l0` (unit), `l1` (service), `l2` (MCP), `l3` (integration), `slow`
- **Default run**: Only L0 tests (no external deps)
- **Fixtures**: All in `tests/conftest.py` -- `fresh_config`, `knowledge_base`, `snapshot_manager`, `policy_engine`, `mock_kit_rpc`, `mock_llm_provider`, `client`
- **Adding tests**: Create `tests/test_<module>.py`, mark with `@pytest.mark.l0` for unit tests
- **Adding fixtures**: Add to `tests/conftest.py`
- **Code gen tests**: Add test vectors to `_TEST_VECTORS` in `test_code_generators.py`
- **Patch validator tests**: One test class per rule in `test_patch_validator.py`
- All filesystem-dependent tests use `tmp_path` fixtures to avoid side effects
