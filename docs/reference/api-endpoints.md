# API Endpoints

The Isaac Assist FastAPI service runs on **port 8000** by default. All endpoints are prefixed with `/api/v1/` except `/health`.

---

## Health

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Service health check. Returns LLM mode and active model name. |

```bash
curl http://localhost:8000/health
```

```json
{"status": "ok", "service": "isaac-assist-backend", "llm_mode": "local", "model": "qwen3.5:35b"}
```

---

## Chat Orchestration

**Prefix:** `/api/v1/chat`

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/message` | Send a chat message. The orchestrator routes intent, calls tools, generates code, and returns the assistant response. |
| `POST` | `/reset` | Reset the chat session and clear conversation history. |
| `POST` | `/log_execution` | Log a code patch execution result (success/failure) for the learning loop. |
| `POST` | `/compact_knowledge` | Compact the knowledge base by deduplicating and merging entries. |
| `POST` | `/export_scene` | Export the current session as a reusable scene package (script + README + ROS2 config). |
| `GET` | `/export_scene/download` | Download the last exported scene package as a zip file. |
| `POST` | `/pipeline/plan` | Run the swarm patch planner (coder/critic/QA agents) on a complex request. |

### Send a message

```bash
curl -X POST http://localhost:8000/api/v1/chat/message \
  -H "Content-Type: application/json" \
  -d '{
    "message": "Create a red cube at position 0, 0, 0.5",
    "session_id": "default_session",
    "context": {
      "selected_prim": "/World/MyCube",
      "viewport_b64": null
    }
  }'
```

### Reset session

```bash
curl -X POST http://localhost:8000/api/v1/chat/reset \
  -H "Content-Type: application/json" \
  -d '{"session_id": "default_session"}'
```

### Log an execution result

```bash
curl -X POST http://localhost:8000/api/v1/chat/log_execution \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "default_session",
    "patch_code": "import omni.kit.commands\nomni.kit.commands.execute(\"CreateMeshPrimCommand\", ...)",
    "success": true,
    "error": null
  }'
```

---

## Settings

**Prefix:** `/api/v1/settings`

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/` | Get all current configuration values. |
| `POST` | `/` | Update one or more configuration values. |
| `POST` | `/pull_local` | Re-read the `.env.local` file and apply overrides. |
| `GET` | `/llm_mode` | Get the current LLM mode. |
| `PUT` | `/llm_mode` | Switch the active LLM provider at runtime. |

### Switch LLM mode

```bash
curl -X PUT http://localhost:8000/api/v1/settings/llm_mode \
  -H "Content-Type: application/json" \
  -d '{"mode": "anthropic"}'
```

### Get all settings

```bash
curl http://localhost:8000/api/v1/settings/
```

---

## Governance

**Prefix:** `/api/v1/governance`

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/evaluate` | Evaluate the risk level (low/medium/high) of a code patch. |
| `POST` | `/audit` | Record an approval/rejection decision in the audit log. |
| `GET` | `/audit_logs` | Retrieve the audit log history. |
| `POST` | `/redact` | Redact sensitive information from a code patch before logging. |

### Evaluate risk

```bash
curl -X POST http://localhost:8000/api/v1/governance/evaluate \
  -H "Content-Type: application/json" \
  -d '{"code": "omni.kit.commands.execute(\"DeletePrims\", paths=[\"/World\"])"}'
```

---

## Snapshots

**Prefix:** `/api/v1/snapshots`

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/` | Create a pre-execution state snapshot. Auto-prunes beyond 50 snapshots. |
| `GET` | `/` | List all saved snapshots. |
| `POST` | `/{snapshot_id}/rollback` | Rollback the scene to a specific snapshot. |

### Create snapshot

```bash
curl -X POST http://localhost:8000/api/v1/snapshots/ \
  -H "Content-Type: application/json" \
  -d '{"description": "Before adding physics to all meshes"}'
```

### Rollback

```bash
curl -X POST http://localhost:8000/api/v1/snapshots/abc123/rollback
```

---

## Retrieval (Knowledge & Specs)

**Prefix:** `/api/v1/retrieval`

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/specs` | List all indexed product specifications (sensors, cameras). |
| `GET` | `/specs/lookup` | Fuzzy-match a product name against the spec database. |
| `GET` | `/sources` | List all indexed knowledge sources. |
| `POST` | `/query` | Run a RAG query against the knowledge base. |
| `POST` | `/sources/{source_id}/index_mock` | Trigger mock indexing for a knowledge source. |

### Lookup a sensor spec

```bash
curl "http://localhost:8000/api/v1/retrieval/specs/lookup?product_name=RealSense+D435i"
```

### RAG query

```bash
curl -X POST http://localhost:8000/api/v1/retrieval/query \
  -H "Content-Type: application/json" \
  -d '{"query": "How to create an OmniPBR material in Isaac Sim 5.1"}'
```

---

## Analysis

**Prefix:** `/api/v1/analysis`

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/run` | Run a stage analysis on the current USD scene. |
| `GET` | `/packs` | List available analysis packs (rule sets). |

```bash
curl -X POST http://localhost:8000/api/v1/analysis/run \
  -H "Content-Type: application/json" \
  -d '{"pack": "physics_audit"}'
```

---

## Plans (Patch Planner)

**Prefix:** `/api/v1/plans`

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/generate` | Generate a multi-step patch plan from a natural language request. |
| `POST` | `/{plan_id}/apply` | Execute a previously generated plan. |

```bash
curl -X POST http://localhost:8000/api/v1/plans/generate \
  -H "Content-Type: application/json" \
  -d '{"request": "Set up a tabletop manipulation scene with a Franka robot and three colored cubes"}'
```

---

## Fine-tuning

**Prefix:** `/api/v1/finetune`

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/export` | Export collected chat-action pairs as a fine-tuning dataset. |
| `GET` | `/download` | Download the exported dataset file. |

```bash
curl -X POST http://localhost:8000/api/v1/finetune/export \
  -H "Content-Type: application/json" \
  -d '{"format": "jsonl", "min_quality": 0.7}'
```

---

## Environment Fingerprint

**Prefix:** `/api/v1/fingerprint`

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/collect` | Collect environment fingerprint (Isaac Sim version, GPU, extensions). |
| `POST` | `/collect` | Collect and store fingerprint. |
| `GET` | `/resolve` | Resolve version-specific API mappings based on the fingerprint. |

```bash
curl http://localhost:8000/api/v1/fingerprint/collect
```
