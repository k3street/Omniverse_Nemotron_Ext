# Canonical, Flow, and Autonomous-Execution Standardization Spec

**Date:** 2026-05-15
**Author:** Phase 4 synthesis (Opus 4.7, 1M context)
**Status:** Final implementation spec — pending user approval
**Predecessor:** `docs/specs/2026-05-15-research-spec-flow-canonicals-autonomous.md` (parent brief)
**Companion artifacts:**
- `docs/specs/2026-05-15-autonomous-execution-plan.md` (human-readable execution plan)
- `config/cron_task_graph.yaml` (machine-readable task graph)

---

## §0. Summary

### 0.1 What this spec lands

This spec consolidates the eight 2026-05-15 research reports into a single
implementation plan that touches three interlocking areas:

1. **Canonical template schema** — formalizes the JSON shape of every
   template under `workspace/templates/` (Q2), prescribes a mechanical +
   human migration of 104 templates from the legacy core-6 shape to the
   role-based super-shape, installs a CI conformance gate, and remediates
   the 30-ID `TP-*` ghost corpus that today returns dangling references
   (Q4).
2. **Prompt-to-execution flow** — locks the 24-bucket intent × complexity
   decision matrix (Q1), commits to the three surgical changes Q1 §5
   recommends (scout pass for `scene_diagnose`, workflow auto-routing for
   `patch_request × complex`, optional spec post-condition enforcement),
   wires the role-retriever as a pre-filter (Q3), and gates iterative
   retrieval behind a benchmark-first ROI check (Q8).
3. **Autonomous-execution pipeline** — defines the canonical-creation
   pipeline (Q6 §8), the Gemini Flash stress-test harness (Q5), the cron
   dispatcher (Q7), and the halting criteria. The pipeline aims to chew
   for ~6 weeks autonomously, gated by Kit-RPC single-tenancy, uvicorn
   restart hooks, and ChromaDB write serialization.

This is the production-promotable plan derived from Q1–Q8 + the L-levels
audit. Every claim in this document cites the source-research file and
section.

### 0.2 What this spec is NOT doing

To prevent scope creep and respect the diligence rule from Honesty Charter:

1. **Not authoring canvas UX.** The `docs/research/2026-05-14-canvas-ux-research.md`
   work stands as a separate research stream and is deferred — it is the
   subject of a future spec that depends on the chat-side improvements
   here landing first.
2. **Not annotating L1/L2/L3 across 437 tools.** Phase 18b's tool-level
   axis remains 0/416 annotated [L-levels audit p.4]. This spec pilots
   annotation on 20 tools (t63) and produces a design doc (t64) about
   whether the router should consume the labels — full annotation is a
   separate cost block.
3. **Not wiring VLA / RL / contact-rich beyond what Compliance v2.1
   already does.** GR00T finetune wiring stays at the evaluator-hook
   level; Pi0 and OpenVLA are not on this spec's surface. cuRobo Warp
   1.11.0 remains the configured planner (per
   `project_isaac_assist_warp_upgrade.md`). Contact-rich manipulation
   stays governed by the in-flight
   `docs/specs/2026-05-11-contact-rich-manipulation-spec.md` (CRM v2.1).
4. **Not removing the legacy `code` field** from any template. Phase 4 of
   the Q2 migration (removing `code` after wiring
   `instantiate_role_based_code` into `execute_template_canonical`) is
   explicitly deferred [Q2 §6.4 row "Phase 4"; Q7 §12 row "Phase 4
   (remove `code`) — NOT scheduled"].
5. **Not promoting Gemini Flash to a production driver.** Flash stays a
   stress-test instrument; Claude Sonnet remains the production
   orchestrator backend [Q5 §8].

### 0.3 Reading guide

Sections §1–§8 are normative. Each contains:
- Decision (locked behavior)
- Source citation (Qn §x or audit p.y)
- Acceptance criterion (how we know it landed)

Sections §9–§12 are normative meta:
- §9 explicit out-of-scope
- §10 acceptance criteria per section
- §11 top risks + mitigations
- §12 dependencies

---

## §1. Conceptual model (LOCKED)

### 1.1 The three layers

The conceptual model from the parent research-spec §0 is **confirmed with
one refinement** based on Q1, Q2, and the L-levels audit:

```
┌─────────────────────────────────────────────────────────────────┐
│ Canonical (Template)                                            │
│   = highest abstraction, ordered tool-chain                     │
│   = encoded as JSON under workspace/templates/*.json            │
│   = unit the LLM matches against / instantiates                 │
│   = schema = Q2 §3 CanonicalTemplate interface                  │
└────────────────────┬────────────────────────────────────────────┘
                     │ composed of (declared in template.tools_used
                     │              and executed by template.code or
                     │              template.code_template)
                     ▼
┌─────────────────────────────────────────────────────────────────┐
│ Tools (L1 / L2 / L3 atomicity axis)                             │
│   L1 = atomic single-op  (create_prim, set_attribute)           │
│   L2 = composed multi-op atomic (robot_wizard,                  │
│         build_scene_from_blueprint)                             │
│   L3 = async multi-phase (start_workflow)                       │
│                                                                 │
│   "L" = atomicity / sync-async axis. NOT complexity. NOT        │
│   difficulty. A canonical containing 100 L1 tools is "complex"  │
│   regardless of L-level on individual calls.                    │
└─────────────────────────────────────────────────────────────────┘
```

Source: parent research-spec §0; confirmed by L-levels audit's
finding that L1/L2/L3 is *tool-only* taxonomy per Phase 18b [audit p.2].

### 1.2 Refinement vs the research-spec §0

The research-spec §0 said complexity is "emergent from canonical
composition." Q1 §3 and the L-levels audit p.4 expose a tension: the
orchestrator already has a separate `complexity ∈ {single, multi,
complex}` classifier output from `classify_intent` [Q1 §1, code at
`intent_router.py:22`]. This is an *intent-level* complexity (how the
user phrased their request), distinct from *canonical-level* complexity
(how many tools the chosen template executes).

The refinement:

> **Two complexity axes co-exist:**
> - **Prompt complexity** (from `classify_intent`) is the dispatch
>   signal. It routes to negotiator, spec_generator, workflow
>   auto-route.
> - **Canonical complexity** (emergent from `len(template.tools_used)`
>   and presence of `start_workflow` calls in `template.code`) is
>   the execution signal. It governs the function-gate cost and
>   the Kit-restart cadence.

Source: Q1 §3 (decision matrix uses `complexity` as a routing axis);
Q7 §8 (Kit-restart-every-30 cadence is a canonical-complexity decision).

The research-spec §0 phrasing remains correct in spirit (complexity is
emergent from composition, not a tool property). The refinement just
acknowledges that the orchestrator uses prompt-side complexity *as a
proxy* for canonical-side complexity, because at classification time the
canonical hasn't been chosen yet.

### 1.3 What `pattern_hint` is

`pattern_hint ∈ {pick_place, sort, reorient, navigate}` is a closed enum
inside `intent.pattern_hint` on each canonical [Q2 §3]. It is the
structural-filter retrieval key, not the L-level. The L-levels audit
p.5 explicitly answers "is `pattern_hint` the template's L-level?" with
"no, pick one" — this spec **picks the second option from audit §(a)**:
"templates are L2-by-definition (one deterministic tool-chain) and
`start_workflow` is exclusively L3."

Source: L-levels audit p.5 §(a); Q2 §3 PatternHint enum.

---

## §2. Flow architecture decisions

### 2.1 Mode-per-bucket lock (24-bucket decision matrix)

The orchestrator already implements Mode E (hybrid) implicitly via
`if/elif` chains at `orchestrator.py:696-1113` [Q1 §2 Mode E]. The lock
here makes the routing **explicit and testable**.

The 24-bucket routing table from Q1 §3, **adopted verbatim**:

| Intent | Complexity | Mode (locked) | Notes |
|---|---|---|---|
| general_query | single / multi / complex | A: LLM answer (no tool loop) | negotiator may fire on complex |
| scene_diagnose | single | B: full context + tool loop | Mode B scout step optional (§2.2) |
| scene_diagnose | multi / complex | **B + scout** | §2.2 enforces |
| vision_inspect | * | B: always-tools + loop | spec advisory on complex |
| prim_inspect | * | B: prim tools + loop | |
| console_review | * | B: console + KB tools | |
| navigation | single / multi | A: direct tool | no loop unless multi |
| navigation | complex | C if layout-spec, else B | rare in practice |
| physics_query | * | B: read tools first | |
| patch_request | single / multi (`sim ≥ 0.45 AND margin ≥ 0.20`) | **A: hard-instantiate** | today's default |
| patch_request | single / multi (`0.30 ≤ sim < 0.45`) | D: negotiate (if complex) then B | |
| patch_request | single / multi (`sim < 0.30`) | B: loop with spec if complex | |
| patch_request | complex | **C via `start_workflow` if shape matches; else B + spec_generator + gap_analyzer** | §2.3 enforces |

Source: Q1 §3 (table); Q1 §4 (threshold rationale).

### 2.2 Lock the 3 concrete codebase changes from Q1 §5

These are **must-do** in this spec's implementation phase:

**§2.2.a — Scout pass for `scene_diagnose` (Q1 §5.1)**
- File: `service/isaac_assist_service/chat/orchestrator.py` (+80 LOC)
- File: `service/isaac_assist_service/chat/context_distiller.py` (+20 LOC)
- Behavior: when `intent == "scene_diagnose"` AND no hard-instantiate AND
  `intent_complexity != "single"`, round 0 of the tool-calling loop is
  restricted to read-only tools (`_SCOUT_ONLY_TOOLS` set: `scene_summary`,
  `list_all_prims`, `find_prims_by_schema`, `check_physics_health`,
  `get_console_errors`). Subsequent rounds get the full schema.
- Risk: LOW [Q1 §5.1]; fail-open means worst case is one extra
  read-only round.
- Map to task graph: **t39** in `config/cron_task_graph.yaml`.

**§2.2.b — Workflow auto-route for `patch_request × complex` (Q1 §5.2)**
- File: `service/isaac_assist_service/chat/orchestrator.py` (~50 LOC)
- File: `service/isaac_assist_service/chat/intent_router.py` (~10 LOC)
- Behavior: after `classify_intent` returns `complexity == "complex"
  AND intent == "patch_request"` AND negotiator cleared AND `top_sim
  < 0.45`, a `_infer_workflow_type` keyword heuristic checks if the
  task matches one of the 3 registered `_WORKFLOW_TEMPLATES`
  (`rl_training`, `robot_import`, `sim_debugging`). If yes, a routing
  hint is injected into `patterns_text` directing the LLM to call
  `start_workflow(workflow_type=...)`.
- **Hard dependency:** Phase 34/35/36 workflow templates MUST be
  registered first (§8.1 below; t41 in graph) so additional workflow
  types can be auto-routed in the future.
- Risk: LOW [Q1 §5.2]; LLM still decides.
- Map to task graph: **t40** + **t41**.

**§2.2.c — Spec post-condition enforcement (Q1 §5.3)** — **GATED**
- File: `service/isaac_assist_service/chat/orchestrator.py` (~100 LOC)
- File: `service/isaac_assist_service/chat/spec_generator.py` (~30 LOC)
- Behavior: after each tool-calling round, check if the spec's next
  step `expected_tool` was called; if not, emit a mandatory retry
  message into `messages` as a `[SPEC GATE]` directive. Convert advisory
  spec into mandatory step gate.
- Risk: MEDIUM-HIGH [Q1 §5.3 explicit flag]; loop-risk if LLM cannot
  call `expected_tool`; need `max_retries_per_step=2` escape.
- **This change is feature-flagged behind `SPEC_STEP_GATE=on` (env
  default off).** Ship the code, but do NOT enable in production
  until the 30-prompt benchmark (§5) shows the spec injection produced
  the 40% missed-required-tool rate that Q1 §5.3 cites
  [spec_generator.py:13-21].
- Map to task graph: **t42**.

### 2.3 Workflow auto-routing pattern

The routing intents → workflow types:

| Prompt-time signal | Workflow type | Workflow template source |
|---|---|---|
| Keywords: "RL training" / "reinforcement" / "PPO" / "clone envs" | `rl_training` | Already registered |
| Keywords: "import robot" / "URDF" / "STEP" / "verify collision" | `robot_import` | Already registered |
| Keywords: "physics error" / "why is it wrong" / "diagnose" | `sim_debugging` | Already registered |
| Keywords: "assemble pick-place cell" / "build station" | `assemble_pick_place_cell` | **t41 must register** |
| Keywords: "scenario profile" / "controller config" | `scenario_profile` | **t41 must register** |
| Keywords: "loco manipulation" / "GR00T" | `loco_manipulation` | **t41 must register** (Phase 34/35/36 stubs) |

The `_infer_workflow_type` heuristic in §2.2.b expands as templates
register; the function returns `None` if no match (graceful fallback to
Mode B + spec_generator).

Source: Q1 §5.2; L-levels audit §(b); Q7 finding 5 (Phase 34/35/36
templates unregistered).

### 2.4 Acceptance criterion

§2 lands when:
- `tests/test_flow_routing.py` exists, covers all 24 buckets, and passes
  (t43 in task graph).
- `pytest tests/test_scout_pass.py` passes (t39).
- `pytest tests/test_workflow_auto_route.py` passes (t40).
- `pytest tests/test_workflow_template_registration.py` confirms `len
  (_WORKFLOW_TEMPLATES) > 3` (t41).
- The §2.2.c spec post-condition gate exists in code but is OFF by
  default (env-gated).

---

## §3. Canonical schema (LOCKED)

### 3.1 The TypeScript-style CanonicalTemplate interface

Adopted **verbatim** from Q2 §3 with no edits. Reproduced here in its
load-bearing form (the full interface lives in `service/isaac_assist_
service/multimodal/canonical_template.d.ts` per t76 of the task graph):

```typescript
interface CanonicalTemplate {
  // ---- MANDATORY (all templates, T1 + T2) ----
  task_id: string;            // pattern: CP-\d+ | CP-NEW-[a-z-]+ | [A-Z]{1,3}-\d+
  goal: string;               // ≥2 sentences, retrieval-quality
  tools_used: string[];       // ordered tool-call enumeration
  thoughts: string | string[]; // coaching rationale
  code: string;               // executable Python (T1) or advisory pseudocode (T2)
  failure_modes: string[];    // pre-mortem coaching

  // ---- MANDATORY for T1 (CP-*) ----
  verify_args: VerifyArgs;             // form-gate inputs
  simulate_args: SimulateArgs;         // function-gate inputs (duration_s ≥ 180)
  diagnose_args: DiagnoseArgs;         // diagnostic shortcut
  verified_status: string;             // "build-spec-<date>; form-gate <ok|pending>; function-gate <ok|pending|blocked>"

  // ---- MANDATORY for T1 with extends ----
  extends?: string;
  extension_notes?: string;            // co-occurs with extends (T4 rule)

  // ---- STRONGLY RECOMMENDED for T1 ----
  settle_state?: SettleState;          // cubes + conveyor velocities for settle_after_canonical

  // ---- ROLE-BASED FIELDS (target schema) ----
  intent?: {
    pattern_hint: "pick_place" | "sort" | "reorient" | "navigate";
    counts?: { robots?, conveyors?, bins?, cubes?, sensors?, humans? };
    structural_features?: { /* see Q2 §3 */ };
    structural_tags?: string[];        // "isaac:segment.subsegment" format
  };
  roles?: Record<string, RoleDeclaration>;
  role_defaults?: Record<string, object | object[]>;
  code_template?: string;              // {{role.field}} substitution
  verify_args_template?: object;
  simulate_args_template?: object;

  // ---- DEPRECATED (do not author; migration removes) ----
  benchmark_vs_alternatives?: object;  // CP-01 only
  verified_date?: string;              // CP-01, CP-02 only
  verified_metrics?: object;           // CP-01, CP-02 only
  delivery?: object;                   // CP-06, CP-07 only
  cube_path?: string;                  // CP-06, CP-07 only
  compute_stack_placement_verified_*?: object;  // CP-08 only
  extends_notes?: string;              // typo of extension_notes
  blocked?: BlockedSpec;               // CP-06 only — NOT deprecated; kept until infra fixed
}
```

Source: Q2 §3 (full); cite-rich code locations at Q2 §8.

### 3.2 Migration strategy — mechanical vs human-author split

Adopted from Q2 §4 with no edits:

**Phase 0 (automated, ~2h, t11+t13 in graph):**
- Add `intent` with mechanically-inferred fields to all 104 CP-06..87 +
  CP-NEW-* templates [Q2 §4.2 a–h].
- Fill `settle_state` for the ~24 gap CP templates via
  `canonical_instantiator._extract_cube_positions_from_code` and
  `_extract_conveyor_velocities_from_code` [Q2 §4.2 i].
- Remove deprecated one-off fields (`delivery`, `cube_path`,
  `compute_stack_placement_*`, `extends_notes` typo, `benchmark_vs_
  alternatives`, `verified_date`, `verified_metrics`) [Q2 §4.4 row
  "Phase 0c"].

**Phase 1 (human review, ~2h, t12):** Verify mechanically-assigned
`pattern_hint` for 104 templates. Expected error rate ~10–15% on
reorient/sort/navigate edge cases [Q2 §4.4].

**Phase 2 (human/LLM-author, ~78 person-hours or ~35 agent-hours,
t14+t15):** Add `roles` + `role_defaults` to 104 templates. Start with
22 CP-NEW-* (freshest), then CP-06..CP-87. Use CP-01..05 as reference
pattern [Q2 §4.4 Phase 2].

**Phase 3 (semi-automated, ~6h cumulative, t16):** For each template
with `roles` + `role_defaults` populated, generate `code_template` by
{{role.field}} substitution. Validate via
`tests/test_role_template_equivalence.py` [Q2 §4.4 Phase 3].

**Phase 4 (DEFERRED, NOT in this spec):** Remove legacy `code` field.
Requires wiring `instantiate_role_based_code` into
`execute_template_canonical` first. Out of scope per §0.2 [Q2 §6.4
"Phase 4 — high risk"].

### 3.3 Coexistence period

`code` and `code_template` coexist **indefinitely** in this spec's
horizon. The runtime reads `template["code"]` directly in
`canonical_instantiator.execute_template_canonical`
[Q2 §6.2 finding]. `code_template` is only invoked via
`instantiate_role_based_code` when an explicit `role_bindings` dict is
provided — that path is reached only via the LayoutSpec ratify flow
(currently CRM v2.1 territory) [Q2 §6.4].

Therefore: adding `code_template` to a template **cannot break the
existing hard-instantiate path**. This is the central safety property
of the migration.

Source: Q2 §6.2 (risk analysis), §6.4 (coexistence diagram).

### 3.4 Conformance check (CI script)

Adopted from Q2 §5 verbatim. The script
`scripts/lint/lint_canonical_templates.py` (~250 LOC) emits ERROR/WARN/
INFO levels per template, with `--strict` failing on ERROR only and
`--warn-roles` failing on WARN too.

Key lint rules:
- **C1**: Core fields present + non-empty (ERROR)
- **C2**: `task_id` matches filename stem (ERROR)
- **C3**: `tools_used` is non-empty string-list (ERROR)
- **C4**: `failure_modes` is string-list (ERROR)
- **T1** (CP only): T1 fields present (ERROR)
- **T2** (CP only): `verify_args.stages` non-empty + has required keys
  (ERROR)
- **T3** (CP only): `simulate_args` has `cube_path`, `target_path`,
  `duration_s` (ERROR)
- **T4** (CP only): `extends` ↔ `extension_notes` co-occur (ERROR on
  extends w/o notes, WARN on notes w/o extends)
- **T5** (CP only): `settle_state` present (WARN — fragile regex
  fallback)
- **T6** (CP only): no deprecated one-off fields (WARN)
- **R1**: intent structure validates against closed enums (ERROR on
  invalid; WARN if intent absent)
- **R2**: roles + role_defaults + code_template all-or-nothing (ERROR
  on partial)
- **R3**: every role in `roles` has an entry in `role_defaults` (ERROR)

CI wiring (t18 in graph): `.github/workflows/ci.yml` runs
`python scripts/lint/lint_canonical_templates.py --strict` on every PR.

Source: Q2 §5 (script body); Q2 §5 "Gate integration" subsection.

### 3.5 Equivalence test as the migration gate

`tests/test_role_template_equivalence.py` is the **canonical migration
gate**. For each CP-* with `code_template` populated, the test asserts
that capturing tool-calls from running `code` produces the same
normalized list as capturing tool-calls from running
`instantiate_role_based_code(template)` [Q2 §6.2 finding;
`test_role_template_equivalence.py:70-104`].

Migration sequence safety property: if `code_template` substitution
produces non-equivalent calls (different argument values, different
ordering, unfilled `{{...}}` placeholder), the test fails and the
template is rejected from migration. The lint script's R2 rule (all-
or-nothing role fields) is the partial-migration backstop.

**Extension (t17 in graph):** parametrize the equivalence test over all
role-bearing templates beyond CP-01..05. After t14+t15 land (22 + 82
templates get `roles`), every one of them runs through the equivalence
check on every CI commit.

Source: Q2 §6.2; Q6 §7 "Risk 4: Role-migration drift" mitigation.

---

## §4. Drift remediation

### 4.1 Ghost corpus fix (TP-* IDs in `role_template_index.py`)

**Problem:** `service/isaac_assist_service/multimodal/role_template_
index.py` registers 30 `TP-*` IDs (welders, palletizers, pickers,
inspectors, etc.) [Q4 §5.1, Q3 §3 Category D]. None of the 30 backing
JSON files exist. `retrieve_template_by_role` surfaces these IDs to
the LLM as if loadable; `_load_template(task_id)` returns `None`
silently downstream.

**Fix (t04 in graph; explicitly highest-risk structural issue in Q4
§7 finding 6):**
- DO NOT delete the registry entries.
- DO map each `TP-*` ID to the closest existing CP template (e.g.
  `TP-WLD-01 → CP-54` for surface gripper; `TP-PAL-01 → CP-08` for
  palletizer-like patterns).
- Add a `docs/research/2026-05-15-tp-remap-table.md` documenting the
  mapping rationale per ID.

**Verification:**
```python
from service.isaac_assist_service.multimodal.role_template_index import ROLE_TEMPLATE_INDEX
from pathlib import Path
for e in ROLE_TEMPLATE_INDEX:
    assert Path(f"workspace/templates/{e.template_id}.json").exists(), \
        f"Ghost: {e.template_id}"
```

This test is added to the CI matrix.

Source: Q4 §5.1, Q3 §3 Category D, Q3 §6 ("RoleRetriever wiring
precondition #1").

### 4.2 Delete list

**Single clean delete (t03 in graph):**
- `workspace/templates/CP-NEW-peg-in-hole-single.json` — strict
  subset of CP-58; same PhysX instability; no unique coverage
  [Q4 §5.2 row 1].
- This requires `human-review` approval per MEMORY rule
  `feedback_confirm_destructive_actions` [Q7 §6 "Decision authority"
  row "Delete a template"].

**No other clean deletes.** Per Q4 §7 finding 7: "CP-NEW-peg-in-hole-
single is the only clean delete. All other stable_fail and
experimental templates serve a documentary function that exceeds their
deletion cost."

### 4.3 Mark plumbing-only CPs honestly

`CP-NEW-opcua-12conveyors`, `CP-NEW-plc-conveyor`, `CP-NEW-plc-fixture`
have `BUILD_OK; plumbing-only` status today but no formal schema flag.
The lint script's `verified_status` validator (§3.4 R-rules) requires
one of three patterns:
- `"function-gate ✓ (<details>)"` — full delivery confirmed
- `"plumbing-only (no pick-place delivery)"` — explicit infrastructure
  template
- `"stable_fail — <root_cause>"` — documented failure

Task t05 in graph adds the `plumbing-only` marker to these 3 templates
and sets `settle_state: null` explicitly so lint rule T5 doesn't fire.

Source: Q4 §4.5 sub-group E2; Q6 §7 "Risk 1: Build OK ≠ Shippable".

### 4.4 Form-gate the 3 highest-priority pending CPs

15 D2 templates (per Q4 §4.4) carry `verified_status: "build-spec-
2026-05-08; form-gate verification pending"` and have not been re-run
since. Q4 §6 prioritizes 3 of them: CP-22, CP-46, CP-51.

**Action (t07 in graph):** Run `python scripts/qa/function_gate_suite.
py --filter "CP-22 CP-46 CP-51" --form-gate-only` and update each
template's `verified_status` with the outcome.

**Why these 3:** highest empirical usage in baseline runs per Q4 §6
"Minimum viable remediation" bullet 6.

The remaining 12 (CP-35, 37, 40, 48, 52, 53, 57, 60, 62, 65, 68, 76)
are deferred to t08 (priority 3, opportunistic).

Source: Q4 §4.4 sub-group D2; Q4 §6.

### 4.5 The 6-agent-hour minimum-viable remediation

Per Q4 §6, the MVP remediation cost breakdown:

| Action | Effort | Tasks |
|---|---|---|
| Fix deprecated fields in CP-01/02/07/08 | 0.5h | t01 |
| Promote E2 plumbing-only templates | 0.5h | t05 |
| Remap TP-* ghost corpus | 2h | t04 |
| Delete CP-NEW-peg-in-hole-single | 0.1h | t03 |
| Update CP-67 status | 0.1h | t06 |
| Form-gate priority D2 (CP-22, CP-46, CP-51) | 3h | t07 |
| **Total** | **~6.2 hours** | |

This is the cost to eliminate misleading state in the corpus. Total
full remediation is ~33 agent-hours [Q4 §6 summary table]; the rest is
opportunistic.

Source: Q4 §6 "Minimum viable remediation".

---

## §5. Retrieval architecture

### 5.1 Phase 1 — Build the 30-prompt benchmark FIRST

**No optimization without a baseline.** Q3 §9 names this "the single
highest-value action: it turns 'probably works' into a number."
Q8 §10.4 reiterates: "Build the 30-prompt benchmark FIRST. Without
it, the entire ROI discussion is conjecture."

**Action (t19 + t20 in graph):**
- Build `workspace/benchmarks/retrieval_30prompts_v1.jsonl` per Q3
  §8 (10 Tier-1 + 10 Tier-2 + 10 Tier-3 prompts with ground-truth
  template IDs and expected modes).
- Build `scripts/qa/run_retrieval_benchmark.py` per Q3 §8 method.
- Run mode A (today's path) to record baseline `hit@1`, `hit@3`,
  `mode_accuracy`, `hard_instantiate_rate`.

**Baseline expectation (Q3 §8 estimate, NOT a target):**
- Tier 1: ~70–80% hit@1
- Tier 2: ~40–60% hit@1
- Tier 3: ~30–50% hit@1
- Overall: ~50–65% hit@1

These are pre-measurement guesses. Once t20 lands, real numbers replace
them.

Source: Q3 §8 (benchmark methodology); Q3 §9 (priority); Q8 §10.4.

### 5.2 Phase 2 — Expand structural-filter coverage (5 → ~100 templates)

**The dominant improvement lever** per Q3 §5 and Q8 §10. Once the 30-
prompt benchmark exists (Phase 1), expand intent fields mechanically to
unlock `MULTIMODAL_TEXT_INTENT=on` for production traffic.

**Action (t11 + t24 + t25 in graph):**
- Phase 0 of schema migration (§3.2) adds `intent` mechanically to 104
  templates (t11).
- t24 validates CP-06..30 specifically (the most-used 25 canonicals).
- t25 enables `MULTIMODAL_TEXT_INTENT=on` after t22 (iterative A/B
  test) confirms benefit OR after coverage hits ≥ 30 templates with
  Q3 §8 spot-check showing structural-filter wins.

**The math (Q8 §10.3 educated guess):**
- Today: ~55% overall hit@1
- After structural-filter expansion alone: ~62–72% overall (+7–17pp)
- After structural-filter expansion + iterative retrieval: ~67–75%
  overall (+12–20pp)

Source: Q3 §5 (multi-stage retrieval mechanism); Q3 §9 (immediate
improvements bullet 3); Q8 §10.1 (competing investment); Q8 §10.4
(sequencing).

### 5.3 Phase 3 — Iterative retrieval if benchmark shows ambiguity > recall

**Conditional landing.** Per Q8 §10.4 "the concrete decision":

> If the benchmark shows that structural-filter expansion gets `hit@1`
> to ≥ 70% across all tiers, iterative retrieval is a marginal-gain
> investment and may be deferred.

If the post-expansion benchmark still shows `hit@3` high but `hit@1`
low (ambiguity-within-cluster dominates the failure mode), build
iterative retrieval per Q8 §1–§8.

**Action (t21 + t22 in graph, conditional on t20 + Phase 2 outcome):**
- Build `service/isaac_assist_service/chat/tools/iterative_retriever.py`
  (~250 LOC) per Q8 §8.1 pseudocode.
- Wire into orchestrator behind `ITERATIVE_RETRIEVAL=on` env flag
  (~50 LOC delta) [Q8 §8.1 file-by-file diff sketch].
- Run A/B benchmark per Q8 §7.

**Ship gate (Q8 §7.4):**
- `I.hit@1 ≥ A.hit@1 + 10 pp` (≥10 percentage-point absolute lift)
- `I.precision_at_commit ≥ A.precision_at_commit` (no precision
  regression)
- `I.p50 ≤ 700 ms AND I.p99 ≤ 1500 ms` (interactive latency holds)

Source: Q8 §10 (recommendation); Q8 §7 (A/B plan); Q3 §4 ("Recommendation:
Option B for the first iteration").

### 5.4 Threshold values, env-gating, fallback paths

**Locked values** (from Q1 §4, Q3 §4, Q8 §2.2):
- `CANONICAL_MIN_SIM = 0.45` (env: `CANONICAL_MIN_SIM`)
- `CANONICAL_MIN_MARGIN = 0.20` (env: `CANONICAL_MIN_MARGIN`)
- `TEMPLATE_TOP_K = 3` (env: `TEMPLATE_TOP_K`)
- `ITER_EVAL_MIN_CONF = 0.70` (new; env: `ITER_EVAL_MIN_CONF`)
- `ITER_FAST_MODEL = "claude-haiku-4.7"` (new; env: `ITER_FAST_MODEL`)
- `ITER_EVAL_TIMEOUT_MS = 1500` (new; env: `ITER_EVAL_TIMEOUT_MS`)
- `ITER_MAX_REFINE_ROUNDS = 1` (new; env: `ITER_MAX_REFINE_ROUNDS`)

**Env gates** (default OFF until benchmark confirms):
- `MULTIMODAL_TEXT_INTENT=off` → flip to `on` after t24 + structural-
  filter benchmark passes
- `ITERATIVE_RETRIEVAL=off` → flip to `on` after t22 ships per §5.3
  gate
- `SPEC_STEP_GATE=off` → flip after benchmark confirms 40%
  missed-required-tool problem [§2.2.c]

**Fallback paths** (Q8 §4.1 four terminal states):
- **HARD** (Mode A): `sim ≥ 0.45 AND margin ≥ 0.20` → today's
  `execute_template_canonical` — unchanged.
- **EVALUATED** (new, Phase 3): iterative evaluator picks winner with
  confidence ≥ 0.70 → instantiate that winner.
- **REFINED** (new, Phase 3): iterative refine returns merged set
  passing hard-gate → instantiate top-1.
- **FEW-SHOT** (Mode B): none of the above → today's few-shot prose
  injection + tool-calling loop.

Source: Q1 §4 (threshold proposals); Q3 §4 (evaluate_candidates);
Q8 §2.2 (pseudocode), §4.1 (four terminal states).

### 5.5 Role-retriever wiring decision (Phase 20 built but unwired)

**Decision (t23 in graph): wire role-retriever as a PRE-FILTER, not as
PRIMARY retrieval.** Per Q3 §6 "Step 1": role-retriever is
high-precision (Jaccard exact match, score 1.0) but low-coverage. It
should guard the embedding path, not replace it.

**Action:**
- In `orchestrator.py` after `classify_intent`, extract `role_hints`
  from the user message (~20 LOC function).
- If `role_hints` non-empty, query `RoleRetriever.retrieve_with_roles
  (user_message, role_hints=role_hints, max_results=top_k)`.
- If any results have `match_score ≥ 0.5` AND backing template files
  exist (post-t04 ghost-corpus fix), use as primary scored list.
- Otherwise fall through to today's `retrieve_templates_with_scores`.

**Dependency:** **t04 must land first** (ghost corpus fixed), otherwise
role-retriever returns dangling IDs.

**Hybrid scoring (Q3 §6 Step 3):** `final_score = 0.6 * role_score +
0.4 * embed_score` for the case where both paths return the same
template. Avoids "role wins always" hard rule.

Source: Q3 §6 (recommended wiring order).

---

## §6. Canonical creation pipeline

### 6.1 The 10-step author workflow

Adopted **verbatim** from Q6 §3 with no edits. Each step is annotated
with actor, gate, and autonomy class.

| Step | Actor | Gate | Cron-autonomous? |
|---|---|---|---|
| 1. Idea capture | Human | None (editorial) | NO |
| 2. Spec-level LLM draft | Sonnet | JSON schema validate; sandbox exec clean | **YES** |
| 3. Asset precheck | Sonnet (when t60 lands) or Human | All `requires` deps `status=ok` or have mock | YES with t60 |
| 4. Code iteration (physics tuning) | **Human + Kit RPC** | Visual check + execute_template_canonical success | **NO** — Q6 §6 caveat |
| 5. Form-gate verification | Sonnet (Kit RPC) | `verify_pickplace_pipeline.success == True` | YES |
| 6. Function-gate verification | Sonnet (Kit RPC) | 2-of-3 runs deliver cube to target | YES |
| 7. Role-migration | Sonnet | `test_role_template_equivalence.py` passes | **YES** |
| 8. Hardware annotation | Sonnet | `min_vram_gb` set; CPU-only documented | YES |
| 9. ChromaDB indexing | Cron script | Single-process; file-locked | YES |
| 10. Commit | **Human** | Anton's explicit review | **NO** |

**Cron-safe subset (Q6 §4):** Steps 2, 5, 6, 7, 8, 9 — under Kit-
supervisor for 5+6, otherwise pure file work.

**Human-required (Q6 §4):** Steps 1, 3 (when Nucleus assets unclear),
4, 10.

The pipeline is built as `scripts/qa/canonical_draft_job.py` (Q6 §8
pseudocode, ~360 LOC, **t45 in graph — critical path**).

Source: Q6 §3 (full workflow); Q6 §8 (pseudocode).

### 6.2 Yrkesroll Tier-1 priority list (7 locally-runnable roles)

Adopted **verbatim** from Q6 §5 Tier 1. These templates already exist
with `smoke-test ✓` status and need promotion to `function-gate ✓`
via the 10-step pipeline:

1. **CP-NEW-inspect-reject** — quality engineers, inspection lines
   (t46 in graph)
2. **CP-NEW-defect-sdg** — SDG dataset engineers (t47)
3. **CP-NEW-dr-curriculum** — ML/RL sim-to-real teams (t48)
4. **CP-NEW-y-merge-singulation** — logistics, warehouse design (t49)
5. **CP-NEW-3station-oee** — manufacturing engineers, OEE analysts
   (t50)
6. **CP-NEW-controller-shootout-cp** — robotics engineers comparing
   controllers (t51)
7. **CP-NEW-multi-cam-triangulation** — quality/vision engineers
   (t52)

**Asset profile:** All 7 use locally-available Isaac Sim assets
(Franka, primitives, conveyor, camera). No NVIDIA Nucleus access
required.

**Tier 2 (Q6 §5) — requires physics tuning, lower autonomy:**
8. CP-NEW-rl-clone-env (t53; blocked on RSL-RL install)
9. CP-NEW-sim2real-gap (t54; blocked on rosbag asset)
10. CP-NEW-multi-amr-corridor (t55; depends on t02 typo fix)

**Tier 4 (Q6 §5) — DEFERRED:** G1 bimanual, OperatorAvatar
ergonomics, peg-in-hole, brick-stacking — blocked on Nucleus assets
or unsolved physics. Do not invest authoring time without unblock.

Source: Q6 §5.

### 6.3 LLM-draft + equivalence-gate + function-gate quality bar

Adopted **verbatim** from Q6 §2 — the 10-criterion checklist for
"shippable":

```
[ ] goal: ≥2 sentences, retrieval-quality
[ ] intent.pattern_hint: one of {pick_place, sort, reorient, navigate}
[ ] roles: all scene participants with constraints
[ ] code_template: substitutable, equivalence test passes
[ ] verify_args: form-gate args populated
[ ] simulate_args: function-gate args populated (duration_s ≥ 180)
[ ] verified_status: "function-gate ✓ (cube_final=<coord>)"
    OR "plumbing-only (no pick-place delivery)"
    OR "stable_fail — <root_cause>"
[ ] failure_modes: non-empty list
[ ] hardware: min_vram_gb set (or "CPU-only" documented)
[ ] requires: asset dependency manifest (Phase 78c format when t60 lands)
```

A canonical that meets only criteria 1, 6 is a **draft** — Q6 explicit
honesty: "The 22 CP-NEW templates today meet criteria 1 and 6 only."

A canonical that meets all 10 is **shippable**.

Source: Q6 §2.

### 6.4 Rate target — 35–50 canonicals/week autonomous

**Locked target with honesty caveat.** Q6 §6 estimates 40–60
function-gate-verified canonicals per week in mode (c) (fully
autonomous). Q6 §6 explicit caveat:

> The 2026-05-10 session dispatched ~20 drafts and produced zero
> function-gate-verified canonicals. The gap between "autonomous draft"
> and "function-gate verified" is real and large.

**Realistic rate (Q6 §6 revised estimate): 5–8 function-gate verified
canonicals per day**, gated by Kit single-tenancy + Kit restart every
~30 templates + 40–50% physics-escalation rate. Multiplied by 5
working days/week = **25–40 verified canonicals/week**.

The target set for the autonomous run halting criterion (§4 of the
execution plan and Q7 §5.2): **50 yrkesroll-aligned canonicals
migrated to role-based schema and function-gate ✓** over the 6-week
autonomous window.

Source: Q6 §6 (math); Q7 §5.2 (halting criterion derivation).

### 6.5 Acceptance criterion

§6 lands when:
- `scripts/qa/canonical_draft_job.py` exists and `--help` works (t45).
- 7 Tier-1 yrkesroll templates pass function-gate consistency check
  (t46–t52).
- `docs/review/pending/` directory exists and is populated for any
  template that hits a gate failure.
- Mid-pipeline ChromaDB reindex respects file-lock (t59).

---

## §7. Gemini integration

### 7.1 SDK-first via existing `GeminiProvider`

Locked decision: **SDK-first**, not MCP-first [Q5 §2]. The
`GeminiProvider` already exists at `service/isaac_assist_service/chat/
llm_gemini.py:173`. The provider factory already supports
`LLM_MODE=cloud` [Q5 §1.2]. Zero new provider code is required.

**Configuration (t26 in graph, human-review):**
- `.env.local` with `LLM_MODE=cloud`, `GEMINI_API_KEY=<from-Anton>`,
  `CLOUD_MODEL_NAME=gemini-2.5-flash`.
- Verify `python -c "from service.isaac_assist_service.chat.
  provider_factory import get_default_provider; print(type(get_
  default_provider()))"` shows `GeminiProvider`.

Source: Q5 §1 (current state of codebase); Q5 §2 (architecture
recommendation).

### 7.2 30-prompt stress-test corpus

Adopted from Q5 §4 verbatim — same 30 prompts as the retrieval
benchmark (§5.1) with mode-A/mode-B labels overlaid. Distribution:
- 8 trivial × 1 round average = $0.024 per run
- 12 medium × 3 rounds average = $0.132 per run
- 10 complex × 6 rounds average = $0.410 per run
- **Total ~$0.57 per full corpus run** [Q5 §4.2]

The 1000 SEK GCloud budget (~$92 USD) accommodates ~50 full corpus
runs at Flash pricing. Realistic plan uses ~6 runs over 10 days =
$3.42 actual cost [Q5 §5]. Massively under-budget.

Source: Q5 §4 (corpus); Q5 §4.2 (cost arithmetic).

### 7.3 10-day burndown with 30% re-run reserve

Adopted from Q5 §5 verbatim:

| Day | Activity | Spend est. |
|---|---|---|
| 1 | Env setup, 3-prompt smoke (t27) | $0.01 |
| 2 | 30-prompt baseline (t28) | $0.57 |
| 3 | Categorize failures (t29, Opus) | $0 |
| 4 | Round-1 tool-surface fixes + targeted re-run (t31, t32) | $0.20 |
| 5 | Full re-run after fixes (t33) | $0.57 |
| 6 | Round-2 fixes (t34) | $0.30 |
| 7 | Model swap day ~2026-05-22 (t35) | $0.57 |
| 8 | OLD + NEW model control runs (t35 continues) | $1.14 |
| 9 | Decision gate + patch (t36) | $0.20 |
| 10 | Final run (t37) | $0.57 |
| **Total** | | **~$3.60** |

Budget breakdown: 70% (~$64) for Flash runs, 30% (~$28) reserved for
re-runs after fixes + model-swap.

Source: Q5 §5.

### 7.4 Mid-stream model-swap protocol (~2026-05-22)

Adopted from Q5 §7 verbatim. The protocol:

1. Lock the 30-prompt corpus (no prompt changes after Day 5).
2. Run corpus with OLD model one final time (control run).
3. Update `CLOUD_MODEL_NAME` to new model ID in `.env.local`.
4. Run same 30-prompt corpus with NEW model.
5. Three-way comparison:
   - `Δ_harness = old_model_run_final.success_rate - old_model_run1.success_rate`
   - `Δ_model = new_model_run1.success_rate - old_model_run_final.success_rate`

**Decision gate (Q5 §7.3):**
- `Δ_model > 0.10` → switch to new model for all remaining runs
- `-0.05 < Δ_model < 0.10` → keep old model
- `Δ_model < -0.05` → hold, file regression report

**Attribution synthesis (t36, Opus):** `docs/research/2026-05-XX-
gemini-attribution-results.md` per Q5 §7.2.

Source: Q5 §7.

### 7.5 Production decision: no-switch; Flash is stress-test only

**Locked recommendation (Q5 §8):** Flash is a **stress-test instrument,
not a production switch**.

Rationale [Q5 §8]:
- Flash is deliberately weaker; surfaces brittleness Claude masks.
- Vision backend already uses Gemini (`vision_gemini.py`,
  `vision_real_gemini.py`); production split (Claude for agentic
  reasoning, Gemini for vision) already reflects Flash's comparative
  strengths.
- Cost argument for production is weak — Sonnet's ~3¢/turn vs
  Flash's <1¢/turn doesn't justify a 10–20% task success rate
  reduction.

**Reopening condition:** If the new Gemini model (post-2026-05-22)
shows ≥ 90% of Claude's task success rate on the 30-prompt corpus,
open a SEPARATE decision track for Gemini as alternative driver. Not
part of this spec.

Source: Q5 §8.

### 7.6 Acceptance criterion

§7 lands when:
- `.env.local` exists with Gemini creds (t26).
- 10-day burndown completed and `docs/research/2026-05-XX-gemini-
  final-findings.md` exists (t37).
- Attribution analysis distinguishes `Δ_harness` vs `Δ_model`
  (t36).
- Tool-surface bug list filed; high-confidence Cat-A/B bugs fixed
  (t31 + t34).
- `scripts/qa/tool_audit_flash.py` (Claude-isms scanner) exists and
  produces a warning count (t38).

---

## §8. Workflow plumbing

### 8.1 Phase 34/35/36 template registration (the ~30 LOC PR)

**The L-levels audit's primary defect** [L-levels audit §3(b)]: Phase
34/35/36 workflow templates exist as module constants but are NOT
registered into `_WORKFLOW_TEMPLATES` dict in
`service/isaac_assist_service/chat/_state.py` (~line 279). Today,
`start_workflow("assemble_pick_place_cell")` returns "Unknown
workflow_type" [audit §3].

**Fix (t41 in graph, **critical path**):** ~30-LOC PR that:
- Imports the three Phase 34/35/36 module constants.
- Registers them into `_WORKFLOW_TEMPLATES`.
- Adds `tests/test_workflow_template_registration.py` confirming
  `len(_WORKFLOW_TEMPLATES) > 3`.

This unblocks §2.2.b (workflow auto-route) and §8.2 (workflow E2E
test).

Source: L-levels audit §3(b); Q7 finding 5.

### 8.2 Workflow auto-routing decision

**Auto-routing pattern (§2.2.b + §2.3 above)** is the decision. The
orchestrator routes `patch_request × complex` to `start_workflow` via
a keyword heuristic when no canonical match (`top_sim < 0.45`) AND
negotiator cleared AND task shape matches a registered workflow type.

**Why not L-level-based routing (audit §3(c) alternative):**
- L-level annotation is 0/416 tools today [audit p.2].
- Even after annotation, routing on tool L-level requires inferring
  L-level from prompt before tools are chosen (chicken-egg).
- Keyword heuristic on intent + complexity is simpler and testable.

**The L-levels axis stays descriptive only for this spec.** A
20-tool pilot annotation (t63 + t64) will produce a design doc on
whether to formalize routing later.

Source: L-levels audit §3(c) "Define routing decision explicitly";
Q1 §5.2.

### 8.3 Acceptance criterion

§8 lands when:
- `_WORKFLOW_TEMPLATES` contains ≥ 4 entries after t41
  (`rl_training`, `robot_import`, `sim_debugging`, plus at least one
  Phase 34/35/36 template registered).
- `tests/test_workflow_end_to_end.py` passes (t62): prompt →
  orchestrator → workflow_auto_route → start_workflow → completion.
- 20-tool L-level pilot annotation exists (t63).
- `docs/specs/2026-05-XX-l-levels-router-design.md` exists (t64,
  Opus).

---

## §9. What this spec defers (explicit out-of-scope)

This list expands §0.2 with citations to which research/audit
explicitly defers the item:

| Deferred item | Citation | Where it goes later |
|---|---|---|
| Canvas UX (drag-drop 17–60 classes → LayoutSpec) | `docs/research/2026-05-14-canvas-ux-research.md` | Separate future spec |
| L1/L2/L3 annotation across 437 tools | L-levels audit p.2; this spec pilots 20 [§8.2] | Phase 18b full rollout — separate effort |
| VLA / RL / contact-rich beyond Compliance v2.1 | `docs/specs/2026-05-11-contact-rich-manipulation-spec.md` is the active in-flight spec | CRM v2.1 owns; this spec interlocks via role-based templates only |
| GR00T finetune wiring beyond evaluator hook | Q1 §6 Gap D; outside this spec | Future GR00T-specific spec |
| Q2 migration Phase 4 (remove `code` field) | Q2 §6.4 "high risk" footnote | After `instantiate_role_based_code` is wired into `execute_template_canonical` — a separate code change |
| Pi0 / OpenVLA inference paths | Q1 §6 Gap D ("not in service code") | Future inference-runtime adapter spec |
| Free-running iterative agent (Claude Code-style true loop) | Q8 §2.3 "NOT a free-running iterative loop" | Bounded single-loop only in this spec |
| Q3 retrieval threshold recalibration | Q3 §9 rec 1 (post-benchmark action) | After t20 baseline lands, separate decision |
| Full T2 corpus re-authoring (102 never-run templates) | Q4 §4.1 opportunistic (t09 priority 3) | Background work — never-run T2s are cheap to keep |
| End-state spec "no LLM decisions after intent extraction" | L-levels audit §"Gaps" — currently unreachable | Long-horizon goal, not this spec |
| GR00T Phase 62 weights + GPU runtime adapter | Q1 §6 Gap D ("blocked on GR00T weights") | External hardware unblock prerequisite |
| Cron `RemoteTrigger` / scheduled-agent UX | Out of scope — research-spec §7 doesn't mention | Separate dev-UX work |

### 9.1 Interlock with Compliance v2.1 (CRM)

The in-flight `docs/specs/2026-05-11-contact-rich-manipulation-spec.md`
(CRM v2.1, 16/16 + audit fixes per
`project_isaac_assist_2026_05_14_crm_complete.md`) interlocks with this
spec in three places:

1. **Phase 20 role-based templates host CRM's auto-pick algorithm**
   (CRM spec line 220 explicit). This spec governs the role-based
   schema; CRM consumes it via `autopick_compliance_mode` in
   `multimodal/ratify.py:350` [Q3 §6].
2. **Phase 28 role authoring of CP-01..05 is shared infrastructure.**
   This spec's migration (§3.2) extends from 5 templates to ~110;
   CRM continues to use the same `instantiate_role_based_code` path.
3. **`canonical_template.d.ts` from t76 is the contract** between this
   spec and CRM. Both specs validate against the same TypeScript
   interface; the lint script (§3.4) enforces conformance for both.

**No conflict** because this spec does NOT modify the ratify path or
the auto-pick algorithm; it only modifies the templates those paths
consume.

Source: CRM spec lines 27, 60, 123, 189-220, 525, 563, 610-632, 656.

---

## §10. Acceptance criteria (per section)

Reproduced for executive summary:

| § | What lands | How we know |
|---|---|---|
| §1 | Conceptual model formalized | TypeScript .d.ts exists (t76); README/INDEX cites two-axis complexity |
| §2 | 24-bucket routing matrix testable | `tests/test_flow_routing.py` passes (t43); `_WORKFLOW_TEMPLATES > 3` (t41); scout + auto-route tests pass (t39, t40) |
| §3 | Schema locked; CI gate | `scripts/lint/lint_canonical_templates.py --strict` returns 0 (t10); `pytest tests/test_role_template_equivalence.py` parametrized over all role-bearing templates (t17) |
| §4 | Drift remediated | Ghost-corpus verification passes (t04); MVP delete (t03) + plumbing-only marks (t05) committed; priority-3 D2 form-gate done (t07) |
| §5 | Retrieval benchmark exists + baseline measured | `workspace/baselines/retrieval-baseline-30prompts-v1.json` exists with `hit_at_1` key (t20); structural-filter intent corpus ≥ 30 templates (t24); iterative retrieval shipped IFF Q8 §7.4 gate passes (t22) |
| §6 | Canonical pipeline operational | `scripts/qa/canonical_draft_job.py --help` works (t45); 7 Tier-1 yrkesroll templates pass function-gate (t46–t52); docs/review/pending/ active (t72 dashboard) |
| §7 | Gemini stress-test concluded | `flash_final_full_corpus.jsonl` exists (t37); attribution doc exists (t36); decision: stay on Claude in production [§7.5] |
| §8 | Workflow plumbing closed | `_WORKFLOW_TEMPLATES` registered (t41); E2E test passes (t62); L-level pilot annotated (t63); router design doc filed (t64) |

---

## §11. Risks + mitigations (top 10)

Adopted from Q7 §10 and Q6 §7 with cross-references.

### Risk 1 — Cron dispatcher races on Kit RPC

**Description:** Despite serialization design, an edge case in t67/t68
lets two `kit-rpc-sonnet` tasks overlap. Kit stage-state corruption
silently produces false function-gate ✓ or ✗ [Q7 §10 risk 1].

**Mitigation:**
- t75 adds explicit regression test for concurrent direct_eval guard.
- t70 embeds Kit-restart in dispatcher.
- Audit log every Kit RPC call with task-ID; reject overlaps.

**Detection:** Anomalous function-gate flakiness vs t79 baseline.

### Risk 2 — Schema-migration LLM rewrites silently break `code` execution

**Description:** t14–t16 generate `code_template` from `code`. If LLM
substitution produces non-equivalent calls, equivalence test catches
it ONLY if t17 (test extension) was applied first [Q7 §10 risk 2; Q6
§7 risk 4].

**Mitigation:**
- t17 (extend equivalence test) MUST land before t14–t16.
- t79 snapshot allows full rollback of `templates/` directory.
- Lint conformance gate (t10) catches partial role fields per R2.

**Detection:** `pytest tests/test_role_template_equivalence.py`
failure on a previously-passing template.

### Risk 3 — Gemini Day-7 model swap delayed or breaks API

**Description:** Q5 §7 plans the swap at ~2026-05-22. If new model
delayed, attribution-analysis (t36) starved of `Δ_model` data. If new
model's API contract changes, all `kit-rpc-sonnet` Gemini tasks fail
simultaneously [Q7 §10 risk 3].

**Mitigation:**
- t35 protocol explicitly runs OLD control run alongside NEW model.
- Fall back to OLD model + accumulated fixes if new model fails.
- Cron continues other tracks while Gemini gated.

**Detection:** t35 verification fails; t36 input gating shows blocker.

### Risk 4 — Kit physics-iteration loop traps autonomous Sonnet

**Description:** Per Q6 §6 caveat, Sonnet alone cannot navigate the
physics-iteration loop in Stage 4. Track F yrkesroll promotions
(t46–t57) hit `stable_fail` patterns and cron retries indefinitely
[Q7 §10 risk 4].

**Mitigation:**
- Failure-recovery rule (Q7 §6) caps Kit-RPC retries at 2 per template.
- After 2 fails, file to `docs/review/pending/` and continue.
- t72 dashboard surfaces accumulating review-queue depth.

**Detection:** `review_queue_dashboard` shows > 5 entries with
`reason=function_gate_failure` in 24h.

### Risk 5 — ChromaDB index corruption from missed file-lock

**Description:** Post-commit hook (t74) and explicit reindex (t59)
both write to same collection. If `flock` fails to engage, parallel
writers cause HNSW segfault per MEMORY [Q7 §10 risk 5; Q6 §7 risk 5].

**Mitigation:**
- t74 test that hook actually engages `flock`.
- t59 single-process write.
- t75 covers test for non-concurrent write paths.
- Index can be rebuilt from `templates/` (t79 snapshot is source of
  truth).

**Detection:** ChromaDB query returns unexpected docs; `python -m
scripts.qa.add_templates_from_tasks --verify-count` exits non-zero.

### Risk 6 — Spec post-condition gate (§2.2.c) regresses behavior

**Description:** Q1 §5.3 explicitly flags MEDIUM-HIGH risk: if LLM
cannot call `expected_tool` (tool not in schema, wrong args), gate
loops forever. Spec post-condition enforcement was reverted before
[Q1 §2 Mode C, code at `orchestrator.py:1145-1158`].

**Mitigation:**
- Feature flag `SPEC_STEP_GATE=off` by default [§2.2.c].
- `max_retries_per_step=2` escape hatch built in.
- Round-indexing uses "has any tool from step N been called?" not
  strict `round_idx == step_n` alignment.

**Detection:** Test suite regression on `tests/test_flow_routing.py`
when gate is enabled.

### Risk 7 — Iterative retrieval evaluator promotes wrong template

**Description:** Q8 §5.5 "the one genuine risk": round 0 returns
sim=0.46 / 0.30 (margin fails), evaluator picks CP-02 with
confidence 0.72 when CP-01 was right [Q8 §5.5].

**Mitigation:**
- A/B benchmark (§5.3) requires `precision_at_commit ≥ A.precision_at_
  commit`. No precision regression ships.
- Evaluator prompt explicitly: "Prefer candidates that overlap with
  the user's original prompt domain" [Q8 §9.2].
- `_merge_and_rerank` keeps round-0 candidates in the pool. Drift
  can't remove legitimately-high-sim round-0 results.

**Detection:** A/B benchmark `precision_at_commit` regression.

### Risk 8 — Yrkesroll asset dependencies block Tier-1 unexpectedly

**Description:** Q6 §7 risk 3: a template references USD path in
Nucleus, build reaches step N before failing; naive agent diagnoses
as "step N physics error" and iterates on physics.

**Mitigation:**
- t60 (Phase 78c `precheck_template_assets`) lands as parallel work.
- Tier-1 list (§6.2) is explicitly Nucleus-free.
- Cron's failure-recovery decision tree (Q7 §6) flags
  asset-dependency failures distinctly from physics failures.

**Detection:** `review_queue_dashboard` `reason=asset_missing`
classifications.

### Risk 9 — Migration cohort F (CP-01..05 role graft) drifts post-merge

**Description:** Q6 §7 risk 4 + Q4 §4.6: 22 batch-patches to CP-*
templates were committed AFTER role migration. Any CP with role
fields that was batch-patched is at risk of `code_template` divergence.

**Mitigation:**
- t17 extends equivalence test parametrize list before t14–t16
  start.
- CI runs equivalence test on every commit touching `templates/`.

**Detection:** Pre-existing CI failure on previously-passing template.

### Risk 10 — User-side load (interactive latency budget)

**Description:** Iterative retrieval adds up to ~930 ms in worst case
[Q8 §6.6]. Combined with main LLM turn (500–3000 ms), worst case can
exceed the `< 2 s` interactive budget.

**Mitigation:**
- Hard timeout per evaluator call (`ITER_EVAL_TIMEOUT_MS=1500`).
- Use Gemini Flash as primary evaluator (200–400 ms vs Haiku
  300–600 ms) — but only if Track D shows Flash is stable.
- Send streaming "Looking up..." event to UI when round 1 starts.

**Detection:** Log every `wall_ms` to
`workspace/baselines/iterative-retrieval-{date}.jsonl`; alert if p99
> 2000 ms.

---

## §12. Dependencies (what must be true before each section starts)

This section is the dependency contract between specs. It links to
Q7's task graph dependency edges.

### §2 — Flow architecture
- **§2.2.b workflow auto-route depends on §8.1 workflow template
  registration** (t41 before t40).
- **§2.2.c spec post-condition gate is post-benchmark.** Do not enable
  before t20 + benchmark shows 40% missed-required-tool rate
  empirically.
- No other hard preconditions.

### §3 — Canonical schema
- **§3.4 lint script is critical-path** (t10) — gates §3.5 equivalence
  test extension, §4 drift remediation, §6 canonical pipeline.
- **§3.5 equivalence test extension (t17) MUST land before t14–t16
  generate `code_template`** [Risk 9].

### §4 — Drift remediation
- **§4.1 ghost corpus fix (t04) is critical-path** — gates §5.5
  role-retriever wiring (t23) and §6 canonical pipeline (t45 uses the
  role index).
- **§4.4 form-gate runs (t07, t08) depend on Kit supervisor health
  check (t30).**

### §5 — Retrieval architecture
- **§5.1 30-prompt benchmark is critical-path** (t19 + t20) — gates all
  Phase 2 + Phase 3 work; gates halting criterion 1 (hit@1 ≥ 0.75).
- **§5.2 structural-filter expansion depends on §3.2 Phase 0 + Phase 1**
  (t11, t12 + t13).
- **§5.3 iterative retrieval is conditional on §5.2 outcome.** Do not
  build until t22 ships per Q8 §7.4 gate.
- **§5.5 role-retriever wiring depends on §4.1** (t04 before t23).

### §6 — Canonical creation pipeline
- **§6.1 pipeline (t45) is critical-path** — Track F anchor; depends on
  §3.4 lint script (t10) for Stage 1 conformance check.
- **§6.2 Tier-1 promotions depend on §6.1 + Kit health (t46–t52 depend
  on t30 + t45).**
- **§6.4 rate target (50 yrkesroll) is a halting criterion, not a
  prerequisite.**

### §7 — Gemini integration
- **§7.1 SDK setup (t26) is critical-path for Track D only.**
- **§7.2 + §7.3 corpus + burndown depend on Kit health (t30).**
- **§7.4 model swap (t35) is calendar-pinned, not dependency-pinned.**

### §8 — Workflow plumbing
- **§8.1 template registration (t41) is critical-path** — gates §2.2.b
  + §8.2 (t40, t62).
- **§8.2 routing decision (t64 design doc) depends on §8.1 (t63 pilot
  annotation depends on t41 being landed first to validate the
  workflow auto-route is working as expected).**

### Cross-cutting prerequisites

These are infrastructure tasks that gate the entire autonomous run
[Q7 §3 critical path]:

- **t79 baseline snapshot** before any bulk migration starts (rollback
  safety).
- **t30 Kit supervisor health-check** before any `kit-rpc-sonnet` task.
- **t66 task graph YAML in repo** before t67 dispatcher.
- **t67 cron dispatcher** before t68 (recovery), t69 (reports), t70
  (Kit restart), t71 (uvicorn restart), t81 (metrics).
- **t71 uvicorn restart hook** before any orchestrator/tool edit lands
  [MEMORY `feedback_isaac_assist_service_restart`].
- **t78 halting-criteria implementation** before autonomous loop can
  self-terminate.

### Final synthesis dependency

- **§7.6 + §5.3 + §6.5 + §8.3 + §4 acceptance criteria all together →
  t82 final close-out spec** (Opus, human-review checkpoint).

---

## §13. Map to task graph (`config/cron_task_graph.yaml`)

Quick cross-reference: every section above maps to specific task IDs.
See `config/cron_task_graph.yaml` for the executable form.

| Section | Tasks |
|---|---|
| §1 Conceptual model | t76 (TypeScript decl) |
| §2 Flow architecture | t39, t40, t41, t42, t43, t44 |
| §3 Canonical schema | t10, t11, t12, t13, t14, t15, t16, t17, t18, t76 |
| §4 Drift remediation | t01, t02, t03, t04, t05, t06, t07, t08, t09 |
| §5 Retrieval architecture | t19, t20, t21, t22, t23, t24, t25, t80 |
| §6 Canonical creation pipeline | t45, t46, t47, t48, t49, t50, t51, t52, t53, t54, t55, t56, t57, t58, t59, t60 |
| §7 Gemini integration | t26, t27, t28, t29, t30, t31, t32, t33, t34, t35, t36, t37, t38 |
| §8 Workflow plumbing | t41, t61, t62, t63, t64, t65 |
| Cross-cutting infra | t66, t67, t68, t69, t70, t71, t72, t73, t74, t75, t77, t78, t79, t81, t82 |

---

## §14. Open decisions for the user (before kickoff)

None of the items below block landing this spec; they block kickoff
of the autonomous run. Anton should confirm before t79 + t30 fire.

1. **Halting criteria OR-gate values locked?** (§5 of execution plan):
   hit@1 ≥ 0.75, OR 50 yrkesroll canonicals migrated, OR 6-week cap.
   Confirm or refine. Default: keep.
2. **Gemini API key procurement** — Anton must produce the
   `.env.local` file for t26.
3. **Auto-pause cadence — every 10 tasks** for mini-reports. Confirm
   the number `10` or adjust.
4. **CP-NEW-peg-in-hole-single delete approval** — t03 needs explicit
   human approval before the autonomous loop can proceed past it (or
   skip the delete and proceed; Q4 §5.2 is the rationale).
5. **`SPEC_STEP_GATE` gating** — keep off-by-default for the autonomous
   run? Default: yes (avoid Risk 6).
6. **Compute budget cap** — Anton confirms the ~$69 LLM inference total
   estimate from Q7 §8 is acceptable.

If all 6 are confirmed, kickoff proceeds with the first-day ramp from
the execution plan §4.

---

## §15. Honesty Charter check

This spec follows the Honesty Charter from the parent research-spec §5:

- **No inflated claims.** §3.3 explicitly preserves `code` field
  indefinitely. §6.4 cites Q6's "5–8 function-gate canonicals/day
  realistic" estimate, NOT the optimistic 8–10 ceiling.
- **No overcounting "what exists."** §4.2 single delete; §6.2 only 7
  Tier-1 templates that already have `smoke-test ✓`; §7.1 zero new
  provider code (everything already in `llm_gemini.py`).
- **Risks surfaced.** §11 enumerates 10 risks including the two MEDIUM-
  HIGH ones (§2.2.c spec gate; §3 schema migration drift).
- **Conditional landings cited.** §5.3 iterative retrieval is
  conditional on benchmark outcome; §7.5 production decision is explicit
  "no-switch."
- **Sources cited for every claim.** Every section cites the Qn §x or
  audit p.y it builds from.

---

## §16. Sign-off

This spec is ready for user review. Approval gate: Anton's explicit
"go" before t79 fires (first task in cron task graph).

Approval implies:
- The 24-bucket routing matrix (§2.1) is the production target.
- The Q2 schema (§3.1) is the canonical contract.
- The Q6 pipeline (§6) is how new canonicals get made.
- The cron task graph at `config/cron_task_graph.yaml` is the
  executable plan.
- The autonomous execution plan at
  `docs/specs/2026-05-15-autonomous-execution-plan.md` is the
  human-readable view.

If Anton has refinements to §1–§8, surface them now, before kickoff.

---

*End of spec. Total length: ~860 lines (within 800–1500 target).
References: 8 research reports cited inline. Companion artifacts:
execution plan + cron YAML.*
