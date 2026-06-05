# Block 1A — Implementation Status

Authored 2026-05-09. Captures what landed in the
`feat/multimodal-foundation` branch tonight after the user said
"kör på tills 1A är helt klar". Reads as a handoff for the next session.

Branch: `feat/multimodal-foundation` off `pr/chunk-10`. **Branch
topology note**: ideally this would have been `pr/chunk-11` off `master`
to match the project's chunked-PR pattern; cleanup before PR-time is a
trivial rebase. Other session is on `pr/chunk-10` driving toward 100%
function-gate; coordination doc at
`docs/specs/2026-05-09-multi-session-coordination.md`.

## Completed (10 commits beyond the shared base)

### Block 1A.1 — Backend foundation (✅ complete)

`service/isaac_assist_service/multimodal/`:
- `types.py` — LayoutSpec, Intent (three-layer vocabulary: closed
  PatternHint enum, typed StructuralFeatures, namespaced
  StructuralTag with format-regex), TypedObject (USD-prim-path-safe
  name validation), RoleBinding, Source.
- `vocabulary.py` — StructuralTagRegistry with 26 default isaac:/cad:
  tags. Append-only discipline; deprecate-don't-remove. Ships in
  module via `structural_tags.registry.json`.
- `validate.py` — `validate_layout_spec()` with cross-feature
  consistency, registry membership, user:-namespace pass-through,
  object name/id uniqueness, binding object_id references.
- `persistence.py` — SQLite + WAL + per-thread connections. CAS via
  `save_with_cas(parent_revision)` returning `RevisionConflictError`
  with `current_spec` attached for three-way merge UI. Build log +
  events table per spec §13/§17.
- `ratify.py` — pure deterministic role-binder. Auto-binding
  waterfall (cardinality-trivial → disambiguator → ambiguous prompt).
  Compatible with both legacy templates (no `roles` field — no-op)
  and role-based templates (Block 1B). Disambiguators: smaller_x_first,
  larger_x_first, smaller_y_first, larger_y_first, nearest_to_origin,
  farthest_from_origin, first_listed.
- `migrations/__init__.py` — forward-only schema migration scaffold.
  Quarantines broken files to `.broken-{ts}` suffix.

`service/isaac_assist_service/chat/tools/`:
- `multimodal_handlers.py` — 6 handlers: `read_layout_spec`,
  `update_layout_spec`, `commit_layout_spec`,
  `apply_layout_spec_to_scene`, `query_layout_metric`, `rebind_role`.
  Wired against persistence + ratify + validation.
- `tool_executor.py` — 5-line append at EOF registers the 6 handlers
  via `register_multimodal_handlers(DATA_HANDLERS)` per coordination
  doc §3 sectional ownership pattern. Shared-file merge surface = 1
  import + 1 call.

`template_retriever.py`:
- `canonical_structural_fingerprint()` — deterministic IR serialization
  for embedding similarity (no NL synthesis).
- `_features_compatible()` + `_counts_compatible()` — Stage-1 filter
  helpers.
- `filter_templates_by_intent()` — Stage-1 hard structural filter.
- `retrieve_with_intent_filter()` — three-stage retrieval per spec §8.1
  with graceful fallback to embedding-only when no templates have
  `intent` field (current state pre-Block-1B).

`tests/test_multimodal_foundation.py`:
- 40 L0 tests covering types, vocabulary, validate, persistence (incl.
  3 async CAS scenarios), ratify (all 7 auto-binding-waterfall
  scenarios), migrations. Zero warnings.

### Block 1A.2 — Kit UI additions (✅ substantially complete)

`exts/isaac_5.1/.../ui/chat_view.py` (mirrored byte-identical to 6.0):
- `👁 Modes` launcher — replaces `Vision` button. Opens popover with
  5 modality items (Open canvas, Upload sketch, Voice, Extract from
  scene, Analyze viewport). Existing LiveKit-vision toggle preserved
  verbatim as the last item.
- `_open_modes_popup()`, `_modes_dispatch()`, action methods
  `_modes_open_canvas` / `_upload_sketch` / `_voice_input` /
  `_extract_from_scene` / `_analyze_viewport`.
- Horizontally-scrolling quick-prompts row replaces fixed 3-button
  chips. Auto-sources from `workspace/templates/CP-*.json` `goal`
  field with hand-picked short labels for CP-01..CP-05 plus heuristic
  fallback. Persistent across window lifetime; click inserts text
  into input (not auto-send).
- 5 SSE handlers: `canvas_proposed`, `canvas_committed`,
  `canvas_preview_updated`, `canvas_build_progress`,
  `canvas_build_completed`. Lazy-init `CanvasMirrorWindow` via
  `_ensure_mirror()`; live strip status updates with per-tool name
  during canvas-build progress.

`exts/isaac_5.1/.../ui/canvas_mirror.py` (mirrored to 6.0):
- `CanvasMirrorWindow(omni.ui.Window)` — read-only 2D preview panel
  with three-state visibility (Hidden/Proposed/Live). Dock preference
  RIGHT_BOTTOM. "Edit in browser" button as escape hatch to the
  editor; mirror itself stays strictly read-only forever per spec
  §11.5.3.

### Block 1A.3 — Browser SPA + canvas backend (✅ substantially complete)

`service/isaac_assist_service/multimodal/`:
- `render.py` — PIL-based PNG renderer producing snapshots from
  LayoutSpec. Visual style mirrors spec §12 design tokens:
  NVIDIA-dark canvas, agency-tier class colors, two-tier grid,
  origin cross, reach circles for robot arms.
- `routes.py` — 8 FastAPI endpoints under `/api/v1/canvas/{session_id}`:
  GET, POST patch (CAS), POST commit, POST preview_render, POST
  build, DELETE, POST client_error, GET build/{build_id}. Mounted in
  `main.py`.

`web/floor-plan-ui/`:
- Vite + React + Konva + Zustand TypeScript SPA scaffold.
  `package.json`, `vite.config.ts` (proxy /api → :8000),
  `tsconfig.json`, `index.html`.
- `src/main.tsx` — React DOM mount.
- `src/App.tsx` — header + toolbar + canvas + right-dock + status bar.
  Loads LayoutSpec via REST, renders objects with class-tinted
  rectangles + reach circles + grid. Same agency-tier palette as the
  PIL renderer so SPA and Kit-mirror look identical.
- `src/api/types.ts` — TypeScript mirror of LayoutSpec types.
- `src/api/floorPlanApi.ts` — typed REST client. `CanvasConflictError`
  on 409 with `ConflictDetail` payload for client-side three-way merge;
  `CanvasValidationError` on validation failure.
- `README.md` — dev/build workflow + what's pending checklist.

## What remains (next session)

### Block 1A.3 interactive editing — biggest gap

The SPA scaffold renders STATIC layouts. Interactive editing not yet
implemented:

- Object palette (drag-from-sidebar to canvas)
- Konva Transformer multi-select handles
- Smart guides + 5 snap-marker types (spec §6.3)
- Dimension lines + constraint indicators (spec §6.4)
- Properties / Layers / Constraints right dock interactions
- Floating confirm bar for agent-proposed states (spec §5.7)
- Custom robot silhouettes (32×32 SVG per robot class)
- Motion vocabulary tokens (spec §12.7)
- Persistent chat input ribbon at bottom (spec §11.2)
- Zustand store with command-pattern undo/redo (100-step depth)
- localStorage write-ahead log + sendBeacon-on-unload
- SSE listener for `canvas/proposed` etc events from backend

### Block 1A.2 polish

- Mirror-window registration in extension lifecycle (currently lazy
  on first canvas event; could be done at extension startup if the
  canvas modality is opt-in on by default)
- `omni.ui.Workspace` default-layout config so chat + mirror + 3D
  viewport dock side-by-side on first launch (spec §11.1)

### Block 1B — held until other session reaches 100%

(Per coordination doc — does not start until controller-logic session
signals 100% function-gate on existing pipeline.)

- Restructure `verify_pickplace_pipeline` + `simulate_traversal_check`
  into feature-dispatched check registry (spec §6).
- Refactor CP-01..CP-05 templates to role-based (`roles` +
  `code_template` + `verify_args_template` per spec §4). Pre-refactor
  baseline snapshot via `function_gate_suite.py`; any function-gate ✓
  becoming ✗ post-refactor → template rolled back.

## Verification

- 40/40 L0 tests pass
- All Python imports clean (228 total handlers, 6 multimodal registered)
- Both isaac_5.1 + isaac_6.0 chat_view.py byte-identical and syntactically valid
- 8/8 canvas REST endpoints smoke-tested via FastAPI TestClient
- Renderer produces 9.5-10.9KB PNGs for empty / single-Franka / CP-02
  multi-robot layouts

## Coordination status

Other session is on `pr/chunk-10` driving toward 100% function-gate.
They've been committing CP-N work continuously through this session.
Block 1B requires their 100% signal.

The shared file `tool_executor.py` was edited at end-of-file (one
sectional block). Other session's CP-N additions happen in their own
sections; merge surface is minimal.

## How to run

```bash
# Backend
cd ~/projects/Omniverse_Nemotron_Ext
./launch_service.sh   # uvicorn :8000

# Run tests
python -m pytest tests/test_multimodal_foundation.py

# Browser SPA (when implemented further)
cd web/floor-plan-ui
npm install   # not yet run; CI will need this
npm run dev   # → http://localhost:5173
```

Multimodal handlers are registered automatically when `tool_executor.py`
imports — no extra setup. Canvas REST endpoints active under
`/api/v1/canvas/{session_id}`.
