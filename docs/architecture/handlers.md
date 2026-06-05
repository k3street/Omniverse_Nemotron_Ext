# Handler architecture — read this first

> Phase 18 of `specs/IA_FULL_SPEC_2026-05-10.md`. This document is the
> entry point for any engineer about to touch a tool handler. Read it
> before opening `tool_executor.py` or any module under
> `service/isaac_assist_service/chat/tools/handlers/`.

Status (2026-05-13 post-Phase-9-followup): **describes the live shape**.
Phases 1, 2, 2b, 3, 5, 6, 7, 8b, 8c, 9, 10 (partial), 11b, 11c, 12,
17, 17b, 18b, 18c, 49b, 90, and 0b have landed; Phase 8 (full shared-
utility migration), Phase 11 (validator pipeline), Phase 13 (recovered-
state archive), Phase 14 (dispatch shim), Phase 15 (workflow stateful),
Phase 16 (resolve stateful) remain partial or unstarted. Where the
current code lags the target shape, the section is marked **TODO** and
refers to the phase that closes the gap.

## 1. Orientation

A "handler" is the Python function bound to a tool name in the
416-entry `ISAAC_SIM_TOOLS` schema list. When the chat surface or MCP
client calls `create_prim`, `setup_pick_place_controller`, or
`start_workflow`, the dispatcher looks up the name and invokes the
matching handler. Post-Phase-9, handlers live in **17 themed modules**
under `service/isaac_assist_service/chat/tools/handlers/` and the
dispatcher walks a registry populated by each theme's
`register(data, codegen)` function.

`tool_executor.py` retains only the dispatch core
(`execute_tool_call`), result-cap enforcement (`_apply_result_cap`),
and the "recovered-state" module-level constants/classes that
`handlers/*.py` still reference via lazy import. The recovered-state
block is Phase 13's deletion target; Phase 8 migrates the symbols out
first.

## 2. Request flow

```
tool_schemas.py (ISAAC_SIM_TOOLS list, 416 entries)
     |
     | name lookup
     v
tool_executor.py:execute_tool_call(name, args)
     |
     | dispatch
     v
handlers/_dispatch.py:register_handlers(data, codegen)
     |  (called once at module-import time of tool_executor.py)
     v
handlers/<theme>.py:register(data, codegen) — 17 themes
     |  + external registrators: register_multimodal_handlers,
     |    register_diagnose_handlers, register_bridge_handlers,
     |    _register_ros2_live (handles ros-mcp ImportError)
     v
DATA_HANDLERS[tool_name](args)  — for data-returning tools
CODE_GEN_HANDLERS[tool_name](args)  — for patch-emitting tools
     |
     | if patch-emitting
     v
patch_validator.validate_patch(code) -> blocking issues?
     |  no: proceed
     v
kit_tools.queue_exec_patch(code) -> Kit RPC at :8001
```

Phase 9 (2026-05-13) flipped the dispatch from inline
`DATA_HANDLERS["X"] = _handle_X` assignments to a single
`register_handlers(DATA_HANDLERS, CODE_GEN_HANDLERS)` call at module
import. Inline assignments are now forbidden by
`scripts/lint/no_handler_in_dispatch.py` (pre-commit).

## 3. Themes

The 17 themed modules under `handlers/` (declared in `_dispatch.py:_THEME_MODULES`):

| Module             | Target scope                                                            | Actual LOC | Data | Codegen |
| ------------------ | ----------------------------------------------------------------------- | ----------:| ----:| -------:|
| `scene_authoring`  | USD prim CRUD, attrs, references, layers, materials, snapshots.         |       4504 |   38 |      41 |
| `physics`          | Physics scene config, articulations, joints, drives, collision.         |       2151 |   19 |      19 |
| `robot`            | Robot import/anchor, IK, gripper, motion policy, kinematics, setups.    |       5222 |   28 |      32 |
| `pick_place`       | `setup_pick_place_controller`, ros2 bridge, vision pipelines.           |       5784 |    0 |      11 |
| `sensors`          | Cameras, lidars, contact sensors, proximity, NIR, barcode.              |        990 |    9 |       6 |
| `sdg`              | Replicator pipelines, DR ranges, presets, COCO/YOLO writers.            |        865 |    2 |      10 |
| `training`         | IsaacLab env, training launch, RL/GR00T, Eureka, checkpoints.           |       2387 |   33 |      10 |
| `ros2`             | `ros2_connect`, topics, services, OmniGraph bridge, industrial bridges. |        870 |    3 |       6 |
| `teleop`           | Start/stop sessions, record/validate demos, hardware mapping.           |        838 |    1 |       7 |
| `scene_blueprints` | Catalog search, generate/validate/build blueprints, templates.          |       1211 |   12 |       4 |
| `diagnostics`      | `verify_pickplace_*`, `check_*`, `fix_error`, `explain_error`.          |       4773 |   28 |      19 |
| `arena`            | Arena create/run/leaderboard/compare_policies.                          |        283 |    1 |       3 |
| `vision`           | `vision_detect_objects`, `vision_bounding_boxes`, plan_trajectory.      |       1129 |   16 |       8 |
| `rendering`        | Lights, camera params, HDRI skydomes, render mode/resolution.           |        353 |    0 |       8 |
| `animation`        | Keyframes, timeline range, play animation, audio prims.                 |        306 |    0 |       5 |
| `workflow`         | Start/edit/approve/reject/revise/cancel/status — **stateful target**.   |        737 |   15 |       0 |
| `resolve`          | The 12 `resolve_*` NL-disambiguation handlers — **stateful target**.    |        792 |   12 |       0 |
|                    | **TOTAL**                                                               |    **32195** | **217** | **189** |

External registrators (not in `_THEME_MODULES`):

- `chat/tools/multimodal_handlers.py:register_multimodal_handlers` —
  16 multimodal vision/audio handlers (Phase wave 5).
- `service/isaac_assist_service/diagnose/tool.py:register_diagnose_handlers` —
  diagnose-scene-feasibility (Phase 1 of master plan).
- `chat/tools/bridge_tools.py:register_bridge_handlers` — Modbus, OPC-UA,
  MQTT-Sparkplug, OpenPLC industrial-bridge subprocess registration.
- `_dispatch._register_ros2_live` — handles 11 `ros2_*` live tools with
  the `ros-mcp` ImportError fallback.

Total dispatch entries: 246 data + 173 codegen = 419 (matches
pre-Phase-9 inventory byte-for-byte).

Stateless themes (the 15 above except `workflow` and `resolve`) landed
under Phases 3-7. The two stateful themes — `workflow` and `resolve` —
have data-handlers moved (Phase 7) but their state machines
(`WORKFLOWS` singleton, resolution cache) are still located in
`tool_executor.py` and accessed via lazy import. Phases 15 + 16
complete the stateful migration.

## 4. The `_shared.py` / `_state.py` discipline

Two infrastructure modules sit underneath every theme.

### `_shared.py` — cross-handler utilities

`handlers/_shared.py` is the future home for the high-fan-in utilities
that the Phase 2b cross-reference audit
(`docs/audits/handler_cross_refs.md`) identified as being called by
three or more handlers. The shipping list (9 functions):

- `_get_viewport_bytes` — viewport capture helper (vision theme).
- `_get_vision_provider` — vision-LLM provider selector.
- `_query_run_ipc` — training IPC helper.
- `_resolve_run_id` — training run-id resolver.
- `_check_real_data_path` — finetune / sim data validator.
- `_wf_now_iso` — workflow timestamp helper.
- `_parse_last_json_line` — subprocess output parser.
- `_safe_robot_name` — USD-path sanitiser.
- `_validate_env_id` — IsaacLab env-id validator.

Read-only cross-handler constants live in `_shared.CONSTANTS`, never
in `_state.py` (whose dataclasses are mutable by contract).

#### PEP 562 lazy re-export bridge (still active)

`handlers/_shared.py` resolves legacy utility names via a module-level
`__getattr__` (PEP 562). A consumer that writes

```python
from ._shared import _safe_robot_name
```

gets the existing implementation in `tool_executor.py`. Phase 8 lifts
the function bodies into `_shared.py` directly; the consumer's import
line is unchanged.

The bridge is one-directional: `_shared.py` may pull from
`tool_executor.py`; `tool_executor.py` must never reach into
`_shared.py` for state. The list of bridged names is the
`_LEGACY_REEXPORT_NAMES` tuple.

Today, handler modules import directly from tool_executor via
`from ..tool_executor import _X` (102 such imports as of 2026-05-13).
The recovered-state audit (`scripts/audit_recovered_state.py` +
`docs/audits/recovered_state_audit.md`) lists every symbol still in
the tool_executor.py recovered-state block — 50 of them are referenced
by handlers/, 7 are internal-only.

### `_state.py` — per-theme state singletons

`handlers/_state.py` holds the five mutable state containers shared
across handlers:

- `WORKFLOWS` (`WorkflowState`) — workflow lifecycle, owned by
  `handlers.workflow` (Phase 15 target).
- `EUREKA` (`EurekaState`) — reward-generation runs, owned by
  `handlers.training` (Wave 3 §3 #2 flagged that the legacy
  `_eureka_runs` is read but never written, fixed alongside Phase 64).
- `TRAINING` (`TrainingState`) — subprocess pids and IPC handles,
  owned by `handlers.training`.
- `DR` (`DRState`) — DR range hints and correlations, owned by
  `handlers.sdg`.
- `BRIDGES` (`BridgeState`) — industrial-bridge subprocess registry
  (Modbus, OPC-UA, MQTT-Sparkplug, OpenPLC), owned by `handlers.ros2`
  and Phase 31b.

Cross-theme state imports are **forbidden**. `handlers/sdg.py` may
import `DR`; it must never import `WORKFLOWS` or `EUREKA`. Phase 9
lands the CI lint that enforces this — a `_state` import audit walks
each theme module and rejects any singleton that does not belong to
the named slice.

Tests that mutate any singleton must call `_state.reset_all_state()`
in teardown. The helper is test-only — never invoke it from
production code.

## 5. The `register(...)` contract

Every theme module exposes:

```python
def register(
    data: Dict[str, Callable[..., Awaitable[Any]]],
    codegen: Dict[str, Callable[..., Any]],
) -> None:
    # Phase 9: populate the dispatch dicts with this theme's entries.
    data["my_data_tool"] = _handle_my_data_tool
    codegen["my_codegen_tool"] = _gen_my_codegen_tool
```

`data` is the `DATA_HANDLERS` dict — handlers that return JSON-shaped
results. `codegen` is the `CODE_GEN_HANDLERS` dict — handlers that
emit a USD-Python patch for the Kit RPC to execute. A theme module
populates whichever dicts its handlers belong to.

Phase 9 (2026-05-13) flipped the contract from a no-op placeholder to
the authoritative dispatch population. The pre-commit lint
`scripts/lint/no_handler_in_dispatch.py` rejects new
`_handle_X` / `_gen_X` defs in `tool_executor.py` and new inline
`DATA_HANDLERS[X] = ...` lines — the only place a tool name binds to
a handler is inside a theme's `register()`.

The `_dispatch.register_handlers` orchestrator iterates the themes in
a fixed order: `scene_authoring, physics, robot, sensors, sdg,
training, ros2, teleop, scene_blueprints, diagnostics, arena, vision,
rendering, animation, pick_place, workflow, resolve`. Order is
informational (tool names are disjoint across themes per the byte-
diff audit) but documents the dependency intent: stateless themes
register first, stateful themes last.

## 6. Pydantic model generation flow

The generator `scripts/gen_handler_models.py` AST-parses
`tool_schemas.py:ISAAC_SIM_TOOLS` and emits one permissive Pydantic
input model per tool to
`service/isaac_assist_service/chat/tools/handlers/_models.py`.

Generated module shape:

```python
# handlers/_models.py (auto-generated)
class CreatePrimArgs(BaseModel):
    """Create a new USD prim..."""
    model_config = ConfigDict(populate_by_name=True, extra='allow')
    prim_path: str = Field(..., description="...")
    prim_type: str = Field(..., description="...")
    position: Optional[List[float]] = Field(None, ...)
    ...

MODEL_REGISTRY = {
    "create_prim": CreatePrimArgs,
    "delete_prim": DeletePrimArgs,
    ...  # 416 entries
}
```

Generator behavior:

- **Permissive mode**: optional fields → `Optional[T] = None`;
  unknown shapes (mixed-type union, no `properties`) → `Any` or
  `Dict[str, Any]`; `extra='allow'` so unrecognised kwargs pass through.
- **Reserved-name handling**: Python keywords + Pydantic v1 method
  names (`schema`, `dict`, `json`, `copy`, `validate`, …) get
  aliased — e.g., the `schema` field in `bulk_apply_schema` becomes
  `schema_` with `alias="schema"` so JSON input still uses the
  original name.

**TODO (Phase 10 follow-up)**: update handler signatures from
`async def _handle_X(args: Dict)` to
`async def _handle_X(args: SomeArgs)`. The 416 handler signature
changes are the high-risk half of Phase 10; the daytime-supervised
batch should run with Kit RPC available so every handler's runtime
shape can be verified. The model framework (Phase 10 partial) ships
today as a callable contract that handlers MAY adopt incrementally.

The pre-commit hook `scripts/lint/regen_models_check.py` detects when
`tool_schemas.py` is newer than `_models.py` and warns (soft-fail
today; will be `--strict` once handlers consume the models).

## 7. Patch-validator rule pipeline

Handlers that mutate the live USD stage emit a USD-Python "patch" —
a string of Python code that runs against the open stage inside the
Kit RPC sandbox at `:8001`. Between handler and RPC sits the
patch-validator pipeline.

Today (pre-Phase 11): `patch_validator.py` aggregates 22 `_check_*`
functions into a single `validate_patch(code)` entry point. Each
function pattern-matches against the patch and returns
`List[PatchIssue]`. The orchestrator concatenates and the dispatcher
calls `has_blocking_issues(issues)` to decide pass / block.

**TODO (Phase 11)**: pluggable validator rules. Rule sources live in
`service/isaac_assist_service/chat/tools/patch_validators/rules/`.
Each rule subclasses `PatchValidatorRule` and implements
`check(patch) -> Optional[ConstraintViolation]`. The pipeline runs
every registered rule against the patch and aggregates results into
a `ValidationResult` (Phase 11b generic violation framework).

When a patch fails validation, the handler returns `success=False`
with a `ConstraintViolation` payload. The mutation never reaches the
Kit RPC. This is load-bearing: a `success=True` from a patch-emitting
handler must mean the patch validated *and* executed *and* mutated
the stage in a re-readable way (Honesty Charter §2.1, §5 "Silent
`Apply` on invalid prims").

## 8. Pointers

Source-of-truth files (open these to read the current shape):

- `service/isaac_assist_service/chat/tools/handlers/__init__.py` —
  authoritative theme list and module docstring summary.
- `service/isaac_assist_service/chat/tools/handlers/_dispatch.py` —
  the `register_handlers(data, codegen)` orchestrator. Phase 9 made
  this the sole dispatch entry point.
- `service/isaac_assist_service/chat/tools/handlers/_shared.py` —
  shared utility surface and the PEP 562 re-export bridge.
- `service/isaac_assist_service/chat/tools/handlers/_state.py` —
  per-theme state singletons and the `reset_all_state()` test
  helper.
- `service/isaac_assist_service/chat/tools/handlers/_models.py` —
  AUTO-GENERATED. Regenerate via `scripts/gen_handler_models.py`.

Companion architecture docs (read alongside this one when you touch a
handler):

- `docs/architecture/honesty.md` — the Honesty Charter (Phase 18c).
  Every handler return is subject to its §4 four-check audit; every
  patch-emitting handler must respect §5 "Silent `Apply` on invalid
  prims". When in doubt, the charter is the tie-breaker.
- `docs/architecture/mandate_boundary.md` — the RL/IA scope-boundary
  guard (Phase 17b). RL strategic-content tokens are banned from
  handler code, doc text, and tool descriptions. The CI scanner
  (`scripts/lint_mandate.py`) blocks pre-commit on violation.
- `docs/architecture/action_levels.md` — the L1 / L2 / L3 taxonomy
  (Phase 18b). When you add a new tool, decide its action level
  before merging — bulk annotation is deferred but the classification
  rule applies to every new entry from Phase 18b forward.

Audit + lint scripts (run before opening a PR):

- `scripts/audit_recovered_state.py` — classifies the 57 module-level
  symbols in `tool_executor.py:32-1572` (the "recovered-state" block)
  as DEAD / INTERNAL_ONLY / HANDLER_USED / EXTERNAL_USED. Phase 8 +
  Phase 13 reference targets.
- `scripts/audit_handler_cross_refs.py` — handler↔utility fan-in /
  fan-out (Phase 2b).
- `scripts/audit_phase_file_writes.py` + `scripts/safe_batch.py` —
  per-phase file-write matrix for safe parallelisation (Phase 2b).
- `scripts/diag_imports.py` — graphviz-renderable dependency graph
  for the handlers package (Phase 12).
- `scripts/lint/no_handler_in_dispatch.py` — rejects new handlers /
  inline assignments in `tool_executor.py` (Phase 17).
- `scripts/lint/regen_models_check.py` — `_models.py` freshness vs
  `tool_schemas.py` (Phase 17).
- `scripts/lint_mandate.py` — RL/IA mandate-boundary scanner
  (Phase 17b).
- `scripts/gen_handler_models.py` — generate `handlers/_models.py`
  from `tool_schemas.py` (Phase 10).

## 9. Current state vs target

| Phase | Status | Notes |
| --- | --- | --- |
| 1 (audit), 2 (skeleton), 2b (cross-ref) | landed | |
| 3 (scene-authoring moves) | landed | 79 handlers |
| 5 (physics moves) | landed | 38 handlers |
| 6 (robot/sensor/SDG/training moves) | landed | 161 handlers |
| 7 (ros2/teleop/scene_blueprints/diagnostics/arena/vision/animation/pick_place/rendering moves) | landed | 105 handlers across multiple themes |
| 8 (extract shared utilities to _shared.py) | **partial** | _shared.py + _state.py shells in place; PEP 562 bridge active; 50 symbols still referenced via `from ..tool_executor import _X`. See `docs/audits/recovered_state_audit.md`. |
| 8b (determinism harness), 8c (typed primitives) | landed | |
| 8d (stable-baseline taxonomy) | **partial** | Logic split across `scripts/qa/`; consolidation deferred — needs runtime CP-37 verification. |
| 9 (dispatch swap) | landed | 2026-05-13 — `register_handlers()` is sole dispatch entry; zero inline assignments. |
| 10 (Pydantic input models) | **partial** | `_models.py` generated (416 models); handler signature changes deferred to daytime-supervised batch. |
| 11 (patch validator pipeline) | not started | 22 `_check_*` functions still in `patch_validator.py`; refactor to per-rule classes pending. |
| 11b (ConstraintViolation), 11c (ctrl namespace) | landed | |
| 12 (no circular imports) | landed | 20 tests; graph is clean DAG. |
| 13 (recovered-state archive) | blocked by 8 | 1540 lines in `tool_executor.py:32-1572`. Audit scripted; daytime migration of 50 HANDLER_USED symbols required first. |
| 14 (dispatch shim) | blocked by 13 | Target: `tool_executor.py` ≤ 500 lines. Currently 5508. |
| 15 (workflow stateful) | not started | Phase 6 moved handlers; state machine still in `tool_executor.py`. |
| 16 (resolve stateful) | partial | Handlers in `handlers/resolve.py`; resolution cache still in `tool_executor.py`. |
| 17 (pre-commit hooks for tools hygiene) | landed | 2026-05-13 — `no_handler_in_dispatch`, `regen_models_check`, `mandate-guard` wired in `.pre-commit-config.yaml`. |
| 17b (mandate guard), 18b (action levels), 18c (honesty) | landed | |
| 18 (this doc) | landed | 2026-05-13. |

Until the in-progress / blocked column closes:

- New handlers MUST be authored inside a theme module under
  `handlers/<theme>.py`. The `no_handler_in_dispatch` lint blocks
  any new `_handle_X` / `_gen_X` def in `tool_executor.py`.
- New utilities that look high-fan-in (≥3 callers) belong in
  `handlers/_shared.py` — extend `_LEGACY_REEXPORT_NAMES` if you
  cannot move the body yet.
- New state belongs in `handlers/_state.py` as a new dataclass
  singleton; do not park it on `tool_executor.py` module-level.
- New patch-emitting handlers must call `queue_exec_patch` and
  surface `success=False` with a named error on any failure path
  (Honesty Charter §2.1).
- When you edit `tool_schemas.py`, regenerate `_models.py` by
  running `python scripts/gen_handler_models.py`. The
  `regen_models_check` lint warns when they drift; will hard-fail
  once Phase 10's handler signature changes ship.
