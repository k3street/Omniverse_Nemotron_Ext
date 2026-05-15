# L1/L2/L3 Canonical Discovery & Tool Chaining — Current State Audit

**Date:** 2026-05-14
**Researcher:** Opus agent
**Scope:** Audit how task classification + canonical discovery + tool
chaining actually works in production today

## Executive summary

**What actually happens when a prompt arrives:**

1. Chat orchestrator does ONE LLM-classifier call producing
   `(intent, multi_step, complexity)` — but NO L1/L2/L3 axis
2. Similarity-based template retrieval (top-K=3) over 321 templates
3. If top sim ≥ 0.45 AND margin ≥ 0.20: **hard-instantiate** template
   `code` deterministically (no LLM)
4. Else: inject top-K templates as **few-shot guidance** + LLM authors
   own tool sequence in multi-round loop

**No L1/L2/L3 routing. No separate "complex → workflow" branch.
No auto-invocation of workflow engine.**

**Answers to user's 5 questions:**

1. **L3 task setup** goes through SAME pipeline as everything else.
   No L3-specific path. User/LLM must explicitly call `start_workflow`;
   only 3 workflow types wired (rl_training, robot_import, sim_debugging).
2. **L1/L2/L3 is tool-only taxonomy** per Phase 18b. NOT template.
   Templates have NO L-level metadata.
3. **Canonical discovery moderately tested** for similarity path on
   CP-01..05. Structural-filter and role-retriever paths unwired from
   live orchestrator (have unit tests, no production traffic).
4. **Tool chaining today** = LLM emits multiple `tool_calls` per round,
   orchestrator dispatches, feeds back, loops. NO `ChainedToolCall`
   type. Only deterministic chain = `execute_template_canonical`.
5. **End-state (per spec)**: prompt → LayoutSpec.intent → ratify →
   execute_template_canonical → verify_registry. Today only first
   half is half-wired (5/321 templates have `intent` fields, ratify
   missing, verify_registry partially exists).

## Maturity per layer

| Component | Status |
|---|---|
| Tool retrieval (ChromaDB top-K) | Production, well-used |
| Template retrieval (similarity-only) | Production, well-used |
| Hard-instantiate (CP-01..CP-05 path) | Production, well-used |
| Structural-filter retrieval (intent-based) | Code landed, 1.5% of templates participate, env-gated `off` by default |
| Role-based retrieval | Landed as TOOL the LLM can call, NOT auto-invoked |
| L1/L2/L3 tool annotation | **0/416 deferred** — auditor exists, data missing |
| Workflow engine + Phase 34/35/36 templates | Code landed, NOT registered in `_WORKFLOW_TEMPLATES` |
| Intent → workflow router | **Does not exist** |

## L1/L2/L3 verdict

- Defined in `docs/architecture/action_levels.md` + Phase 18b
- L1=atomic primitive, L2=composed task, L3=multi-phase workflow
- `grep -c "x-action-level" tool_schemas.py` → **0**
- `grep -l '"level"\|"L1"\|"L2"\|"L3"' workspace/templates/*.json` → **0**
- Closest template-level taxonomy: `intent.pattern_hint` enum, but
  only **5/321 templates have an `intent` field** (CP-01..CP-05)
- "L1/L2/L3" string literals DO appear in `arena_benchmark_spec.py`
  and `sub_phase_62b_groot_n17_eval_harness.py` — but those are
  **difficulty** axes, not action-level axes

## Classification step today

`orchestrator.py:696` → `classify_intent(user_message)` returns:
```python
{
  "intent": "general_query|scene_diagnose|vision_inspect|prim_inspect|
             patch_request|physics_query|console_review|navigation",
  "multi_step": bool,
  "complexity": "single|multi|complex",
  "confidence": float
}
```

NO L-level. Drives:
- `complexity=="complex"` → run negotiator + spec_generator/gap_analyzer
- `intent in {patch_request, scene_diagnose}` → take undo-snapshot
- `multi_step=True` → emit trace event, no behavioral change

## Retrieval chain (orchestrator.py:880-997)

```
classify_intent
   ↓
negotiator gate (if complexity==complex)
   ↓
template retrieval
  ├── if MULTIMODAL_TEXT_INTENT=on: retrieve_with_intent_filter
  └── else (DEFAULT): retrieve_templates_with_scores
   ↓
  if top_sim ≥ 0.45 AND margin ≥ 0.20:
    HARD-INSTANTIATE
    + execute_template_verify + settle_after_canonical
    + replace LLM tool schema with ALLOWED_AFTER_INSTANTIATE (28 tools)
  else:
    inject templates as few-shot prose
   ↓
spec_generator + gap_analyzer (if complexity==complex)
   ↓
distill_context (uses tool_retriever.retrieve_tools)
   ↓
multi-round tool-calling loop
```

## Tool chaining mechanisms

**Mechanism A — LLM-emitted tool_calls per round:**
Agent emits multiple `tool_calls` in one LLM response. Orchestrator
iterates, dispatches each, feeds results back, loops up to
`max_rounds`. Plain agentic tool-use; LLM is responsible for chaining
output → input.

**Mechanism B — Deterministic template execution:**
`canonical_instantiator.execute_template_canonical` runs template
`code` field as fixed tool-call sequence. Chain is hard-coded in
template source.

**No `ToolSequence`/`MultiToolPlan` class.** No conditional/branching
plan runtime.

**Resolve-tools as chain-pattern:** 12 `resolve_*` tools in
`_ALWAYS_TOOLS` (context_distiller.py:148). Expected pattern:
`resolve_X(phrase) → concrete_args → call concrete_tool(concrete_args)`.
Convention enforced by prompt rules, NOT by code.

## Testedness

**Existing tests (`tests/`):**
- `test_canonical_instantiator.py` (485 lines) — hard-instantiate path
- `test_canonical_templates_b1b.py` — CP-01..05 shape
- `test_intent_router.py` (133 lines) — classify_intent
- `test_role_based_templates.py` (240 lines) — RoleRetriever scoring
- `test_phase_21_role_template_index.py` — inverted index
- `test_workflow_engine.py` — WorkflowEngine transitions
- `test_workflow_template_*` — Phase 34/35/36 data shape
- Plus governance / checkpoint / rollback / slash-discovery

**Missing:**
- `tests/test_template_retriever.py` — DOES NOT EXIST
- `tests/test_tool_retriever.py` — DOES NOT EXIST
- End-to-end "prompt → orchestrator → template match → instantiate
  → verify" — no single test file
- Retrieval-accuracy benchmark — no file in `workspace/baselines/`
- Workflow integration — Phase 34/35/36 templates aren't registered,
  `start_workflow("assemble_pick_place_cell")` returns
  "Unknown workflow_type"

**Empirical hit rate:** anecdotal calibration in `orchestrator.py:858`:
- "pick and place cell with Franka" → CP-01 sim 0.49, gap 0.24 ✓
- CP-02 paraphrase → CP-02 sim 0.55, gap 0.21 ✓
- VR-19 prompt → CP-02 sim 0.51, gap 0.006 (ambiguous, fallback)

Thresholds (0.45 / 0.20) calibrated on these 3 data points.

## Gaps current → intended end-state

| Aspect | Spec intent | Reality |
|---|---|---|
| Tool L-level annotation | 416 tools annotated | 0/416 |
| Template intent fields | All 321 | 5/321 (CP-01..05) |
| LayoutSpec.intent from text | Auto on every prompt | Env-gated `off` |
| `ratify()` (role-binder) | Pure deterministic fn | Doesn't exist; closest: CRM auto-pick |
| Phase 34/35/36 templates registered | Live | Module constants, NOT registered |
| WorkflowEngine wired | Live | Only in convergence test; live handlers use legacy dict |
| Verifier registry | Feature-dispatched | 2 hand-wired verifiers; no dispatch |
| Workflow auto-routing | Implicit from complexity | Doesn't exist |

## Critical gaps

1. **No taxonomy at template level.** Spec separates L-levels (tool)
   from `pattern_hint` (template intent). Only 5 templates declare
   `pattern_hint`. Other 316 are pure similarity bag-of-words.
2. **Spec's "no LLM decisions after intent extraction" unreachable**
   today — when template retrieval misses, entire build is LLM-driven.
3. **Workflow surface is dead weight in production.** Handlers wired
   + tested, but no orchestrator code routes to them. 3 of 3
   spec-canonical workflow templates not registered. User asking
   "Build me a pick-place cell" never hits workflow path.

## 3 concrete suggestions

### (a) Decide: is `pattern_hint` the template's L-level, or orthogonal?

Spec is clear: L1/L2/L3 is a *tool* axis. But user's intuition that
"CP-01 is L2-ish, CP-NEW-3station-oee is L3-ish" is real — there's a
missing template-level axis. Pick one:
- Add `complexity_tier` field to templates (independent of tool
  L-level), use in retrieval ranking
- Or commit to "templates are L2-by-definition" (one deterministic
  tool-chain) and use `start_workflow` exclusively for L3

### (b) Land Phase 34/35/36 template registration

3 files exist with data structure but no caller registers them into
`_WORKFLOW_TEMPLATES`. ~30-line PR makes spec-claimed "landed" status
real. Until then, `start_workflow("assemble_pick_place_cell")` fails.

### (c) Define routing decision explicitly

Either:
- Add 4th classifier axis `task_kind: "atomic" | "templated" |
  "workflow"`, route in orchestrator
- Or document explicitly that workflow invocation is LLM-judgment
  (the LLM sees `start_workflow` in schema, system prompt biases
  toward calling it for multi-phase requests), and measure how
  often it actually happens

User's confusion comes from gap between Phase 18b's clear L1/L2/L3
vocabulary and absence of code path that consumes the labels.
Annotating tools is necessary but not sufficient — without a router
that USES the labels, the taxonomy is descriptive only.
