# Floor Plan Tool Spec — Opus Deep Critique

Authored 2026-05-08. Six Opus agents reviewed the floor plan spec from distinct
perspectives. This document synthesizes their findings for the next session to
evaluate alongside the original spec.

The single most important finding: **the strategic agent (Agent 7) recommends NOT
BUILDING the tool as specified**. The other five agents recommend specific
modifications if it is built. These views are not contradictory — they answer
different questions ("should this exist?" vs "if it exists, how should it be
built?"). Both perspectives are documented below.

---

## 0. Top-line Verdicts

| Agent | Perspective | Verdict |
|---|---|---|
| 1 — Adversarial review | Engineering risk | **GO with significant mods** — 4 high-severity issues, 21-30 days estimate is 2× optimistic |
| 2 — Cognitive ergonomics | UX / mental models | **Modify** — replace tabs with split-view; rename proposed/committed to sketched/built |
| 3 — Failure cascade | Reliability / state | **Modify** — 8 high-severity cascade scenarios, 5 missing mechanisms |
| 4 — Architecture reversibility | Bet-the-farm decisions | **Modify** — replace JSON-per-session with SQLite, swap Phase 4 ↔ Phase 5 |
| 5 — Gap audit | What's missing | **Modify** — 5 must-fix gaps before Phase 1 (testing, telemetry, migrations, i18n, security) |
| 6 — Visual design rigor | "Bara det bästa" standard | **Modify** — custom robot silhouettes, persistent chat ribbon, agency-tier color recoding |
| 7 — Strategic positioning | Should this exist? | **DO NOT BUILD** — solves wrong problem; Alt B+A (~9 days) directly serves goal better |

---

## 1. The Strategic Challenge (Agent 7)

The most consequential finding. Agent 7's argument:

### 1.1 The stated goal vs the floor plan tool

Anton's stated goal: *"type a free-form prompt → working scene."*

Floor plan tool stated value: *"2D top-down spatial editor for robot cells."*

**These are not the same problem.** The goal optimizes for *less talking, more
building*. The floor plan tool re-introduces talking — just in a different
modality (drag instead of prose). It does not eliminate friction; it relocates it.

### 1.2 Hard-instantiate already does the goal for T1

CP-02 owngoal demo: `prompt → hard-instantiate → settle → verify` works as a
single backend turn. The friction in the goal-pipeline today is **NOT** "user
can't communicate spatial layout" — it's "T2/T3/T4/T5 don't fire reliably,
ranking is fragile, LLM hallucinates paths in T5."

**The floor plan tool addresses none of these.**

### 1.3 ROI vs Open Questions

| Path | Effort | Goal-alignment |
|---|---|---|
| **A. Template ranking** | 1-3 days iterative | Direct — increases T1 fire-rate (commit `a812a26` already shows the pattern) |
| **B. T2 parameterized canonicals** | 2-4 days | Direct — multiplies coverage from 5 canonicals to 5×N parameter families |
| **D. T3 composition** | 5-10 days | Direct — addresses actual LLM-reasoning gap |
| **E. LLM ignoring paths** | 1-2 days | Direct — closes residual hard-instantiate quirk |
| **Floor plan tool** | 21-30 days, 6 new LLM tools, new SPA, new persistence layer | Indirect — sidesteps actual reasoning gap |

A+B+D+E together: **8-18 days targeting measured failure modes**.
Floor plan: **21-30 days targeting unmeasured assumed pain**.

### 1.4 MULTIMODAL-01 may obsolete floor plan

Floor plan tool is essentially MULTIMODAL-01 minus the parsing problem. The user
does the "parsing" themselves by dragging structured objects.

**This is exactly the wrong direction.** The cost of the parsing problem is the
LLM call (which Anton already pays). The cost of the structured-input UI is the
user's time, every session.

MULTIMODAL-01 with Gemini Robotics-ER (~8h, per prior research) plausibly
obsoletes floor plan tool entirely — user sketches/photographs/describes →
LLM parses → existing canonical machinery fires.

### 1.5 3D viewport already exists

Isaac Sim viewport supports drag-prims, scale, rotate. The floor plan tool's
primary differentiator (top-down 2D) is achievable as orthographic camera + grid
overlay in the existing Kit viewport at near-zero cost. Onshape and Fusion 360
explicitly chose 3D-with-top-view over 2D-floor-plan because the moment you
commit to 2D, you've lost the surface_z dimension.

### 1.6 Recommendation if NOT BUILDING

In order:
1. **MULTIMODAL-01 sketch input via Gemini Robotics-ER** (~1 day)
2. **Open Q A (template ranking) + Open Q B (T2 params)** (~5 days)
3. **Open Q E (paths in directives)** (~1 day)
4. **Run agent-eval at scale** (~2 days)
5. **THEN re-evaluate floor plan tool with data**

If after measurement spatial-editing demand is clear, build **Alt C (read-only
preview, ~5 days)** first. Don't jump to full editing tool.

Net: **~9 days that directly advances the stated goal**, vs **21-30 days of
tool that addresses an unmeasured pain**.

### 1.7 Minimal experiment if GO is overridden

Day 1-2: Build Alt C (read-only preview, no editing).
Day 3: Live with 3 real prompts (assembly, sorting, compact-2x2).
Day 4: Decision point — *"did the preview catch any 'wrong scene' agent generated?"*
- YES → continue with full floor plan
- NO → kill project; Alt B+A are the right path

---

## 2. High-Severity Engineering Issues (Agent 1)

If the tool IS built, these four issues must be resolved before Phase 1:

### HIGH-1: Auto-query generation re-introduces spec_generator regression

**Location**: §9.2, §8.6, §9.7

**Problem**: §1.3 P1 claims the tool is "deterministic." But §9.2 introduces
server-side classification: floor plan structure → English description →
embedding lookup → canonical retrieval → suggestion. **This is exactly the
shape of the spec_generator pipeline reverted on 2026-04-19.**

**Mitigation**:
- Cut §9.2 auto-query and §8.6 canonical-match suggestion entirely from v1
- Replace §8.3 compact summary with stable structured JSON dump (no NL synthesis)
- Replace §9.7 layout-intent regex with explicit user action (button click)

### HIGH-2: User-renamed objects break canonical round-trip

**Location**: §9.4 `_patch_verify_args` helper

**Problem**: CP-01.json hardcodes `/World/Franka` across `code` field, `verify_args`,
`simulate_args`, `settle_state.cubes` (5 keys), `settle_state.conveyors`,
`setup_pick_place_controller(...)`. If user renames Franka → "FrankaLeft", the
spec patches `verify_args` only. Other 5 sites still reference `/World/Franka`.

**Mitigation**:
- Extend to `_patch_canonical_for_floor_plan(template, floor_plan_state)` with
  AST-based source-level rewrite of `code` + verify_args + simulate_args + settle_state
- Add unit test: load CP-01, rename Franka, run end-to-end, assert pipeline_ok=True
- Make size mapping (§3.6) class-by-class adapter, not single table entry

### HIGH-3: 376-handler tool surface vs 6 floor-plan tools

**Location**: §3.1 (16 object classes), §8.1 (6 tools)

**Problem**: tool_executor.py has handlers for `add_revolute_joint`,
`set_drive_gains`, `apply_dr_preset`, `setup_loco_manipulation_training`, etc.
The floor plan tool cannot express any of these. Spec quietly asserts floor
plan view = canonical view = build-tool view. Only true for CP-01..CP-05 family.

**Mitigation**:
- Add §3.1.0 "What this tool does NOT model" — explicit boundary
- Add `metadata.custom` escape hatch (already half-present)
- Define post-instantiate floor-plan-edit policy

### HIGH-4: localStorage tab-lock + autosave race

**Location**: §10.1, §2.4, §2.5

**Problem**: Three independent persistence mechanisms (server JSON, localStorage,
tab-lock) with three different cadences. Multiple lost-edit windows. Service
workers not addressed. SSE backpressure not specified.

**Mitigation**:
- Single source of truth: server, not localStorage
- localStorage as write-ahead log only
- Cancel debounce on `beforeunload` via `navigator.sendBeacon`
- Drop tab-lock; let last-writer-win at field granularity
- Specify SSE backpressure with size limits

### Additional from Agent 1

- 21-30 day estimate is **2× optimistic** — realistic is 35-50 days
- Phases 4 and 5 carry integration risk and may double
- §0.1 missing: "Why this, why now" justification vs alternatives

---

## 3. Cognitive Ergonomics (Agent 2)

### 3.1 The tab metaphor is wrong

§5.1.2 makes Chat / Floor Plan / 3D Scene mutually exclusive tabs. **The user
always needs at least two of these visible.** Specifically:
- Chat + Floor Plan: agent says "here's a sorting layout" while floor plan
  animates. Tab system forces three context switches per turn.
- Floor Plan + 3D Scene: did the build succeed?

**Recommendation**: replace tabs with split-view layout. Chat anchored as 380px
right rail (matching existing chat_view.py width). Floor Plan as main canvas.
3D Scene as togglable overlay. Tabs become layout presets (VS Code panel system).

### 3.2 "Proposed vs committed" is wrong vocabulary

Borrowed from git/version-control. Maya gets it; Alex thinks "is it real?"; Erik
wants CAD vocabulary.

**The metaphor that fits CAD**: *sketched vs built* (Onshape/SolidWorks). A
sketch can have undefined geometry; the solid is the result of feature-execute.
The visual language already maps. Spec is internally inconsistent —
`commit_floor_plan` (developer-facing), Accept (§5.7), Build (§7.4) — three
verbs for one operation. Pick one: **Accept = ack proposal; Build = run canonical**.

### 3.3 Persona-specific friction

- **Maya** (code-first): wants `View as JSON` button, no canvas. Spec gives 0 affordances.
- **Alex** (30-min clock): the [Build][Adjust][Discard] confirm bar is too many
  decisions. Wants 2 buttons: [Build it][Try again].
- **Erik** (AutoCAD veteran): expects safety-zone polygons (ISO 10218 / ANSI
  R15.06) around robots. Spec only has reach circles — Erik will ignore as
  "kindergarten."

### 3.4 The reach circle is a top-down lie

Reach is actually a torus minus self-collision sphere minus base-blocked
hemisphere. 0.855 m is max at favorable z; at z = base + 0.5 m it's ~0.55 m.
**Maya knows this. When the tool says "within reach (green)" but the gripper
cannot approach, she will distrust the tool from that moment forward.**

Add inline `i` glyph with tooltip: "reach is 2D approximation, not IK-validated
workspace."

### 3.5 Top 3 changes (Agent 2 ranking)

1. **Replace tab system with split-view** — affects every agent-driven flow
2. **Rename proposed/committed → sketched/built** — single rename, large cognitive return
3. **Add T-tier match badge + structured notes flags** — surfaces canonical-first
   architecture as visible UX; replaces regex-fragile note parsing

---

## 4. Failure Cascade (Agent 3)

8 high-severity scenarios + 14 medium. Top 5 missing mechanisms:

### 4.1 Document revision + compare-and-swap

Every floor plan has `revision: int`. Every POST /patch carries `parent_rev`.
Server rejects mismatches with 409 Conflict. Per-session asyncio write-lock.
**Solves**: autosave races, build-reads-stale-state, two-tab conflicts, undo drift.

### 4.2 Event-sourced build log + resume

Build is server-side task with `build_id`, persistent state record, append-only
log of `{tool, args, status, ts}`. Client polls/SSEs by `build_id`. Resume from
arbitrary tool index. Cleanup-on-failure with paths from log.
**Solves**: hard-instantiate dies mid-build at tool 7/12 leaving partial scene,
SSE event lost mid-build, user closes tab mid-build, T5 fallback partial state.

### 4.3 Expanded sim_state state machine

Current: `unbuilt | building | live | error`.
Add: `partial`, `verify_failed`. Each with structured detail (`last_build_progress`,
`verify_issues`, `sim_dirty`). UI distinguishes "needs build" from "needs verify"
from "needs reset."

### 4.4 Three-way merge UI on stale-state recovery

Spec offers binary choice (reload-from-server vs keep-local). When local + server
diverge, show per-change checkbox diff. Backed by server-side journal of last 5
minutes of patches.

### 4.5 Cross-session resource locks

Server-tracked locks for: (a) per-session active writer (force-take UX), (b) Kit
RPC build slot (queue + position), (c) ChromaDB write serialization. All visible
to UI as status indicators.

**Solves**: ChromaDB segfault on parallel writes (memory note), Kit RPC
single-tenant concurrent builds, two browser windows same session.

### 4.6 Worst-case scenario (compound)

User builds 2-hour layout. Click Build. Hard-instantiate sim=0.62 margin=0.18 →
falls to T5. Agent generates 28 tool calls. Tool 17 fails (Kit OOM). Agent chat
truncates oldest tool_results. Browser autosaves mid-mutation. User reloads.
**End state**: 30 prims worth of build progress irrecoverable, 60s of edits
lost, agent's chat reasoning truncated, trust in tool destroyed.

---

## 5. Architecture Reversibility (Agent 4)

### 5.1 JSON-per-session is BET-THE-FARM

**Most under-defended decision in the spec.** Reversal cost: 1 week code +
migration script for existing data; 3-4 weeks if cloud auth needed later.

Spec doesn't address:
- No version history (accepted agent proposal overwrites good layout, gone forever)
- No multi-tab story beyond localStorage tab-lock
- No schema migration story (forward-only on first read, no rollback)
- No backend concurrency (two POSTs same session — last-writer-wins contradicts
  spec's stated (id, field) pair semantics because file-rewrite is whole-document)

**Recommendation**: SQLite with `revision: int` column from day 1. The JSON-file
feel preserved with `GET /api/v1/floor_plan/{session_id}/export` returning
pretty-printed JSON view. **2-day decision that prevents 3-week migration in v1.5.**

### 5.2 Phase 4 ↔ Phase 5 ordering wrong

**Spec order (1, 2, 3, 4, 5)**: lets demo "manual layout → CP-01 build" at end
of Phase 4. **The standalone-tool-without-agent isn't a real user shape for this
product.** The Anton-product is LLM-driven. That user doesn't exist.

**Recommended (1, 2, 3, 5, 4)**: build LLM contract first; canonical-translation
is easier and better-isolated. If Phase 5 reveals mutation shape is wrong, you
discover it in 5 days not 12.

### 5.3 `_patch_verify_args` is wrong shape

Spec's heuristic patcher is "magic" path-rewriting. Works for CP-01 with one
robot. Fails on multi-robot template (which floor plan robot maps to which stage?).

**Recommendation**: templates declare `floor_plan_binding` field that names which
floor-plan object IDs map to which `verify_args` slots. Explicit beats heuristic.
30 lines per template, 5 templates total. Aligns with `project_isaac_assist_typed_resolvers`
pattern (atomic, additive resolvers).

### 5.4 Hidden assumptions

The spec rests implicitly on:
- Isaac Sim Kit stays single-user, single-tenant, single-stage
- Anthropic/Gemini APIs keep structured-output / function-calling
- Canonicals stay roughly current code-field-as-Python shape
- User base stays single-user
- Linux-primary, browser-localhost deployment
- Two Isaac versions (5.1, 6.0)

If any breaks, hidden coupling cost is large.

---

## 6. Gap Audit (Agent 5)

5 must-fix gaps before Phase 1 begins:

### 6.1 Test plan + FP-01..FP-N task specs

Spec has zero test plan. Existing project has 30+ test files, smoke-test culture,
direct_eval/canary suite. Floor plan must inherit. **Cost: half-day for plan + 5
task specs.**

Minimum FP task specs: cold_start_franka, agent_generates_layout,
constraint_tight_2x2, canonical_round_trip, user_renames_paths, AD prompts for
invalid layouts and out-of-reach.

### 6.2 Telemetry event schema

`provider_incidents.jsonl` mentioned once; no floor-plan event taxonomy.
**No way to know if tool is used vs ignored.** Add `workspace/floor_plans/events.jsonl`
with documented schema covering opened, object_placed, agent_proposal_shown/resolved,
canonical_suggestion_shown/resolved, build_triggered, build_result.

### 6.3 Schema migration plan

`floor_plan_version: "1.0"` declared, no migration path. **Day-1 disk format
becomes immortal.** Add `service/.../floor_plan/migrations/` skeleton + version-bump
test fixture. **1 hour now; weeks later.**

### 6.4 i18n string-extraction wrapper

Anton works in Swedish. UI strings hardcoded English in spec. Adopting
`react-i18next` after Phase 6 polish is 5× more expensive than during Phase 1.
Default strings stay English; the wrapper goes in place day 1.

### 6.5 Security threat model

Routes are `/api/v1/floor_plan/{session_id}/...` with no auth. `allow_origins=["*"]`.
Notes may contain customer/proprietary data. Notes fed to LLM cloud providers.
**Document threat model + add `notes_sensitive: bool` flag** that strips before
LLM context distillation.

### 6.6 Three categories spec is entirely silent on

- **Cost / quota / rate-limit policy**: agent-generated layouts call cloud LLMs;
  no per-session call cap
- **Audit trail for committed builds**: no record of "who built what from what plan"
- **Multimodal input** — photo/sketch as starting state for floor plan; CadCreator
  parallel project does this; spec says "data model is compatible" but no UI surface

---

## 7. Visual Design Rigor (Agent 6)

### 7.1 The current design is generic

Verdict: *"Strong skeleton, generic skin. The spec is rigorous about WHAT exists;
it is timid about WHAT MAKES IT Isaac Assist's."*

Failure points:
- Background `#111214` could be any tool — Linear/Vercel/Figma default
- 6 distinct semantic uses of NVIDIA green (selection, status, accent, etc.) → noise
- Class colors arbitrary; deuteranopia simulation fails on 3 of 4 critical pairs
- Object representation as outlined-with-light-fill is minimum viable
- Snap markers wholesale-copied from AutoCAD (cyan/amber/green) clash with NVIDIA dark aesthetic
- 11px default typography copied from generic web; 12-13px is CAD/Onshape standard
- 8px grid wrong for power-user density; should be 6px in technical panels

### 7.2 Top 3 visual changes

1. **Custom robot silhouettes** (~2 designer-days): Franka, UR5e, UR10e, Kinova,
   IIWA, Jaco7, Carter, Quadruped. Top-down 32×32 px single-color SVG. **No other
   CAD tool does this.** Highest ROI distinguishing investment.

2. **Persistent chat input ribbon across all modes**: a 40px chat input above
   status bar, in *every* mode (Chat, Floor Plan, 3D Scene). Not just chat-mode.
   The chat is the home base; the canvas is what changes above it. **This is the
   "grön bok" feel — the conversation never leaves you.**

3. **Class colors recoded by agency tier, not arbitrary hue**:
   - Tier A (autonomous): robots in single signature blue, luminance variants
   - Tier B (powered: conveyors/sensors): amber/teal high saturation
   - Tier C (passive: bins/cubes/tables): desaturated greys with subtle hue tint
   - Tier D (boundaries): pure neutral
   Information hierarchy emerges from color, not despite it.

### 7.3 Visual North Star (proposed)

> *"A still and gridded dark room where machine intent (agent, robot, motion)
> glows in two restrained colors against unobtrusive geometry, and every
> interaction is announced by exactly one well-timed motion."*

Use to evaluate any new screen: third color? second motion? visual noise where
geometry should suffice? If yes, redesign.

### 7.4 Motion vocabulary (spec lacks one)

Define discipline:
| Token | Duration | Easing | When |
|---|---|---|---|
| `instant` | 0ms | none | Mid-drag updates |
| `flash` | 80ms | linear | Snap acquisition |
| `react` | 160ms | ease-out-cubic | Hover, button press |
| `commit` | 200ms | ease-out-cubic | Object placement |
| `arrive` | 240ms | overshoot(0.34, 1.56, 0.64, 1) | Confirm bar appear, modal open |
| `transit` | 280ms | (0.4, 0, 0.2, 1) | Mode switch |
| `breathe` | 1600ms | sine | Status pulse |

Never-mix rules: arrive + transit (overshoot during slide reads buggy); animate
both moving object + dependent annotation (reach circle desyncs).

### 7.5 NVIDIA + CAD tension — synthesizing principle

Risk: schizophrenic. Two design lineages without bridging principle.

**Fix**: write into spec verbatim — *"CAD form, NVIDIA hand"*. Every CAD signifier
uses NVIDIA color/motion/typography vocabulary, not its native CAD vocabulary.
Dimension annotations: ISO-129 *geometry*, NVIDIA palette. Snap markers: AutoCAD
*types*, NVIDIA monochrome treatment. Constraints: AutoCAD *semantics*, NVIDIA
visual language.

---

## 8. Cross-Cutting Themes

Several findings appear across multiple agents:

### 8.1 Regex-family fragility (Agents 1, 2, 4, 6)

Multiple agents independently flag that the spec quietly re-introduces classification
fragility:
- Auto-query generation (Agent 1)
- Layout-intent detection (Agent 1)
- Free-text notes parsed by LLM (Agent 2)
- `_patch_verify_args` heuristic (Agent 4)
- Auto-suggest canonical match (Agent 1)

This is the same family the project has worked away from
(`project_isaac_assist_spec_generator_reverted`).

### 8.2 The "harness deterministic" principle is violated (Agents 1, 7)

Spec adds 6 new LLM tools. Project memory `project_isaac_assist_silent_success_audit`
documents 10 honesty holes found in just first audit slice of 344 handlers. Adding
6 tools = 6 future audits.

### 8.3 The phasing sequence is wrong (Agents 1, 4)

Standalone-tool-without-agent (end of Phase 4) is not a real user shape. Phase 5
agent integration should come first to surface contract bugs before Phase 4
canonical translation builds on assumptions.

### 8.4 The 21-30 day estimate is optimistic (Agents 1, 5)

Realistic estimate: 35-50 days. Phases 4-5 carry integration risk; Phases 6-7
(polish, accessibility) always blow estimates.

### 8.5 Persistence layer needs re-architecting (Agents 1, 3, 4)

JSON-per-session is bet-the-farm and under-defended. Multiple cascading failure
modes (Agent 3) trace to weak persistence semantics. Agent 4 recommends SQLite
with revision column from day 1.

---

## 9. Synthesized Recommendation Stack

Combining the agents' findings:

### If GO is decided, REQUIRED before Phase 1 begins:

1. **Strategic justification document** (Agent 7): write a §0.1 to the spec
   answering "Why this, why now" — comparing against MULTIMODAL-01 (~8h) +
   T2 parameterized canonicals + better template ranking.

2. **Cut classification surfaces** (Agent 1): remove auto-query generation,
   canonical-match-suggestion, layout-intent regex, NL synthesis in compact summary.

3. **SQLite + revision column** (Agent 4): replace JSON-per-session persistence.

4. **Phase swap** (Agent 4): build agent integration (current Phase 5) before
   canonical translation (current Phase 4).

5. **Five gap fixes** (Agent 5): test plan + FP task specs, telemetry schema,
   schema migration plan, i18n wrapper, security threat model. Each ≤4 hours;
   combined ~2 days.

6. **Template-declared floor_plan_binding** (Agent 4): replace `_patch_verify_args`
   heuristic with explicit per-template binding fields.

7. **Document revision + 5 missing mechanisms** (Agent 3): add server-side resource
   locks, event-sourced build log, expanded sim_state, three-way merge UI.

8. **Visual design system locked** (Agent 6): tokens, motion vocabulary, custom
   silhouettes, persistent chat ribbon, agency-tier colors. Cannot be Phase 6
   afterthought.

9. **Re-estimate to 35-50 days** (Agents 1, 5): with Phase 4-5 integration risk
   buffer.

### If NOT GO (Agent 7's recommendation):

1. **MULTIMODAL-01 sketch input** (~1 day): Gemini Robotics-ER + structured-output
   prompts → existing canonical machinery.
2. **T2 parameterized canonicals + template ranking** (~5 days): Open Q A + B.
3. **Open Q E paths in directives** (~1 day).
4. **Agent-eval at scale** (~2 days): for the first time, measure where T1/T5 fail.
5. **THEN re-evaluate floor plan tool with data.**

If after measurement spatial-editing demand is clear: build **Alt C (read-only
preview, ~5 days)** first. Don't jump to full editing tool.

### Minimal experiment if GO override:

Day 1-2: Build read-only preview only. Day 3: 3 real prompts. Day 4: decision —
"did the preview catch any wrong-scene agent generated?" Three runs is enough signal.

---

## 10. Author Position

This synthesis preserves the agents' substance without forcing a meta-verdict.
The split between Agent 7 (don't build) and Agents 1-6 (build with mods)
reflects a legitimate split between *strategic* and *engineering* perspectives.

The author's read of the cumulative evidence:

- **Agent 7's strategic case is strong.** The hard-instantiate landing (CP-02
  owngoal demo) is recent and material. Agent-eval at scale hasn't run.
  Open Q A/B/E + MULTIMODAL-01 add up to ~9 days targeting measured failure
  modes, vs 35-50 days targeting unmeasured pain.
- **Agents 1-6's engineering critique is also strong.** If the tool ships, it
  must address regex-fragility, persistence semantics, phase ordering,
  testing/telemetry/migration/i18n/security gaps, and visual design rigor.
  Anything less than 35-50 days delivers something below the project's bar.

These views compose. The honest synthesis is: **postpone, run measurements,
re-decide.** Anton retains the call.

---

## 11. References

- Original spec: `docs/specs/2026-05-08-floor-plan-tool-spec.md`
- Project state: `docs/specs/2026-05-08-session-summary-and-handoff.md`
- Gap analysis: `docs/specs/2026-05-08-canonical-task-gap-analysis.md`
- Memory note (regex aversion): `project_isaac_assist_spec_generator_reverted`
- Memory note (audit cost): `project_isaac_assist_silent_success_audit`
- Memory note (Kit single-tenant): `feedback_isaac_assist_kit_concurrency`
- Parallel research: `docs/specs/2026-05-08-kcode-research-and-vault-spec.md`
