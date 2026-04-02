# Isaac Assist — Code Generator Specification Index

## Product Overview

Isaac Assist is a retrieval-first, scene-aware repair assistant implemented as an Isaac Sim extension with an optional local background service. It helps users diagnose, explain, and repair issues across scene construction, simulation setup, asset import, sensor configuration, extension conflicts, Python scripts, and Isaac Lab task/training workflows.

**Product type:** Isaac Sim extension + local background service + local knowledge index  
**Primary users:** Simulation engineers, robotics researchers, Isaac Lab users, ROS/Isaac ROS integrators  
**North-star metric:** Mean time to diagnose and repair a scene or environment issue  
**Delivery horizon:** 16-week MVP program

---

## Architecture Summary

The system is split into two runtime layers:

1. **Extension (in-process):** Thin UI layer running inside Isaac Sim. Owns the chat pane, stage hooks, Commands Tool integration, selection observer, patch preview, approval dialogs, and immediate scene inspection.
2. **Background Service (local process):** Owns indexing, retrieval, compatibility resolution, patch planning, long-running validation, and repro packaging. Communicates with the extension via local service APIs.

This split keeps the UI responsive and enables alternate front ends (VS Code, headless batch, CI companion).

---

## Module Specifications

Each file below is a self-contained code-generation spec for one module. Files are numbered by build order (matching the project phases).

| File | Module | Runtime | Phase |
|------|--------|---------|-------|
| [01_EXTENSION_SHELL.md](./01_EXTENSION_SHELL.md) | Extension skeleton + dockable UI pane | Extension | 1 |
| [02_ENVIRONMENT_FINGERPRINT.md](./02_ENVIRONMENT_FINGERPRINT.md) | Environment discovery + compatibility matrix | Background | 1 |
| [03_SNAPSHOT_MANAGER.md](./03_SNAPSHOT_MANAGER.md) | State snapshots, diff, and rollback | Both | 1 |
| [04_SOURCE_REGISTRY.md](./04_SOURCE_REGISTRY.md) | Source registry + retrieval layer | Background | 1–2 |
| [05_STAGE_ANALYZER.md](./05_STAGE_ANALYZER.md) | Scene analysis + validator packs | Extension | 2 |
| [06_PATCH_PLANNER.md](./06_PATCH_PLANNER.md) | Repair planning + execution engine | Background | 3 |
| [07_APPROVAL_ENGINE.md](./07_APPROVAL_ENGINE.md) | Approval dialogs + dry-run + apply | Extension | 3 |
| [08_KNOWLEDGE_BASE.md](./08_KNOWLEDGE_BASE.md) | Local knowledge base + experiential memory | Background | 4 |
| [09_TELEMETRY_EVAL.md](./09_TELEMETRY_EVAL.md) | Telemetry pipeline + evaluation framework | Background | 4 |
| [10_CHAT_UX.md](./10_CHAT_UX.md) | Conversational UX + escalation flows | Extension | 1–4 |

---

## Cross-Cutting Concerns

| Concern | Specification |
|---------|--------------|
| Data models / schemas | Defined inline in each module spec under `## Data Models` |
| API contracts (extension ↔ service) | Each module spec defines its service API surface under `## API Contract` |
| Security / governance | Covered in [07_APPROVAL_ENGINE.md](./07_APPROVAL_ENGINE.md) and cross-referenced from other modules |
| Version strategy | Covered in [02_ENVIRONMENT_FINGERPRINT.md](./02_ENVIRONMENT_FINGERPRINT.md) |

---

## Build Order Dependencies

```
Phase 1 (Weeks 3–5):
  01_EXTENSION_SHELL ──┐
  02_ENVIRONMENT_FINGERPRINT ──┤
  03_SNAPSHOT_MANAGER ──┤
  04_SOURCE_REGISTRY ──┘── Foundation complete

Phase 2 (Weeks 6–8):
  05_STAGE_ANALYZER ──── requires 01, 02, 04

Phase 3 (Weeks 9–11):
  06_PATCH_PLANNER ──── requires 03, 04, 05
  07_APPROVAL_ENGINE ── requires 01, 03, 06

Phase 4 (Weeks 12–14):
  08_KNOWLEDGE_BASE ──── requires 04, 06
  09_TELEMETRY_EVAL ──── requires all above

Continuous (Weeks 3–14):
  10_CHAT_UX ──── iterates alongside all phases
```

---

## Tech Stack Assumptions

- **Extension framework:** Omniverse Kit / `omni.ext` / `omni.ui`
- **USD access:** `pxr` (OpenUSD Python bindings)
- **Background service:** Python (FastAPI or similar), local-only binding
- **Knowledge index:** Local vector store (e.g., ChromaDB, LanceDB) or SQLite FTS5
- **LLM integration:** Abstracted behind a provider interface; Claude API as default
- **Serialization:** JSON for API contracts, USD for scene snapshots
- **Target platforms:** Linux (primary), Windows (secondary)
