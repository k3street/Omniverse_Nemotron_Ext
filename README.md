# Isaac Assist - Unified Project Tracker

Welcome to the central repository for the **Isaac Assist** Omniverse Extension and Background Service.

> **Master Spec Reference:** See `Docs/00_INDEX.md` for the entire ecosystem spec and data models.

## Phase 1: Foundation (Weeks 3–5)
- [x] **10_CHAT_UX (Partial)**: Scaffolded the multi-turn conversational UI within Isaac Sim.
- [x] **01_EXTENSION_SHELL (Partial)**: Initialized 5.1 and 6.0 compatible Omniverse UI hooks and dockable window.
- [x] **Background FastAPI Service**: Spin up the local API server bridging the Extension UI to the LLM brains.
- [x] **02_ENVIRONMENT_FINGERPRINT**: Telemetry collection for hardware, Omniverse version, and active extensions.
- [x] **03_SNAPSHOT_MANAGER**: USD stage state serialization and rollback system.
- [x] **04_SOURCE_REGISTRY**: Omniverse documentation scraping and localized vector retrieval.

## Phase 2: Analysis (Weeks 6–8)
- [x] **05_STAGE_ANALYZER**: Scene constraint checks and validator packs.

## Phase 3: Planning & Safety (Weeks 9–11)
- [x] **06_PATCH_PLANNER**: Repair execution engine.
- [ ] **07_APPROVAL_ENGINE**: Dry-run UI dialogs for user governance over USD edits.

## Phase 4: Long-term Memory (Weeks 12–14)
- [ ] **08_KNOWLEDGE_BASE**: Local experiential memory persisting fixes over time.
- [ ] **09_TELEMETRY_EVAL**: Evaluation and offline RLHF telemetry piping.
