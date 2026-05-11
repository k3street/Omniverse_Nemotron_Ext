# Harness layers & failure modes — 2026-05-08

Captures the failure analysis + architectural framing surfaced during
Phase 0 + Phase 1.x autonomous work on 2026-05-07/08. Companion to the
post-mortem at `2026-05-07-vr19-form-function-verification.md` and the
roadmap at `2026-05-08-next-session-autonomous-plan.md`.

---

## What this session uncovered

### L1 — Orphan-empty ChromaDB collection (ROOT CAUSE)

The hardest failure to diagnose. `template_retriever.py` and
`tool_retriever.py` both use `chromadb.PersistentClient` against
`workspace/tool_index/`. Their lazy-init pattern was:

```python
try:
    _collection = _client.get_collection(NAME)
except Exception:
    _collection = _client.create_collection(NAME)
    _build_index()
```

When the persist directory was hard-deleted (Phase 0.1 of the plan
asked us to delete `workspace/tool_index/`), ChromaDB recreated an
empty SQLite + collection-metadata structure. `get_collection`
**succeeded** with `count() == 0`. The except branch never fired,
`_build_index()` never ran, and every subsequent retrieval returned
`[]`.

**Behavioral cascade:**
- Agent had no canonical patterns retrieved
- Hence agent invented its own builds (bare Xforms, `target_source="spline"`)
- Hence verify naturally failed
- Hence agent fell back to `run_usd_script` (anti-pattern)
- Hence post-mortem read this as "agent ignores templates" — wrong
  diagnosis. Templates were never delivered.

**Fix (committed 487aadf):** check `count() == 0` after `get_collection`
and trigger `_build_index()` defensively. Same fix in both retrievers.

### L2 — Payload-size driven 503 throttling

Gemini Flash returns 503 ("This model is currently experiencing high
demand") on requests with payloads >1 MB at significantly higher rates
than smaller requests. Verified empirically:

| payload | result |
|---|---|
| 2 KB single message | OK in 1.1s, attempt 1 |
| 7 KB single message | OK in 1.7s, attempt 1 |
| 502 KB single message | OK in 45.8s, attempt 1 |
| 1.0 MB single message | OK in 109s, attempt 1 |
| 1.5 MB single message | OK in 28s, attempt 1 |
| 1.5 MB across 28 messages | OK in 105s after 1 retry |
| 1.5 MB across 38 messages (real chat hist.) | repeated 503s, eventually 200 after 116s |
| 2.9 MB across 49 messages | repeated 503s, eventually OK at 86s |

The throttle is not on tokens — it's on the JSON request payload size
× model load. Multi-message contexts compound the issue because each
message is parsed and processed separately.

**Where the payload comes from:** conversation history. Each tool
call's args are small (~100 bytes) but tool results can be large
(verify returns ~2 KB, run_usd_script results can be 5 KB+).
Agent iteration produces history accumulation: after 14 tool rounds
the history is ~1 MB, after 30 it's ~2.5 MB.

### L3-L5 — All cascade from L1

Once templates were retrievable (post-fix), Phase 1.3 retest showed
the agent:
- Used `robot_wizard` for Franka import (was: bare Xform)
- Used `create_conveyor` (was: assumed `/World/Conveyor_1` existed)
- Used `target_source="curobo"` (was: "spline")
- Called both `verify_pickplace_pipeline` AND `simulate_traversal_check`
- When verify reported issues, used `teleport_prim` + re-anchor
  + re-install (fix-not-thrash)
- Single-shot `run_usd_script` fallback (was: primary build mechanism)

These weren't separate bugs. They were L1 symptoms.

---

## Harness layers — the design philosophy

**The thesis:** weaker model + stronger harness = predictable behaviour
+ fast UX + lower cost. The harness reduces the LLM's degrees of
freedom; the LLM contributes reasoning over a constrained space rather
than open-ended programming.

### Existing harness components (in approximate constraint order)

| Layer | What it does | Constraint contribution |
|---|---|---|
| Resolvers (`resolve_*`) | Vague phrase → structured value | Maps infinite input space to discrete values |
| Template retriever | Goal text → CP-N few-shot | Forces agent toward verified patterns |
| Tool retriever | 346 tools → top-15 | Reduces action space ~23x |
| Spec generator | Complex prompt → structured execution plan | Discretizes planning steps |
| Verifiers (form) | Built scene → `pipeline_ok` + issues | Hard gate on structural correctness |
| Simulator (function) | Built scene → `cube_arrives` boolean | Hard gate on behavioural correctness |
| Honesty rewriter | Dishonest claim + tool failures → forced rewrite | Eliminates false success |
| Auto-author hooks | Common omissions (e.g. DomeLight) → automatic fix | Picks up small mistakes |

### Strong-strong combo

Strong harness + strong model is not "same result faster". It's
**precision-uplift** — harness handles known patterns deterministically
(80% of cases), the model uses its reasoning budget on subtle judgment
calls (orientation conventions, drop clearance heuristics) without
needing to figure out the API surface.

---

## Architectural choice surfaced — iteration vs template-instantiation

### What we currently do

Agent reads template's `code` field as **inspiration**:
- Gets injected into system prompt as `**Approach**: ...` and `**Pattern**:`
- Treated as a reference, not a directive
- Agent re-authors a similar plan, fills in specifics, builds, verifies
- If verify fails, iterates

**Problem:** every iteration accumulates conversation history. For
complex prompts, agents do 30-40 tool calls and accumulate 1.5-3 MB
context. This drives L2 (503 throttling) and reduces determinism.

### What we said we'd do (per plan)

> For task shapes that match a canonical, plan-then-execute beats
> agentic-iteration. Build the canonical first (deterministic).
> Validate. THEN run as agent-eval.

Plan-then-execute = template instantiation. Agent matches prompt to
canonical, executes the canonical's `code` field directly, verifies
once, simulates once, done.

### Comparison

| | Agentic + iterate | Template-instantiation |
|---|---|---|
| Tool calls for VR-19 | 42 (observed) | ~25 (CP-02's `code` length) |
| Conversation history | Grows ~100 KB/iteration | Constant (~5 KB template + tool results) |
| Final-turn payload | 1.5-3 MB | <500 KB |
| 503 risk | High | Low |
| Determinism | Variable | High |
| Time to result | Long (5-25 min) | Short (~1 min if templates work) |
| Generalization | Better on novel prompts | Brittle on prompts that don't match canonicals |

### Hybrid proposal

Detect canonical match strength. High confidence → instantiate. Low
confidence → fall back to agentic iteration with templates as guidance.

```python
# In orchestrator, before normal tool loop:
templates = retrieve_templates(user_message, top_k=3)
if templates and templates[0].match_score > 0.85:
    # Hard match — execute canonical directly
    pre_execute_template(templates[0].code)
    system_prompt += f"\nTask scaffolded from {templates[0].task_id}. " \
                      "Verify, simulate, report results — do not rebuild."
    # Agent does ~3 tool calls (verify + simulate + reply)
else:
    # Soft match or no match — current iteration loop
    inject_templates_as_few_shot(templates)
```

ChromaDB retrieves with similarity scores already; just need to gate
on the score.

---

## Complexity-aware model routing — proposal

`intent_router` already classifies prompts by complexity (trivial /
simple / medium / complex) with confidence. Coupling model selection
to complexity:

| Complexity | Model | Rationale |
|---|---|---|
| trivial (e.g. "create a cube") | Flash | Instant, cheap, deterministic outcomes |
| simple (e.g. "add a robot") | Flash | Fast, templates handle common patterns |
| medium (e.g. "build pick-place") | Flash | Templates load, agent fills params |
| complex (e.g. assembly line) | Pro+thinking | Multi-stage reasoning, larger context |
| novel (no canonical match) | Pro+thinking | Open-ended exploration |

Implementation: a switch in `llm_gemini.py` that reads `complexity`
from the orchestrator's context and overrides `CLOUD_MODEL_NAME`.

---

## Fallback chain proposal

Multi-provider chain, ordered by speed-then-quota-availability:

```
Flash (1st choice — speed)
  └─ on 503 OR payload>1MB → kimi-k2-0905-preview (alt provider, separate quota,
                                                  thinking traces)
       └─ on hard fail OR repeated 503 → Pro+thinking-on (max capability)
```

Kimi context per memory: `api.moonshot.ai`, model
`kimi-k2-0905-preview`, ~$0.60/MTok, has thinking traces. Different
quota pool from Gemini = serves as 503-fallback.

Implementation in `llm_gemini.py`: detect 503, switch provider,
preserve conversation context.

---

## Determinism — the deeper goal

User stated philosophy: **chasing determinism**. Strong harness reduces
variance:

- Resolvers: vague input → fixed output
- Templates: goal → identical canonical sequence (with instantiation)
- Verifiers: same scene → same pass/fail
- Simulator: same physics state → same arrival check

Each layer eliminates a degree of freedom in agent behaviour. The end
state is: same prompt → same scene, run after run.

The current agentic iteration breaks determinism because:
- LLM stochasticity in re-authoring
- Iteration order varies (which fix path the agent picks)
- Iteration count varies (how many rounds before "done")

Template-instantiation restores determinism by removing re-authoring.

---

## Status at end of this session

- Phase 0 (foundation): complete (4 commits)
- Phase 1.1 (verifier strengthening): complete (3 commits)
- Phase 1.2 (simulate_traversal_check): complete (1 commit)
- Phase 1.3 (run VR-19 against Gemini): partial — agent followed
  canonical patterns post L1 fix; final reply produced but truncated
  in transcript; tool sequence captured in session_traces
- Phase 1.4 (Pro+thinking experiment): in flight at time of writing

Open work for next session:
- Decide template-instantiation vs continued iteration approach
- Implement complexity-routing if iteration retained
- Implement payload compression in `context_distiller` if iteration retained
- Investigate Kimi as 503-fallback
- Phase 2 (SORT-01) — design discussion needed before implementation
