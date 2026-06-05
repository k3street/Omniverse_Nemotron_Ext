# Multimodal Foundation — Opus Deep Analysis

Authored 2026-05-08. Three Opus agents investigated the deep design questions
for the multimodal foundation in parallel:
- **Question A** — IR expressivity vs strictness
- **Question B** — Role binding protocol across modalities
- **Question C** — Native Kit panel feasibility for CAD-grade canvas

This document synthesizes their findings. Each section pairs the strongest
conclusions with the sharpest residual critique. The agents' full responses are
captured here verbatim where most useful.

Reference docs:
- `docs/specs/2026-05-08-multimodal-foundation-working-draft.md` — the IR draft
- `docs/specs/2026-05-08-floor-plan-tool-opus-critique.md` — prior critique round
- `docs/specs/2026-05-08-floor-plan-tool-spec.md` — original spec under iteration

---

## Question A — IR expressivity vs strictness

### Recommended regime: three-layer vocabulary

| Layer | Field | Mutability | Owner |
|---|---|---|---|
| **L0** | `pattern_hint` (closed enum, versioned) | major schema bump | template authors |
| **L1** | `structural_features` (typed booleans + numerics) | additive only | template authors |
| **L2** | `structural_tags` (namespaced strings, registry-validated) | append-only registry | anyone with namespace |

The working draft's `StructuralTag = string` collapses three concerns into one
field and **is the regex-family backdoor**. The fix is the layered scheme.

### Discriminating rule for `pattern_hint`

> **`pattern_hint` is the simulation-success-criterion shape.**

CP-01/CP-02/CP-04 all share "cube xy inside bin, at rest" → all `pick_place`.
CP-03 is "cube xy inside *correct-color* bin" → `sort`. CP-05 is "cube xy inside
bin AND upright" → `reorient`. If success criterion is materially different,
pattern is different.

CP-04 (constraint) is *fundamentally a pickplace task*. The constraint is a
**feature** (`has_bounded_footprint: true`, `footprint_xy_max_m: [2.0, 2.0]`),
not a pattern. The working draft would have forced CP-04 into a `"constraint"`
bucket; the layered scheme correctly puts it as `pick_place` + bounded-footprint
feature.

### Retrieval becomes structurally-gated, similarity-tiebroken

```
def retrieve(spec):
    candidates = filter(templates,
        pattern_hint == spec.intent.pattern_hint
        AND structural_features compatible with spec.intent.structural_features
        AND counts within tolerance OR template marked param-flexible)
    fingerprint = canonical_serialize(spec.intent)
    scores = embed_similarity(fingerprint, candidates)
    return tier_classify(scores)
```

**Stage 1 is hard structural filter.** Stage 2 is embedding similarity over a
canonical structural fingerprint. **No NL synthesis anywhere on the retrieval
path.** This eliminates the rank-collision problem (VR-19's CP-01 vs CP-02 fight
disappears because `n_robot_stations=2` is a hard filter).

### Where parameters belong (the schema-axis question)

| Field | Role | Examples |
|---|---|---|
| `intent.counts` | Integer structural facts | `cubes: 4`, `robots: 2` |
| `intent.structural_features` | Typed scene-shape facts | `has_bounded_footprint: true`, `footprint_xy_max_m: [2,2]` |
| `parameters` | T2 substitution targets | `n_cubes: 4`, `belt_speed_m_s: 0.2` |
| `source.metadata` | Provenance / observability only | `vlm_confidence`, `prompt_text` |

Test: *Does varying it change which template should match?* Yes → structural
fact. No → parameter.

Test: *Does the verifier read it?* Yes → structural feature. No → parameter.

### Concrete `intent` schema (recommended replacement for working draft §60-105)

```typescript
type PatternHint = "pick_place" | "sort" | "constraint" | "reorient" | "navigate";
// REJECT "custom" — once it exists, it becomes the dumping ground.

interface Counts {
  robots: number; conveyors: number; bins: number; cubes: number;
  sensors: number; humans: number;
}

interface StructuralFeatures {
  n_robot_stations: number;
  n_handoffs: number;
  n_destinations: number;
  destination_kind: "single_bin" | "n_bins_routed" | "shelf" | "fixture";
  routing_axis: null | "color" | "size" | "shape" | "label";
  uses_conveyor_transport: boolean;
  uses_navigation: boolean;
  has_color_routing: boolean;
  has_orientation_requirement: boolean;
  has_bounded_footprint: boolean;
  has_passive_intermediate_station: boolean;
  has_active_intermediate_station: boolean;
  has_human_in_workspace: boolean;
  has_floor_transitions: boolean;
  footprint_xy_max_m: [number, number] | null;
  upright_dot_threshold: number | null;
  human_safety_distance_m: number | null;
}

type StructuralTag = `${"isaac" | "cad" | "user"}:${string}`;
```

### Validation rules

1. `pattern_hint ∈ enum`. Reject unknown.
2. Every `structural_tags[]` matches `^(isaac|cad|user):[a-z0-9_]+(\.[a-z0-9_]+)*$`
   AND appears in `workspace/vocabulary/structural_tags.registry.json` with
   `status: "active"`.
3. Cross-feature consistency: `has_color_routing == true ⇒ routing_axis == "color"`.
4. `pattern_hint = "custom"` REJECTED.

### Sharpest critique (Agent A's own)

**The IR's discipline propagates value only if every consumer enforces structural
dispatch instead of conditional flags.** A typed `intent` with a god-object
verifier is half a victory. `simulate_traversal_check` (per session-handoff §3)
already grew an `require_upright` flag for CP-05; adding `require_color_routing`
+ `require_human_safety` etc. makes that function a god-object.

**Required co-discipline**: when implementing the IR, also restructure
`simulate_traversal_check` and `verify_pickplace_pipeline` into **a registry of
per-feature checks dispatched from `intent.structural_features`**. CP-05's
upright check becomes `dispatch[has_orientation_requirement]`. CP-03's color
routing becomes `dispatch[has_color_routing]`. Same pattern as
`project_isaac_assist_typed_resolvers` — atomic, additive, named.

The IR is structured but consumers branching on string-tags would render the
discipline cosmetic. **Land both or neither.**

---

## Question B — Role binding protocol

### Recommended shape: three-stage propose → ratify → execute

```
modality.emit(LayoutSpec)
       ↓
canonical_pipeline.match(LayoutSpec)
       ↓
role_binder.ratify(template, LayoutSpec)   ← deterministic, the new component
       ↓
   ├── ok  → execute_template_canonical(template, role_bindings)
   └── err → return structured error to caller
```

The ratify step is a **pure function** over `(template.roles, layout_spec.objects,
layout_spec.bindings)`. Three jobs: validate role.constraints, validate
cardinality, check soft positional constraints. Returns
`RatifyResult { status: "ok" | "needs_choice" | "rejected", chosen, diagnostics, unbound_required }`.

Mirrors `execute_template_verify` (`canonical_instantiator.py:281-323`) — verify
is a deterministic gate the LLM never overrules.

### Why single-shot binding at modality emission is wrong

At LayoutSpec-emission time the template is not yet known (retrieval is downstream).
Modalities emit a **modality-internal role-hint** in controlled vocabulary —
`{role_hint: "robot_primary" | "robot_secondary" | "conveyor_input" | "destination"}`.
The ratifier maps modality role-hints to template role names through a fixed
translation table.

### Why negotiated/iterative binding is wrong

Kit RPC is single-tenant (`feedback_isaac_assist_kit_concurrency`). Multi-turn
negotiation between modality and pipeline opens partial-state failure modes.
**One ratify call. On failure, structured error to user.**

### Auto-binding waterfall (when modality didn't bind explicitly)

1. **Cardinality-trivial**: `expected_count == 1` AND exactly one matching object → bind. Most CP-N templates fall here for primary_conveyor, primary_destination.
2. **Single-axis ordering** (the key trick for multi-robot CP-02): templates declare a `disambiguator` field — `"smaller_x_first"`, `"first_listed"`, etc. **The disambiguator is part of the template, owned by the template author, never inferred at runtime.**
3. **Cardinality-multiple, role.unordered=true** (workpieces): bind in modality emission order.
4. **Insufficient information** → ratifier returns `needs_choice`, UI surfaces underspecified roles to user. **Only place ambiguity is resolved by something other than deterministic code.**

### Why never LLM-mediate the binding decision

The temptation to say "LLM, here are two robots and a CP-02 template, pick which
is primary" **is exactly the spec_generator pattern reverted on 2026-04-19**.
Open Q E documents that even when the directive enumerates exact paths, the LLM
hallucinates `/World/Bin/Floor`-style sub-paths. **A model that ignores listed
paths cannot be trusted to pick role assignments from listed objects.**

The disambiguator-in-template approach is uglier in code but matches the
deterministic-guard principle.

### Conflict resolution — five-rule precedence ladder

Evaluated in order; first match wins:

1. **User explicit binding wins over everything.** `bindings[role].source == "user_explicit"` — ratifier accepts without question.
2. **Template positional constraint wins over modality binding.** Bound robot fails reach constraint → reject with structured error.
3. **Most recent edit wins among modality-derived bindings.** Sketch then text-prompt edit → text-prompt wins by timestamp.
4. **Higher-confidence modality wins on tie.** Drag-drop=1.0 always beats sketch=0.7.
5. **Refuse to auto-resolve cross-modality semantic disagreement.** Two agent-derived bindings disagree → `needs_choice`. Cost of asking is one turn; cost of wrong is wrong-scene build.

### Why Open Q E is *better* surface than path-listing

- Paths are strings the LLM can mutate (`/World/Bin → /World/Bin/Floor`).
- Role bindings are structured map entries; LLM never sees role names as strings.
- The execute path doesn't pass through the LLM at all — `execute_template_canonical` runs the substituted template code, not LLM tool calls.

The Open Q E failure mode requires the LLM to *write* a path. The role-binding
architecture **removes the LLM from the writing path entirely.** Strictly
stronger surface.

### Text-prompt is the hardest case — choose "just enough" binding

Three options, only one is project-aligned:

- **Option A — "just enough"**: text-prompt binds only `intent`. NOT `objects`, NOT bindings. Canonical's authored positions are ground truth; T2 substitutes parameters. **No role-binding decision happens for text-prompt at all.**
- **Option B — two-step LLM**: extract intent → match → second LLM call binds roles. Two failure surfaces.
- **Option C — soft bindings re-resolved**: ambiguous in practice; resolver becomes LLM in disguise.

**Project history strongly favors Option A.** Hard-instantiate succeeds via
determinism after match; T2 substitutes counts and class names without touching
positions or roles; Open Q E shows LLM ignores explicit directives.

### Validation produces three rejection types with structured failure UX

```typescript
type RatifyError =
  | { kind: "wrong_class",     role, expected: string[], got: string }
  | { kind: "constraint_fail", role, constraint, diag: string }
  | { kind: "unbindable",      role, candidates: ObjectId[] | null };
```

- **`wrong_class`**: hard reject pre-exec. User re-binds. NO T5 fallback.
- **`constraint_fail`**: hard reject pre-exec. User repositions OR drop to T5.
- **`unbindable`**: REQUIRED role has no candidate. Drop to T5 free-form.

### `rebind_role` as first-class tool

```
rebind_role(role_name: str, object_id: str | path: str) → RatifyResult
```

Lives in `ALLOWED_AFTER_INSTANTIATE`. Mutates `LayoutSpec.bindings`, persists,
calls `ratify`. Re-runs `execute_template_verify` on success. **Why a tool, not
chat-parsed intent**: feedback `feedback_diligence_no_false_positives` warns
explicitly against parsing user intent from chat by regex/LLM. Tool args fail
validation; chat parsing fails silently.

### Sharpest critique (Agent B's own)

**Role-based templates collapse the wrong axis.** They assume the template's
pattern is the unit of variation and bound objects are the variables. True for
CP-01..CP-05. But the dominant failure mode is the *opposite*: same objects in
a different recipe. Two Frankas + two conveyors + one bin can be CP-02 OR a
single-station-with-redundant-robots OR a mirrored-dual-station OR a hand-off
rendezvous. **Roles don't disambiguate which recipe; structural_tags do, and
intent does.** Roles only help once the recipe is chosen.

A protocol that perfects role-binding while leaving template-selection a soft
cosine-similarity decision will still build wrong scenes — just with confidently
bound roles in them.

**This work is necessary but not sufficient.** It removes LLM-mediated decisions
from the build path (closes regex-family door). But the project should not
expect end-to-end improvement until template-ranking + structured-tag
controlled vocabulary (Question A) lands alongside it. **Land both or neither.**

---

## Question C — Native Kit panel feasibility

### Verdict: hybrid decoupled (browser-tab + Kit canvas-mirror panel)

Not native (omni.ui.scene CAD canvas). Not webview-in-Kit (doesn't exist as
productized component). Decoupled — chrome in `omni.ui`, canvas in browser tab,
**non-interactive preview panel inside Kit** that updates via SSE.

### Why not native

`omni.ui.scene` was designed as a **3D viewport overlay framework** for
manipulators above selected prims, not as a screen-space CAD canvas. Specific
blockers:

| Capability | Status |
|---|---|
| Per-primitive hit testing | Yes via `gestures` |
| Color with alpha | Yes via ABGR |
| Wireframe vs filled Rectangle | Yes |
| **Dashed lines / custom stroke patterns** | **NO** — `Line` and `Curve` expose only basic params. Workaround: emit many short Lines — viable but doubles primitive count, looks worse at sub-pixel zoom |
| **Konva-Transformer equivalent** | **Build-it-yourself** — corner/edge handles + rotation handle + dashed border + hit-testing per handle |
| **Smart-guide infrastructure** | **Build-it-yourself** — every drag delta walks all objects, computes alignments, evicts last frame's guides, redraws |
| **Standalone-window pattern** | **Untested** — public forum thread on `with window.get_frame(): scene_view = sc.SceneView()` reports "unable to see anything" with no resolution |

Effort for native canvas relative to Konva: **~3-4× larger** for comparable
quality. Konva ships Transformer, dashed strokes, hit-region testing, layer
system, event delegation as built-ins. omni.ui.scene requires reimplementing
each against an undocumented frontier.

### Why not webview-in-Kit

NVIDIA staff (Richard3D, October 2025): *"Unfortunately, this is something
that is still in long term development."* `omni.ui.WebViewWidget` does not
exist. `omni.kit.widget.browser` is a thin in-app help viewer, not a
general-purpose Chromium with bidirectional JS↔Python.

The supported NVIDIA pattern goes the *other direction*: `web-viewer-sample`
streams Kit content TO a browser. Kit-as-server, browser-as-client. Inverse of
what hybrid-webview-in-Kit would require.

### What the chrome can do (`omni.ui` + `omni.ui.Workspace`)

For non-canvas chrome — **omni.ui is sufficient**:

- Layout containers (HStack, VStack, ZStack, ScrollingFrame) — already used in `chat_view.py`
- Inputs (StringField, FloatField, ComboBox, RadioCollection) — sufficient for properties panel
- Toolbar w/ icon-only buttons + tooltips — exact pattern `chat_view.py:600-638` uses
- Modals and popups — `ui.Window(flags=...)` (already used)
- Right-click context menus — `omni.ui.Menu` / `MenuItem`
- **Resizable splitter — `omni.ui.Workspace.set_dock_id_width()`** ([Workspace docs](https://docs.omniverse.nvidia.com/kit/docs/omni.ui/2.26.5/omni.ui/omni.ui.Workspace.html))
- Persistence across Kit restarts via `dump_workspace()` / `restore_workspace()`

What's missing vs web SPA: no `display: flex`, no CSS animations (must
hand-write timer interpolation; chat_view.py's `_apply_scale_to_all_async` is
the closest precedent), **no documented drag-from-palette pattern across
windows** (the load-bearing UX for `§5.3.1`).

### Hybrid decoupled architecture

| Layer | Tech | Rationale |
|---|---|---|
| Kit chrome — chat panel, status, "Open Floor Plan" button | `omni.ui` (extends current `chat_view.py`) | Already exists; styling baseline; same window |
| Floor Plan canvas — the CAD work | Konva SPA in default browser, served from FastAPI at `/floorplan` | The only stack with proven CAD-grade affordances |
| **Kit "canvas mirror" panel — non-interactive preview** | `omni.ui` Window with `ui.Image` driven by SVG/PNG snapshots from FastAPI on every commit | Lets chat + 3D viewport + canvas-preview be visible simultaneously inside Kit |
| State bus | FastAPI + SSE (existing) | Same channel as chat live-progress |

**Communication**:
- Browser → Python: HTTP POST `/floorplan/commit` writes `LayoutSpec`. SSE broadcasts.
- Python → Browser: SSE pushes `floor_plan/proposed` events; SPA renders ghosts.
- Kit ↔ Kit: existing `KitRPCServer` handles 3D-viewport mutations on Sync.

Mirror panel is small new feature: `ui.Image(self._png_path)` re-set on SSE.
Editing happens in browser tab; **the three-pane split-view Anton asked for is
preserved as far as Kit chrome reads.**

### Cross-version compatibility — free

`exts/isaac_5.1/.../chat_view.py` and `exts/isaac_6.0/.../chat_view.py` are
**byte-identical** (verified). Both Kit version trains ship Workspace with the
needed APIs. The webview decision moves canvas out of Kit blast radius entirely.

### Sharpest critique (Agent C's own)

**The canvas-mirror panel is a degraded experience.** Anton's stated framing is
*interactive* canvas, not a thumbnail. A read-only mirror inside Kit is not "the
canvas in Kit" — it's a screenshot. To nudge a robot 5cm right, the user finds
the browser, focuses, drags, alt-tabs back. On dual-display the friction is
small but real; on single display worse than today's experience.

**Deeper failure mode — feature creep**: once mirror exists, "could you make it
pannable?" → "pannable AND zoomable?" → "could it select objects?" → slide back
into native-canvas territory the long way around. Discipline requires treating
mirror as **strictly read-only forever** and accepting alt-tab cost.

If that's not acceptable, the answer collapses to native — at the cost of a
project several times larger resting on undocumented Kit corners.

**Second risk — preview-refresh stutter**: SSE round-trip (commit → server
render → PNG → SSE → `ui.Image` reload) lags ~100-300ms behind the browser
canvas. Not correctness, but tells against the "feels like one app" pitch.
Mitigation: throttle to ~3-5 emissions/sec, accept staleness; or render preview
client-side and ship serialized SVG.

---

## Cross-cutting themes

Three findings appear across all three agents:

### 1. Land all three or none

- Agent A: structured intent IS the regex-killer, but consumers must dispatch on features (verifier registry); else discipline is cosmetic.
- Agent B: role-binding closes LLM-from-build-path; but template-selection still soft until structured intent (A) lands.
- Agent C: hybrid mirror requires chrome (omni.ui) + state bus (SSE); preserves split-view; but requires user discipline (read-only forever).

**Each is necessary; none is sufficient alone.** The multimodal foundation is
the union: structured intent + deterministic ratification + decoupled canvas
with Kit mirror.

### 2. Verifier decomposition is the hidden requirement

Agent A surfaces it; Agent B implies it. The current `simulate_traversal_check`
and `verify_pickplace_pipeline` are growing flag-fields per pattern. Without
restructuring as registry-dispatch on `structural_features`, the IR's discipline
doesn't reach the verifier and regex-family creeps in via flags.

This is the **typed-resolver pattern from `project_isaac_assist_typed_resolvers`
applied to verifiers**. Atomic + additive + named. Same mental model.

### 3. Text-prompt should not produce role bindings

This emerges across A and B. Text-prompt LLM extracts `intent` (structured,
structured_tags from controlled vocabulary). It does NOT produce objects or
bindings. Canonical pipeline supplies positions; disambiguators supply role
bindings. **The LLM never makes a binding decision on the build path.**

This is the cleanest articulation of the harness-deterministic principle for
multimodal: the LLM intersects the build path at exactly one place — intent
extraction from natural language — and that intersection produces a typed
artifact with no string-substitution downstream.

---

## Recommended sequence

The agents converge on this implementation sequence:

1. **Define LayoutSpec + intent schema** per Question A (three-layer vocabulary).
2. **Extend canonical templates with `roles` + `disambiguator` + `code_template` (using `{{role.path}}` placeholders)** per Question B.
3. **Restructure verify-pipeline as registry-dispatched checks per `structural_features`** per Question A's hidden requirement.
4. **Build ratify component as pure function** per Question B.
5. **Refactor text-prompt modality** to produce LayoutSpec.intent only — no objects, no bindings.
6. **Add structural-filter-first retrieval** per Question A's protocol.
7. **`rebind_role` tool in ALLOWED_AFTER_INSTANTIATE** per Question B.
8. **For canvas-modality work** — hybrid decoupled per Question C: extend chrome in omni.ui chat extension, add canvas-mirror panel with SSE-driven `ui.Image`, build Konva SPA as planned in original spec but treat browser tab as the editing surface.

Steps 1-6 happen *before* any visual canvas tool. They establish the
multimodal-foundation that all modalities (text, sketch, drag-drop, photo,
voice, viewport-edit) target.

---

## What this changes about the original floor plan spec

Many of the original spec's decisions become moot or change shape:

| Original spec section | Status after this analysis |
|---|---|
| §3.5 LayoutSpec/FloorPlan schema | **Replace with three-layer intent + role-based templates** |
| §6.6 retrieval via auto-query NL synthesis | **Drop — replaced by structural-filter-first retrieval** |
| §8.3 compact summary in `_format_floor_plan_for_llm` | **Replace with structured JSON dump (no NL)** |
| §8.5 free-text notes parsed by LLM at runtime | **Replace with structured `flags: { is_anchor, priority_first, ... }` typed enum + free-text as observability only** |
| §8.6 canonical-match suggestion + 0.72 threshold | **Drop — retrieval is structurally-gated; no separate suggestion classifier** |
| §9.2 layout-intent regex detection | **Drop — explicit user button only; no auto-detect** |
| §9.4 `_patch_verify_args` heuristic | **Replace with template-declared `roles` + ratify** |
| §2.1 "browser SPA only" hosting | **Augment with omni.ui Kit chrome + canvas-mirror panel** |
| §5.1.2 Tab system (Chat ↔ Floor Plan ↔ 3D Scene) | **Replace with omni.ui.Workspace docked panels (split-view)** |
| Phasing 1→8 | **Re-ordered: IR + role-based templates + verifier registry FIRST; canvas modality LAST** |

The original spec is salvageable but not as written. The implementation
sequence above is the actual roadmap.

---

## Honest summary

The three Opus agents converge cleanly: structured intent + deterministic
ratification + decoupled canvas-with-mirror is the multimodal foundation
architecture that respects every project principle (harness deterministic, no
regex family, smart-on-bounded-domain LLM, role-binding never LLM-mediated on
build path).

Each agent flagged that their question alone is necessary-not-sufficient. The
package matters. Implementing the IR without the ratifier or the verifier
restructure would leave regex-family pathways open. Implementing the canvas
without the IR foundation would re-create the floor-plan-spec's pattern of
hidden classification fragility.

The architectural cost is real. The discipline that makes this work is the
project's existing discipline (deterministic primitives, typed resolvers,
hard-instantiate path). This proposal extends that discipline to multimodal
input rather than introducing a new philosophy.

The next session evaluates this and decides scope.
