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

## Wave 2 — landed (2026-05-14 ~10:30–12:30 CEST)

### CONC-1: Module-state concurrency locks — done — `836b277`
- 4 lock strategies: global `_COMPLIANCE_LOCK`, per-workflow `_lock` via `setdefault`, double-checked `_TURN_RECORDER_LOCK`, build_id+building flag for `_STAGE_INDEX`
- 11 new tests in `tests/test_concurrency_locks.py` (spec asked ≥8)
- Known hole: `multimodal/routes.py` `_forward_workflow_approve`/`_forward_workflow_reject` do own RMW on `_WORKFLOWS[wf_id]`. Lazy-setdefault makes it safe but file wasn't touched. See Wave 3 CONC-2b.

### DEPR-1: datetime.utcnow() + asyncio.get_event_loop() — done — 14 commits `d5ea95d..27adfbe`
- All 30 spec sites fixed. `governance/models.py` now passes `-W error::DeprecationWarning`.
- Pydantic `datetime` field defaults switched to `Field(default_factory=lambda: datetime.now(timezone.utc))`.
- 3 `get_event_loop()` calls in `mcp_server.py` consolidated to single `get_running_loop()` at top of `run_stdio()`.
- 8 extra `utcnow()` sites found out-of-scope in `workflow.py`, `_shared.py`, `scene_blueprints.py` — see Wave 3 DEPR-2.

### TYPE-1: Type hint backfill — done — `7621d3a`, `52a98dd`
- 3 `robot.py` handlers (`_handle_setup_isaac_ros_cumotion_moveit`, `_handle_setup_ros2_control_compat`, `_handle_list_available_controllers`) → `(args: Dict[str, Any]) -> Dict[str, Any]`
- 9 `pick_place.py` `_gen_*` generators fully annotated → `-> str`

### SCHEMA-1: Schema/handler drift — done — `f126903`, `e978bfb`, `fafeb63`, `5a4a141`, `2dac6df`
- S1: `set_physics_params` — removed dead `solver_iterations` from schema
- S2: `export_policy` — added `job_id`, `output_dir` to schema
- S3: `configure_camera` — marked deprecated, `set_camera_params` is canonical
- S4: `get_camera_params` — description leak repaired
- Followed by `_models.py` regen

### DOCS-1: Docstring backfill Wave 1 — done — `c299c99`, `f3732b7`, `bfeb9ad` (+ 6 piggybacked on `54aa246`)
- 10 top-priority MISSING docstrings → Args/Returns
- GOOD count: 35 → 45 of 419
- Note: 6 `robot.py` docstrings landed via DEPR-1's commit `54aa246` due to file overlap — DOCS-1 commits then no-op'd for those 6

## Wave 3a — landed (2026-05-14 ~13:00–14:30 CEST)

### DEPR-2: 8 extra utcnow() sites — done — `b6e4b3a`, `c4bd52b`, `67ed62f`
- _shared.py (1), workflow.py (4), scene_blueprints.py (3). All passed `-W error::DeprecationWarning`.

### CONC-2b: multimodal/routes.py workflow RMW — done — `a091ae9`
- `_forward_workflow_approve`/`reject` wrapped with `_wf_lock_for(wf)`. T11 test in `test_concurrency_locks.py` proves serialization via SleepingLock+Barrier.

### CONC-2: lazy-load lru_cache wrap — done — `531d9c0`
- 6 funcs wrapped: `_load_deformable_presets`, `_load_physics_materials`, `_load_catalog`, `_load_specs`, `_load_sensor_specs`, `_build_asset_index`.
- Excluded `_load` in `deprecations_index.py` (side-effect loader, not return-value cache).
- Test fixes: monkeypatch direct + `cache_clear()` in fixtures.

### CONC-3: dead singleton delete — verified-no-op
- All 5 candidates (WORKFLOWS/EUREKA/TRAINING/DR/BRIDGES) have writes in `tests/test_handlers_shared_state.py`. Per "if any write exists, leave it" rule: zero deletions.
- Surfaced: `EUREKA.runs` has 1 prod read in `training.py:1308` but 0 prod writes — dead-read returns "not_found" forever. See Wave 3d EUREKA-1.

### KB-1: Phase 94b time injection — done — `4ab2e9b`
- `KBFreshnessAuditor._scan/audit/list_stale/list_all` accept `now: datetime | None = None`.
- 2 boundary tests pin threshold semantics: `>` strict (90d = fresh, 91d = stale).

### MAGIC-1: magic number sweep — done — `7f1ae78`, `858ff83`, `aa5dda0` (+ scene_blueprints in `67ed62f`)
- 23 constants extracted: sensors.py (9), scene_authoring.py (6), scene_blueprints.py (6), pick_place.py (8).
- Examples: `_FRANKA_PALM_TO_FINGERTIP_M`, `_GRAVITY_MS2`, `_RMPFLOW_MAX_SUBSTEP_S`, `_CAM_APERTURE_MM`.

## Wave 3b — landed (2026-05-14 ~14:30–16:30 CEST)

### DOCS-2: 162 docstrings across 12 commits — done
- DOCS-2a (`be5b9d4`, `46b8184`): 13 in pick_place + diagnostics. Bonus: orphaned dead-statement docstring in `_gen_pick_place_curobo` merged.
- DOCS-2b (`5a79974`): 44 in training.py — file now 51/51 GOOD.
- DOCS-2c (`2dfbd86`): 78 in scene_authoring.py.
- DOCS-2d (`5605ce1` → `e5e3a6b`, 8 commits): 27 across sensors/vision/sdg/ros2/workflow/teleop/robot residual/scene_blueprints. compliance.py needed no work.
- Estimated coverage after Wave 3b: ~207/419 GOOD (~49%, up from 45/419 = 11% after Wave 2). Remaining backlog lives in non-handler modules (multimodal/, planner/, finetune/, etc.) — defer to Wave 4 if needed.

## Wave 3c — landed — `5caca5e`
- `yard_map.pgm`/`yard_map.yaml` deleted (empty 0x0 stubs from default `export_nav2_map` output) + added to .gitignore.
- 2 research docs (composition-research-report, specs-2-3-4-review) committed.

## Wave 3d — landed (Phase 64) — `10b0a55`
- EUREKA-1 chose wire-up over delete since spec Phase 64 explicitly schedules writers.
- `_handle_generate_reward` seeds `EUREKA.runs[run_id]` with status="initialized", iteration counters.
- `_handle_iterate_reward` accepts optional `run_id`, increments + tracks best fitness + auto-completes.
- `eureka_state_persisted.py` PHASE_STATUS: scaffold → landed.
- 8 new tests; baseline 6543 → 6550.

## Wave 4 — landed — `0e2c50b`
- DOCS-3a (multimodal/): 12 batched commits + 1 recovery commit after 600s stall (in-progress edits quality-verified before commit).
- DOCS-3b (planner/+finetune/): 41 docstrings, 10 commits `f0079a2..c7feacf`.
- DOCS-3c (analysis/knowledge/chat/governance/snapshots/fingerprint/etc): 6 commits `cc1bebf..7f343dd`.
- Zero Section 19 honesty holes detected in planner/finetune sweep.

---

## Metrics

- Pre-Wave-1 baseline: 6529 pass, 0 fail
- Post-Wave-1: 6529 pass, 0 fail (silent-success was additive, no behavior change)
- Post-Wave-2: 6540 pass, 0 fail (+11 from CONC-1's `tests/test_concurrency_locks.py`)
- Post-Wave-3a: 6543 pass, 0 fail (+1 CONC-2b T11, +2 KB-1 boundary; CONC-2 also recovered ~3 previously-broken tests via fixture cleanup)
- Post-Wave-3b: 6543 pass, 0 fail (docstring-only, no test changes)
- Post-Wave-3d (Phase 64): 6550 pass, 0 fail (+7 Eureka writer tests)
- Post-Wave-4 (DOCS-3): **6550 pass, 0 fail** (docstring-only)
- Branch tip after Wave 4: `0e2c50b`
