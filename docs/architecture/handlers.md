# Handler architecture — read this first

> Phase 18 of `specs/IA_FULL_SPEC_2026-05-10.md`. This document is the
> entry point for any engineer about to touch a tool handler. Read it
> before opening `tool_executor.py` or any module under
> `service/isaac_assist_service/chat/tools/handlers/`.

Status: **describes the target architecture**. Phases 1, 2, 2b, 8, 8b,
8c, 8d, 11b, 11c, 17b, 18b, 18c, 49b, 90, and 0b have landed; phases 3,
5, 6, 7, 9, 10, 11, 13, 14, 15, 16, and 17 are in progress or partial.
Where the current code lags the target shape, the section is marked
**TODO** and refers to the phase that closes the gap.

## 1. Orientation

A "handler" is the Python function bound to a tool name in the
416-entry `ISAAC_SIM_TOOLS` schema list. When the chat surface or MCP
client calls `create_prim`, `setup_pick_place_controller`, or
`start_workflow`, the dispatcher looks up the name and invokes the
matching handler. Until Phase 9 lands, every handler lives at module
level inside a single 35-kloc file (`tool_executor.py`); after Phase 9,
handlers live in 14 themed modules and the dispatcher walks a registry
populated by each theme's `register(data, codegen)` function.

This document explains how the dispatch flows, how the themed modules
divide responsibility, what discipline the shared utility and state
modules enforce, and how the patch-validator pipeline sits underneath.
The point is to make a new engineer self-sufficient: after reading
this, you know which file to open for a given tool name, which utility
module to import from, which state singleton you may touch, and how
your change interacts with the honesty charter and the mandate
boundary.

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
handlers/_dispatch.py:register_handlers (Phase 9, in progress)
     |
     | per-theme call
     v
handlers/<theme>.py  (scene_authoring, physics, robot, ...)
     |
     | uses
     v
handlers/_shared.py  <- shared utilities (Phase 8)
handlers/_state.py   <- per-theme state singletons (Phase 8)
     |
     | if patch-emitting
     v
kit_tools.queue_exec_patch(code) -> patch_validator -> Kit RPC :8001
```

Phase 9 is the swap: today, `tool_executor.execute_tool_call` consults
inline `DATA_HANDLERS["X"] = _handle_X` assignments; after Phase 9, it
calls `handlers._dispatch.register_handlers(data, codegen)` once at
startup and the inline assignments go away.

## 3. Themes

The 14 themed modules under `handlers/` (declared in `__init__.py`):

| Module             | Target scope                                                            | LOC budget |
| ------------------ | ----------------------------------------------------------------------- | ---------- |
| `scene_authoring`  | USD prim CRUD, attrs, references, layers, materials, snapshots.         | ~2.5k      |
| `physics`          | Physics scene config, articulations, joints, drives, collision.         | ~2.2k      |
| `robot`            | Robot import/anchor, IK, gripper, motion policy, kinematics.            | ~3.0k      |
| `sensors`          | Cameras, lidars, contact sensors, proximity, NIR, barcode.              | ~2.0k      |
| `sdg`              | Replicator pipelines, DR ranges, presets, COCO/YOLO writers.            | ~2.0k      |
| `training`         | IsaacLab env, training launch, RL/GR00T, Eureka, checkpoints.           | ~2.5k      |
| `ros2`             | `ros2_connect`, topics, services, OmniGraph bridge, industrial bridges. | ~2.5k      |
| `teleop`           | Start/stop sessions, record/validate demos, hardware mapping.           | ~1.5k      |
| `scene_blueprints` | Catalog search, generate/validate/build blueprints, templates.          | ~1.8k      |
| `diagnostics`      | `verify_pickplace_*`, `check_*`, `fix_error`, `explain_error`.          | ~2.0k      |
| `arena`            | Arena create/run/leaderboard/compare_policies.                          | ~1.0k      |
| `workflow`         | Start/edit/approve/reject/revise/cancel/status — **stateful**.          | ~1.5k      |
| `resolve`          | The 10 `resolve_*` NL-disambiguation handlers — **stateful**.           | ~1.5k      |
| `vision`           | `vision_detect_objects`, `vision_bounding_boxes`, plan_trajectory.      | ~1.5k      |

Stateless themes (scene_authoring, physics, robot, sensors, sdg,
training, ros2, teleop, scene_blueprints, diagnostics, arena, vision)
land first under Phases 3-7. The two stateful themes — `workflow` and
`resolve` — land later: `workflow` is Phase 15, `resolve` is Phase 16.
The deferral is deliberate: their state machines (workflow-checkpoint
graph, resolution cache) are coupled to the dispatch surface and want
the stateless themes already migrated before touching them.

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

#### PEP 562 lazy re-export bridge

Until Phases 3-7 physically move the function bodies out of
`tool_executor.py`, `_shared.py` resolves legacy utility names via a
module-level `__getattr__` (PEP 562). A consumer that writes

```python
from ._shared import _safe_robot_name
```

gets the existing implementation in `tool_executor.py`, today,
without any code-shape difference. When Phase 3-7 lift the function
into `_shared.py` directly, the consumer's import line is unchanged.

The bridge is one-directional: `_shared.py` may pull from
`tool_executor.py`; `tool_executor.py` must never reach into
`_shared.py` for state. The list of bridged names is the
`_LEGACY_REEXPORT_NAMES` tuple. Adding a new high-fan-in utility to
the bridge is a Phase 3-7 deliverable, not a free-form import.

### `_state.py` — per-theme state singletons

`handlers/_state.py` holds the five mutable state containers shared
across handlers:

- `WORKFLOWS` (`WorkflowState`) — workflow lifecycle, owned by
  `handlers.workflow` (Phase 15).
- `EUREKA` (`EurekaState`) — reward-generation runs, owned by
  `handlers.training` (Phase 6; Wave 3 §3 #2 flagged that the legacy
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
    data: Dict[str, Callable[..., Any]],
    codegen: Dict[str, Callable[..., Any]],
) -> None:
    ...
```

`data` is the `DATA_HANDLERS` dict — handlers that return JSON-shaped
results. `codegen` is the `CODE_GEN_HANDLERS` dict — handlers that
emit a USD-Python patch for the Kit RPC to execute. A theme module
populates whichever dicts its handlers belong to.

Status:

- **Phase 2 (landed):** every theme exposes a `register()` that is a
  no-op. The package skeleton is in place.
- **Phases 3-7 (in progress):** moves real handler bodies into the
  theme modules and has their `register()` populate the dicts.
- **Phase 9 (in progress):** flips `tool_executor.execute_tool_call`
  to call `_dispatch.register_handlers(data, codegen)` at startup,
  retiring the inline `DATA_HANDLERS["X"] = ...` assignments.

The `_dispatch.register_handlers` orchestrator iterates the themes in
a fixed order (`scene_authoring, physics, robot, sensors, sdg,
training, ros2, teleop, scene_blueprints, diagnostics, arena, vision,
workflow, resolve`). Order matters once registers do real work:
stateless themes register first, stateful themes last.

## 6. Pydantic model generation flow

Today handlers accept `Dict[str, Any]` for tool arguments and inspect
keys at runtime. **TODO (Phase 10):** generate `handlers/_models.py`
from `tool_schemas.py`. Each tool name maps to a typed Pydantic
model; the dispatcher validates the args once at the boundary, and
handlers downstream consume a typed object. The generator script is
run as a pre-commit hook so the generated module never drifts from
the schema list. Until Phase 10 ships, the `Dict[str, Any]` shape is
authoritative and runtime `args.get("foo")` is the right pattern.

## 7. Patch-validator rule pipeline

Handlers that mutate the live USD stage emit a USD-Python "patch" —
a string of Python code that runs against the open stage inside the
Kit RPC sandbox at `:8001`. Between handler and RPC sits the
patch-validator pipeline.

**TODO (Phase 11):** pluggable validator rules. Rule sources live in
`service/isaac_assist_service/chat/tools/patch_validators/rules/`.
Each rule subclasses `PatchValidatorRule` and implements
`check(patch) -> Optional[ConstraintViolation]`. The pipeline runs
every registered rule against the patch and aggregates results into
a `ValidationResult`. Rules can be enabled / disabled by config
without rewriting the dispatcher.

The shape of a violation comes from Phase 11b — the generic
`ConstraintViolation` framework — which standardises the error
channel (rule id, rule scope, offending span, suggested fix, severity).
Phase 47 wires the validator into the mainline dispatch (warnings
must block, not annotate; see Honesty Charter §5). Phase 47b grows
the rule set against the silent-success long-tail.

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
  the `register_handlers(data, codegen)` orchestrator.
- `service/isaac_assist_service/chat/tools/handlers/_shared.py` —
  shared utility surface and the PEP 562 re-export bridge.
- `service/isaac_assist_service/chat/tools/handlers/_state.py` —
  per-theme state singletons and the `reset_all_state()` test
  helper.

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

## 9. Current state vs target

| Phase | Status |
| --- | --- |
| 1 (audit), 2 (skeleton), 2b (cross-ref), 8 (shared shells), 8b/8c/8d (types/baselines/determinism), 11b (violations), 11c (ctrl namespace), 17b (mandate guard), 18b (action levels), 18c (honesty), 49b (cache key), 90 (redactor), 0b (fork triage) | landed |
| 3 (scene-authoring moves), 5 (physics), 6 (robot/sensor/SDG/training), 7 (ros2/teleop/...), 9 (dispatch swap), 10 (type narrow), 11 (validator pipeline), 13 (recovered-state archive), 14 (dispatch shim), 15 (workflow move), 16 (resolve move), 17 (pre-commit hooks), 18 (this doc) | in progress / partial |

Until the in-progress column closes:

- New handlers may be authored directly in `tool_executor.py` if the
  theme module is still a stub. Mark the handler with an
  `# IA-MIGRATE-TO: handlers/<theme>.py` comment so Phase 3-7 can
  pick it up.
- New utilities that look high-fan-in (≥3 callers) belong in
  `handlers/_shared.py` — extend `_LEGACY_REEXPORT_NAMES` if you
  cannot move the body yet.
- New state belongs in `handlers/_state.py` as a new dataclass
  singleton; do not park it on `tool_executor.py` module-level.
- New patch-emitting handlers must call `queue_exec_patch` and
  surface `success=False` with a named error on any failure path
  (Honesty Charter §2.1).
