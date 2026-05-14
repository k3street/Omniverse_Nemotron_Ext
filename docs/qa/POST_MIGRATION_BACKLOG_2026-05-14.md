# Post-Migration QA Backlog — 2026-05-14

Tech-debt surface uncovered after the Phase 1-145 monolith→themed-module
refactor (foundation-night-1 branch) landed. The refactor MOVED 344
handlers from `tool_executor.py` into 17 themed modules but did NOT audit
individual handlers for hygiene. This backlog catches up.

**Status legend**: `pending` / `in-progress` / `done — <hash>` / `deferred`

---

## Wave 1 — landed (2026-05-14 04:00–06:00 CEST)

- ✅ **Silent-success Tier 1+2** (43 handlers across 10 files) — commits 46ed81b..4740ebf. Section 19 honesty gate now enforced. Baseline 6529 preserved.
- ✅ **Phase 92 time bug** — fda8b56. `_ref_now().timestamp()` anchoring.
- ✅ **Orchestrator l1 mocks** — 0ade3c3. IntentClassification dict + honesty-rewrite stub.

## Wave 2 — pending (this session)

### CONC-1: Module-state concurrency locks (Opus)
- **Source**: opus concurrency audit 2026-05-14
- **Findings**: 2 DANGEROUS, 3 AT-RISK module-level state objects with no locks
- **Targets**:
  - `_INSTALLED_COMPLIANCE` (compliance.py:58) — add `threading.Lock`; wrap setup/set_params/release writes
  - `_WORKFLOWS` (_state.py:202) — per-workflow lock OR module-level lock; wrap deep mutations
  - `_TURN_RECORDER_SINGLETON` — double-checked locking OR eager init
  - `_STAGE_INDEX` / `_STAGE_INDEX_META` — build_id counter for async-build coordination
- **STATUS**: pending

### DEPR-1: datetime.utcnow() + asyncio.get_event_loop() (Sonnet mechanical)
- **Source**: deprecation audit 2026-05-14
- **Targets**: 24 `datetime.utcnow()` + 6 `asyncio.get_event_loop()` call sites
- **Files**: governance/models.py, snapshots/manager.py, chat/orchestrator.py, chat/routes.py, planner/swarm_generator.py, planner/generator.py, fingerprint/collector.py, finetune/turn_recorder.py (4×), chat/tools/handlers/scene_blueprints.py, chat/tools/handlers/robot.py, mcp_server.py (3×), exts/isaac_5.1/.../kit_rpc.py, tests/test_routes.py, tests/test_ft_sensor_extension.py
- **Severity**: governance/models.py FAILS under `-W error::DeprecationWarning`
- **STATUS**: pending

### TYPE-1: Type hint backfill — 12 funcs (Sonnet small)
- **Source**: type-hint audit 2026-05-14
- **Targets**: 12 functions concentrated in `pick_place.py` (9 internal generators) + `robot.py` (2 dispatch entries + 1 partial)
  - robot.py:4621 `_handle_setup_isaac_ros_cumotion_moveit`
  - robot.py:5319 `_handle_setup_ros2_control_compat`
  - robot.py:5470 `_handle_list_available_controllers`
  - pick_place.py: 9 `_gen_pick_place_*` variants
- **STATUS**: pending

### SCHEMA-1: Schema/handler drift (Sonnet small)
- **Source**: schema/handler drift audit 2026-05-14
- **Targets**:
  - `set_physics_params` — schema declares `solver_iterations`, handler ignores. Either implement or drop from schema.
  - `export_policy` — handler reads `job_id` + `output_dir`, schema doesn't declare. Add to schema.
  - `configure_camera` vs `set_camera_params` — functional duplicates. Dedupe.
  - `get_camera_params` — description text leaked from `camera_path` field. Replace.
- **STATUS**: pending

### DOCS-1: Docstring backfill (Sonnet swarm, large)
- **Source**: docstring audit 2026-05-14
- **Scope**: 96 MISSING + 279 THIN of 419 functions = 89% sub-standard
- **Priority order**:
  1. `robot.py` top 10 worst (193 lines down to 75 lines, all MISSING) — `_gen_robot_wizard`, `_gen_import_robot`, etc.
  2. `pick_place.py` — all 11 are THIN, 4 hold 400-900 lines of logic
  3. `training.py` — 41/44 THIN, no GOOD
  4. `scene_authoring.py` — 29 MISSING + 49 THIN of 80
  5. `diagnostics.py:_handle_simulate_traversal_check` — PARTIAL with 378 lines; safety-critical
- **STATUS**: pending

## Wave 3 — deferred / low-priority

### MAGIC-1: Magic number sweep
- **Source**: code-quality audit 2026-05-13
- **Example**: `barcode_reader_sensor` position default `[0.4, 0.4, 0.835]` unnamed
- **STATUS**: deferred

### DOCS-2: Remaining THIN/MISSING (~300 funcs after Wave 2 top-30)
- **STATUS**: deferred until Wave 2 completes; will redo audit then

### CONC-2: Lazy-load file readers (`_deformable_presets` etc.)
- **Source**: concurrency audit, LOW-RISK bucket
- **Fix**: wrap in `functools.lru_cache(maxsize=1)`
- **STATUS**: deferred (idempotent so currently safe)

### CONC-3: Delete unwritten scaffolding singletons (WORKFLOWS/EUREKA/TRAINING/DR/BRIDGES in _state.py)
- **Source**: concurrency audit found zero writes anywhere
- **Risk**: pre-bakes future bugs by sitting unused with no lock pattern
- **STATUS**: deferred (separate refactor decision)

### KB-1: Phase 94b time injection
- **Source**: time-brittleness audit MEDIUM-LOW
- **Risk**: theoretical — tests use 5/10/30/60/100/120/400 days vs 30/90 thresholds, all wide margins. Boundary-day case (89 vs 90) could flip on slow CI.
- **Fix**: `_scan(now: datetime | None = None)` injection
- **STATUS**: deferred

### YARD-MAP cleanup
- **Source**: untracked files in working tree
- **Files**: `yard_map.pgm`, `yard_map.yaml`, 2 research docs
- **Action**: triage — commit / gitignore / delete
- **STATUS**: pending (user decision)

---

## Metrics

- Pre-Wave-1 baseline: 6529 pass, 0 fail
- Post-Wave-1: 6529 pass, 0 fail (silent-success was additive, no behavior change)
- Target Wave-2 completion: 6580+ pass (depends on test additions)
