# Session summary 2026-05-07/08 + handoff for next session

End-of-night status capture after a long autonomous session covering
Phase 0, Phase 1.1, Phase 1.2, Phase 1.3 (partial), and a multi-iteration
implementation of hard-instantiate canonical templates.

---

## What's verified working (in order of importance)

### 1. The L1 root-cause fix (commit `487aadf`)

ChromaDB's `get_collection()` succeeds with `count() == 0` after a hard-
delete of `workspace/tool_index/` — the lazy-init path's except branch
never fires, so `_build_index()` doesn't run and retrieval returns `[]`
forever. **All of yesterday's "agent ignores templates" failure mode was
this — templates were never delivered.** Both `template_retriever.py`
and `tool_retriever.py` now defensively rebuild on empty.

### 2. Verifier strengthening (Phase 1.1, commits `82cc039 → 8ba6790`)

`verify_pickplace_pipeline` got three form-checks:
  - **conveyor_active** — handoff-gap > 0.30 m must be spanned by an
    active conveyor (PhysxSurfaceVelocityAPI + non-zero velocity)
  - **controller_installed** — per-robot pick-place subscription must
    exist in `builtins`
  - **cube_source_bridged** — cube must be at first pick zone OR on an
    active conveyor that bridges into it

Smoke tests in `scripts/qa/verifier_smoke_tests.py` cover known-good
+ two known-broken fixtures.

### 3. `simulate_traversal_check` tool (Phase 1.2, commit `2e9f96d`)

New tool: stops timeline, captures cube initial pos, plays for
`duration_s`, captures final pos+velocity, checks against target bbox.
Function-gate counterpart to verify's form-gate.

### 4. Hard-instantiate canonical templates (4 commits: `baf456b → a99cd76`)

The biggest architectural addition. When a user prompt confidently matches
a canonical template (similarity ≥ 0.45 AND margin ≥ 0.20):

  1. Orchestrator hard-executes the template's `code` field as tool calls
     via a sandboxed exec (`canonical_instantiator.execute_template_canonical`)
  2. Orchestrator hard-executes the template's `verify_args`
     (`canonical_instantiator.execute_template_verify`)
  3. The LLM tool schema is replaced with `ALLOWED_AFTER_INSTANTIATE`
     (23 verify/inspect/fix tools, NO build tools)
  4. The directive surfaces:
     - That hard-instantiate fired + which template
     - Exact prim paths created
     - Form-gate result (pipeline_ok + issues)
     - Canonical's `verify_args` and `simulate_args` as JSON literals

LLM's role collapses to "summarize the verification, optionally call
simulate_traversal_check, write reply".

Smoke test in `scripts/qa/hard_instantiate_smoke_tests.py` (3/3 pass).

---

## The 5-tier match spectrum (architectural framing)

| Tier | Name | Match-score | Reasoning needed | Implementation status |
|---|---|---|---|---|
| T1 | Exact canonical | sim ≥ 0.85 AND margin ≥ 0.20 | None (similarity alone) | ✅ Implemented |
| T2 | Parameterized canonical | sim 0.7-0.85 + parameter-substitutable | Low (value substitution) | ❌ Future |
| T3 | Composed canonical | 2+ templates relevant, sim > 0.5 | Medium (combination planning) | ❌ Future |
| T4 | Adapted canonical | sim 0.4-0.7 + twist | High (adaptation logic) | ❌ Future |
| T5 | Novel | sim < 0.4 (no match) | Maximum (free planning) | ✅ Default fallback (few-shot iteration) |

The 5-tier progression maps to a model-routing progression: T1 wins
with cheap model + strong harness; T5 needs strong model + weak harness.
Determinism and UX speed both flow from T1's design.

---

## Open architectural questions (priorities for next session)

### A. Template ranking quality

The current retrieval (sentence-transformers all-MiniLM-L6-v2) ranks
CP-01 above CP-02 for VR-19's "3-station assembly line" prompt. The
margin is so small the gate correctly aborts hard-instantiate, but
ideally CP-02 would rank cleanly first.

Possible improvements:
- Embed `goal + thoughts + tools_used + failure_modes` instead of
  just `goal + tools_used`
- Try a stronger embedding model (gte-large, e5-mistral)
- Add structural features (n_robots, n_stations, robot_kind) into the
  similarity score as a re-rank step

### B. T2 implementation: parameterized canonicals

Templates like CP-01 with N-cubes parameter, robot-kind parameter, etc.
Would let the same template serve "build pick-place with 4 cubes" and
"build pick-place with 8 cubes" — currently both go through few-shot
iteration even though only the cube count differs.

Sketch:
- Template gets a `parameters` field (JSON schema)
- Retrieval extracts param values from user prompt (resolver-style)
- `code` field uses `{{param_name}}` placeholders
- Hard-instantiate substitutes before sandbox-exec

### C. Gemini 503 mitigation strategies

- Adaptive payload compression (truncate old tool_results before
  re-sending in conversation history)
- Multi-provider fallback (Flash → Kimi → Pro+thinking)
- Coupling model choice to complexity classification

### D. T3 (composition) reasoning

Combining CP-01 (pick-place) + CP-03 (color-sort) for "pick from belt,
sort by color into two bins". Needs LLM reasoning over template
compatibility and execution order.

### E. The remaining LLM behaviour gap in T1

Even with verify pre-executed and prim paths in directive, LLM
sometimes still calls verify with its own paths. Pre-executing verify
mitigates this but the LLM still hallucinates sub-paths
(`/World/Bin/Floor`, `/World/Conv2/PickZone`). For T1 this is mostly
moot (verify already ran + LLM's call goes to a now-allowed tool with
either correct or incorrect args, with no consequence to scene state).
But it indicates the LLM is fundamentally pattern-matching the user's
natural language regardless of system-prompt directives.

---

## Recommended next-session priorities

In rough order:

1. **Template ranking improvement** (Open question A) — biggest
   leverage. If retrieval ranks confidently, T1 fires more often,
   downstream concerns shrink.

2. **Phase 2 SORT-01** — build CP-03 (color-sort canonical) +
   `verify_args`/`simulate_args` + task spec. Tests T1 on a new
   pattern AND validates the "color routing" design choice
   (controller-arg vs typed-resolver — plan defers to user). Plan
   recommends controller-arg for simplicity.

3. **T2 parameterized canonicals** (Open question B) — natural extension
   of T1. Lets one CP-N template serve a parameter family.

4. **Gemini 503 mitigation** (Open question C) — pragmatic compression
   strategy. Simplest: truncate tool_result strings older than N turns.

5. **Phase 3 CONSTRAINT-01 / Phase 4 REORIENT-01** — additional
   canonical patterns. Each needs design discussion (per plan).

---

## Commits today (2026-05-07/08, all on `anton/feat/live-progress-ui`)

```
a99cd76  qa: hard_instantiate_smoke_tests.py — regression boundary
1df9fe2  chat: pre-execute verify_pickplace_pipeline alongside hard-instantiate
6407178  chat: replace tool schema with explicit verify/fix subset on hard-instantiate
87b83bf  chat: env-configurable hard-instantiate thresholds + stronger directive
baf456b  chat: hard-instantiate canonical templates when match is confident
ee82dd5  docs: harness layers + failure modes — Phase 1.x architectural findings
487aadf  chat/retrievers: defensive rebuild on orphan-empty ChromaDB collections
2e9f96d  chat: simulate_traversal_check + Phase 1.1 hook-cleanup fix (Phase 1.2)
8ba6790  verify_pickplace_pipeline: add cube_source_bridged check (Phase 1.1.3)
d592012  verify_pickplace_pipeline: add controller_installed check (Phase 1.1.2)
82cc039  verify_pickplace_pipeline: add conveyor_active check (Phase 1.1.1)
78383d5  qa: VR-19 v2 — form + function dual gate (Phase 0.4, doc only)
8a8f878  qa: verifier_smoke_tests.py — regression boundary (Phase 0.3)
0acd441  templates: slim CP-01/CP-02 thoughts (Phase 0.2)
a080091  qa: scrub_shared_data.py — Phase 0.1 codified scope
2dbf32f  templates: CP-01 add DomeLight + switch to cube-only rubber
697ea48  qa: persist CP-01/02 deterministic test scripts
```

PR #89 untouched. All work on `feat/live-progress-ui`.

Smoke tests:
- `scripts/qa/run_cp01.py` + `run_cp02.py` — canonical builds
- `scripts/qa/verifier_smoke_tests.py` — verify form-gate fixtures
- `scripts/qa/hard_instantiate_smoke_tests.py` — gate logic + instantiation
- `scripts/qa/scrub_shared_data.py` — Kimate-path scrub

Env config:
- `CANONICAL_INSTANTIATE` (on/off, default on)
- `CANONICAL_MIN_SIM` (default 0.45)
- `CANONICAL_MIN_MARGIN` (default 0.20)
- `TEMPLATE_TOP_K` (default 3)
- `LLM_MODE=cloud`, `CLOUD_MODEL_NAME=gemini-3-flash-preview`
