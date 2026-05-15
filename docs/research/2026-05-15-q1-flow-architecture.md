# Q1 — Right Prompt-to-Execution Flow Architecture

**Date:** 2026-05-15
**Researcher:** Claude Sonnet agent
**Prior:** `docs/research/2026-05-14-l-levels-discovery-audit.md`
**Question:** What is the right prompt-to-execution flow for this codebase? Compare modes A–E and recommend per intent × complexity bucket.

---

## 1. ASCII Flowchart — Today's Flow

File:line citations on every branch.

```
USER MESSAGE
     │
     ▼
┌─────────────────────────────────────────────────────────────────┐
│ 1. Slash-command check                                          │
│    orchestrator.py:655                                          │
│    if message.startswith("/") → run slash handler, return       │
└─────────────────────────────────────────────┬───────────────────┘
                                              │ normal message
                                              ▼
┌─────────────────────────────────────────────────────────────────┐
│ 2. classify_intent()   [intent_router.py:22]                    │
│    → {intent, multi_step, complexity, confidence}               │
│    8 intents × 3 complexities; one LLM round-trip               │
│    orchestrator.py:696                                          │
└────────────┬───────────────────────────────┬────────────────────┘
             │ complexity == "complex"         │ else
             │ AND intent != general_query     │
             │ AND STRATEGIC_NEGOTIATOR=on     │
             │ AND NOT prior-clarification     │
             ▼                                │
┌────────────────────────┐                   │
│ 3. negotiate()         │                   │
│    negotiator.py:162   │                   │
│    one fast-LLM call   │                   │
├────────────────────────┤                   │
│ needs_clarification?   │                   │
│  YES → return questions│                   │
│        (turn ends)     │                   │
│  NO  → fall through    │                   │
└────────┬───────────────┘                   │
         │                                   │
         └────────────────┬──────────────────┘
                          ▼
┌─────────────────────────────────────────────────────────────────┐
│ 4. Turn snapshot (patch_request / scene_diagnose only)          │
│    orchestrator.py:781  turn_snapshot.capture()                 │
└─────────────────────────────────────────────┬───────────────────┘
                                              ▼
┌─────────────────────────────────────────────────────────────────┐
│ 5. Scene context gather (Kit RPC alive?)                        │
│    orchestrator.py:806  get_stage_context()                     │
│    full=True for scene_diagnose | prim_inspect, else summary    │
└─────────────────────────────────────────────┬───────────────────┘
                                              ▼
┌─────────────────────────────────────────────────────────────────┐
│ 6. RAG / KB retrieval  orchestrator.py:819–848                  │
│    retrieve_context, find_matching_patterns,                    │
│    get_error_learnings, get_success_learnings,                  │
│    get_negative_patterns, deprecations_index.lookup             │
└─────────────────────────────────────────────┬───────────────────┘
                                              ▼
┌─────────────────────────────────────────────────────────────────┐
│ 7. Template retrieval  orchestrator.py:879                      │
│    env MULTIMODAL_TEXT_INTENT=on?                               │
│     YES → produce_layout_spec_from_text → retrieve_with_intent_filter│
│     NO  → retrieve_templates_with_scores (DEFAULT)             │
│                                                                 │
│    top_sim = scored[0].similarity                               │
│    margin  = top_sim - scored[1].similarity                     │
│                                                                 │
│    CANONICAL_INSTANTIATE=on                                     │
│    AND top_sim >= 0.45  (CANONICAL_MIN_SIM)                     │
│    AND margin  >= 0.20  (CANONICAL_MIN_MARGIN)                  │
├───────────────────┬─────────────────────────────────────────────┤
│  HARD-INSTANTIATE │  FEW-SHOT GUIDE (default / fallback)        │
│  (Mode A)         │  (iterative loop, Mode B-ish)               │
│  orchestrator.py  │  orchestrator.py:988                        │
│  :931             │  inject top-3 templates as prose in         │
│                   │  patterns_text                              │
│  execute_template │                                             │
│  _canonical()     │                                             │
│  settle_after_    │                                             │
│  canonical()      │                                             │
│  execute_template │                                             │
│  _verify()        │                                             │
│                   │                                             │
│  replace          │                                             │
│  selected_tools   │                                             │
│  with ALLOWED_    │                                             │
│  AFTER_INSTANTIATE│                                             │
│  (28 tools)       │                                             │
│  canonical_inst-  │                                             │
│  antiator.py:73   │                                             │
└───────────────────┴──────────────────────┬──────────────────────┘
                                           ▼
┌─────────────────────────────────────────────────────────────────┐
│ 8. Spec + gap (complexity==complex AND STRATEGIC_SPEC=on)       │
│    orchestrator.py:999                                          │
│    generate_spec()   → StructuredSpec  spec_generator.py:181   │
│    gap_analyze()     → GapReport       gap_analyzer.py:102     │
│    format_spec_as_checklist() injected into patterns_text       │
│    (only when no high-similarity template match)                │
└─────────────────────────────────────────────┬───────────────────┘
                                              ▼
┌─────────────────────────────────────────────────────────────────┐
│ 9. distill_context()   context_distiller.py:649                 │
│    → messages[], selected_tools[]                               │
│    _ALWAYS_TOOLS (24 tools) always included                     │
│    + intent-category tools (context_distiller.py:139-146)      │
│    + tool_retriever embedding-rank (top-K semantic match)       │
│                                                                 │
│    HARD-INSTANTIATE path: replace selected_tools                │
│    with ALLOWED_AFTER_INSTANTIATE  orchestrator.py:1100         │
└─────────────────────────────────────────────┬───────────────────┘
                                              ▼
┌─────────────────────────────────────────────────────────────────┐
│ 10. Tool-calling loop  orchestrator.py:1189                     │
│     for round_idx in range(max_rounds):                         │
│       LLM.complete(messages, tools)                             │
│       if no tool_calls → break                                  │
│       for each tool_call:                                       │
│         execute_tool_call(fn_name, fn_args)                     │
│         append result to messages                               │
│         spam-halt if ≥6 consecutive failures                    │
│       loop                                                      │
└─────────────────────────────────────────────┬───────────────────┘
                                              ▼
┌─────────────────────────────────────────────────────────────────┐
│ 11. Post-loop verify-contract  orchestrator.py:1843             │
│     For patch_request / scene_diagnose:                         │
│     (a) prim_exists checks on /World/... mentions               │
│     (b) count_prims_under_path checks on numeric claims         │
│     (c) get_world_transform checks on pose claims               │
│     (d) list_applied_schemas checks on schema claims            │
│     (e) get_attribute checks on attribute=value claims          │
│     (f) turn_diff unsubstantiated-path check                    │
│     Mismatches appended as warning blocks to reply              │
└─────────────────────────────────────────────────────────────────┘
```

---

## 2. Per-Mode Analysis

### Mode A — Single-shot retrieve + instantiate (TODAY'S DEFAULT for high-confidence matches)

**What exists:**
- `canonical_instantiator.py`: `execute_template_canonical`, `settle_after_canonical`, `execute_template_verify` — full implementation, tested in `test_canonical_instantiator.py` (485 lines)
- `orchestrator.py:931`: hard-instantiate path executes when `top_sim >= 0.45 AND margin >= 0.20`
- `ALLOWED_AFTER_INSTANTIATE` (28 tools): enforces verify-only after build to prevent rebuild
- Threshold calibration: 3 data points (CP-01, CP-02, VR-19 paraphrase)

**What's missing:**
- Threshold calibration is thin (3 data points). No held-out retrieval-accuracy benchmark (`workspace/baselines/` has no retrieval test files; prior audit confirmed `test_template_retriever.py` does not exist)
- 5/321 templates have `intent` field — structural-filter path (`MULTIMODAL_TEXT_INTENT=on`) participates only CP-01..CP-05
- No feedback loop: when hard-instantiate produces wrong result, no mechanism captures it to tune thresholds
- `confident_match` gate is binary — no graceful degradation (e.g. medium-confidence → ask before instantiating)

**Performance evidence:**
- CP-01..CP-05 canonical family: high success (CP-01, CP-02, CP-04 stable_ok in multiple baseline runs)
- `batch-c-baseline.json`: 3/5 stable_ok
- `yrkesroll-n3-baseline.json` (7 tasks): 5/7 stable_ok, 2 flaky
- `full-n1-overnight-baseline.json` (109 tasks): 31 stable_ok / 70 stable_fail — Mode A only works for the canonical subset; non-canonical tasks fall through to iterative loop and struggle

**Verdict:** Mode A is production-ready for the ~30 canonical pick-place variants. It is NOT a general solution; ~65% of the broader task corpus (non-canonical shapes) gets no benefit from it.

---

### Mode B — Iterative explore (Claude Code style: observe → plan → act)

**What exists:**
- The iterative tool-calling loop (`orchestrator.py:1189`) already allows multi-round LLM tool-calling — the LLM can call `scene_summary`, `list_all_prims`, `find_prims_by_name`, etc. as read-only reconnaissance before mutating
- `_ALWAYS_TOOLS` includes `scene_summary`, `resolve_*` family, `verify_pickplace_pipeline` — observe-tools always available
- `spec_generator.py:181` encourages "diagnostic and inspection tools FIRST when user asserts something about current scene" in its system prompt
- The revertion of the read-only round-0 gate (`orchestrator.py:1145-1158`) was explicitly motivated by empirical evidence that forcing a planning round regressed behavior ("agent shoved all steps into a single atomic script")

**What's missing:**
- No structured "observation phase" before mutation. The LLM is never explicitly instructed to spend round 0 building a mental model of the scene before acting. It may do so opportunistically, but there is no code enforcement
- No scene-state diff between "before observation" and "after mutation" that feeds back into a replanning step
- No "scratchpad" turn accumulation — the LLM sees only the final distilled messages, not an explicit "what I learned from reading" accumulation mechanism
- For GR00T / loco-manipulation workflows (`groot_finetune_pipeline.py:Phase 62`), inference execution requires weights + GPU (opus-runtime gated); the pipeline is scaffolded but cannot actually call `finetune_groot` without external runtime. Pi0 and OpenVLA are not present in the service code (only `gr00t` appears, in the eval harness with difficulty levels L1-L5). Their inference patterns are not implemented.

**Cost of adding Mode B properly:**
- A "scout round" where the distiller emits only read-only tools (and injects a "build mental model, then call done_scouting" sentinel tool) would require:
  - New `scout_round_complete` sentinel tool or a state machine in the loop (~50-100 LOC in orchestrator)
  - Per-intent gating: only worthwhile for `scene_diagnose`, `prim_inspect`, `vision_inspect` — adds latency for simple `patch_request` tasks where the scene is known
- The empirical evidence from the reverted read-only gate is cautionary: forced planning phases can make things worse by encouraging mega-patches

**Verdict:** Mode B capability partially exists via the iterative loop + always-tools. A true Mode B requires modest plumbing (~100-150 LOC) but has a documented risk of regression if the scout phase is too long or encourages batched mutations. Best suited for diagnostic intents where observation precedes action.

---

### Mode C — Plan-then-execute with checkpoints

**What exists:**
- `spec_generator.py`: generates a `StructuredSpec` with ordered steps, expected tools, post-conditions — this IS a plan artifact. It is injected into the system prompt, not executed step-by-step
- `gap_analyzer.py`: cross-checks expected tool names against registry — feeds back into the plan display
- `workflow.py:_handle_start_workflow` (`orchestrator.py`-adjacent): full plan-checkpoint-execute-verify cycle exists for 3 workflow types (`rl_training`, `robot_import`, `sim_debugging`) with `approve_workflow_checkpoint` gating at each phase — this IS Mode C for those 3 workflows
- `_WORKFLOW_TEMPLATES` in `_state.py:279`: 3 registered types; Phase 34/35/36 templates are module constants but NOT registered (prior audit confirmed)

**What's missing:**
- The spec from `spec_generator` is advisory only — there is no code that steps through it, enforces each tool call, or gates on post-conditions. The LLM reads the checklist and MAY follow it; it does not MUST
- WorkflowEngine (`workflow.py`) is wired for 3 types but there is no orchestrator-level router that sends "complexity==complex AND multi_step==True" to `start_workflow` automatically. User or LLM must explicitly call `start_workflow`; the orchestrator never routes there
- No "plan approval gate" before execution for non-workflow complex tasks — the plan is injected inline without user sign-off
- No step-level verification loop: Mode C requires "execute step N → verify post-condition N → if ok proceed to N+1, else retry/branch" — the current loop is round-based not step-based

**Cost of adding Mode C properly:**
- Needs a step-execution runtime in the loop (similar to WorkflowEngine but at the spec step level): ~200-300 LOC
- OR: route `complexity==complex` to `start_workflow` (if LLM judges the task fits a known workflow type) — this is plausible with ~30-50 LOC in the orchestrator routing block
- WorkflowEngine already implements the full checkpoint-execute-verify cycle; the gap is only the auto-routing

**Verdict:** Mode C exists fully for the 3 registered workflow types if `start_workflow` is called. It does NOT exist as an auto-routed path from `complexity==complex`. The `spec_generator` generates a plan but does not enforce execution. Major plumbing is NOT required if the approach is "route complex tasks to `start_workflow`" — that is a 30-50 LOC change. Full step-enforcement runtime is more work.

---

### Mode D — Negotiator + canonical

**What exists:**
- `negotiator.py:162`: fast-LLM clarification gate, single-turn, fires only on `complexity==complex AND intent!=general_query` — this IS Mode D's clarification half
- Hard-instantiate path (`orchestrator.py:931`): canonical execution after match — this IS Mode D's execute half
- The two halves are NOT in sequence: negotiator fires before template retrieval; hard-instantiate fires after. Together they form a partial Mode D for the case where complexity==complex AND template matches confidently

**What's missing:**
- No canonical matching for the case where negotiator fires AND clears AND the template retrieval then hits. The negotiator stops the turn; the next turn re-runs from scratch including retrieval. There is no "carry forward the negotiation result into the retrieval query" mechanism
- Negotiator is domain-agnostic (per `negotiator.py:25`: "It is NOT a full spec_generator. There is no plan output, no post-conditions, no gap-analyzer over the tool catalog") — it asks for missing intent-level inputs only, not for parameters the agent could self-discover

**Verdict:** Mode D is partially implemented — the negotiation half exists, the canonical execution half exists. They operate sequentially across turns rather than in one tight "negotiate → match → instantiate" pipeline. Good enough for current use; the multi-turn behavior is documented and accepted.

---

### Mode E — Hybrid (different modes per intent/complexity)

**What exists:**
- The current orchestrator IS Mode E in practice: the routing code at `orchestrator.py:696-1113` makes per-turn decisions based on `(intent, complexity, top_sim, margin)` that map to different execution paths
- Specifically:
  - `intent==general_query` → skip negotiator, skip spec_generator → pure LLM answer (no tool loop needed)
  - `complexity==complex AND intent!=general_query AND STRATEGIC_NEGOTIATOR=on` → negotiator gate
  - `top_sim >= 0.45 AND margin >= 0.20` → hard-instantiate (Mode A)
  - else → few-shot guide + tool loop (Mode B-ish)
  - `complexity==complex AND STRATEGIC_SPEC=on` → spec_generator + gap_analyzer injected (partial Mode C)
  - User explicitly calls `start_workflow` → full Mode C workflow

**What's missing:**
- No explicit documentation of the hybrid routing table in code — it is implicit in the `if/elif` chain
- The routing conditions are not tested as a whole (no `test_flow_routing.py`)
- `multi_step=True` is classified but produces zero behavioral change (`orchestrator.py:1162-1174` explicitly notes the read-only gate was reverted)
- No routing to GR00T/Mimic inference path from the orchestrator (those modules are standalone; no `intent=loco_manipulation` type)

---

## 3. Decision Matrix: 24-Bucket Recommendation

### Definitions
- **Complexity**: single (1 tool), multi (2-4 sequential), complex (5+ components / multi-subsystem)
- **Current behavior**: what actually happens today
- **Recommended mode**: what SHOULD happen
- **Gap**: work required to close the gap

| Intent | Complexity | Current Behavior | Recommended Mode | Gap |
|---|---|---|---|---|
| general_query | single | LLM reply, no tools | A: answer directly | None — already works |
| general_query | multi | LLM reply, few-shot guide | A: answer directly | None — multi_step flag unused anyway |
| general_query | complex | negotiator (may fire) → LLM reply | D: negotiate scope, then A | Negotiator already fires; OK |
| scene_diagnose | single | full context + tool loop | B: observe → single report | Mode B scout step would help; low priority |
| scene_diagnose | multi | full context + tool loop | B: observe → diagnose → fix → verify | Scout phase worth adding (see §5.1) |
| scene_diagnose | complex | negotiator → spec_generator → tool loop | B+D: negotiate scope → spec checklist → stepped diagnosis | Already closest to correct; spec is advisory not enforced |
| vision_inspect | single | vision_provider call | A: direct capture + describe | Already works |
| vision_inspect | multi | tool loop with camera tools | B: capture → analyze → correlate | Works adequately via loop |
| vision_inspect | complex | spec_generator + tool loop | B+C: multi-angle capture + structured report | Spec injection helps but is advisory |
| prim_inspect | single | prim tools | A: direct read + report | Works |
| prim_inspect | multi | tool loop | B: traverse hierarchy + report | Works via loop |
| prim_inspect | complex | spec_generator + loop | B: deep traverse + schema audit | Works adequately |
| patch_request | single | hard-instantiate if canonical, else loop | A: direct apply | Works for canonical shapes; iterative loop adequate otherwise |
| patch_request | multi | few-shot guide + loop | A with hard-instantiate OR B with verify | Non-canonical multi-patch is the hard case; needs verify after each step |
| patch_request | complex | negotiator → spec_generator → loop | C: plan → approve → step execute | WorkflowEngine covers `robot_import`+`sim_debugging`; orchestrator should auto-route here |
| physics_query | single | physics tools | A: direct read + report | Works |
| physics_query | multi | tool loop | B: read joint states → diagnose → report | Works via loop |
| physics_query | complex | spec_generator + loop | B+D: negotiate robot/joint scope → observe → report | Negotiator fires; adequate |
| console_review | single | console tools | A: direct read + parse | Works |
| console_review | multi | tool loop | B: read → parse → look up KB → report | Works via loop + KB retrieval |
| console_review | complex | spec_generator + loop | B: full diagnostic sweep | Works adequately |
| navigation | single | navigation tools | A: direct navigate | Works |
| navigation | multi | tool loop | A: sequence navigation calls | Works |
| navigation | complex | spec_generator + loop | C: layout-spec → navigate → verify | Rare in practice; adequate via loop |

### Key observations from the matrix

1. **Mode A (hard-instantiate) is only well-supported for `patch_request × single/multi` canonical shapes**. The 5/321 templates with `intent` field means structural retrieval is near-blind.

2. **Mode B is the de-facto fallback** for the other ~22 buckets. It works but lacks a structured observation phase, meaning the agent sometimes starts mutating before understanding current scene state.

3. **Mode C (plan-then-execute) is the right target for `patch_request × complex`** — these are multi-subsystem builds (RL training cell, full robot cell with conveyor + sensor + bin). WorkflowEngine already handles 3 cases. The gap is auto-routing.

4. **Mode D (negotiate + canonical) is correct for `general_query × complex` and `*_inspect × complex`**. Already works.

5. **Mode E is what we have** — the existing code is already hybrid. The goal is to make the routing explicit, tested, and complete.

---

## 4. Threshold Proposals

### Current thresholds
```
CANONICAL_MIN_SIM = 0.45   (env: CANONICAL_MIN_SIM)
CANONICAL_MIN_MARGIN = 0.20 (env: CANONICAL_MIN_MARGIN)
TEMPLATE_TOP_K = 3          (env: TEMPLATE_TOP_K)
```

### Proposed routing table

The following replaces the implicit if/elif chain in `orchestrator.py:879-997` with an explicit routing policy:

```
Intent             complexity     top_sim / margin       → MODE
───────────────    ─────────      ──────────────────     ───────────────────────────────
general_query      any            any                    → LLM answer, skip tool loop
                                                           (if multi_step: still skip tools,
                                                           LLM answers from KB only)

vision_inspect     any            any                    → Mode B: always-tools + loop
                                                           (vision tools dominate; template
                                                           match would over-constrain)

prim_inspect       any            any                    → Mode B: prim tools + loop

console_review     any            any                    → Mode B: console + KB tools

navigation         any            any                    → Mode A: direct tool; no loop needed
                                                           unless multi (then Mode B)

scene_diagnose     any            sim >= 0.45 AND        → Mode A: instantiate diagnostic
                                  margin >= 0.20           canonical then verify
                   any            else                   → Mode B: SCOUT_ROUND then loop
                                                           (see §5.1)

physics_query      any            any                    → Mode B: read tools + loop

patch_request      single/multi   sim >= 0.45 AND        → Mode A: hard-instantiate
                                  margin >= 0.20
                   single/multi   0.30 <= sim < 0.45    → Mode D: negotiate if complex,
                                                           then few-shot guide + loop
                   single/multi   sim < 0.30            → Mode B: loop with spec if complex
                   complex        any                    → Mode C: route to start_workflow
                                                           if task type matches known workflow;
                                                           else spec_generator + loop
```

### Numeric rationale

- `sim >= 0.45 AND margin >= 0.20`: current calibration; keep until a retrieval benchmark exists
- `0.30 <= sim < 0.45`: "probably relevant template" band — inject as guidance, negotiate intent before executing
- `sim < 0.30`: no useful template; fall through to pure iterative (Mode B)
- `patch_request × complex`: complexity threshold already flags these; add explicit `start_workflow` route for `rl_training` / `robot_import` / `sim_debugging` shape tasks

---

## 5. Three Concrete Actionable Changes

### 5.1 — Add an explicit diagnostic scout pass for `scene_diagnose` intents

**Files:** `service/isaac_assist_service/chat/orchestrator.py` (add ~80 LOC), `service/isaac_assist_service/chat/context_distiller.py` (add ~20 LOC)

**What:** When `intent == "scene_diagnose"` AND the hard-instantiate path did NOT fire, inject a round-0 instruction that limits tools to read-only (`scene_summary`, `list_all_prims`, `find_prims_by_schema`, `check_physics_health`, `get_console_errors`). After round 0 returns, allow full tool schema for subsequent rounds. This prevents the common failure mode where the agent starts patching before understanding the scene.

**How (sketch):**
```python
# orchestrator.py, before the tool-calling loop (line ~1185)
_scout_mode = (
    intent == "scene_diagnose"
    and not _instantiated_build_tools
    and intent_complexity != "single"
)
# In loop round 0:
if _scout_mode and round_idx == 0:
    _loop_tools = [t for t in selected_tools
                   if t["function"]["name"] in _SCOUT_ONLY_TOOLS]
else:
    _loop_tools = selected_tools
```

**Why not already done:** The prior read-only gate was reverted (`orchestrator.py:1145-1158`) because it caused regression on `patch_request` (agent batched everything into one script). Scoping the gate to `scene_diagnose` only avoids that failure mode.

**Estimated LOC:** ~80 LOC (constant definition + loop gating + distiller hint)

**Risk:** Low. Fail-open: if `_SCOUT_ONLY_TOOLS` check misclassifies, tool loop still runs; worst case is one extra read-only round before acting.

---

### 5.2 — Auto-route `patch_request × complex` to `start_workflow` when task shape matches

**Files:** `service/isaac_assist_service/chat/orchestrator.py` (~50 LOC), `service/isaac_assist_service/chat/intent_router.py` (~10 LOC)

**What:** After `classify_intent` returns `complexity=="complex"` AND `intent=="patch_request"`, AND the negotiator cleared (no `needs_clarification`), AND `top_sim < 0.45` (no canonical match), check if the clarified task text matches one of the 3 registered `_WORKFLOW_TEMPLATES` types. If yes, auto-call `start_workflow` before the tool loop and let the WorkflowEngine own the execution.

**How (sketch):**
```python
# orchestrator.py, after negotiator gate (line ~769)
if (
    intent == "patch_request"
    and intent_complexity == "complex"
    and not confident_match          # no canonical template match
    and not _prior_was_clarification
):
    wf_type = _infer_workflow_type(user_message)  # simple keyword heuristic
    if wf_type:
        # Route to workflow handler; LLM will see start_workflow in tools
        # and the auto-inject note will guide it to call start_workflow(wf_type)
        _workflow_hint = (
            f"\n## Routing hint\n"
            f"This request matches workflow type '{wf_type}'. "
            f"Call start_workflow(workflow_type='{wf_type}', goal=...) first.\n"
        )
        patterns_text = _workflow_hint + (patterns_text or "")
```

`_infer_workflow_type` is a simple keyword heuristic (~10 LOC): "RL training" / "reinforcement" → `rl_training`; "import robot" / "URDF" / "STEP" / "verify collision" → `robot_import`; "debugging" / "physics error" / "why is it wrong" → `sim_debugging`.

**Why not already done:** Prior audit confirmed no orchestrator code routes to WorkflowEngine. The WorkflowEngine is fully wired and tested; only the auto-routing is missing.

**Estimated LOC:** ~50 LOC (keyword heuristic + hint injection)

**Risk:** Low. The hint only suggests calling `start_workflow`; the LLM still decides. If the heuristic misclassifies, the LLM will see `start_workflow` in schema + the hint, evaluate it wrong, and fall back to normal tool loop. No structural regression.

---

### 5.3 — Enforce spec post-conditions in the tool-calling loop

**Files:** `service/isaac_assist_service/chat/orchestrator.py` (~100 LOC), `service/isaac_assist_service/chat/spec_generator.py` (~30 LOC)

**What:** The `StructuredSpec` already contains `post_condition` per step. Currently the checklist is injected as a system-prompt note and the LLM may ignore it. Add loop-level enforcement: after each round, check if the spec's next step `expected_tool` was called; if not, emit a mandatory retry message into the messages list ("You have not yet called `<expected_tool>`. Do NOT proceed to the next step without calling it."). This converts the advisory spec into a mandatory step gate.

**How (sketch):**
```python
# In the tool-calling loop, after tool results are appended:
if spec_steps and round_idx < len(spec_steps):
    current_step = spec_steps[round_idx]
    expected = current_step["expected_tool"]
    was_called = any(
        t["tool"] == expected for t in executed_tools
    )
    if not was_called and expected not in _NONTOOL_SENTINELS:
        messages.append({
            "role": "user",
            "content": (
                f"[SPEC GATE] Step {current_step['n']} requires "
                f"`{expected}` — you have not called it yet. "
                f"Call it now before continuing."
            ),
        })
```

**Why this matters:** The 2026-05-04 broad-persona canary found 40% of fails were "skipped required tool" (`spec_generator.py:13-21`). The spec injection alone had minimal effect because it was advisory. Making it a hard gate converts the spec from documentation into enforcement.

**Estimated LOC:** ~100 LOC (step tracker + gate message + spec-step index progression logic)

**Risk:** Medium. If the LLM cannot call `expected_tool` (tool not in schema, wrong args), the gate loops forever. Need a `max_retries_per_step=2` escape hatch. Also, round-indexing to spec steps is a simplification — actual execution is non-linear. Should use "has any tool from step N been called?" rather than strict `round_idx == step_n` alignment.

---

## 6. Honest Gaps — Where We Are Too Far From a Mode to Recommend It Without Major Work

### Gap A: Mode B "structured observe-then-act" is a design choice, not missing code

The iterative loop already allows the agent to call `scene_summary` before mutating. The reason it often does NOT is purely prompt-level: the RULE_BASE system prompt (`context_distiller.py:182`) says "you MUST execute the FULL plan end-to-end in this turn" — which inadvertently discourages cautious reconnaissance. A real Mode B needs a deliberate system-prompt split: "round 0 = observe, report what you see; round 1+ = act". This is culturally a regression risk (reverted read-only gate shows it can hurt). Proceed with the scoped `scene_diagnose` gate only (§5.1).

### Gap B: Mode C step-execution runtime for non-workflow complex tasks

Turning `spec_generator` output into a step-enforced execution runtime (not just a prompt hint) requires:
- A spec-step state machine that tracks which steps are complete
- Per-step verification (calling the `post_condition` as a read tool to confirm it holds)
- A retry/branch mechanism if a step fails

This is ~300 LOC and touches the core loop. WorkflowEngine ALREADY implements this for 3 workflow types — the cleanest path is to register more workflow types (Phase 34/35/36 templates from the prior audit need registration) rather than build a parallel runtime.

### Gap C: No retrieval benchmark — threshold tuning is blind

Thresholds `sim >= 0.45 AND margin >= 0.20` are calibrated on 3 manually-checked prompts. There is no held-out benchmark. Before tightening or loosening thresholds (e.g. moving to 0.40 to catch more canonicals) it is necessary to have a retrieval test suite. The prior audit's `test_template_retriever.py` gap is blocking here. Estimated work: ~100 LOC benchmark script + 30-50 labeled (prompt → expected_template) test cases. Until this exists, threshold changes are guesswork.

### Gap D: GR00T / Mimic / Pi0 inference path is not wired into the orchestrator

`groot_finetune_pipeline.py` and `sub_phase_62b_groot_n17_eval_harness.py` exist as standalone modules. There is no `intent` type for "loco-manipulation / VLA inference" in `intent_router.py:22`. There is no orchestrator routing that would call `finetune_groot`, `load_groot_policy`, or `evaluate_groot` in response to a user prompt. These tools exist in the tool executor but get there only if the LLM spontaneously calls them. Adding a proper Mode B "observe simulation → select policy → run inference → evaluate" pipeline for GR00T would require a new intent type, a new workflow template registration, and an inference runtime adapter. This is major work (>500 LOC) and blocked on having GR00T weights + GPU available in the service environment.

### Gap E: Negotiator is intentionally thin — cannot negotiate plumbing parameters

The negotiator's system prompt (`negotiator.py:88`) explicitly excludes asking for file paths, default poses, or discoverable parameters. This is correct design for preventing over-asking. But for tasks like "build a 3-station cell from my STEP files", the agent has no way to know STEP paths exist without calling `list_local_files` first. The result is that complex tasks with external assets either: (a) get negotiated badly (agent asks for paths unnecessarily), or (b) hallucinate asset paths. Neither is ideal. A better approach: negotiator clears → agent calls `list_local_files` / `catalog_search` in round 0 (scout) → reports discovered assets → acts. This requires the scout-phase addition (§5.1) and a `list_local_files` entry in the scout tool set.

---

## 7. Summary Table

| Mode | Code exists? | Production traffic? | Recommended for |
|---|---|---|---|
| A (hard-instantiate) | Yes — `canonical_instantiator.py` | Yes — when `sim >= 0.45, margin >= 0.20` | `patch_request × single/multi` canonical shapes |
| B (iterative explore) | Partial — iterative loop exists; no structured scout phase | Yes — fallback for all non-canonical prompts | All inspect/diagnose intents; non-canonical patch; default fallback |
| C (plan-then-execute) | Yes for 3 workflow types — `workflow.py`; plan-only (advisory) for complex via `spec_generator` | Only if user/LLM calls `start_workflow` explicitly | `patch_request × complex` matching `rl_training / robot_import / sim_debugging` |
| D (negotiate + canonical) | Yes — `negotiator.py` + hard-instantiate path | Yes — `complexity==complex` gate | `general_query × complex`; `scene_diagnose × complex` |
| E (hybrid) | Yes — implicit in orchestrator routing | Yes — every turn | All — current architecture IS Mode E; needs explicit routing table |

---

## 8. One-Page Routing Decision Summary

```
EVERY TURN flows through this decision tree
(implementing this explicitly is the §5 recommendation)

intent == general_query
    → LLM answer from KB only; no tool loop needed
    → if complexity==complex AND negotiator fires: negotiate then answer

intent == vision_inspect | prim_inspect | console_review
    → Mode B: full context + tool loop
    → spec if complex (advisory; see Gap C for enforcement)

intent == navigation
    → Mode A: direct tool; single round

intent == physics_query
    → Mode B: read tools first, report second

intent == scene_diagnose
    → sim >= 0.45 AND margin >= 0.20: Mode A (diagnostic canonical)
    → else: Mode B with scout round (§5.1) for multi/complex

intent == patch_request
    → sim >= 0.45 AND margin >= 0.20: Mode A (build canonical)
    → complexity == complex AND task matches workflow type: Mode C (§5.2)
    → complexity == complex, no workflow match: Mode B + spec_generator + gap_analyzer
    → else: Mode B (few-shot guide + tool loop)
```

---

*Researcher note: All code claims are cited with file:line. All performance figures are from `workspace/baselines/` JSON files read during this session. No production code was modified.*
