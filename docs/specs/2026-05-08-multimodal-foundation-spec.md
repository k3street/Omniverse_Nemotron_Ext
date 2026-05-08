# Multimodal Foundation Spec — v2

Authored 2026-05-08. **Supersedes** `2026-05-08-floor-plan-tool-spec.md`.

This document specifies the multimodal layout-input architecture for Isaac
Assist. It is the synthesis of:

- Original floor-plan spec (`2026-05-08-floor-plan-tool-spec.md`)
- Six-agent Opus critique (`2026-05-08-floor-plan-tool-opus-critique.md`)
- Working draft on multimodal IR (`2026-05-08-multimodal-foundation-working-draft.md`)
- Three-agent Opus deep analysis (`2026-05-08-multimodal-opus-deep-analysis.md`)

The framing is no longer "build a floor plan tool." It is "establish the
multimodal foundation that text, sketch, drag-drop, photo, voice, and
viewport-edit modalities all converge through, with the existing canonical
pipeline as the executor." The drag-drop canvas (formerly "floor plan tool")
becomes one modality among several, and the foundation ships before the canvas.

For evaluation by the next session before any implementation begins.

---

## 0. Reading Guide

The spec has 22 sections organized in three blocks:

- **Block A (§§1-8): The foundation.** What the architecture is and why.
  Independent of any specific modality.
- **Block B (§§9-13): The modalities.** How each input modality (text, drag-drop
  canvas, sketch, voice, photo, viewport-edit) targets the foundation. Most are
  short; canvas is detailed because it is the most complex.
- **Block C (§§14-22): Operational concerns.** Persistence, failure cascade,
  testing, telemetry, security, schema migration, sequencing.

The implementation sequence in §20 is the actual roadmap. Block A (§§3-6) lands
first; modalities follow against the stable foundation; canvas modality is
last, not first.

---

## 1. Vision and Design Principles

### 1.1 What this is

A foundation layer that lets multiple input modalities converge into the
canonical-pipeline. The user types a prompt, draws a sketch, drags objects on
a canvas, photographs a real layout, speaks an instruction, or edits the 3D
viewport directly. All produce the same intermediate representation. The
canonical pipeline executes against that representation deterministically.

### 1.2 What this is not

- Not a single new tool — it is an architectural layer
- Not a replacement for the existing canonical pipeline — it is the layer above
- Not a new LLM surface — it removes LLM-mediated decisions from the build path
- Not a multi-user system — single-user, multimodal future-proofs to multi-user

### 1.3 Core design principles

**P1 — Regex-family lives at every lossy translation point.** Eliminate the
lossy steps and the fragility class disappears. Structured input must reach
structured output without going through natural language as an intermediate.
Roles are first-class; names are display-only.

**P2 — Harness deterministic, LLM smart on bounded domain.** The LLM
intersects the build path at exactly one place — extracting structured intent
from natural language. After that, every decision is deterministic: retrieval
filter, role ratification, template substitution, sandboxed execution,
verifier registry-dispatch.

**P3 — Land all coupled pieces or land none.** The IR, the role-based
templates, the ratifier, and the verifier registry are interdependent. Half
the system is worse than none — partial implementations leave regex pathways
open by mistake. The implementation sequence in §20 reflects this.

**P4 — User edits are committed; agent edits are proposed.** Asymmetric trust.
User intent in direct manipulation is unambiguous. Agent mutations require
explicit user confirmation via UI mechanic, not chat-text dialog.

**P5 — Status visible at all times.** Persistent status indicators, structured
diagnostics, no silent operations. Anton's "användaren vet vad som pågår at all
times."

**P6 — Smooth mode-coexistence.** Modalities coexist in split-view, not modal
tabs. User sees chat + canvas-mirror + 3D viewport simultaneously. Anton's
"extremt smidigt och smooth."

**P7 — Lossless persistence.** Every change is captured at field granularity
with revision tracking; no lost-edit windows.

---

## 2. Architecture Overview

```
┌──────────────────────────────────────────────────────────────────────────┐
│                          MODALITY PRODUCERS                              │
│  text-prompt LLM │ drag-drop canvas │ sketch VLM │ voice STT │ photo VLM │
│                                  │                                       │
│                                  ▼                                       │
│                          ┌──────────────┐                                │
│                          │  LayoutSpec  │  ◄── the IR (§3)               │
│                          │     JSON     │      structured intent +       │
│                          │              │      typed features +          │
│                          │              │      namespaced tags +         │
│                          │              │      objects? + bindings? +    │
│                          │              │      parameters + source meta  │
│                          └──────┬───────┘                                │
└─────────────────────────────────┼────────────────────────────────────────┘
                                  │
                                  ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                          FOUNDATION LAYER                               │
│                                                                         │
│   ┌─────────────────┐     ┌──────────────┐     ┌──────────────────┐   │
│   │ retrieve()      │ ───►│  ratify()    │ ───►│ execute_template │   │
│   │ structural-     │     │  pure func   │     │ _canonical()     │   │
│   │ filter-first    │     │  template +  │     │ + substitute     │   │
│   │ §8              │     │  spec → bind │     │ role paths       │   │
│   └─────────────────┘     │  §5          │     │ §4               │   │
│                           └──────────────┘     └────────┬─────────┘   │
│                                                          │              │
│                                                          ▼              │
│                              ┌──────────────────────────────────────┐  │
│                              │ verify_registry()                    │  │
│                              │ feature-dispatched checks per        │  │
│                              │ structural_features (§6)             │  │
│                              └────────┬─────────────────────────────┘  │
│                                       │                                 │
│                                       ▼                                 │
│                          ┌────────────────────────────┐                │
│                          │ Kit RPC + USD stage state  │                │
│                          └────────────────────────────┘                │
└─────────────────────────────────────────────────────────────────────────┘
```

The foundation is the box in the middle. Modalities are interchangeable
producers. The canonical pipeline is the executor. **No NL synthesis on the
build path.** **No LLM decisions after intent extraction.**

---

## 3. The Intermediate Representation: `LayoutSpec`

### 3.1 Top-level schema

```typescript
interface LayoutSpec {
  version: "1.0";

  intent: Intent;                     // §3.2 — the structured representation
  objects?: TypedObject[];            // §3.3 — present when modality has positions
  constraints?: Constraint[];         // §3.4 — present when known
  bindings?: RoleBindings;            // §4.4 — present when modality bound roles
  parameters: Record<string, JSONValue>;  // §3.5 — T2 substitution targets

  source: {
    modality: Modality;
    confidence: number;               // 0..1
    timestamp: string;                // ISO 8601
    raw_input?: unknown;              // optional — for re-derive
    metadata: Record<string, unknown>;
  };

  revision: number;                   // §15 — monotonically increasing per session
}

type Modality =
  | "text" | "sketch" | "drag_drop" | "photo" | "voice" | "viewport";
```

### 3.2 Intent — three-layer vocabulary (closed enum + typed features + tags)

This is the regex-family-killer. **The working draft's `StructuralTag = string`
collapses three concerns; this schema separates them.**

```typescript
interface Intent {
  // L0 — closed enum, version-bumped, success-criterion-discriminated
  pattern_hint: PatternHint;

  // L0 — fixed integer counts; additive entity classes via minor bump
  counts: Counts;

  // L1 — typed booleans + numerics; additive only; defaults absorb missing
  structural_features: StructuralFeatures;

  // L2 — namespaced strings; registry-validated; format regex on tag SHAPE
  // not on tag CONTENT
  structural_tags: StructuralTag[];
}

type PatternHint =
  | "pick_place"      // success: workpiece in destination, at rest
  | "sort"            // success: workpiece in CORRECT-class destination
  | "constraint"      // ⚠ DELIBERATELY ABSENT — see below
  | "reorient"        // success: workpiece in destination AND oriented
  | "navigate";       // success: mobile platform at goal pose
// "custom" is REJECTED. Once it exists, it becomes the dumping ground.

interface Counts {
  robots: number;
  conveyors: number;
  bins: number;
  cubes: number;
  sensors: number;
  humans: number;
  // Additive: new entity classes require minor schema bump.
}

interface StructuralFeatures {
  // n_*: cardinality of structural elements (distinct from counts —
  // counts are entity-class instances; n_* are role-positions)
  n_robot_stations: number;
  n_handoffs: number;
  n_destinations: number;

  // destination shape
  destination_kind: "single_bin" | "n_bins_routed" | "shelf" | "fixture";
  routing_axis: null | "color" | "size" | "shape" | "label";

  // capability flags
  uses_conveyor_transport: boolean;
  uses_navigation: boolean;
  has_color_routing: boolean;
  has_orientation_requirement: boolean;
  has_bounded_footprint: boolean;
  has_passive_intermediate_station: boolean;
  has_active_intermediate_station: boolean;
  has_human_in_workspace: boolean;
  has_floor_transitions: boolean;

  // numeric facts (null when N/A)
  footprint_xy_max_m: [number, number] | null;
  upright_dot_threshold: number | null;
  human_safety_distance_m: number | null;
}

type StructuralTag =
  `${"isaac" | "cad" | "user"}:${string}`;
```

### 3.3 Discriminating rule for `pattern_hint`

> **`pattern_hint` is the simulation success-criterion shape, not the surface
> task description.**

CP-01 (single pickplace), CP-02 (multi-robot pickplace), CP-04 (compact
pickplace) all share success criterion "cube xy ∈ bin, at rest" → all
`pick_place`. CP-04's bounded-footprint is a **structural feature**, not a
distinct pattern.

CP-03 (color sort): "cube xy ∈ correct-color bin" → `sort`.
CP-05 (reorient): "cube xy ∈ bin AND `cube.up · world_up > 0.95`" → `reorient`.

If two scenes have identical success criteria, they share `pattern_hint` even
if they look superficially different. If they have different success criteria,
they get different patterns.

### 3.4 Why `"custom"` is rejected from the enum

Once a catch-all exists, every borderline case ends up there. The retrieval
filter cannot use it (no template would tag itself `custom`). It signals
"give up classifying" — which is regex-family fragility under another name.

Scenes that don't match any closed pattern fall to **T5 free-form** at
retrieval time. T5 doesn't read `pattern_hint`. There is no use for `"custom"`.

### 3.5 Namespaced tags — format regex only, never content regex

```
Tag format:    ^(isaac|cad|user):[a-z0-9_]+(\.[a-z0-9_]+)*$
```

Examples:
- `isaac:transport.conveyor`
- `isaac:robot.fixed_base.arm`
- `isaac:topology.linear_pipeline`
- `isaac:invariant.cube_upright`
- `isaac:routing.semantic_label.color`
- `cad:imported.fusion360`
- `user:annotation.priority_first`

Validation rules:
1. **Format-regex** (above): well-formedness only. Not content classification.
2. **Registry membership**: `workspace/vocabulary/structural_tags.registry.json`
   lists active tags. Unregistered `isaac:` and `cad:` tags reject; `user:`
   tags pass through but are **ignored by retrieval and instantiation** —
   they are observability/metadata only.
3. **Append-only registry**: removing a tag from registry forbidden. Mark
   `status: "deprecated"` instead. Old data stays valid.

### 3.6 What `parameters` is for

```typescript
parameters: Record<string, JSONValue>
```

`parameters` is for T2 substitution targets — values that vary the same template's
behavior without changing which template matches. Examples:
- `n_cubes: 4` → CP-01's loop iterates 4 times
- `belt_speed_m_s: 0.2` → conveyor surface velocity substituted

**Test**: *Does varying it change which template should match?* Yes →
`structural_feature` (or `count`). No → `parameter`.

**Test**: *Does the verifier read it?* Yes → `structural_feature`. No →
`parameter`.

`parameters` are NOT read by retrieval. They are instantiation-time substitutions.

### 3.7 Validation rules

A `LayoutSpec` is rejected at boundary if:
1. `intent.pattern_hint ∉ enum`
2. Any `structural_tags[]` element fails format-regex OR is unregistered
   (and not in `user:` namespace)
3. `intent.structural_features.has_color_routing == true` AND
   `intent.structural_features.routing_axis != "color"` (cross-feature inconsistency)
4. `intent.structural_features.has_bounded_footprint == true` AND
   `intent.structural_features.footprint_xy_max_m == null`
5. `intent.counts.robots == 0 AND pattern_hint != "navigate"` — warning, not error
6. `version` not supported by current reader (triggers schema migration; §18)

Validation lives in `service/isaac_assist_service/multimodal/validate_layout_spec.py`.

### 3.8 `objects[]`, `constraints[]`, `bindings` — optional shapes

These are present when the modality has positional information (drag-drop,
sketch, photo, viewport-edit) and absent when the modality has only intent
(text, voice).

When `objects[]` is absent, the canonical pipeline supplies positions from the
template's `code_template` defaults. When `bindings` is absent, the ratifier
runs the auto-binding waterfall (§5.2).

`TypedObject` carries class, position, rotation, size, optional notes,
metadata, role_hint (if modality bound it); details in §11.3.

`Constraint` carries distance, alignment, angle, bounds, reach types; same as
prior spec §3.4 with no changes.

---

## 4. Role-based Canonical Templates

### 4.1 Templates declare named roles

Replace hardcoded prim paths in CP-N templates with role-based bindings:

```jsonc
{
  "id": "CP-02",
  "intent": {
    "pattern_hint": "pick_place",
    "structural_features": {
      "n_robot_stations": 2,
      "n_handoffs": 1,
      "destination_kind": "single_bin",
      "uses_conveyor_transport": true
    },
    "structural_tags": [
      "isaac:transport.conveyor",
      "isaac:robot.fixed_base.arm",
      "isaac:topology.linear_pipeline"
    ]
  },

  "roles": {
    "primary_robot": {
      "constraints": ["franka_panda", "ur5e", "kinova_gen3"],
      "expected_count": 1,
      "required": true,
      "disambiguator": "smaller_x_first"
    },
    "secondary_robot": {
      "constraints": ["franka_panda", "ur5e", "kinova_gen3"],
      "expected_count": 1,
      "required": true,
      "disambiguator": "larger_x_first"
    },
    "input_conveyor": {
      "constraints": ["conveyor"],
      "expected_count": 1,
      "required": true,
      "disambiguator": "smaller_x_first"
    },
    "transfer_conveyor": {
      "constraints": ["conveyor"],
      "expected_count": 1,
      "required": true,
      "disambiguator": "larger_x_first"
    },
    "primary_destination": {
      "constraints": ["bin"],
      "expected_count": 1,
      "required": true
    },
    "workpieces": {
      "constraints": ["cube"],
      "min": 1,
      "max": 4,
      "param_name": "n_cubes",
      "unordered": true
    }
  },

  "code_template": "...uses {{primary_robot.path}}, {{secondary_robot.path}}, {{primary_destination.path}}, {{input_conveyor.path}}, {{transfer_conveyor.path}}, {{workpieces[i].path}}...",

  "verify_args_template": "...also role-based...",

  "settle_state_template": "...role-based..."
}
```

### 4.2 Disambiguators — deterministic substitute for LLM tie-breaking

A `disambiguator` is a string identifier for a deterministic ordering function:

| Disambiguator | Description |
|---|---|
| `smaller_x_first` | Sort candidates by `position.x` ascending; bind first |
| `larger_x_first` | Descending |
| `smaller_y_first` | y-axis ascending |
| `larger_y_first` | y-axis descending |
| `nearest_to_origin` | sqrt(x² + y²) ascending |
| `farthest_from_origin` | descending |
| `first_listed` | LayoutSpec.objects emission order |

**The disambiguator is part of the template, owned by the template author,
never inferred at runtime.** This is the deterministic substitute for "LLM
picks which robot is primary" — that pattern is the spec_generator pattern
that was reverted on 2026-04-19.

### 4.3 `code_template` uses role placeholders

Existing hard-instantiate code (canonical_instantiator.py:326) already supports
`{{name}}` substitution via `substitute_template_params`. Extend the substitution
to bind role paths:

Before:
```python
robot_wizard(robot_name="franka_panda", dest_path="/World/Franka",
             position=[0, 0, 0.75], orientation=[0.7071, 0, 0, 0.7071])
```

After:
```python
robot_wizard(robot_name={{primary_robot.class}},
             dest_path={{primary_robot.path}},
             position={{primary_robot.position}},
             orientation={{primary_robot.orientation}})
```

Same for `verify_args.stages[].robot_path`, `simulate_args.cube_path`,
`settle_state.cubes` keys, `setup_pick_place_controller(source_paths=...)`.
Every literal prim path becomes a role reference. **No string substitution at
runtime; the substitute happens once at template-instantiation time, on
ratified bindings.**

### 4.4 `RoleBindings` shape

```typescript
interface RoleBindings {
  [role_name: string]: {
    object_id: string;          // refers to LayoutSpec.objects[].id
    source: BindingSource;
    confidence: number;         // 0..1
    timestamp: string;
  };
}

type BindingSource =
  | "user_explicit"        // drag-drop right-click → "set as primary_robot"
  | "modality_emitted"     // modality producer emitted role_hint
  | "disambiguator"        // ratifier ran auto-binding
  | "user_correction";     // chat-driven rebind_role tool call
```

---

## 5. The Ratify Component

### 5.1 Three-stage protocol: propose → ratify → execute

```
modality.emit(LayoutSpec)
       ↓
canonical_pipeline.match(LayoutSpec.intent)
       ↓
role_binder.ratify(template, layout_spec)   ← deterministic
       ↓
   ├── ok          → execute_template_canonical(template, role_bindings)
   ├── needs_choice → return to UI for user disambiguation
   └── rejected     → structured error to caller
```

`ratify` is a pure function over `(template.roles, layout_spec.objects,
layout_spec.bindings)`. Returns:

```typescript
type RatifyResult =
  | { status: "ok",
      bindings: RoleBindings,
      diagnostics: BindingDiagnostic[] }
  | { status: "needs_choice",
      partial_bindings: RoleBindings,
      ambiguous_roles: AmbiguityRecord[],
      diagnostics: BindingDiagnostic[] }
  | { status: "rejected",
      errors: RatifyError[],
      diagnostics: BindingDiagnostic[] };

type RatifyError =
  | { kind: "wrong_class", role, expected: string[], got: string }
  | { kind: "constraint_fail", role, constraint, diagnosis: string }
  | { kind: "unbindable", role, candidates: string[] | null };
```

### 5.2 Auto-binding waterfall

When a modality didn't bind a role explicitly, the ratifier auto-binds in
**strict priority order**:

1. **Cardinality-trivial**: `expected_count == 1` AND exactly one object
   matches `role.constraints` → bind. Fully deterministic.
2. **Disambiguator**: ordering function from §4.2 applied to candidates → bind
   in order. Fully deterministic.
3. **Cardinality-multiple, `role.unordered == true`**: bind in modality
   emission order.
4. **Insufficient information**: return `status: "needs_choice"`. UI surfaces
   ambiguity to user. **The only place ambiguity is resolved by something other
   than deterministic code.**

**No LLM-mediated binding decision at any stage.** The LLM does not look at the
candidate object list and pick a role. The disambiguator does.

### 5.3 Five-rule conflict precedence ladder

When multiple binding sources disagree, evaluated in order; first match wins:

1. **User explicit binding wins over everything.** Ratifier accepts without
   question; only validates class compatibility.
2. **Template positional constraint wins over modality binding.** Bound robot
   fails reach constraint → reject with `constraint_fail`.
3. **Most recent edit wins among modality-derived bindings.**
4. **Higher-confidence modality wins on tie.** Drag-drop=1.0 always beats
   sketch=0.7.
5. **Refuse to auto-resolve cross-modality semantic disagreement.** Two
   agent-derived bindings disagree → `needs_choice`. Cost of asking is one
   turn; cost of being wrong is wrong-scene build.

### 5.4 Validation produces three rejection types

| Kind | Behavior | Recovery |
|---|---|---|
| `wrong_class` | Modality bound a `cube` to `primary_robot`. Hard reject pre-exec. | User re-binds or modality re-runs |
| `constraint_fail` | Right class, but positional constraint violated. Hard reject. | User repositions or template flagged wrong → drop to T5 |
| `unbindable` | Required role has no candidate. | Drop to T5 free-form (canonical doesn't fit; underlying intent might still be expressible) |

### 5.5 `rebind_role` as first-class tool

```typescript
rebind_role(role_name: string, target: string)  // target = object_id or path
  → RatifyResult
```

- Lives in `ALLOWED_AFTER_INSTANTIATE`.
- Mutates `LayoutSpec.bindings[role_name]`, persists, calls `ratify`.
- On `ok`: re-runs `execute_template_verify` against new bindings (verify only,
  no rebuild by default — re-binding is a structural fix).
- On error: surfaces structured failure to LLM in normal tool-result shape.

**Why a tool, not chat-parsed intent**: per `feedback_diligence_no_false_positives`,
parsing user intent from chat by regex/LLM is exactly what the project moved
away from. A tool call is structured: wrong args fail validation; right args
do the right thing.

---

## 6. Verifier Registry — Feature-Dispatched Checks

### 6.1 The hidden requirement

The IR's discipline propagates value only if every consumer enforces structural
dispatch. Today's `simulate_traversal_check` and `verify_pickplace_pipeline`
grow flag-fields per pattern (`require_upright`, future `require_color_routing`,
future `require_human_safety`). Without restructuring, the verifiers become
god-objects and regex-family creeps in via flags.

### 6.2 Registry shape

```typescript
type CheckId = string;       // namespaced: "verify:reach", "simulate:upright_at_rest"

interface VerifierCheck {
  id: CheckId;
  applies_when: (features: StructuralFeatures) => boolean;
  run: (template: Template, bindings: RoleBindings, args: CheckArgs) => CheckResult;
}

type CheckResult =
  | { status: "pass", diagnostics: string[] }
  | { status: "fail", issues: Issue[] }
  | { status: "skipped", reason: string };

interface VerifierRegistry {
  form_gate: VerifierCheck[];      // pre-build / pre-canonical
  function_gate: VerifierCheck[];  // post-build, sim-running
}
```

### 6.3 Per-feature dispatch

Each check declares which `structural_features` activate it:

| Check | `applies_when` |
|---|---|
| `verify:reach` | `n_robot_stations > 0` |
| `verify:conveyor_active` | `uses_conveyor_transport == true` |
| `verify:controller_installed` | `n_robot_stations > 0` |
| `verify:cube_source_bridged` | `uses_conveyor_transport == true AND counts.cubes > 0` |
| `verify:footprint_within_bounds` | `has_bounded_footprint == true` |
| `verify:color_routing_consistent` | `has_color_routing == true` |
| `simulate:cube_delivered` | `pattern_hint == "pick_place" OR pattern_hint == "sort"` |
| `simulate:upright_at_rest` | `has_orientation_requirement == true` |
| `simulate:human_safety_zone` | `has_human_in_workspace == true` |

Adding CP-06 (collaborative): register a new check `simulate:human_safety_zone`
with `applies_when: features.has_human_in_workspace`. **No edit to existing
verifier code.** Existing CP-01..CP-05 unaffected because their feature flags
are absent/false.

### 6.4 Drop-in pattern for existing checks

`verify_pickplace_pipeline` (today: monolithic) becomes a thin wrapper:
```python
def verify_pickplace_pipeline(template, bindings, ...):
    features = template.intent.structural_features
    return REGISTRY.run_form_gate(template, bindings, features, args)
```

`simulate_traversal_check` likewise becomes:
```python
def simulate_traversal_check(template, bindings, ...):
    features = template.intent.structural_features
    return REGISTRY.run_function_gate(template, bindings, features, args)
```

The existing `require_upright` flag becomes an automatic dispatch:
`simulate:upright_at_rest` registers with `applies_when:
features.has_orientation_requirement`. CP-05 sets that feature; CP-01 doesn't.

### 6.5 Registry-managed via decorator

```python
@registry.form_gate(applies_when=lambda f: f.has_bounded_footprint)
def verify_footprint_within_bounds(template, bindings, args):
    bounds = template.intent.structural_features.footprint_xy_max_m
    # ... check every authored prim's xy bbox
    return CheckResult(...)
```

Adding new checks: write a function with the decorator. Registry auto-collects.
No edits to the central dispatcher.

This is the **typed-resolver pattern from `project_isaac_assist_typed_resolvers`
applied to verifiers**: atomic, additive, named.

---

## 7. The Modality Boundary Contract

Every modality producer obeys the same contract:

### 7.1 Contract

A modality is a function `produce: ModalityInput → LayoutSpec`. The output
must:
- Pass the validation rules in §3.7
- Use only registered structural_tags (or `user:` namespace for
  observability-only)
- Provide `source.modality`, `source.confidence`, `source.timestamp`
- Optionally provide `objects`, `constraints`, `bindings` if the modality has
  positional / role information; omit them otherwise

### 7.2 Modality reliability profile

| Modality | Confidence | Provides objects | Provides bindings | Notes |
|---|---|---|---|---|
| **drag-drop canvas** | 1.0 | Yes | User-explicit when set | UI is most reliable; user is the oracle |
| **viewport-edit** | 1.0 | Yes (read from existing prims) | Reverse-derived | When user manipulates 3D scene directly |
| **text-prompt LLM** | 0.7-0.9 | NO | NO | Just-enough binding (§9.1); produces only intent |
| **voice STT** | 0.5-0.8 | NO | NO | Routes through text-prompt path after transcription |
| **sketch VLM** | 0.4-0.7 | Yes (positions approximate) | Sometimes (role_hint emitted by VLM) | Confidence varies with sketch quality |
| **photo VLM** | 0.3-0.6 | Yes (positions very approximate) | Sometimes | Real-world variability dominates |

### 7.3 Why text-prompt does not produce objects or bindings

This is the cleanest articulation of the harness-deterministic principle for
multimodal:

- Text-prompt LLM extracts `LayoutSpec.intent` (structured, no NL).
- `LayoutSpec.objects` is empty.
- `LayoutSpec.bindings` is empty.
- Canonical pipeline matches by intent alone.
- Template's authored positions become canonical objects at exec time via
  `code_template` substitution.
- Disambiguators run on canonical positions to populate role bindings
  deterministically.
- LLM never sees role names as strings during build; sees only ratify
  diagnostics during the verify phase.

**The LLM intersects the build path at exactly one place — intent extraction —
and that intersection produces a typed artifact with no string-substitution
downstream.**

This is also the only design that preserves the existing
`execute_template_canonical` shape while adding role-based templates. Hard
instantiate's success rate (CP-02 own-goal demo) was driven by determinism after
match; this preserves that determinism.

### 7.4 Sketch and photo modalities

Sketch (when implementable; see §10) and photo modalities provide
`LayoutSpec.objects` with positions derived from the image. They optionally
provide `bindings.role_hint` when the VLM can label objects (e.g., "this
rectangle is the input conveyor"). When they cannot, they emit objects
without role_hints; the ratifier auto-binds via disambiguators.

### 7.5 Drag-drop modality

Drag-drop (canvas; see §9) provides `LayoutSpec.objects` from explicit user
placement. Role binding is via UI affordance — right-click → "set as
primary_robot". When the user has not set a role, the disambiguator runs.

---

## 8. Retrieval Protocol

### 8.1 Structural-filter-first, similarity-tiebroken

```python
def retrieve(spec: LayoutSpec) -> List[(template_id, tier, score)]:
    # Stage 1 — hard structural filter
    candidates = [
        t for t in templates
        if t.intent.pattern_hint == spec.intent.pattern_hint
        and features_compatible(t.intent.structural_features,
                                spec.intent.structural_features)
        and counts_within_tolerance(t.intent.counts, spec.intent.counts)
    ]

    if not candidates:
        return []  # → triggers T5 free-form fallback

    # Stage 2 — embedding similarity over canonical structural fingerprint
    fingerprint = canonical_serialize(spec.intent)
    scores = embed_similarity(fingerprint, candidates)

    # Stage 3 — tier classification per existing thresholds
    return tier_classify(scores)
```

### 8.2 Canonical structural fingerprint

```
"pattern_hint=pick_place; n_robot_stations=2; n_handoffs=1;
 has_color_routing=false; destination_kind=single_bin;
 tags=isaac:robot.fixed_base.arm,isaac:topology.linear_pipeline,isaac:transport.conveyor"
```

Sorted, normalized, deterministic. Embedding model is doing similarity over
**facts about the spec**, not over English. The embedding step is
structurally-bounded — embedding-quality variance affects ranking among
already-eligible templates, never which templates are eligible.

### 8.3 What changes for ChromaDB

The existing `template_retriever.py` uses ChromaDB with embedding similarity
over `goal + thoughts + tools_used`. The change:

1. Add a `intent` field to each template's ChromaDB metadata (counts,
   features, tags).
2. Retrieval pipeline runs `where`-filter on intent before similarity query.
3. Similarity query uses canonical fingerprint as the embedded text, not raw
   `goal`.

Existing collection rebuild defensive logic (commit `487aadf`) is unaffected.
Concurrent-write hazard (memory note) still applies — only one indexer at a
time; no parallel ChromaDB writes.

### 8.4 5-tier match spectrum

Existing thresholds (sim ≥ 0.85 + margin ≥ 0.20 = T1) apply to Stage 2
similarity score. Stage 1 structural filter is a hard gate — if no candidates
pass, the tier is T5 regardless of what similarity would have computed.

This eliminates the rank-collision problem from VR-19 (CP-01 vs CP-02 fight)
because `n_robot_stations=2` is a hard filter, not a soft signal.

---

## 9. Canvas Modality — Hybrid Decoupled Architecture

### 9.1 Why hybrid decoupled, not native or webview-in-Kit

**Native** (`omni.ui.scene` CAD canvas): rejected. `omni.ui.scene` was
designed as a 3D viewport overlay framework, not a screen-space CAD canvas.
Concrete blockers:
- No dashed strokes (must emulate with many short Lines)
- No Konva-`Transformer` equivalent (corner/edge/rotate handles + dashed
  border + per-handle hit-testing must be reimplemented)
- No smart-guide infrastructure (every drag delta must walk all objects,
  evict last frame's guides, redraw)
- Standalone-SceneView in `ui.Window` has unresolved forum thread
  ("unable to see anything", no canonical sample)
- Drag-from-palette across windows is not a documented Kit pattern

Effort relative to Konva: **~3-4× larger** for comparable quality, on
undocumented frontier.

**Webview-in-Kit**: rejected. NVIDIA staff confirmed
*"this is something that is still in long term development"* (October 2025).
`omni.ui.WebViewWidget` does not exist as productized component.
`omni.kit.widget.browser` is a thin in-app help viewer, not bidirectional
JS↔Python.

NVIDIA's supported pattern goes the other direction: `web-viewer-sample`
streams Kit content TO browser. Inverse of what hybrid-webview-in-Kit would
require.

**Decoupled hybrid**: chrome in `omni.ui` Kit-native, canvas in browser tab,
**non-interactive preview panel inside Kit** that updates via SSE. Split-view
preserved as far as Kit reads.

### 9.2 Architecture

```
┌──────────────────────── Kit Window ──────────────────────────────────┐
│                                                                      │
│  ┌──────────┐  ┌──────────────────┐  ┌──────────────────────────┐  │
│  │          │  │                  │  │                          │  │
│  │  CHAT    │  │  CANVAS-MIRROR   │  │  3D VIEWPORT             │  │
│  │  PANEL   │  │  PANEL           │  │  (Isaac Sim native)      │  │
│  │          │  │  (read-only)     │  │                          │  │
│  │  omni.ui │  │  omni.ui.Image   │  │                          │  │
│  │          │  │                  │  │                          │  │
│  └──────────┘  └──────────────────┘  └──────────────────────────┘  │
│        ▲              ▲                                              │
│        │              │  PNG/SVG snapshots via SSE                   │
└────────┼──────────────┼─────────────────────────────────────────────┘
         │              │
         │      ┌───────┴──────────┐
         │      │   FastAPI :8000   │
         │      │   SSE channel     │
         │      └───────┬───────────┘
         │              │
         │              ▼
         │      ┌─────────────────────────────────────┐
         │      │  BROWSER TAB                        │
         │      │  Konva SPA — full editing surface   │
         └──────►  Drag, snap, smart guides,          │
   chat <─────► │  multi-select, dimensions, layers   │
                └─────────────────────────────────────┘
```

### 9.3 Canvas-mirror panel

Inside Kit, a `omni.ui.Window` containing a single `ui.Image` widget. Image
source is `~/.isaac_assist/canvas_preview.png` (or SVG if Kit's Image widget
supports SVG; PNG is safer).

On every commit in the browser SPA:
1. SPA POSTs `/canvas/commit` with new LayoutSpec
2. Backend renders LayoutSpec to PNG (stateless, deterministic; same renderer
   as the SPA but server-side)
3. Backend emits SSE `canvas/preview_updated`
4. Kit chat extension receives SSE, refreshes `ui.Image` source path

Throttle to ~3-5 emissions/sec during active drag. Accept ~100-300ms staleness
in mirror as the cost of the architecture.

**Mirror is strictly read-only forever.** No pan, no zoom, no selection. Once
edits land in mirror, the architecture has slid back to native-canvas territory
the long way around. **Discipline-required**: discard every "could you make
the mirror pannable?" feature request.

### 9.4 Workspace docking

`omni.ui.Workspace.dump_workspace()` / `restore_workspace()` provides
session-persistent layout. Default dock state on first launch:
- Chat panel left
- Canvas-mirror panel center
- 3D viewport right
- All three resizable via drag-splitters

User customizes; layout persists per session via `dump_workspace()` to disk.

Cross-version: `omni.ui.Workspace` API is stable across Isaac 5.1 and 6.0
(verified — `chat_view.py` is byte-identical between the two version-folders).

### 9.5 Browser SPA — Konva implementation

Same Konva.js stack as the prior spec. Chrome (toolbar, palette,
properties panel) lives in the SPA. Canvas itself uses Konva's layered scene
graph. Visual specifications inherited from the original spec but adjusted:

- Custom robot silhouettes (top-down 32×32 SVG per robot class)
- Class colors by agency tier: robots in single signature blue, conveyors/sensors
  amber/teal, passive (bins/cubes/tables) desaturated greys with subtle hue
- Persistent chat input ribbon at the bottom of the SPA window — chat survives
  modality switches inside the browser tab
- Motion vocabulary: `instant`, `flash` (80ms), `react` (160ms), `commit` (200ms),
  `arrive` (240ms overshoot), `transit` (280ms), `breathe` (1600ms)
- Object representation: outlined-with-light-fill rectangles plus
  forward-direction notch on robots, flow chevrons on conveyors

Detailed visual spec deferred to implementation; the visual decisions in the
prior critique (§7) remain valid for the browser SPA scope.

### 9.6 SPA → backend protocol

| Endpoint | Purpose |
|---|---|
| `GET /api/v1/canvas/{session_id}` | Load current LayoutSpec |
| `POST /api/v1/canvas/{session_id}/patch` | Apply field-granular delta with `parent_revision` (§15) |
| `POST /api/v1/canvas/{session_id}/commit` | Promote proposed → committed |
| `POST /api/v1/canvas/{session_id}/preview_render` | Render current state to PNG, persist, emit SSE |
| `POST /api/v1/canvas/{session_id}/build` | Trigger ratify → execute_template_canonical via canonical pipeline |
| SSE `/api/v1/chat/stream/{session_id}` | Existing; new event types `canvas/proposed`, `canvas/committed`, `canvas/preview_updated`, `canvas/build_progress` |

### 9.7 Canvas access via `👁 Modes` launcher

The browser-tab canvas is one of five modalities accessible via the `👁 Modes`
popover in the chat header (see §11.4). Selecting "Open canvas editor" from
the popover invokes
`webbrowser.open(f"http://localhost:8000/canvas?session={session_id}")`.
Canvas-mirror panel inside Kit auto-shows when first preview-update event
fires; otherwise stays hidden. See §11.4 for full launcher specification and
§11.5 for mirror-panel state machine.

---

## 10. Sketch and Photo Modalities — Honest Treatment

### 10.1 Anton's reservation acknowledged

Sketch parsing via Gemini Robotics-ER 1.6 is **uncertain**: Robotics-ER is
preview-API, not stable. Hand-drawn sketches have known accuracy issues even
with strong VLMs (symbol disambiguation, OCR of handwritten labels, motion-arrow
interpretation). Anton cannot verify viability without API access.

This spec **does not commit to sketch modality landing**. It commits to the
foundation being **sketch-ready** — when a viable VLM path exists, the modality
producer is a small adapter that emits LayoutSpec; the foundation absorbs it
without changes.

### 10.2 Sketch-modality contract (when implementable)

```typescript
sketch_modality.produce(image_bytes: Buffer, prompt_context?: string): LayoutSpec
```

- Calls the chosen VLM (Gemini Robotics-ER, Anthropic Vision, or local Ollama
  vision; multi-provider fallback already exists per `vision_router.py`)
- Structured-output prompt directs the VLM to emit `LayoutSpec` JSON directly
- VLM-emitted role_hints flow into `LayoutSpec.bindings[].source = "modality_emitted"`
- Confidence reflects VLM's certainty; ratifier weights it appropriately

### 10.3 Photo modality

Same shape as sketch but lower confidence by default (0.3-0.6). Real-world
variability dominates; modality is exploratory only.

### 10.4 Voice modality

Routes through STT → text-prompt path. No new boundaries beyond chaining
existing components (LiveKit STT exists in the stack per `infra/livekit/`).
LayoutSpec source is `voice` for telemetry; the production pipe is the same
as text after transcription.

### 10.5 Viewport-edit modality

When user manipulates the 3D scene directly (existing Isaac Sim viewport),
the system can reverse-engineer a LayoutSpec from current USD stage state.
Tool: `viewport_to_layout_spec()` reads via Kit RPC, classifies prims by USD
type/schema, produces LayoutSpec with `confidence: 1.0` and
`source.modality = "viewport"`.

This modality enables "I built a scene, now save it as a canonical template."
The output goes through normal validation, ratify, and template-save flow.

---

## 11. UI Layout — Split-view Across Modalities

### 11.1 Split-view in Kit, not tabs

The original spec's tab system (Chat ↔ Floor Plan ↔ 3D Scene) is replaced by
**split-view via `omni.ui.Workspace` docking**. User sees chat + canvas-mirror
+ 3D viewport simultaneously. No mode switches lose context.

Default layout (first launch):
```
┌───────────┬──────────────────┬──────────────────┐
│           │                  │                  │
│   Chat    │  Canvas-Mirror   │  3D Viewport     │
│           │                  │                  │
│   ~30%    │      ~35%        │      ~35%        │
│           │                  │                  │
└───────────┴──────────────────┴──────────────────┘
```

User drags splitters to resize; closes panels; saves layout via
`dump_workspace()`. Layout persists per-session.

### 11.2 Browser SPA layout (canvas modality)

Inside the browser tab, the SPA has its own internal layout because it's a
separate window the user can park anywhere. Internal layout includes:
- Toolbar (left, vertical, 48px wide)
- Object palette (left, collapsible, 200px expanded)
- Properties / Layers / Constraints (right, tabbed, 280px wide, resizable)
- Status bar (bottom, 24px)
- **Persistent chat input ribbon at very bottom** (above status bar) — user
  can chat without leaving the canvas

The chat input in the SPA POSTs to the same `/api/v1/chat/message` endpoint as
the Kit chat panel. Both are views of the same conversation.

### 11.3 Button/control inventory for SPA

(Inherited from original spec §5 with revisions per visual-design critique.)

#### 11.3.1 SPA header (40px tall)

| Element | Position | Size | Action |
|---|---|---|---|
| Logo / Brand | Left, 12px margin | 24×24 + 80×16 wordmark | Click → command palette (Ctrl+K) |
| Settings | Right, 12px margin | 32×32 | Right slide-in 320px wide |
| Help | Right, 4px gap from Settings | 32×32 | Right slide-in 320px wide |
| Open in Kit | Right, 4px gap | 32×32 | Focuses Kit window via OS deeplink (where supported) |

#### 11.3.2 SPA left toolbar (48px wide)

Vertical icon strip, 32×32 icon cells. Groups separated by 1px dividers.

| Group | Icons | Shortcuts |
|---|---|---|
| Modes | Cursor (Select), Plus (Place), Ruler (Annotate), Pin (Lock) | V, P, D, L |
| History | Undo, Redo | Ctrl+Z, Ctrl+Y |
| View | Grid, Snap, Fit-all, Fit-selected | Ctrl+', F9, Ctrl+Shift+H, F |
| Sync | Sync to Sim, View 3D | Ctrl+S, Ctrl+3 |

#### 11.3.3 SPA palette (left, collapsible, 64px collapsed / 200px expanded)

Categories: Robots, Conveyors, Workpieces, Sensors, Fixtures.
Items per category: scaled-footprint cards 88×72px (expanded) or 64×64px
(collapsed) with custom robot silhouettes.

#### 11.3.4 SPA right dock (280px wide, resizable 240-480px)

Three tabs: Properties, Layers, Constraints. Selected object inspector;
typed numeric inputs in monospace; locked roles displayed prominently.

#### 11.3.5 SPA bottom status bar (24px)

State dot + text + object/constraint count. Right cluster: cursor coords,
zoom indicator, mini-map toggle.

#### 11.3.6 Floating confirm bar

Appears when agent has proposed mutations. 48px tall, 600px max width,
centered above status bar. Buttons: Accept (NVIDIA-green), Reject, Refine.

### 11.4 Kit chat panel — multimodal entry surface

The existing `chat_view.py` gains three coordinated additions: a header-level
modality launcher (`👁 Modes`), a horizontally-scrolling quick-prompt row, and
SSE listeners for canvas/multimodal events. No restructuring of existing chat
flow; these are additive surfaces.

#### 11.4.1 `👁 Modes` launcher button (header)

Position: chat-panel header bar, replaces the existing `Vision` button position.

Visual:
- Label: `👁 Modes` (eye glyph + 5-character text)
- Width: ~10-15% wider than current `Vision` button
- Style: matches existing chat_view.py button vocabulary (`COL_BG_USER`
  background, `#DDDDDD` text, hover → border highlight)
- Active state when popover open: text → `#76B900` (NVIDIA green)
- Small `▾` chevron after text indicates dropdown affordance

Behavior:
- Click → popover opens anchored below button
- Click again or click-outside → popover closes
- Esc → popover closes

#### 11.4.2 `👁 Modes` popover content

Popover dimensions: 280×240 px, anchored under launcher with 4 px gap.
Background `#1A1C1F`, border `#2E3237`, drop-shadow `0 4px 16px rgba(0,0,0,0.4)`,
4 px border-radius.

Items (5, ordered most-transformative first; existing-vision-tools last):

| Order | Glyph | Label | Action |
|---|---|---|---|
| 1 | 🎨 | Open canvas editor | Opens browser tab at `/canvas?session={id}`. Auto-activates canvas-mirror panel on first preview event. |
| 2 | 📎 | Upload sketch or photo | File-picker (PNG/JPG); POST to `/api/v1/canvas/{id}/sketch_upload`; sketch modality producer extracts LayoutSpec; canvas-mirror shows proposed state. |
| 3 | 🎤 | Voice input | Push-to-talk via existing LiveKit STT pipe; transcript inserted in chat input; same path as text-prompt. |
| 4 | ↻ | Extract layout from current scene | Calls `viewport_to_layout_spec()` via Kit RPC; produces LayoutSpec from existing stage state; opens canvas editor with that as starting state. |
| 5 | 🔍 | Analyze current viewport | Existing Kimate-introduced vision-tools-toggle (`vision_detect_objects`, `vision_bounding_boxes`, etc). Preserved verbatim. |

Each item: 240×40 px row. Glyph 16×16 px left-aligned with 12 px margin, label
`13 px weight 500`, secondary description `11 px #8A8E92` below label, click
target full row.

Hover: row background → `#22262B`. Click: 80 ms flash → action fires.

#### 11.4.3 Quick-prompt row (horizontally scrolling)

Position: between the existing `Live` strip and the chat input field. Replaces
the existing 3-button row (`Build a pick-and-place scene`, `Add a Franka arm`,
`Inspect the stage`).

Visual:
- Single horizontal row, 32 px tall
- Buttons sized by content, ~120-180 px each
- 8 px gap between buttons
- Container: scrollable horizontally; right-edge fade-out gradient signals
  "scroll for more"
- No left-edge fade — first item always visible as anchor

Content sourcing:
- Buttons auto-generated from `workspace/templates/CP-N.json` files' `goal`
  field (truncated to ~24 chars if longer)
- Plus a small fixed set of meta-prompts ("Inspect the stage", etc) that
  remain regardless of canonical-coverage
- Order: A-tier verified canonicals first, then by recency

Behavior:
- Click → text inserted into chat input field, NOT auto-sent
- User can edit before pressing Send
- Scroll: mouse wheel (when row hovered), trackpad swipe, click-and-drag,
  arrow keys when row has focus

Telemetry:
- Click events logged to `events.jsonl` per §17.1 — measures which canonicals
  are actually used. Drives Open Q A baseline measurement.

#### 11.4.4 SSE listeners

`chat_view.py` subscribes via existing `service.start_stream` mechanism to new
event types:

| Event | UI behavior |
|---|---|
| `canvas/proposed` | Auto-show canvas-mirror panel; render PNG with ghost-state styling; show confirm bar |
| `canvas/committed` | Mirror PNG transitions ghost → solid; confirm bar dismisses |
| `canvas/preview_updated` | Refresh `ui.Image` source path on mirror panel |
| `canvas/build_progress` | Status indicator updates with current build-tool name |
| `canvas/build_completed` | Status indicator → idle; mirror shows final 2D layout (no buttons) |

#### 11.4.5 Status indicator

Position: in the existing `Live` strip area, leftmost text slot.

States (mirrors §14.1 `sim_state`):
- `idle` — empty or "Ready"
- `agent extracting intent` — pulsing dot + "Planning…"
- `agent proposed` — solid dot + "Proposed — review →" (clickable, focuses canvas-mirror)
- `building` — pulsing dot + "Building (3/12 tools)"
- `partial` — red dot + "Build paused — see canvas-mirror"
- `live` — green dot + "Scene ready"
- `verify_failed` — amber dot + "1 reach violation" (clickable, opens issues)

Pulsing matches existing `SPIN_INTERVAL_S` cadence in chat_view.py for
consistency.

### 11.5 Kit canvas-mirror panel

New `omni.ui.Window` class. Single `ui.Image` widget bound to
`~/.isaac_assist/canvas_preview.png`. Path refreshes on SSE
`canvas/preview_updated`.

#### 11.5.1 Three-state visibility model

| State | Trigger | Behavior |
|---|---|---|
| **Hidden** (default) | No modality has produced a LayoutSpec in this session | Panel collapsed in Workspace; 100% of Kit window space available for chat + 3D viewport |
| **Proposed** | Modality emitted LayoutSpec; agent generated layout from prompt; sketch/photo uploaded | Panel auto-shows in Workspace dock; PNG renders ghost objects (40% opacity, blue-tinted outlines); confirm bar floats over panel |
| **Live** | Build completed successfully | Panel stays visible (user can manually close); PNG shows final 2D layout in solid styling; no confirm bar; useful as persistent top-down overview alongside 3D scene |

User can manually dock-close at any state via Kit's standard window controls.
The state machine drives auto-show only; never auto-hide on `Live`.

#### 11.5.2 Confirm bar (floating over mirror panel)

Position: bottom-center of mirror panel, 16 px from edge.
Size: 320×40 px, 4 px border-radius, `#22262B` bg, `#76B900` 1 px border,
drop-shadow.

Buttons (right-aligned, 8 px gap):
- **Accept** — 80×28 px, `#76B900` fill, white text. Triggers
  `canvas/commit` → `apply_layout_spec_to_scene` → build runs.
- **Reject** — 80×28 px, ghost border `#FF4444`, red text. LayoutSpec
  discarded; panel returns to Hidden.
- **Refine →** — 80×28 px, ghost border `#DDDDDD`. Opens browser tab for
  editing; state stays Proposed.

Reason text left of buttons: 11 px `#8A8E92`, max ~150 chars, ellipsis. Examples:
*"Generated from prompt: 'sorting station, 2 cubes, 2 bins'"* or
*"From sketch upload (confidence 0.7)"*.

Auto-commit countdown for T1-confident matches: when retrieval returns a tier-1
match (sim ≥ 0.85, margin ≥ 0.20), the confirm bar shows a 3-second visible
countdown ("Building in 3… [Cancel]") and auto-promotes to commit unless user
clicks Cancel. T2-T5 matches require explicit Accept.

#### 11.5.3 Click behavior — strictly read-only

The mirror panel is **read-only**. Click on any object inside the rendered PNG
calls `webbrowser.open(f".../canvas?session={session_id}&select={object_id}")`
which opens the browser-tab editor focused on that object. Editing happens
only in the browser tab.

Discipline-required: never grow mirror toward editing. See §9.3 — slipping
back into native-canvas territory the long way around is the failure mode the
hybrid architecture explicitly rejects.

---

## 12. Visual Design

Inherited from original spec §4 with adjustments per Opus visual critique.

### 12.1 Visual North Star

> *"A still and gridded dark room where machine intent (agent, robot, motion)
> glows in two restrained colors against unobtrusive geometry, and every
> interaction is announced by exactly one well-timed motion."*

Use to evaluate any new screen: third color? second motion? visual noise where
geometry should suffice? If yes, redesign.

### 12.2 NVIDIA + CAD synthesizing principle

> **CAD form, NVIDIA hand.** Every CAD signifier uses NVIDIA's
> color/motion/typography vocabulary, not its native CAD vocabulary.
> Dimension annotations: ISO-129 *geometry*, NVIDIA palette. Snap markers:
> AutoCAD *types*, NVIDIA monochrome treatment.

### 12.3 Design tokens

```css
/* Surfaces */
--floor-bg:             #111214;
--floor-panel:          #1A1C1F;
--floor-elevated:       #22262B;
--floor-border:         #2E3237;
--floor-border-subtle:  #1E2228;

/* Text */
--text-primary:         #DDDDDD;
--text-secondary:       #8A8E92;
--text-muted:           #52575C;

/* Accent — agent presence + committed user intent ONLY */
--nv-green:             #76B900;
--nv-green-dim:         #3D6100;
--nv-green-glow:        rgba(118,185,0,0.15);

/* Class colors by agency tier */
/* Tier A — autonomous */
--obj-robot-base:       #5A8DEE;       /* Franka */
--obj-robot-mid:        #4A7DCE;       /* UR series */
--obj-robot-low:        #3A6DAE;       /* Carter */
/* Tier B — powered */
--obj-conveyor:         #FFA800;
--obj-sensor:           #00C8B4;
/* Tier C — passive */
--obj-bin:              #5E6571;
--obj-cube:             #8B7355;
--obj-table:            #4A5560;

/* Annotations */
--dim-line:             #C8CC80;       /* warm pale yellow-green */
--dim-text:             #DDDDDD;

/* State */
--state-error:          #FF4444;
--state-warning:        #FFA500;
--state-info:           #409CFF;

/* Motion */
--ease-out-cubic:       cubic-bezier(0.215, 0.610, 0.355, 1.000);
--ease-spring:          cubic-bezier(0.34, 1.56, 0.64, 1);     /* arrive */
--ease-snap:            cubic-bezier(0.000, 0.000, 0.200, 1.000);

--dur-instant:          0ms;
--dur-flash:            80ms;
--dur-fast:             160ms;
--dur-react:            160ms;
--dur-commit:           200ms;
--dur-arrive:           240ms;
--dur-transit:          280ms;
--dur-breathe:          1600ms;
```

### 12.4 Typography

- Inter for UI text (free fallback for NVIDIA Sans)
- JetBrains Mono for numerics in property panel and coordinates
- 12px primary UI labels (palette, properties, tab names)
- 11px secondary (tooltips, status bar)
- 13px section headers and confirm-bar reason text
- 14px monospace cursor coordinate readout

### 12.5 Class colors — agency tier rationale

Information hierarchy emerges from color:
- **Tier A (autonomous)** = cool blues, all robots in same hue family,
  recognizable at a glance
- **Tier B (powered)** = warm/teal, distinct hues at high saturation
- **Tier C (passive)** = desaturated greys with subtle hue tint
- **Selection** stays NVIDIA green; **agent presence** stays NVIDIA green
- These are the **only two saturated greens** in the system

### 12.6 Custom robot silhouettes

Top-down 32×32 px SVG per robot class. Identifies Franka, UR5e, UR10e,
Kinova, IIWA, Jaco7, Carter at a glance. Distinguishes the tool from generic
CAD applications.

### 12.7 Motion discipline

Never-mix rules:
- Never combine `arrive` (overshoot) with `transit` (slide) — buggy feel
- Never animate both moving object AND its dependent annotations (reach
  circle stays instant when robot is `instant`-moved; otherwise desyncs)
- Never animate the canvas grid or origin marker (they are the world; the
  world doesn't move)

---

## 13. Persistence — SQLite with Revision Tracking

### 13.1 Why not JSON-per-session

JSON-per-session was the original spec's choice. The Opus reversibility audit
(`2026-05-08-floor-plan-tool-opus-critique.md` §5.1) classified it as
**bet-the-farm** — most under-defended decision, with no version history, no
multi-tab semantics, no schema migration, no backend concurrency model.

Multiple cascade scenarios (autosave races backend mid-write, build reads
stale state, undo drift) trace to weak persistence semantics.

### 13.2 SQLite + revision column

Single SQLite database at `~/.isaac_assist/state.db` (or
`workspace/multimodal/state.db` for project-local).

Schema:
```sql
CREATE TABLE layout_specs (
    session_id TEXT NOT NULL,
    revision   INTEGER NOT NULL,
    spec_json  TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (session_id, revision)
);

CREATE INDEX idx_session_latest ON layout_specs(session_id, revision DESC);

CREATE TABLE bindings (
    session_id TEXT NOT NULL,
    revision   INTEGER NOT NULL,
    role_name  TEXT NOT NULL,
    object_id  TEXT NOT NULL,
    source     TEXT NOT NULL,
    PRIMARY KEY (session_id, revision, role_name)
);

CREATE TABLE build_log (
    build_id    TEXT PRIMARY KEY,
    session_id  TEXT NOT NULL,
    revision    INTEGER NOT NULL,
    started_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    finished_at TIMESTAMP,
    status      TEXT NOT NULL,    -- "running" | "ok" | "partial" | "failed"
    progress    TEXT              -- JSON: per-tool status array
);

CREATE TABLE events (
    event_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    timestamp  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    event_type TEXT NOT NULL,
    payload    TEXT NOT NULL    -- JSON
);
```

WAL mode for concurrent reads + serialized writes.

### 13.3 Compare-and-swap protocol

Every POST patch carries `parent_revision`:
```
POST /api/v1/canvas/{session_id}/patch
{
  "parent_revision": 42,
  "ops": [...]
}
```

Server:
1. Reads current revision in transaction
2. If `current.revision != parent_revision` → 409 Conflict, returns current state
3. Else applies ops, increments revision, commits transaction, returns new revision

Client on 409:
- Receives current state from server
- Prompts user: "Another change happened — apply your edits anyway, discard
  yours, or merge?"
- Three-way merge UI (per failure-cascade Opus agent §4.4 recommendation)

### 13.4 Local-storage write-ahead log

Browser SPA writes every mutation to `localStorage` BEFORE issuing POST. On
POST success, removes from localStorage queue. On reload, replays unflushed
queue.

`navigator.sendBeacon` on `beforeunload` flushes any pending debounced POST.

This eliminates the lost-edit window the original spec had.

### 13.5 No tab-lock

The original spec's localStorage tab-lock is dropped. With CAS protocol +
field-granular last-writer-wins, two tabs editing the same session no longer
need exclusion. They edit; conflicts surface as 409s; user merges.

---

## 14. Failure Cascade Handling

### 14.1 Expanded `sim_state` state machine

```typescript
type SimState =
  | "unbuilt"          // no scene built yet
  | "building"         // build in progress
  | "partial"          // build started, mid-build error, scene state inconsistent
  | "live"             // build complete, verify pass
  | "verify_failed"    // build complete, verify fail (scene exists but flagged)
  | "error";           // unrecoverable error
```

`partial` is critical — the original spec collapsed it into `error`, hiding
the fact that Isaac Sim has prims even when the floor plan thinks "build
failed."

### 14.2 Event-sourced build log

Every build creates a `build_id` row in `build_log` table. Each tool call
during build appends to `progress` JSON array:

```json
[
  {"tool": "robot_wizard", "args_summary": "...", "status": "ok", "ts": "..."},
  {"tool": "create_conveyor", "args_summary": "...", "status": "ok", "ts": "..."},
  {"tool": "setup_pick_place_controller", "args_summary": "...", "status": "error", "error": "...", "ts": "..."}
]
```

On failure, the log enables:
- **Resume from failed tool**: re-run from the failure point (skip already-done)
- **Cleanup of partial build**: list of created prim_paths to delete
- **Diagnose**: agent can read build_log for the actual failure trace

### 14.3 Three recovery actions on partial build

When `sim_state == "partial"`:
- **Reset stage**: `_reset_stage()` + clear partial prims; sim_state →
  `unbuilt`; user retries
- **Resume from failed tool**: re-runs build from failed-tool-index; sim_state
  → `building`
- **Sync from stage**: reads existing prims into LayoutSpec; sim_state →
  `live` with diagnostic note

UI surfaces these as buttons in the canvas-mirror panel + chat directive.

### 14.4 Cross-session resource locks

| Lock | Scope | Mechanism |
|---|---|---|
| Active writer per session | Per session_id | `active_writers` table; heartbeat-renewed; force-take with warning |
| Kit RPC build slot | Global (single-tenant Kit) | `asyncio.Semaphore(1)`; second build queues with progress "Waiting for current build" |
| ChromaDB write | Global | `asyncio.Lock()` on every write path; reads parallel |

Memory note `feedback_isaac_assist_kit_concurrency` documents the Kit
single-tenant constraint. The Semaphore implements it explicitly.

ChromaDB segfault hazard (memory note) requires the global lock on all writes.
Reads stay parallel.

### 14.5 SSE backpressure

Client-side queue with size limit (200 events). On overflow, force a full-state
re-sync via `GET /api/v1/canvas/{session_id}`. Server replays missed events
on SSE `Last-Event-ID` reconnect (small ring buffer, last 100 events per
session).

### 14.6 Kit RPC failure handling

When Kit RPC unreachable:
- Persistent red status banner in canvas + chat
- Canvas tool works offline (all editing client-side)
- Build button blocked with modal "Kit connection required to build"
- All other features (edit, save, agent chat) continue

---

## 15. Edge Cases & Accessibility

(Inherited from original spec §10 with the cascade-handling additions in §14.)

Cross-cutting accessibility additions per Opus visual critique:

- **Spatial sonar mode** for screen-reader users: cursor movement plays brief
  tones whose pitch encodes proximity to nearest snap point. Borrowed from
  Apple's audio cues.
- **Spatial relationships announced** in screen-reader live region: "Franka_1
  selected, 0.42m from Conveyor near-end, 0.81m from Bin_1, both within 0.855m
  reach."
- **Audio snap feedback**: soft tick (~12ms, ~600Hz, -24dB) on snap acquisition.
  Sighted users see marker; blind users hear tick. Same information, two
  channels.
- **Cognitive accessibility settings**: dyslexia (OpenDyslexic font option),
  ADHD ("essentialist mode" hides decorative motion), autism ("disable
  proactive suggestions" — agent only acts on explicit request)
- **Motor accessibility**: click-to-place mode (click palette, click canvas);
  voice placement via existing LiveKit pipe ("place Franka at 1.5, 0, rotation
  90"); sticky drag (toggle, not held)

---

## 16. Testing Strategy

### 16.1 Test pyramid

| Layer | Tech | Scope |
|---|---|---|
| Frontend unit (Vitest) | TypeScript | `commands.ts` apply/undo, `snap.ts` geometry, store reducers, validate_layout_spec |
| Frontend component (Playwright Component) | TypeScript | Toolbar, PropertiesInspector, ConfirmBar, CommandPalette |
| Frontend E2E (Playwright) | TypeScript | Case studies (cold-start, agent-prompt, hybrid) |
| Backend pytest | Python | Routes, ratify, retrieval, persistence |
| Translator pytest | Python | `floor_plan_to_tool_sequence` round-trip with CP-N templates |
| Integration smoke | Python | `scripts/qa/multimodal_smoke_tests.py` analogous to existing `hard_instantiate_smoke_tests.py` |
| Visual regression | Playwright screenshots | Per-canonical layouts at fixed seeds, pixel-diff with tolerance |
| Performance harness | Playwright + perf API | Drag latency p95, frame times, scaling at N objects |

### 16.2 FP-N task specs

Following existing convention (`docs/qa/tasks/CP-*.md`), add:

- `FP-01_cold_start_franka.md` — Case A from original spec §7.4
- `FP-02_agent_generates_layout.md` — Case B
- `FP-03_constraint_tight_2x2.md` — Case C (CONSTRAINT-01 shape)
- `FP-04_canonical_round_trip.md` — load CP-01, no edits, build → identical
- `FP-05_user_renames_paths.md` — verify role-based templates handle renames
- `FP-AD-01_invalid_layout.md` — agent-proposed overlapping objects must reject
- `FP-AD-02_out_of_reach.md` — agent-proposed bin outside reach surfaces warning
- `FP-AD-03_role_binding_conflict.md` — multi-robot ambiguity, ratifier fallback
- `FP-AD-04_partial_build_recovery.md` — Kit RPC fails mid-build, recover

These plug into existing `direct_eval` + canary suite.

### 16.3 Smoke test pattern

```python
# scripts/qa/multimodal_smoke_tests.py
@pytest.mark.parametrize("template_id", ["CP-01", "CP-02", "CP-03", "CP-04", "CP-05"])
def test_canonical_round_trip(template_id):
    # Load canonical → produce LayoutSpec → ratify → execute → snapshot
    # Assert snapshot matches direct CP-N execution
```

---

## 17. Telemetry + Observability

### 17.1 Event schema

Append to existing `provider_incidents.jsonl` infrastructure with new event
types in `events` SQLite table (§13.2):

```typescript
type EventType =
  | "modality_invoked"            // {modality, session, ms}
  | "intent_extracted"            // {modality, session, intent_summary}
  | "retrieval_completed"         // {session, top_k_with_scores, tier}
  | "ratify_completed"            // {session, status, diagnostics}
  | "rebind_role"                 // {session, role, object_id, source}
  | "build_started"               // {session, build_id, template_id}
  | "build_progress"              // {build_id, tool, status, ms}
  | "build_completed"             // {session, build_id, status, n_tools}
  | "verify_check_run"            // {session, check_id, status, ms}
  | "canvas_proposed_resolved"    // {session, action: accept|reject|refine}
  | "canonical_match_shown"       // {session, template_id, score}
  | "canonical_match_resolved"    // {session, action}
  | "user_correction";            // {session, surface, what}
```

### 17.2 Dashboards

Aggregator `scripts/qa/analyze_multimodal_usage.py`:
- Modality usage breakdown
- T1 fire-rate per session (Open Q A baseline measurement)
- Ratify success rate per modality
- Build-failure modes by tool
- Per-feature verifier check pass rate
- Agent proposal acceptance rate

### 17.3 Frontend error reporting

`window.onerror` + `unhandledrejection` POST to
`/api/v1/canvas/{session_id}/client_error` → `events` table. Optionally Sentry
integration deferred.

---

## 18. Schema Migration

### 18.1 Forward-only migrations

Module: `service/isaac_assist_service/multimodal/migrations/`

```
migrations/
  __init__.py
  v1_0_to_v1_1.py
  v1_1_to_v1_2.py
  ...
```

Each migration is a function `migrate(spec: dict) -> dict`. Read path applies
all migrations from spec's `version` to current. Failures preserve original
file with `.broken-{timestamp}` suffix.

### 18.2 Vocabulary registry migration

`workspace/vocabulary/structural_tags.registry.json` is append-only. Removing
a tag forbidden; mark `status: "deprecated"` instead. Old data referencing
deprecated tags continues to load (warning emitted; retrieval may downgrade
quality but doesn't crash).

`pattern_hint` enum changes are **major version bumps**. Adding a value:
minor bump. Removing: major bump. Renaming: forbidden.

### 18.3 Template version coupling

Templates carry `template_version`. LayoutSpec stores
`template_match: {id, template_version}`. On load, if template version
changed, surface "Template updated. Migrate?" banner with diff preview.

---

## 19. Security + Privacy

### 19.1 Threat model statement

**v1 is single-user localhost only.** Multi-user / remote-browser deployment
is reserved for future. The spec's `allow_origins=["*"]` is acceptable under
this assumption and ONLY this assumption.

### 19.2 Session ID format

128-bit random (UUIDv4 minimum). Generated server-side on first canvas open;
stored in browser localStorage; passed as URL param.

### 19.3 Sensitive notes flag

Per-object `notes_sensitive: boolean` flag. When true, notes are stripped
before LLM context distillation (chat agent never sees them; canvas SPA
displays them locally).

This is the per-object analog of `redact_finetune_data` for the Nexus
pipeline.

### 19.4 Data lifecycle

- LayoutSpecs persist in SQLite per session
- 90-day retention by default; `DELETE /api/v1/canvas/{session_id}` for
  immediate removal
- `events` table retained 30 days (rotated)
- `build_log` retained 14 days

---

## 20. Implementation Sequence

### Parallel-session coordination

This sequencing reflects coordinated work with another active session iterating
on controller-logic, ROS2-bridge, manufacturing-metrics, and new CP-templates.
File-level ownership boundaries and sync protocol are documented in
`docs/specs/2026-05-09-multi-session-coordination.md`. Block 1 below is split
into **1A (parallel-safe; runs concurrently)** and **1B (held until other
session reaches 100% on existing verify pipeline)**.

### Block 1A — Foundation, parallel-safe

These steps add new modules and surfaces. They do **not** modify
`verify_pickplace_pipeline`, `simulate_traversal_check`, or existing CP-N
template `code` fields. They run in parallel with the other session's
controller-logic iteration without rebase risk. Three sub-tracks; can run
in any order or overlapping.

#### Block 1A.1 — Backend foundation

1. **Define LayoutSpec + intent schema** per §3 (three-layer vocabulary,
   format-regex on tags, registry). Lives in new module
   `service/isaac_assist_service/multimodal/`.
2. **Validate + persist**: SQLite schema (§13), validation rules (§3.7),
   migration scaffold (§18). New module entirely.
3. **Build ratify component as pure function** per §5. Unit-tested in isolation
   in new module. **Wrapper extension** to `canonical_instantiator.py`'s entry
   path — wraps **before** `execute_template_canonical` invocation, does
   **not** touch the verify steps that the other session is iterating on.
4. **Multimodal tool handlers in dedicated file**:
   `service/isaac_assist_service/chat/tools/multimodal_handlers.py` (new file,
   not a section in `tool_executor.py`). Imported into `tool_executor.py` via
   one `register_multimodal_handlers(DATA_HANDLERS)` line. Reduces merge
   surface in the shared file from ~50 inline rows to a single line.
5. **Structural-filter-first retrieval extension** in `template_retriever.py`
   per §8.1. Extension only — existing retrieval path preserved as fallback so
   other session's CP-N additions continue to work during the parallel window.

#### Block 1A.2 — Kit UI additions

These touch `chat_view.py` only — controller-logic session does not edit this
file, so no merge conflict surface.

6. **`👁 Modes` launcher** in chat header (replaces existing `Vision` button
   position) per §11.4.1.
7. **Popover with 5 modality items** per §11.4.2 (open canvas, upload sketch,
   voice, extract from scene, analyze viewport).
8. **Horizontally-scrolling quick-prompts row** per §11.4.3, auto-generated
   from `workspace/templates/CP-*.json` `goal` fields. Replaces the existing
   3-button shortcut row.
9. **Canvas-mirror panel registration** per §11.5: new `omni.ui.Window` class
   with three-state visibility (Hidden/Proposed/Live), confirm-bar overlay,
   strict read-only click semantics.
10. **SSE listeners** for new event types (`canvas/proposed`,
    `canvas/committed`, `canvas/preview_updated`, `canvas/build_progress`,
    `canvas/build_completed`) per §11.4.4.
11. **Status indicator** in `Live` strip with sim_state-mapped UI states per
    §11.4.5.

#### Block 1A.3 — Browser canvas SPA

12. **Vite + Konva SPA scaffold** at `web/floor-plan-ui/` per §9.5. Mount via
    `StaticFiles` in FastAPI on `/floorplan` (or `/canvas` per §9.6 endpoints).
13. **Canvas SPA chrome**: header, left toolbar, object palette,
    properties/layers/constraints right dock, status bar, persistent chat
    input ribbon — per §11.2 and §11.3 (inherited button inventory).
14. **Drag-drop modality producer** — Konva-based editing surface. Emits
    `LayoutSpec` with `source.modality = "drag_drop"` via
    `POST /api/v1/canvas/{session_id}/patch` and `/commit`.
15. **Server-side preview renderer** — produces PNG/SVG snapshots from
    LayoutSpec for the canvas-mirror panel via SSE.
16. **Visual polish**: custom robot silhouettes (§12.6), motion vocabulary
    (§12.7), agency-tier color coding (§12.5).

### Block 1B — Foundation, held until other session reaches 100%

These steps modify functions the other session is actively iterating on. They
**wait** until that session signals 100% on the existing pipeline.

17. **Restructure verify-pipeline as registry-dispatched checks** per §6.
    Existing `verify_pickplace_pipeline` and `simulate_traversal_check` become
    thin wrappers around `REGISTRY.run_form_gate` / `run_function_gate`. **No
    new behavior** — pure structural refactor. Adds zero immediate value to
    current canonicals; value materializes for future feature-dispatched checks.
18. **Refactor canonical templates to role-based**: CP-01..CP-05 get `roles` +
    `code_template` + `verify_args_template` per §4. **Discipline non-negotiable:**
    pre-refactor a baseline snapshot is captured per template
    (`function_gate_suite.py` outputs: cube_final positions, delivered counts,
    exact verify diagnostics). Post-refactor the same suite runs against same
    seed; **any function-gate ✓ that becomes ✗ → template rolled back, refactor
    approach reworked.** No exceptions.

After Block 1B, the foundation is fully in place. Hard-instantiate now goes
through ratify and registry-verify instead of monolithic functions.
Regression-tested via existing CP-01..CP-05 smoke + canary suites plus the
function-gate baseline-vs-post-refactor comparison.

### Block 2 — Text-prompt modality refactor (depends on Block 1A.1)

19. **Refactor text-prompt to produce LayoutSpec** per §7.3. LLM now extracts
    intent (structured); build path uses canonical templates with role-based
    substitution after Block 1B lands. Pre-1B: text-prompt path produces
    LayoutSpec.intent and the existing canonical-instantiator continues to
    consume it via the ratify wrapper without role-based substitution. Open Q A
    measurement (T1 fire-rate) is now possible against the new IR.

### Block 3 — Other input modalities (depends on Block 1A.1)

20. **Voice modality** via existing LiveKit STT chain (§10.4). Trivial — STT →
    text → existing text-prompt path.
21. **Viewport-edit modality** (§10.5). `viewport_to_layout_spec()` reads
    stage state via Kit RPC. Useful for "save current scene as canonical
    template" workflow.
22. **Sketch modality** (§10) when VLM viability is confirmed. Anton's
    reservation about Robotics-ER preview-API stability stands; foundation
    is sketch-ready when implementable.
23. **Photo modality** (§10.3) when sketch path lands and VLM stack is proven.

### Block 4 — Canvas wired through canonical pipeline (depends on Block 1A.3 + 1B)

The canvas SPA + canvas-mirror are built in Block 1A.3 as parallel-safe new
files. Wiring them through the **role-based canonical pipeline** depends on
Block 1B having landed (otherwise the role-binding system isn't in place).

24. **Canvas commit → ratify → execute_template_canonical** wiring. The Konva
    SPA emits `LayoutSpec` with objects + bindings; the backend ratify
    component validates against ratified template; execute_template_canonical
    runs with role-based substitution from Block 1B.
25. **Live-build progress** in canvas-mirror via `canvas/build_progress` SSE
    events; mirror PNG transitions ghost → solid as build advances.
26. **`rebind_role` tool integration** (§5.5) — exposed via canvas
    right-click context menu and chat tool-call.

### Block 5 — Operational hardening (interleaves with all prior)

27. **Test plan + FP-N task specs** per §16.
28. **Telemetry events + aggregator** per §17.
29. **Cross-session resource locks** per §14.4.
30. **Edge case + accessibility hardening** per §15.

### Sequencing rationale

Block 1A delivers **parallel-safe foundation work** the multimodal session can
do concurrently with the controller-logic session's 100% drive. Three
sub-tracks (backend, Kit UI, browser SPA) can advance in any order.

Block 1B is the only **hold-point** in the multimodal session's pipeline —
waits for controller-logic 100% signal so the verifier-registry refactor lands
on a stable function-gate baseline.

Block 2 unblocks Open Q A measurement (T1 fire-rate against new IR). Block 3
modalities ship one-by-one against stable foundation. Block 4 wires the canvas
SPA through the now-stable canonical pipeline. Block 5 hardens; can interleave
with all prior blocks.

**Block 1A alone is significant parallel-safe value.** Block 1B unblocks the
roadmap's primary goal (Open Q E: LLM ignoring listed paths) by making role
bindings first-class structured data instead of paths the LLM is asked to
respect.

---

## 21. Open Questions

1. **Sketch modality VLM viability.** Anton cannot verify Robotics-ER access;
   may require alternative VLM. Foundation is sketch-ready; commitment
   deferred.
2. **Photo modality positioning accuracy.** Real-world variability dominates;
   exploratory only.
3. **Canvas-mirror staleness tolerance.** ~100-300ms lag in mirror behind
   browser. Acceptable to Anton, or animation-jarring? Empirical question.
4. **`omni.ui.Workspace` cross-version stability.** Verified for Isaac 5.1
   and 6.0 today; new versions could shift API surface.
5. **Voice modality activation gesture.** Push-to-talk vs voice-activation?
   Out of scope for this spec; punted to LiveKit-specific decision.
6. **Multi-floor / multi-room.** Current IR assumes single-rectangle workspace.
   Future feature; reserved.
7. **Slope / elevation in IR.** `metadata.surface_z` carries it for objects;
   no first-class elevation model. Sufficient for v1.
8. **Light mode.** Rejected for v1 (Kit is dark-only). If browser SPA exposed
   standalone, light mode can derive from token-inversion.
9. **Multi-user collaboration.** Rejected for v1. Foundation supports it
   architecturally (revision-tracking, modality contract); UI changes deferred.
10. **CadCreator integration.** Anton's parallel project. Foundation is
    receptive to a CadCreator → LayoutSpec adapter when CadCreator's output
    format stabilizes; no commitment in this spec.

---

## 22. References

- Original spec: `docs/specs/2026-05-08-floor-plan-tool-spec.md` (superseded)
- Six-agent Opus critique: `docs/specs/2026-05-08-floor-plan-tool-opus-critique.md`
- Multimodal working draft: `docs/specs/2026-05-08-multimodal-foundation-working-draft.md`
- Three-agent Opus deep analysis: `docs/specs/2026-05-08-multimodal-opus-deep-analysis.md`
- Session summary + handoff: `docs/specs/2026-05-08-session-summary-and-handoff.md`
- Canonical task gap analysis: `docs/specs/2026-05-08-canonical-task-gap-analysis.md`
- Harness layers + failure modes: `docs/specs/2026-05-08-harness-layers-and-failure-modes.md`
- **Multi-session coordination** (this session + controller-logic session):
  `docs/specs/2026-05-09-multi-session-coordination.md`

Source files referenced:
- `service/isaac_assist_service/chat/orchestrator.py`
- `service/isaac_assist_service/chat/canonical_instantiator.py`
- `service/isaac_assist_service/chat/tools/template_retriever.py`
- `service/isaac_assist_service/chat/tools/tool_executor.py`
- `service/isaac_assist_service/chat/vision_gemini.py` + `vision_router.py`
- `service/isaac_assist_service/chat/routes.py`
- `service/isaac_assist_service/main.py`
- `exts/isaac_5.1/omni.isaac.assist/ui/chat_view.py`
- `workspace/templates/CP-01.json` through `CP-05.json`

Memory references:
- `project_isaac_assist_spec_generator_reverted` — regex-family aversion
- `project_isaac_assist_typed_resolvers` — atomic-additive-named pattern
- `project_isaac_assist_silent_success_audit` — honesty audit cost
- `feedback_isaac_assist_kit_concurrency` — Kit single-tenant constraint
- `feedback_diligence_no_false_positives` — verify before delegating
- Memory note on ChromaDB segfault — global write lock requirement

---

## Author Position

This spec is the synthesis of three rounds of critical analysis (six-agent
critique → working draft → three-agent deep analysis) plus the original
specification. It addresses the high-severity issues from the prior critique
rounds and the design questions from the deep analysis.

**Updated 2026-05-09** with three coordination-driven additions:

1. **UI design decisions locked**: `👁 Modes` launcher (header), horizontally
   scrolling canonical-derived quick-prompts (replaces 3-button row),
   canvas-mirror panel with three-state visibility model (Hidden/Proposed/Live)
   and Variant-X 2D-mirror approach (Variant-Y 3D-ghost-prims rejected).
2. **Block 1 split into 1A (parallel-safe) and 1B (held)**: 1A advances
   concurrently with the controller-logic session's 100% function-gate drive;
   1B waits for that signal before refactoring `verify_pickplace_pipeline`
   and CP-01..CP-05.
3. **Multi-session coordination doc** at
   `docs/specs/2026-05-09-multi-session-coordination.md` captures
   file-level ownership boundaries, sync points, branch protocol, and the
   sectional-ownership pattern for shared files (`tool_executor.py`,
   `tool_schemas.py`).

**The architectural cost is real.** Three coupled refactors (IR + role-based
templates + verifier registry) must land together to deliver value. Half
delivers worse than zero. The implementation sequence in §20 reflects this.

**Block 1A alone is significant parallel-safe value** — backend foundation +
Kit UI additions + browser canvas SPA scaffold can all advance now without
conflict with the controller-logic session. **Block 1B unblocks the roadmap's
primary goal** (Open Q E: LLM ignoring listed paths) by making role bindings
first-class structured data instead of paths the LLM is asked to respect via
directive.

**The strategic question (build canvas at all?) remains open.** Block 4 is
the largest commitment in this spec. If Blocks 1A.1 and 2 land, role-based
templates fire reliably, and text-prompt + voice + viewport-edit cover the
use cases, the canvas-wiring Block 4 may not be necessary. The decision can
be deferred until Blocks 1-3 are measured. Block 1A.3 (canvas SPA scaffold)
is parallel-safe regardless and gives optionality on the strategic question.

The next session evaluates this and decides scope.
