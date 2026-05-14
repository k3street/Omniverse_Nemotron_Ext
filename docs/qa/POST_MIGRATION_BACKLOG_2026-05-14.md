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

## Wave 3 — deferred / low-priority

### DEPR-2: Extra utcnow() sites (out-of-scope from Wave 2)
- **Source**: DEPR-1 agent surfaced during sweep
- **Sites**: 8 in `workflow.py`, `_shared.py`, 2 more in `scene_blueprints.py`
- **STATUS**: pending

### CONC-2b: multimodal/routes.py RMW on _WORKFLOWS
- **Source**: CONC-1 agent surfaced; agent stayed in scope
- **Fix**: `_forward_workflow_approve`/`_forward_workflow_reject` should `_wf_lock_for(wf)`-wrap their RMW
- **STATUS**: pending (lazy-setdefault makes it currently safe but inconsistent)

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
- Post-Wave-2: **6540 pass, 0 fail** (+11 from CONC-1's `tests/test_concurrency_locks.py`)
- Branch tip after Wave 2: `836b277`
