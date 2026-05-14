# Research report: Composition vs tools clarity

**Date:** 2026-05-11
**Researcher:** Opus agent (via Claude Code)
**Scope:** Verify claims in composition spec against actual codebase
**Methods:** Direct code inspection, file path + line number citations

## Executive summary

Anton is asking whether yesterday's composition spec
(`docs/specs/2026-05-11-composition-spec.md`) actually adds something
we don't already have. The short answer: **the codebase already has
three of the four layers the composition spec proposes**, just under
different names.

Key findings:

- **421 tools registered** in `tool_schemas.py` (line 11 onward); 217
  handler functions in `tool_executor.py`. Many of those tools ARE
  primitives in the spec's sense — `robot_wizard`, `create_conveyor`,
  `setup_pick_place_controller`, `place_on_top_of`, `create_bin` each
  generate 20-100 lines of USD/Python code. The composition spec's
  "franka_at" (~6 tool calls) is roughly equivalent to one call of
  `robot_wizard` (which already encapsulates path validation, USD
  reference, drives, home pose, orientation, ~120 LOC at
  `tool_executor.py:11479`).
- **Templates ARE compositions.** CP-01's `code` field at
  `workspace/templates/CP-01.json:18` is a 45-line sequence of ~25
  tool calls. CP-01 already declares `roles`, `role_defaults`, and a
  `code_template` with `{{placeholders}}` (lines 19-105). The
  composition spec just renames "template" → "composition" and
  atomizes the call sequence further.
- **"Top-5 tool matching" exists.** `tool_retriever.py` (ChromaDB
  sentence-transformers, line 81) ranks 421 tools;
  `template_retriever.py` does the same for 321 templates with
  structural-filter-first retrieval (line 380). Both are operational
  and used by orchestrator (line 913) and context_distiller
  (line 484).
- **Spatial-resolver "middle layer" already exists.** 12 `resolve_*`
  tools (`resolve_coordinate_reference`, `resolve_relational_property`,
  `resolve_constraint_phrase`, etc., schemas at
  `tool_schemas.py:524-1260`) plus `place_on_top_of` (line 1264)
  handle geometric language → numbers. They're flagged as
  `_ALWAYS_TOOLS` (always shown to LLM, `context_distiller.py:148`).
  This IS the middle layer the user remembers.
- **A primitive library was never the original architecture.** IA Full
  Spec Phase 18b (`specs/IA_FULL_SPEC_2026-05-10.md:2104`) calls tools
  L1, composed-tools L2, workflows L3, and explicitly counts ~370 L1
  + ~30 L2 + ~3 L3 today. The composition spec inserts a NEW L1.5
  layer between tools and templates. That is genuinely new — but it's
  net-additive abstraction on top of existing primitives, not
  "filling a missing layer."
- **Canvas per spec is a PRIMARY modality, not clarification.**
  Multimodal foundation spec §7.2 (line 691) gives drag-drop
  confidence=1.0, "Yes" for objects, "Yes" for bindings — the most
  reliable modality. User's reframe to "clarification" contradicts
  the current spec.

**Recommendation: pivot the composition spec.** Three-quarters of it
duplicates existing infrastructure (the spec authors didn't read the
registered tools carefully). What IS missing is *automated patch
propagation* across the 321 templates (the spec's actual problem
statement in §1.1), and that doesn't require a primitive library —
it requires either a parametric template generator or a code-mod
tool. Section 8 below proposes the alternative.

---

## 1. What we have today

### 1.1 Tools (the atom layer)

**Count:** 421 tool schemas in
`service/isaac_assist_service/chat/tools/tool_schemas.py` (the
`ISAAC_SIM_TOOLS` list starts at line 11). 217 `_handle_*` async
functions in `tool_executor.py` (35,841 lines total).

**Examples by abstraction level:**

| Level | Example | Schema line | Handler size |
|---|---|---|---|
| **Atom** | `create_prim`, `set_attribute`, `apply_api_schema` | 16, 66, 97 | ~20 LOC each |
| **Compound** | `add_proximity_sensor`, `create_bin`, `apply_physics_material` | 3400, etc. | ~50-100 LOC |
| **Macro** | `robot_wizard`, `create_conveyor`, `setup_pick_place_controller` | 3216, 3365, 3445 | 100-500+ LOC |
| **Skill** | `setup_pick_place_with_vision`, `build_scene_from_blueprint` | 2139, 1653 | Composes 5-20 tools |

`robot_wizard` (`tool_executor.py:11479`) is illustrative: it accepts
`robot_name`, looks up the asset URL, validates the path against
deprecated 4.x patterns, AddReferences the USD, applies drive gains,
configures variants, sets home joints, applies position/orientation —
200+ LOC of generated Python in one tool call. **This IS the
"franka_at primitive" the composition spec proposes (lines 142-184),
under a different name.**

**Built-in composition / chaining:**
- Function-call sequences are the LLM's natural mode (per
  `context_distiller.py:172` RULE_BASE).
- `run_usd_script` lets the agent emit an arbitrary Python block —
  full composition, but unstructured.
- `build_scene_from_blueprint` consumes a structured blueprint and
  fans out into many primitives.
- **No declarative composition file format exists** — composition
  lives in template `code` fields (string) and in agent turn-by-turn
  tool calls.

### 1.2 Templates (the composition-already-exists layer)

**Count:** 321 JSON files in `workspace/templates/`. The 109 number
refers specifically to the CP / CP-NEW canonicals.

**Structure of CP-01.json:**

| Field | Purpose | Lines |
|---|---|---|
| `task_id` | Unique ID | 2 |
| `goal` | Natural-language description | 3 |
| `tools_used` | List of tool names | 4-16 |
| `thoughts` | Non-obvious patterns | 17 |
| `code` | **Plain-text sequence of tool calls** | 18 |
| `intent` | Retrieval filter | 19-32 |
| `roles` | Named role constraints | 33-57 |
| `role_defaults` | Role defaults | 58-87 |
| `code_template` | With `{{placeholders}}` | 88 |
| `verify_args_template`, `simulate_args_template` | Templated args | 89-104 |
| `settle_state` | Post-build state | 105-135 |
| `failure_modes`, `verified_*`, `benchmark_*` | Evidence | 136-198 |

**Key observation: CP-01's `code` field IS a composition** of 11
distinct tool calls producing ~25 effects.

### 1.3 Tool retriever ("top-5 matching")

`service/isaac_assist_service/chat/tools/tool_retriever.py` — 104
lines.

- `_build_index()` (line 63) embeds every tool's `name + description
  + param-key list` as a ChromaDB document.
- `retrieve_tools(query, top_k=15)` (line 81) returns top-K tool
  names by cosine similarity.
- Used by `context_distiller.py:484-485`.

Plus `template_retriever.py` doing the same for 321 templates with
structural filter (line 380), gated by orchestrator at sim ≥ 0.45
+ margin ≥ 0.20.

**This is functioning today.**

### 1.4 Typed-resolver layer (the "middle layer" user remembers)

Located in `tool_executor.py` + `tool_schemas.py:524-1260`.

**12 `resolve_*` tools** for geometric/linguistic phrase → typed value:

| Tool | What it resolves | Schema line |
|---|---|---|
| `resolve_material_properties` | "metal", "rubber" → friction/density | 524 |
| `resolve_constraint_phrase` | "with 5cm clearance" → numeric SI | 542 |
| `resolve_sequence_phrase` | "first X, then Y" → ordered fragments | 560 |
| `resolve_context_reference` | "another one" → prim path | 578 |
| `resolve_coordinate_reference` | "origin", "corner of X" → coords | 595 |
| `resolve_relational_property` | "twice the size of X" → numeric | 618 |
| `resolve_success_condition` | "place X in Y" → verifier args | 640 |
| `resolve_skill_composition` | "pick-and-place" → tool-chain recipe | 1123 |
| `resolve_count_vagueness` | "a few", "many" → integer | 1141 |
| `resolve_robot_class` | "a manipulator" → `franka_panda` | 1168 |
| `resolve_size_adjective` | "small cube" → 0.05m | 1195 |
| `resolve_prim_reference` | "the cube" → `/World/Cube_1` | 1231 |

Plus `place_on_top_of` (line 1264) — the working pilot for
"spatial language → coordinates."

**All in `_ALWAYS_TOOLS`** (`context_distiller.py:148-162`) — always
offered to LLM. The system prompt directs agent to call them BEFORE
concrete tools.

**This IS the middle layer the user is asking about. It exists today.**

### 1.5 Canvas modality

Per `docs/specs/2026-05-08-multimodal-foundation-spec.md` §9
(lines 804-942) and §7.2 (line 689):

- **Modality reliability:** drag-drop canvas has confidence 1.0, on
  par with viewport-edit, BETTER than text (0.7-0.9).
- **Architecture:** hybrid decoupled — Kit-native chrome + canvas-
  mirror panel inside Kit + Konva-based SPA in browser tab.
- **Spec positioning:** Canvas is THE most reliable modality. The
  whole §3-7 IR machinery is designed so Canvas can emit a complete
  LayoutSpec with high confidence.

**Spec frames Canvas as primary, not clarification.** User's reframe
("ett sätt att finjustera") contradicts the written spec.

---

## 2. The user's confusion (verbatim + clarification)

### 2.1 "Va, men vi har byggt tools? Vad är byggblocks du syftar på?"

**Where confusion comes from:** Yesterday's spec line 22 claims
primitives don't exist. They DO exist — `robot_wizard`,
`create_conveyor`, `create_bin`, `create_rotary_table`. What the spec
calls "primitives" are at a slightly higher granularity (defaulting
args) than existing macro-tools.

**Genuine delta:** ~1-3 lines of args defaulting on top of tools we
already have. The spec's claim of a "missing building block layer" is
**misleading.**

### 2.2 "Jag förstår inte riktigt vad du menar med compositional system."

**Where confusion comes from:** CP-01.json `code` field IS already a
composition — of 11 tool types, 25+ invocations. The spec is renaming
"tool call sequence" → "primitive composition" without changing the
underlying execution. Same information, just one extra level of
indirection.

### 2.3 "Detta låter mer som det vi pratat om med tool chaining"

**Tool chaining IS happening today.** The agent emits N consecutive
tool calls per turn. The composition spec's `compose_scene([...])` is
a more declarative form of the same thing.

**Actual answer:** composition spec = tool chaining with three
differences:
1. The chain is **declarative JSON**, not procedural Python.
2. The primitives are **stored centrally**, not regenerated each turn.
3. Bug fixes propagate by editing the primitive, not patching N
   templates.

Only #3 is a genuine win. #1 and #2 are isomorphic to today's `code`
field + tool registry.

### 2.4 "Drag and drop canvas ser jag mer som ett sätt att finjustera"

**Two valid framings:**
- **Spec framing:** Canvas as primary entry (build from blank).
- **User framing:** Canvas as refinement (text → emit → canvas-tweak
  → re-emit).

User's framing has lower implementation cost and matches Anton's
actual workflow. Worth adjusting Phase 24b implementation priority,
not the spec itself.

---

## 3. Was "primitive library" ever the plan?

**Short answer: No, not as a library of JSON primitives.**

### Evidence from IA Full Spec

- Phase 18b (line 2104) — **L1/L2/L3 action-level taxonomy:**
  L1 = atomic (create_prim, set_attribute), L2 = composed
  (build_scene_from_blueprint, setup_pick_place_controller),
  L3 = strategic multi-phase plan. ~370 L1 + ~30 L2 + ~3 L3 today.
  The fix isn't to add a new layer between L1 and L2 — it's to grow
  L3.
- Phase 20 (line 2392) — **Role-based template refactor** — adds
  `roles` to templates so they're reusable across robot
  classes/counts WITHOUT splitting into primitives.
- Phase 25 (line 2687) — **Object palette: 17 → 60 classes** with
  metadata. This is the Canvas-side library, NOT code-emitting
  primitives.

### Evidence from memory

- `project_isaac_assist_typed_resolvers.md` — typed-variable-resolver
  pattern: "spatial-language → coordinates." This IS the missing
  layer the user remembers — and it's already being built
  incrementally as new `resolve_*` tools.
- `project_isaac_assist_spec_generator_reverted.md` — spec_generator/
  gap_analyzer/complexity axis was built 2026-04-19 and **reverted
  same day**: "caused regression; reverted same day; clean code hit
  5/5 structural (new best)." Lesson: adding new mid-layer
  abstractions tends to regress.

### Evidence from code

- **No `workspace/primitives/` directory** — verified.
- **No `compose_scene`, `list_primitives`, `describe_primitive`
  handlers** in `tool_executor.py`.
- **No semi-built primitive scaffolding.** Closest analog:
  `build_scene_from_blueprint` (one-shot orchestrator, not a
  primitive library).

### Verdict on Claude's earlier claim

> "Detta byggdes aldrig. Vi har byggt slutprodukterna men inte
> byggblocken."

**Partially accurate, but misleading:**

- **Accurate part:** the specific JSON-primitives shape proposed
  yesterday doesn't exist.
- **Misleading part:** implies building blocks are entirely missing.
  They exist as 421 typed tools (many at primitive-grain), 12 typed-
  variable resolvers for spatial language, role-based template
  pattern, ChromaDB retrieval layer.

**Honest framing:** "We have building blocks at a coarser grain than
the composition spec proposes. The spec adds a finer grain WITH AN
EXTRA LAYER OF INDIRECTION." Not the same as "we have no building
blocks."

---

## 4. Comparison: tools, templates, primitives, compositions

| Layer | Today's instance | Example call count | LLM-emittable? |
|---|---|---|---|
| **Atom tool** | `create_prim`, `set_attribute` | 1 → 1 USD op | Yes |
| **Compound tool** | `add_proximity_sensor`, `create_bin` | 1 → 3-10 ops | Yes |
| **Macro tool** | `robot_wizard`, `create_conveyor` | 1 → 50-300 LOC | Yes |
| **Typed resolver** | `resolve_coordinate_reference`, `place_on_top_of` | 1 → typed value | Yes |
| **Skill tool** | `setup_pick_place_with_vision`, `build_scene_from_blueprint` | 1 → 5-20 tools | Yes |
| **Template** (existing) | `CP-01.json` ... 109 CPs + 212 non-CP | 10-100 tools | No (retriever match) |
| **Primitive** (proposed) | `franka_at.json` (doesn't exist) | 5-15 tools | Indirectly |
| **Composition** (proposed) | List of primitive refs | 4-10 primitives | Indirectly |

### Pros vs cons of inserting the new middle layer

**Pros:**
- Single point of fix for cross-template bug
- Higher-level vocabulary for LLM ("franka_at" reads cleaner)
- Canvas drag-drop class → primitive 1:1 mapping

**Cons:**
- 30-50 new JSON files to author + version
- Composition engine = ~3000-4500 LOC new code
- 109 CP migrations + equivalence tests
- Bug-propagation could be solved WITHOUT new layer (parametric
  template generator)

**Honest take:** the spec's main argument (deduplicate cross-template
patches) is solvable WITHOUT inserting a new abstraction layer. A
**parametric template generator** is a build-time tool that emits CPs
from a single source, ~500 LOC. Smaller, easier to verify.

The runtime layer's value is mostly **LLM-driven composition** —
letting agent emit `compose_scene([...])` instead of long `code`
blocks. That value is real but smaller than spec implies.

---

## 5. Claude Code-style alternative

### 5.1 Today: prescribed-catalog model

1. User message → `template_retriever.retrieve_templates_with_scores`
2. If top sim ≥ 0.45, margin ≥ 0.20: **hard-instantiate** —
   execute template's `code` field, filter build-tools, agent verifies
3. Otherwise: inject templates as few-shot, agent re-authors via
   tool calls

In hard-instantiate path: **3 tool calls total** (verify, simulate,
reply). Soft path: 25-50+ calls.

### 5.2 Alternative

- Agent sees a small starter prompt + tool catalog
- Calls `scene_summary`, `list_all_prims`, `list_extensions` to build
  mental model
- Picks tools based on observed state, not pre-matched template
- Templates become docs (looked up via `lookup_knowledge`), not
  hidden similarity matches

### 5.3 Trade-offs

| Dimension | Prescribed (today) | Claude-Code-style |
|---|---|---|
| Tool calls per build | 3-50 | 20-80 |
| Determinism | High | Low |
| Generalization to novel | Brittle | Strong |
| 503 / payload risk | Low | High |
| Debug surface | Bounded | Unbounded |
| Success rate today | ~80-100% on canonicals | Unknown |

### 5.4 Recommendation

**Keep the hybrid (today), but invest in the SOFT path for novel-
task generalization.**

- Hard-instantiate stays for the ~150 canonical scenarios — fast,
  deterministic.
- Soft path needs Claude-Code-style enrichment: more `list_*`/`get_*`
  exploratory tools, longer trajectories accepted, templates injected
  as DOCS with full thoughts/failure_modes, agent runs as reasoning
  loop.
- `compose_scene([...])` could be a useful soft-path tool — letting
  agent emit structured composition over **existing macro tools**.
  That doesn't require 30-50 primitives.

---

## 6. Canvas modality positioning

### What spec says
Per multimodal foundation spec §7.2, §9: Canvas is one of six
modalities; drag-drop confidence=1.0; produces full LayoutSpec
including objects + bindings; can be primary entry point OR
refinement surface.

### What user thinks
Canvas as refinement / clarification of existing draft (text → text-
emitted scene → canvas-tweak → re-emit), not as a primary entry.

### Reconcile
**Both framings valid; spec accommodates both. Implementation phasing
should reflect user's framing.**

- **De-prioritize "blank-canvas → full build" path** (requires Konva
  SPA palette, snap, smart-guides, multi-select, dimensions — high
  cost).
- **Prioritize "refinement after text-emit" path** — SPA renders
  existing LayoutSpec, accepts drag, snap to nearest 0.05m, emits
  patch. Much cheaper, matches Anton's workflow.

Roadmap call, not spec rewrite.

---

## 7. The "middle layer" / spatial resolver layer

### 7.1 What exists today

12 `resolve_*` tools + `place_on_top_of` — all in `_ALWAYS_TOOLS`,
always exposed to LLM.

**The pattern is typed-variable resolver**: phrase → typed value →
agent chains to next concrete tool.

- "twice the size of /World/Cube" → `resolve_relational_property` →
  `0.10` → next: `create_prim(..., size=0.10)`
- "the corner of the table" → `resolve_coordinate_reference` →
  `[1.0, 0.5, 0.75]` → next: `create_prim(..., position=...)`
- "on top of the cube" → `place_on_top_of(source, target)` → done
- "a few cubes" → `resolve_count_vagueness` → `4` → loop
- "small cube" → `resolve_size_adjective` → `0.05` → next

### 7.2 Gaps

**Missing spatial resolvers:**
- "next to" / "vid sidan av" — partial via
  `resolve_coordinate_reference("edge_+y")`, but not dedicated
- "between X and Y" — no resolver
- "around X" / "encircle" — no resolver
- "facing X" / "oriented toward X" — no generic facing resolver
- "perpendicular to" / "parallel to" — no resolver
- "<adverbial> closer/further/higher/lower" — no resolver

### 7.3 Architecture for the gaps

The typed-variable-resolver pattern in memory: **atomic + additive**.
Each new spatial phrase becomes a new `resolve_*` tool. **No mid-
layer required**; tools compose at the agent level via natural
language parsing → resolver → concrete tool.

**The spatial-resolver work is orthogonal to the composition spec**,
and is arguably the more important investment.

---

## 8. Recommendation

### Option A — Keep composition spec as written

**Pros:** Comprehensive, integrates with Canvas.
**Cons:** 3000-4500 LOC new code; 50+ primitive JSONs; 109
migrations; equivalence-test framework — multi-month investment.

### Option B — Modify composition spec (recommended)

**Three changes:**

1. **Remove the JSON primitive layer.** "Primitives" become the
   existing macro-tools (`robot_wizard`, `create_conveyor`, etc.) —
   they ARE primitives at the right grain.
2. **Keep `compose_scene` as a new tool** — composes existing tools,
   not JSON primitives. ~200 LOC instead of ~4500.
3. **Address the cross-template-patch problem differently.** Build a
   **parametric template generator** — one Python file emits
   CP-01.json, CP-02.json, ..., parameterized over `n_cubes`,
   `robot_class`, `belt_speed`. ~500 LOC.

This gets ~80% of the spec's value at ~10% of the cost. Aligns with
IA Full Spec's L1/L2/L3 taxonomy without inserting a new L1.5 layer.

### Option C — Pivot entirely

**Replacement: "Patch-Propagation Tooling + Targeted Spatial-Resolver
Expansion".**

- Build parametric template generator (Option B point 3)
- Add missing spatial resolvers (`resolve_between`, `resolve_around`,
  `resolve_facing`, `resolve_parallel_to`) — each is atomic, ~50 LOC
- Improve template retrieval ranking (role-based filter extension)
- Drop composition spec entirely

### Honest assessment

The composition spec is well-written and internally consistent, but
it solves a problem at the WRONG layer. Actual pain points:

- **Cross-template patches** → parametric template generator
- **Spatial language** → more `resolve_*` tools (already happening)
- **Tool chaining for novel scenes** → better retrieval + better
  thoughts/failure_modes, not new abstraction
- **Canvas not yet useful as primary entry** → refine-mode-first SPA

**Top recommendation: pivot to Option B.** It captures the spec's
best ideas (centralized fix points, `compose_scene` for declarative
chaining, Canvas integration) without the heavy primitive-library
investment. The spec author seems to have undercounted the existing
macro-tools, typed resolvers, and role-based template machinery.
