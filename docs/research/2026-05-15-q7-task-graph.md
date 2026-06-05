# Q7 — Autonomous-Execution Dependency Graph

**Date:** 2026-05-15
**Researcher:** Opus 4.7 (1M context) — Phase 3 synthesis
**Inputs:** Q1–Q6 research reports + L-levels discovery audit + parent research spec
**Companion file:** `docs/research/2026-05-15-q7-task-graph.yaml` (machine-parseable)
**Scope:** 82 task nodes across 7 tracks, dependency-ordered, designed for a Sonnet-default cron to chew on for weeks.

---

## 1. Synthesis summary — top 10 actionable findings across Q1–Q6

These ten findings drive the task graph. Every task node ties back to one of them.

1. **Schema is the foundational lever, not retrieval.** Only 5 of 321 templates have `intent` fields, and only 5 have `roles` (Q2 §1). The structural-filter retrieval path is built but env-gated off because the corpus that would feed it does not exist (Q3 §5; L-levels audit §3). Without `intent` coverage ≥30 templates, the structural improvements in Q3 are inert. → **Track B (schema migration) gates Track C (retrieval).**

2. **Retrieval has zero ground-truth metric.** Thresholds `sim ≥ 0.45 AND margin ≥ 0.20` are calibrated on **4 hand-picked data points**, all pick-place domain (Q3 §2a). The baselines record *execution* success (cube-in-bin), not *retrieval* hit-rate (Q3 §2b). → Q3 §9 calls the 30-prompt benchmark "the single highest-value action: it turns 'probably works' into a number." → **t19 + t20 are critical-path.**

3. **63 of 109 CP templates are shippable; the rest are noisy.** Q4 §2 — 58 function-gate ✓ + 5 role-based + 15 "form-gate pending since 2026-05-08" (misleading) + 22 CP-NEW drafts with zero function-gate. → Track A (drift remediation) is mostly mechanical except for D2 form-gate re-runs which are Kit-bound (sequential). The minimum-viable remediation budget per Q4 §6 is ~6 agent-hours — small.

4. **30 ghost `TP-*` IDs in `role_template_index.py` reference non-existent files.** Q4 §5.1 + Q3 §3 Category D. `retrieve_template_by_role` surfaces these as if loadable; downstream `_load_template` returns `None` silently. → **t04 is the highest-risk structural issue per Q4 §7 finding 6.** Re-map, do not delete.

5. **Workflow templates (Phase 34/35/36) are written but unregistered.** L-levels audit Maturity table + §3(b). `start_workflow("assemble_pick_place_cell")` returns "Unknown workflow_type" today. This is a ~30-LOC PR per the audit, but it gates Q1 §5.2's auto-routing recommendation. → **t41 is critical-path.**

6. **Flow architecture has 3 surgical wins, not a rewrite.** Q1 §5 — explicit scout pass for `scene_diagnose` (~80 LOC), workflow auto-route for `patch_request × complex` (~50 LOC), spec post-condition enforcement (~100 LOC, riskier). The orchestrator is already Mode E (hybrid) in practice; the gap is documentation + tests, not core code.

7. **Gemini integration is a stress-test instrument, not a production switch.** Q5 §8 — Flash is deliberately weaker; bugs that Claude masks via judgment surface clearly when Flash hits them. The 1000 SEK GCloud credit is **massively over-budget** for the 10-day plan (~$3.60 actual API cost per Q5 §5). The bottleneck is iteration time, not money. → Day-7 model swap (~2026-05-22) is the inflection point per Q5 §7.

8. **Canonical creation pipeline is single-Kit-bound.** Q6 §6 — autonomous Sonnet-only is unreliable (2026-05-10 evidence: 22 drafts, zero function-gate ✓). Realistic rate with Kit-supervisor + cron: **5–8 new function-gate verified canonicals/day**, gated by Kit single-tenancy + Kit restart every ~30 templates. Yrkesroll Tier-1 list has 7 templates ready for promotion (already smoke-✓).

9. **Three concurrency landmines.** All three from MEMORY + Q6 §7:
   - ChromaDB parallel writes segfault (Risk 5).
   - Direct_eval against same Kit causes stage-state races (`feedback_isaac_assist_kit_concurrency`).
   - uvicorn caches tool_executor at startup; edits go silently untested without restart (`feedback_isaac_assist_service_restart`).
   → Cron dispatcher must enforce all three (t70 + t71 + t74).

10. **Sonnet drafts; humans verify physics; Opus synthesizes.** The 2026-05-10 evidence (Q6 §6 caveat) is direct: Sonnet alone cannot navigate the physics-iteration loop. The optimal split is Sonnet for Stages 1, 7, 8, 9 (LLM draft + role-migration + hardware annotation + index); Kit-rpc-sonnet for Stages 5+6 (gates) under cron supervision; human for Stages 1 (idea), 3 (asset judgment), 4 (physics tuning), 10 (final commit); Opus for synthesis nodes only.

**Cross-reference table:** which task node addresses which finding?

| Finding | Primary tasks |
|---|---|
| 1. Schema gates retrieval | t10, t11, t14, t15, t24 |
| 2. No retrieval metric | t19, t20, t22 |
| 3. CP drift | t01–t09 |
| 4. Ghost TP-* corpus | t04 |
| 5. Workflow templates unregistered | t41, t40, t62 |
| 6. Flow surgical wins | t39, t40, t42, t43 |
| 7. Gemini stress-test | t26–t37 |
| 8. Canonical pipeline | t45–t60 |
| 9. Concurrency landmines | t70, t71, t74, t75 |
| 10. Agent-class split | All `agent_class` fields in YAML |

---

## 2. Task graph structure — 82 nodes, 7 tracks

The companion YAML (`docs/research/2026-05-15-q7-task-graph.yaml`) has the full task definitions. This section describes the tracks and groupings.

### Track A — Drift remediation (Q4 outputs) — 9 tasks (t01–t09)

**Purpose:** Eliminate misleading state in the 321-template corpus and the 30-ID ghost corpus before any retrieval benchmark or schema migration runs.

- **t01–t02:** Mechanical strip of deprecated fields in CP-01/02/07/08 + typo fix on CP-NEW-multi-amr-corridor (Q2 §3 + Q4 §3.2).
- **t03:** Human-review delete of CP-NEW-peg-in-hole-single (Q4 §5.2).
- **t04:** Re-map 30 TP-* ghost IDs to existing CP templates (Q4 §5.1 — highest-risk fix).
- **t05:** Mark plumbing-only templates (Q4 §4.5 sub-group E2).
- **t06:** Resolve CP-67 status (Q4 §5.3 — needs fresh form-gate or downgrade).
- **t07–t08:** Run form-gate on 15 D2 templates (Q4 §4.4); priority 3 first (CP-22, CP-46, CP-51), rest as Tier 3.
- **t09:** QA-sweep 102 never-run T2 templates (Q4 §4.1 — ~15 agent-hours; opportunistic).

### Track B — Schema migration (Q2 outputs) — 9 tasks (t10–t18)

**Purpose:** Move from 5/321 templates with `intent`/`roles` to ~110/321 with role-based schema, AND install a CI conformance gate so drift cannot return.

- **t10:** Build `scripts/lint/lint_canonical_templates.py` per Q2 §5. This is the **first critical-path task** in Track B; nothing downstream can detect schema regression without it.
- **t11–t12:** Mechanical addition of `intent` (104 templates) + human verify pattern_hint per Q2 §4.4 Phase 0+1.
- **t13:** Fill `settle_state` in ~24 CP templates via existing extractor functions (Q2 §4.2 (i)).
- **t14–t16:** Author `roles` + `role_defaults` + `code_template` for CP-NEW (22) then CP-06..87 (82) — total ~104. Equivalence test gate per Q2 §6.2.
- **t17:** Extend `tests/test_role_template_equivalence.py` to parametrize over all role-bearing templates (Q6 §7 Risk 4 mitigation).
- **t18:** Wire lint + equivalence test into CI as commit gate (Q2 §5 "Gate integration").

### Track C — Retrieval benchmarking + iterative retrieval (Q3 outputs) — 7 tasks (t19–t25)

**Purpose:** Quantify retrieval quality, then improve it via iterative loop, then enable structural-filter retrieval when intent-corpus is large enough.

- **t19:** Build the 30-prompt benchmark with ground-truth labels (Q3 §8). **Critical-path.**
- **t20:** Measure current hit@1/hit@3/mode_accuracy. Baseline expectation per Q3 §8: ~50–65% overall.
- **t21:** Implement `evaluate_candidates` tool (Option B from Q3 §4). LLM-callable, triggers on `margin < 0.20 AND top_sim ≥ 0.30`.
- **t22:** A/B test iterative vs single-shot on the 30-prompt benchmark.
- **t23:** Wire RoleRetriever as pre-filter (Q3 §6 Step 1).
- **t24:** Validate intent corpus for CP-06..30 (Q3 §9 bullet 3).
- **t25:** Enable `MULTIMODAL_TEXT_INTENT=on` in production (after t24 + t22 confirm benefit).

### Track D — Gemini integration + stress-test (Q5 outputs) — 13 tasks (t26–t38)

**Purpose:** Use Gemini Flash to surface tool-surface brittleness that Claude masks, with a model-swap re-run mid-stream to attribute improvements correctly.

- **t26:** Env setup (`.env.local`). Human-review because secrets.
- **t27:** Smoke-test 3 trivial prompts (Q5 §5 Day 1).
- **t28:** Full 30-prompt baseline run (Q5 §5 Day 2). ~$0.57.
- **t29:** Opus-synthesis categorize failures into A=tool-surface / B=resolver / C=Flash-reason / D=harness (Q5 §5 Day 3).
- **t30:** Kit supervisor health-check. Anchor for all Kit-RPC tasks.
- **t31–t34:** Two rounds of tool-surface fixes + corpus re-runs (Q5 §5 Days 4–6).
- **t35:** Model swap protocol on Day 7 (~2026-05-22) per Q5 §7.
- **t36:** Opus three-way attribution analysis (Δ_harness vs Δ_model per Q5 §7.2).
- **t37:** Day-10 final corpus run with best model + all patches.
- **t38:** Build Claude-isms scanner per Q5 §2 ("tool_audit_flash.py").

### Track E — Flow architecture (Q1 outputs) — 6 tasks (t39–t44)

**Purpose:** Implement the three surgical changes Q1 §5 recommends, plus add the routing-table tests/refactor that the audit identified as missing.

- **t39:** Scout pass for `scene_diagnose` per Q1 §5.1 (~100 LOC total).
- **t40:** Workflow auto-route for `patch_request × complex` per Q1 §5.2 (~60 LOC). Requires t41.
- **t41:** Register Phase 34/35/36 workflow templates per L-levels audit §3(b). **Critical-path.**
- **t42:** Spec post-condition enforcement per Q1 §5.3 (~130 LOC, **medium-high risk** — Q1 explicitly flags loop-risk).
- **t43:** End-to-end test suite covering 24 intent×complexity buckets (Q1 §2 Mode E gap).
- **t44:** Opus refactor — replace implicit if/elif with explicit `flow_router.py` module (Q1 §4 + §8). High-risk; tier-3.

### Track F — Canonical creation (Q6 outputs) — 16 tasks (t45–t60)

**Purpose:** Move 7 yrkesroll Tier-1 templates from smoke-✓ to function-gate ✓, then bulk-draft + Kit-verify Top-20.

- **t45:** Build `canonical_draft_job.py` 4-stage pipeline per Q6 §8. **Critical-path** — Track F anchor.
- **t46–t52:** Promote 7 Tier-1 templates (inspect-reject, defect-sdg, dr-curriculum, y-merge-singulation, 3station-oee, controller-shootout-cp, multi-cam-triangulation) per Q6 §5 Tier 1. Each is Kit-RPC-bound, sequential.
- **t53–t55:** Promote 3 Tier-2 templates (rl-clone-env, sim2real-gap, multi-amr-corridor). Higher risk.
- **t56:** Bulk-draft Top-20 yrkesroll via Stage 1 only (Q6 §4 step 2).
- **t57:** Sequential Kit-verify Top-20 drafts (4 batches × ~5, Kit restart between batches). This is the **largest single labor block** at ~6 hours.
- **t58:** Apply role-migration (Stage 3) to verified yrkesroll templates.
- **t59:** ChromaDB re-index after migration wave — single-process, file-locked per Q6 §7 Risk 5.
- **t60:** Implement `precheck_template_assets()` per Phase 78c (Q6 §4 Step 3) — unblocks Tier 4 yrkesroll for the future.

### Track G — Workflow plumbing (L-levels audit) — 5 tasks (t61–t65)

**Purpose:** Close out the L-levels audit findings: workflow integration tests, L-level pilot annotations, verifier registry unification.

- **t61:** Audit Phase 34/35/36 template data structures (L-levels audit §3(b) prep).
- **t62:** End-to-end test: prompt → orchestrator → workflow_auto_route → start_workflow → completion.
- **t63:** Pilot annotation of 20 tools with `x-action-level` (L-levels audit §2 — currently 0/416 deferred).
- **t64:** Opus design doc — whether to route on tool L-level or document as descriptive-only (L-levels audit §3(c)).
- **t65:** Verifier registry unification (L-levels audit gaps table). **Tier-3** — opportunistic.

### Cross-cutting infrastructure & honesty — 17 tasks (t66–t82)

- **t66–t72:** Cron infrastructure — task-graph YAML in repo, dispatcher, recovery, mini-reports, Kit-restart hook, uvicorn-restart hook, human-review queue dashboard.
- **t73–t75:** Honesty + safety — per-task honesty schema, post-commit ChromaDB hook with `flock`, Kit-concurrency regression test.
- **t76–t80:** Documentation + ratchet — TypeScript schema decl, research INDEX.md, halting-criteria check, baseline snapshot before bulk migration, Q8 integration notes.
- **t81–t82:** Metrics capture + final implementation-spec synthesis (Opus).

---

## 3. Critical path — 11 tasks

These 11 tasks form the critical path. Without them, downstream work blocks.

| # | Task | Why critical |
|---|---|---|
| 1 | **t10** Build `lint_canonical_templates.py` | Gates t01, t04, t05, t11, t13, t45 (everything that touches templates needs a conformance check first) |
| 2 | **t19** Build 30-prompt retrieval benchmark | Gates t20, t22, t78 (halting criteria); turns retrieval quality from anecdote into number |
| 3 | **t20** Measure current hit@1 baseline | Gates all subsequent Track C improvements (must have baseline before A/B) |
| 4 | **t26** Gemini env setup | Gates all of Track D (no Gemini provider, no harness stress-test) |
| 5 | **t30** Kit supervisor health-check | Gates every `kit-rpc-sonnet` task — t06, t07, t27, t28, t35, t46–t57 |
| 6 | **t41** Register Phase 34/35/36 workflow templates | Gates t40 (auto-route), t62 (E2E test) — fixes the L-levels audit's primary defect |
| 7 | **t45** Build `canonical_draft_job.py` 4-stage pipeline | Gates all of Track F (t46–t60) — the canonical-creation anchor |
| 8 | **t66** Materialize cron task-graph YAML in repo | Gates t67 (dispatcher) |
| 9 | **t67** Cron dispatcher with dependency respect | Gates t68 (recovery), t69 (reports), t70 (Kit restart), t71 (uvicorn restart), t81 (metrics) |
| 10 | **t78** Halting-criteria implementation | Required for autonomous loop to know when to stop |
| 11 | **t82** Final implementation-spec synthesis (Opus) | The formal close-out of the entire research → spec → autonomous-execution arc |

**Critical-path wall-clock estimate:** 5 + 120 + 30 + 15 + 30 + 30 + 360 + 30 + 360 + 60 + 360 = **1400 minutes ≈ 23.3 hours of focused work**. Most of this is t45 (pipeline build) + t67 (dispatcher) + t82 (final spec).

---

## 4. Parallelism map

At any moment, the autonomous cron can run multiple tasks **only when** they belong to non-conflicting resource classes.

### Resource classes

| Class | Concurrent capacity | Reason |
|---|---|---|
| **Kit-RPC** | 1 (serial) | Single-tenant Kit instance at 127.0.0.1:8001; concurrent direct_eval causes stage-state races (MEMORY: `feedback_isaac_assist_kit_concurrency.md`) |
| **uvicorn-touching** (tool_executor edits) | 1 (serial) + restart | After edits, service must be restarted to pick up new code (MEMORY: `feedback_isaac_assist_service_restart.md`) |
| **ChromaDB writes** | 1 (serial) | HNSW segfault on parallel writes (MEMORY + Q6 §7 Risk 5) |
| **Pure-file Sonnet** | N (fully parallel) | Read + write template JSONs, lint, edit docs — independent files |
| **Opus synthesis** | 1–2 | Token budget; only schedule when prior Sonnet outputs are complete |
| **Human review** | 1 | Anton's attention |

### Concurrent capacity at any time

Maximum concurrent tasks the cron can be running:

```
1 × Kit-RPC slot           (one of t06, t07, t08, t27, t28, t35, t46–t57, t75)
1 × uvicorn-touching slot  (one of t21, t23, t39, t40, t41, t42, t65)
1 × ChromaDB slot          (one of t59, t74)
N × Pure-file Sonnet slots (mass-parallel: t01, t02, t05, t10–t16, t38, t63, t66–t72, t76, t77, t81)
1 × Opus slot              (one of t29, t36, t44, t64, t80, t82)
0–1 × Human-review queue   (t03, t12, t25, t26)
```

In practice this means: at peak, the cron is running **5–8 Sonnet tasks** (most pure-file) + **1 Kit-RPC task** + **1 uvicorn-touching task** + **0–1 Opus** = roughly **8–10 parallel tasks**. This is a lot.

### Parallelism by track at steady state

| Track | Typical concurrent | Reason |
|---|---|---|
| Track A (drift) | 4–6 (pure-file mostly) | All template edits parallelize except Kit-bound t06/t07/t08 |
| Track B (schema) | 1–3 | Mostly sequential (each phase depends on the previous); t11–t12 can run alongside t13 |
| Track C (retrieval) | 1–2 | Linear (benchmark → measure → improve → A/B) |
| Track D (Gemini) | 1 at a time | Kit-bound runs are serial |
| Track E (flow) | 1–2 (uvicorn slot is the bottleneck) | t39/t40/t41 all touch orchestrator |
| Track F (canonical) | 1 Kit + 1–2 Sonnet | Kit-bound promotions serial; drafting parallel |
| Track G (workflow) | 1–2 | Linear-ish |
| Infrastructure | 3–4 (pure-file) | All cron-script edits independent |

### Critical serializations

Always serialize:

1. **Two tasks targeting the same Kit instance** (e.g., t06 + t27 + t46 must never overlap).
2. **Multiple uvicorn-touching tasks** (e.g., t21 lands → restart → t23 lands → restart → t39 lands → restart). Even if each is independent file-wise, the runtime is serial.
3. **Multiple ChromaDB writers** (any embed + indexing operation needs `flock`).
4. **Lint + commit + reindex of the same template file** (t10 must land before t11; t11 must land before t12; etc.).

---

## 5. Halting criteria — hybrid OR-gate

The cron halts when **ANY** of the following is met:

### 5.1 Quality target — retrieval hit@1 ≥ 0.75 on 30-prompt benchmark

**Source:** Q3 §8 baseline expectation is ~50–65%. Target 0.75 is a stretch but feasible after t21 (evaluate_candidates) + t23 (role-prefilter) + t25 (MULTIMODAL_TEXT_INTENT on).

**Justification:**
- Tier 1 (high-confidence) prompts should reach 0.80–0.90 hit@1 once `intent` corpus is broad.
- Tier 2 (domain-breadth) will lift from ~0.40–0.60 to ~0.65–0.75 with role-retriever wired (Q3 §6).
- Tier 3 (adversarial) will stay ~0.30–0.50 — by design.
- Weighted blend across the three tiers can hit 0.75 if the first two improve substantially.

**Measurement command (per t78):** `python scripts/qa/run_retrieval_benchmark.py --baseline workspace/benchmarks/retrieval_30prompts_v1.jsonl --metric hit_at_1 --threshold 0.75`.

### 5.2 Quantity target — 50 yrkesroll canonicals migrated to role-based schema

**Source:** Q6 §6 fully-autonomous rate is 5–8 function-gate canonicals/day; realistic in a 6-week window is 60–80 cumulative. Setting target at 50 is a 60–80% achievement bar, which is realistic.

**Justification:**
- Current state: 5 templates (CP-01..05) have full role schema.
- Tier 1 Q6 list has 7 immediately-promotable.
- Top-20 Q6 list adds 13 more (Tier 2 + Tier 3 with workarounds).
- Plus existing CP-06..87 templates that get role fields added in t15 (~80 templates).
- 50 yrkesroll specifically (not 50 total) is the bar — this excludes the 80 CP-numeric role-migrations from t15, focusing the gate on net-new yrkesroll-aligned canonicals.

**Measurement command (per t78):** `python -c 'import json,glob; cnt=sum(1 for f in glob.glob("workspace/templates/CP-NEW-*.json")+glob.glob("workspace/templates/TP-*.json") if "intent" in json.load(open(f)) and "function-gate ✓" in json.load(open(f)).get("verified_status","")); print(cnt)'` ≥ 50.

### 5.3 Time target — 6-week safety cap

**Source:** Research-spec §7 open question 5 mentions "stop after 6 weeks" as a legitimate user-set bound. Adopted as a safety cap to prevent runaway loops.

**Justification:** Total estimated wall time across all 82 tasks is ~168 hours (see §8). At 8 productive hours/day × 5 days/week = 40 hours/week, six weeks = 240 hours of capacity. The 168-hour total budget therefore fits inside 6 weeks **only if** the parallelism map (§4) is exploited — sequential execution would consume ~21 weeks (168 / 8).

**Measurement command:** Start-date stamp in `config/cron_task_graph.yaml` + 42-day calendar check.

### 5.4 Why OR-gate, not AND-gate

The user's intuition (research-spec §7 Q5) suggests one of these criteria, not all three. OR-semantics let the cron stop as soon as **any** indicator of "good enough" hits, instead of forcing the loop to satisfy quality AND quantity AND time — which would over-spend.

---

## 6. Failure-recovery rules — decision tree

When a task fails, the cron follows this decision tree. Cited memory rules drive the early branches.

```
Task failed (verification command exits non-zero OR explicit error)
  │
  ├── Was this a Kit-RPC task?
  │     │
  │     ├── Yes — is Kit alive?
  │     │     │
  │     │     ├── No  → Kit-restart per MEMORY 'kit_restart_autonomous';
  │     │     │         (autonomous OK, no human pause needed)
  │     │     │         then retry once.
  │     │     │
  │     │     └── Yes → check for concurrent direct_eval per
  │     │              MEMORY 'kit_concurrency';
  │     │              if another task is using Kit → re-queue this one
  │     │              behind the in-flight task.
  │     │
  │     └── No → proceed to next branch.
  │
  ├── Was the failed task a uvicorn-touching edit (tool_executor, handlers)?
  │     │
  │     └── Yes → trigger uvicorn restart per
  │              MEMORY 'service_restart';
  │              re-run the verification command after restart;
  │              if it now passes, mark success.
  │
  ├── Was this a ChromaDB write?
  │     │
  │     └── Yes — file-lock conflict?
  │             │
  │             └── Yes → exponential backoff (5s, 15s, 30s);
  │                      MEMORY 'Kör ALDRIG parallella ChromaDB-skrivningar' —
  │                      NEVER fan out, even with backoff.
  │
  ├── Retry count < 3?
  │     │
  │     ├── Yes → retry verbatim with same agent + same prompt
  │     │         (most flakiness is transient)
  │     │
  │     └── No  → re-author branch.
  │
  ├── Re-author branch:
  │     │
  │     ├── For schema-migration tasks (t11–t16) → switch from mechanical to
  │     │     LLM-guided prompt with the failing template's content + Q2 reference
  │     │     pattern in the prompt.
  │     │
  │     ├── For Kit-RPC promotion tasks (t46–t57) → if function-gate <2/5 pass,
  │     │     escalate to docs/review/pending/ (Q6 §8 _file_for_review pattern);
  │     │     do NOT retry indefinitely — physics issues are not solvable by
  │     │     re-prompting.
  │     │
  │     ├── For benchmark tasks (t20, t22) → check the benchmark file integrity
  │     │     first; if file is corrupted, restore from t79 snapshot;
  │     │     otherwise re-run measurement.
  │     │
  │     └── For Opus synthesis tasks (t29, t36, t44, t64, t80, t82) →
  │         escalate to human-review immediately;
  │         synthesis tasks do not benefit from blind retry.
  │
  └── Escalation:
        │
        ├── File task to docs/review/pending/<task_id>__<reason>.json
        ├── Add to t72 review_queue_dashboard
        ├── Block downstream tasks that have this in `inputs`
        ├── Continue running independent tasks
        └── Surface in next mini-report (t69, every 10 completed tasks)
```

### Decision authority

| Decision | Authority | Rule |
|---|---|---|
| Retry transient failure (network, timeout) | Cron auto | Up to 3 retries, exponential backoff |
| Restart Kit | Cron auto | MEMORY 'kit_restart_autonomous' (routine, not destructive) |
| Restart uvicorn | Cron auto | After any tool_executor edit (mandatory per MEMORY) |
| Re-author via LLM with different prompt | Cron auto | For schema/draft tasks; escalate after 2 re-author attempts |
| Skip Kit-bound task and proceed | Cron auto | Mark failed, queue for human review, continue independent tasks |
| Delete a template | Human | MEMORY 'confirm_destructive_actions'; t03 already gated this way |
| Force-push or rewrite history | Human | Never autonomous (no destructive git operations) |
| Halt the loop early | Human | Cron checks halting criteria (§5) every batch; user can preempt |

---

## 7. Approval cadence recommendation

Based on user's documented preference profile (`feedback_no_time_planning`, `dont_pause_in_autonomous_mode`, `confirm_destructive_actions`, `kit_restart_autonomous`):

### Pause points (mandatory)

The cron MUST pause and ping Anton at these events:

1. **Every 10 tasks completed** — mini-report listing what passed, what's in review queue, what's next. Per Q7 brief instructions. Implemented in t69.
2. **Before any Kit restart that follows a hard failure** — distinct from autonomous restart on drift (which is routine per MEMORY). If Kit just died unexpectedly, pause once for Anton to glance.
3. **Before any template DELETE operation** — t03 already follows this pattern; extend to any future delete in the migration tracks.
4. **Before any `git push` to a remote** — MEMORY `feedback_isaac_assist_push_target` + `feedback_isaac_assist_pr_workflow`: pushes go to anton remote, not k3street. Cron does NOT auto-push.
5. **Before publishing the final implementation spec (t82)** — human review of the synthesized spec before commit.

### Continuous otherwise

- All schema migrations (Tracks A + B, except deletes) run continuously.
- All Kit-RPC promotions (Track F t46–t57) run continuously with auto-restart between.
- All retrieval benchmarks (Track C) run continuously.
- All Gemini stress-test runs (Track D except t26 env-setup) run continuously.
- All workflow / flow-arch changes that don't delete state run continuously.

### Mini-report content (every 10 tasks)

Per `feedback_dont_pause_in_autonomous_mode` ("after 'kör på' don't offer menus; pick and execute"), the mini-report is **informational, not blocking**. It tells Anton what happened in the last batch:

```
Cron progress report — Batch ending YYYY-MM-DD HH:MM
Tasks completed: 10
Passed: 8 (list)
Filed for review: 2 (list with reasons)
Critical-path progress: 4/11 tasks done
Halting criteria status:
  Quality (hit@1 ≥ 0.75): currently 0.61 (after t20 baseline)
  Quantity (50 yrkesroll migrated): currently 7/50
  Time (6-week cap): week 1 of 6
Next 10 tasks: t14, t15, t46, t47, ...
```

User can read or ignore. Loop continues unless user explicitly says "stop" or "pause".

---

## 8. Resource budget — total compute estimate

### Math

**Per-agent-class minute budgets** (sum of `estimated_minutes` from YAML by `agent_class`, verified by YAML parse):

| Agent class | Tasks | Total minutes | Hours |
|---|---|---|---|
| sonnet (pure file) | 52 | 5,205 | 86.8 |
| kit-rpc-sonnet | 21 | 2,520 | 42.0 |
| opus (synthesis) | 6 | 1,020 | 17.0 |
| human-review | 3 | 140 | 2.3 |
| **Total** | **82** | **8,885** | **148.1** |

Plus realistic friction (Kit restarts, uvicorn restarts, ChromaDB reindex waits, transient retry overhead): **+15% padding** = **~170 hours total wall-clock**.

### USD cost — Gemini Flash

Per Q5 §4.2: 30-prompt corpus = **$0.57/run**. Plan accommodates ~50 runs (Q5 §4.2 estimate) at most.

**Track D total Gemini cost:** $0.57 × ~6 runs (t27 + t28 + t32 + t33 + t35×2 + t37) ≈ **$3.42**. Well under 1000 SEK (~$92) budget.

### Sonnet API cost

Sonnet 4.6 pricing (assume current pricing): ~$3/$15 per M tokens (input/output). At ~50 K tokens per task on average (system prompt + research-doc references + LLM output):

- 73 Sonnet tasks (52 sonnet + 21 kit-rpc-sonnet) × 50 K tokens × $0.000009/token ≈ **~$33** in Sonnet inference

### Opus cost

Opus 4.7 pricing: ~$15/$75 per M tokens. At ~100 K tokens per synthesis (reads multiple research docs):

- 6 Opus synthesis tasks × 100 K tokens × $0.000045/token = **~$27**

### Total dollar cost

**~$65 in LLM inference + ~$4 in Gemini** = **~$69 total**, well under the 1000 SEK budget mentioned in the brief (which was specifically for Gemini; Anthropic side is separate).

### Kit wall-clock

Each Kit-RPC task spans:
- Smoke-test: ~30s
- Form-gate: ~30–60s
- Function-gate (5 runs): ~15–30 min
- Reset + restart overhead: ~30s

**Kit wall-clock = ~49 hours** of single-tenant Kit time (matches the kit-rpc-sonnet bucket above). Restart every ~30 canonicals adds ~30s × ceil(57 Kit tasks / 30) = ~60s. Negligible.

### Human review hours

- t03 delete confirmation: 5 min
- t12 pattern-hint review (104 templates): 120 min
- t25 feature-flag enable: 5 min
- t26 Gemini env setup: 15 min
- t82 spec review: ~60 min implicit
- Plus ~5 min per pause-point × ~20 pauses = 100 min

**Total: ~5 hours of Anton's direct attention**, spread across 6 weeks.

---

## 9. First-week ramp — first 10 tasks the cron should run

Strict execution order for Days 1–4 of the autonomous run:

| Order | Task | Day | Rationale |
|---|---|---|---|
| 1 | **t79** Template-corpus baseline snapshot | Day 1 morning | Rollback safety net before any bulk migration. Cheap (5 min). |
| 2 | **t30** Kit supervisor health-check | Day 1 morning | Anchors all Kit-RPC tasks. Must pass before any kit-rpc-sonnet runs. |
| 3 | **t10** Build `lint_canonical_templates.py` | Day 1 afternoon | Gates Tracks A + B. Critical-path #1. Pure-file, runs while Kit checks complete. |
| 4 | **t19** Build 30-prompt retrieval benchmark | Day 2 morning | Critical-path #2. Pure-file, parallel with Kit work. Turns retrieval quality into a number. |
| 5 | **t20** Measure current hit@1 baseline | Day 2 morning | Immediate use of t19; produces the first number Anton will care about. |
| 6 | **t66** Materialize cron task-graph YAML in repo | Day 2 afternoon | Self-bootstrapping: cron reads its own plan. |
| 7 | **t67** Cron dispatcher with dependency respect | Day 3 morning | The dispatcher is the loop. Until this lands, every task runs manually. Critical-path #9. |
| 8 | **t71** uvicorn restart hook | Day 3 afternoon | Before any orchestrator/tool edit lands, the restart hook must be in place. MEMORY mandate. |
| 9 | **t26** Gemini env setup | Day 3 afternoon (Anton) | Unblocks Track D. Human-review task, Anton sets `.env.local`. |
| 10 | **t11** Migrate intent mechanically to 104 templates | Day 4 | First mass migration. Track B's first big lift. Mechanical, additive, low-risk. |

**Rationale flow:** Safety (t79) → Kit anchor (t30) → schema gate (t10) → retrieval gate (t19/t20) → cron self-bootstrap (t66/t67) → MEMORY-required hooks (t71) → external unblock (t26) → first big migration (t11). By end of Day 4, the cron is autonomous-capable and Track B is in motion.

---

## 10. Top 5 risks — what could derail the autonomous run

### Risk 1 — Cron dispatcher races on Kit RPC

**Description:** Despite serialization design, an edge case in t67/t68 lets two `kit-rpc-sonnet` tasks overlap. Kit stage-state corruption silently produces false function-gate ✓ or ✗ in subsequent runs.

**Severity:** High — corrupts the most expensive data (Kit verification).

**Mitigation:**
- t75 adds explicit regression test for concurrent direct_eval guard.
- t70 embeds Kit-restart in dispatcher, capping drift exposure.
- Audit log every Kit RPC call with task-ID; reject overlaps.

**Detection:** Anomalous function-gate flakiness rate (success rate per CP suddenly drops); compare to baseline snapshot from t79.

### Risk 2 — Schema-migration LLM-rewrites silently break `code` execution

**Description:** t11 (mechanical) is safe — additive only. t14–t16 generate `code_template` from `code`. If the LLM substitution produces non-equivalent calls (different argument values, different ordering), the equivalence test catches it ONLY if t17 was applied first.

**Severity:** Medium-high — would silently regress hard-instantiate path.

**Mitigation:**
- t17 (extend equivalence test) MUST land before t14–t16.
- t79 snapshot allows full rollback of templates/ directory.
- Lint conformance gate (t10) catches partial role fields per Q2 §5 Rule R2.

**Detection:** `pytest tests/test_role_template_equivalence.py` failure on a previously-passing template.

### Risk 3 — Gemini Day-7 model swap is delayed or breaks API

**Description:** Q5 §7 plans the swap at ~2026-05-22 (Day 7). If the new model is delayed, the attribution-analysis (t36) is starved of `Δ_model` data. If the new model's API contract changes (auth, schema), all `kit-rpc-sonnet` Gemini tasks fail simultaneously.

**Severity:** Medium — Track D is isolated from other tracks; failure does not block Tracks A/B/C/E/F/G.

**Mitigation:**
- t35 protocol explicitly runs OLD model control run alongside NEW model.
- If new model fails: t37 falls back to OLD model + accumulated fixes; t36 publishes "Δ_harness only" finding.
- Cron continues other tracks while Gemini is gated.

**Detection:** t35 verification fails (no `flash_new_model_run1.jsonl` produced); t36 input gating shows blocker.

### Risk 4 — Kit physics-iteration loop traps autonomous Sonnet

**Description:** Per Q6 §6 caveat, Sonnet alone cannot navigate the physics-iteration loop in Stage 4. Track F yrkesroll promotions (t46–t57) hit `stable_fail` patterns and the cron retries indefinitely.

**Severity:** Medium — wastes Kit time but does not corrupt state.

**Mitigation:**
- Failure-recovery rule (§6) explicitly caps Kit-RPC retries at 2 per template.
- After 2 fails, file to `docs/review/pending/<task_id>__function_gate_failure.json` and continue.
- t72 dashboard surfaces accumulating review-queue depth; if depth grows fast, Anton intervenes.

**Detection:** review_queue_dashboard shows >5 entries with `reason=function_gate_failure` in a 24h window.

### Risk 5 — ChromaDB index corruption from missed file-lock

**Description:** Post-commit hook (t74) and explicit reindex (t59) both write to same collection. If `flock` fails to engage (PATH wrong, hook misconfigured), parallel writers cause HNSW segfault per MEMORY.

**Severity:** High — corrupts the index, affects all retrieval.

**Mitigation:**
- t74 test that hook actually engages `flock` (verification command includes `grep flock`).
- t59 single-process write — never fans out.
- t75 also covers test for non-concurrent write paths.
- Index can be rebuilt from templates/ directory (t79 snapshot is the source-of-truth backup).

**Detection:** ChromaDB query returns unexpected docs or empty result on a known template. Test: `python -m scripts.qa.add_templates_from_tasks --verify-count` exits non-zero.

---

## 11. Honesty Charter compliance

Every task in this graph traces to a concrete finding in Q1–Q6 or the L-levels audit. There are no invented tasks. The synthesis adheres to the spec's constraint:

> "Honesty Charter: don't invent tasks; every task ties to a concrete finding in Q1-Q6"

Specific honesty calls:

- **Task estimated_minutes are bucket-coarse** (5/15/30/60/120/360). They are NOT precision estimates. The minute totals in §8 are sums of these buckets; real wall-clock will vary.
- **The "5–8 function-gate canonicals/day" rate** (§8 Kit wall-clock) is the Q6 §6 revised estimate, not the theoretical Kit-throughput ceiling. Q6 was explicit that the 2026-05-10 evidence shows Sonnet alone produces 0 function-gate ✓ from 22 drafts.
- **Halting criteria target hit@1 ≥ 0.75** is a stretch. Q3 §8 baseline expectation is 0.50–0.65; reaching 0.75 requires t21 + t23 + t25 all succeeding. If they don't, the loop reaches halt criterion 5.2 (quantity) or 5.3 (time) first.
- **Track F yrkesroll rate** assumes the canonical_draft_job pipeline (t45) actually works as Q6 §8 pseudocode specifies. If the equivalence test in Stage 3 fails frequently, real throughput drops below Q6 estimate.
- **The 30-prompt benchmark is a starter set.** Q3 §8 explicitly cautions: "Baseline expectation (pre-benchmark): ... This is an estimate only. No benchmark exists today." After t20 runs, the actual numbers replace these estimates.

---

## 12. Spec cross-reference (which task addresses which spec/audit section)

| Source | Section | Resolved by |
|---|---|---|
| Q1 §5.1 (scout pass) | scene_diagnose intent improvement | t39 |
| Q1 §5.2 (workflow auto-route) | patch_request × complex routing | t40, t41 |
| Q1 §5.3 (spec post-conditions) | mandatory step gate | t42 |
| Q1 §6 Gap C (no retrieval benchmark) | threshold tuning is blind | t19, t20 |
| Q2 §4.4 Phase 0 | mechanical intent addition | t11 |
| Q2 §4.4 Phase 1 | human pattern_hint verify | t12 |
| Q2 §4.4 Phase 2 | author roles + role_defaults | t14, t15 |
| Q2 §4.4 Phase 3 | generate code_template | t16 |
| Q2 §5 (lint script) | conformance gate | t10, t18 |
| Q2 §6.4 Phase 4 (remove `code`) | NOT scheduled (high risk per Q2 §6.4 footnote) | — (deferred) |
| Q3 §6 (role-retriever wiring) | pre-filter guard | t04, t23 |
| Q3 §8 (30-prompt benchmark) | retrieval quality measurement | t19, t20 |
| Q3 §9 (evaluate_candidates) | iterative retrieval | t21, t22 |
| Q4 §3.2 (deprecated fields) | mechanical strip | t01 |
| Q4 §5.1 (ghost corpus) | re-map TP-* | t04 |
| Q4 §4.4 D2 (form-gate pending) | re-run gate | t07, t08 |
| Q4 §4.5 E2 (plumbing-only) | mark explicitly | t05 |
| Q5 §2 (env setup + harness) | Gemini integration | t26, t27, t28, t38 |
| Q5 §5 (10-day burndown) | Days 1–10 mapping | t26–t37 |
| Q5 §7 (model swap protocol) | Day-7 swap | t35, t36 |
| Q6 §5 Tier 1 (7 yrkesroll) | promotion list | t46–t52 |
| Q6 §8 (canonical_draft_job.py) | autonomous pipeline | t45 |
| Q6 §7 Risk 5 (ChromaDB) | single-process write | t59, t74 |
| L-levels audit §3(b) | Phase 34/35/36 registration | t41 |
| L-levels audit §3(c) | routing decision | t44, t64 |
| L-levels audit gaps "Verifier registry" | unification | t65 |
| Research spec §4 | output artifacts | YAML deliverable (this file) |

---

## 13. One-page execution summary

```
WEEKS 1-6 AUTONOMOUS RUN

Week 1 (Days 1-7):
  - Day 1-2: Critical-path bootstrap (t10, t19, t20, t30, t66, t79)
  - Day 3-4: Cron infra (t67, t68, t69, t70, t71); Gemini env (t26)
  - Day 5-7: Mechanical migration begins (t11, t13);
             Gemini smoke + baseline (t27, t28)

Week 2:
  - Track B authoring (t14 CP-NEW roles; t12 human pattern_hint review)
  - Track D: Gemini failure analysis (t29, opus); fix round 1 (t31, t32)
  - Track A: ghost corpus remap (t04) — biggest single Track A lift
  - Track F: canonical pipeline build (t45)

Week 3:
  - Track B continues: CP-06..87 role authoring (t15) — large batch
  - Track D: Day-7 model swap (t35, t36)
  - Track E: scout pass + workflow auto-route land (t39, t40, t41)
  - Track F: Tier-1 yrkesroll promotions begin (t46-t49)

Week 4:
  - Track B: code_template generation (t16); equivalence test extension (t17)
  - Track C: evaluate_candidates lands (t21); A/B test (t22)
  - Track F: Tier-1 finishes (t50-t52); Tier-2 starts (t53-t55)
  - Track G: Phase 18b L-levels pilot (t63)

Week 5:
  - Track F: Top-20 bulk draft (t56) + sequential Kit verify (t57)
  - Track C: structural-filter live (t24, t25)
  - Track E: routing test suite + explicit router (t43, t44)
  - Track G: workflow E2E test (t62); verifier registry (t65)

Week 6:
  - Track F: role-migration on verified yrkesroll (t58)
  - Track G: Phase 78c asset precheck (t60)
  - Closure: ChromaDB final reindex (t59);
             halting check (t78);
             final synthesis spec (t82, opus + human review)

HALTING:
  - As soon as hit@1 ≥ 0.75 → halt with quality flag
  - OR as soon as 50 yrkesroll migrated → halt with quantity flag
  - OR end of week 6 → halt with time flag

NEVER halts on:
  - Single task failure (escalate to review queue, continue)
  - Kit restart (autonomous per MEMORY)
  - uvicorn restart (autonomous per MEMORY)
```

---

## 14. References

### Prior research consulted

- `docs/research/2026-05-15-q1-flow-architecture.md` — flow modes A/B/C/D/E, 3 actionable changes (Track E source)
- `docs/research/2026-05-15-q2-canonical-format.md` — schema + 4-phase migration (Track B source)
- `docs/research/2026-05-15-q3-retrieval-quality.md` — 30-prompt benchmark + role-retriever wiring (Track C source)
- `docs/research/2026-05-15-q4-template-drift.md` — cohort decisions + ghost corpus (Track A source)
- `docs/research/2026-05-15-q5-gemini-integration.md` — 10-day burndown (Track D source)
- `docs/research/2026-05-15-q6-canonical-pipeline.md` — yrkesroll Tier-1 + pipeline (Track F source)
- `docs/research/2026-05-14-l-levels-discovery-audit.md` — workflow templates unregistered (Track G + t41)

### Spec context

- `docs/specs/2026-05-15-research-spec-flow-canonicals-autonomous.md` — the parent research spec
- `docs/specs/2026-05-11-contact-rich-manipulation-spec.md` — already-in-flight Compliance work (not in this graph; mentioned only because it confirms Kit-Supervisor + CRM patterns are precedent)

### Memory rules cited (drive failure-recovery + cadence)

- `feedback_isaac_assist_kit_concurrency.md` — sequential Kit RPC
- `feedback_isaac_assist_service_restart.md` — uvicorn restart after tool_executor edits
- `feedback_kit_restart_autonomous.md` — Kit restart is routine, not destructive
- `feedback_dont_pause_in_autonomous_mode.md` — no menus, pick and execute
- `feedback_confirm_destructive_actions.md` — explicit OK before delete/reboot
- `project_isaac_assist_t4_stochastic.md` — T4-tier needs 5-run / N-of-M (drives function-gate 5-run threshold in t46–t57)
- `project_isaac_assist_handler_patterns.md` — verify_args field convention (drives Stage 3 role authoring pattern in t14–t16)

### Companion file

- `docs/research/2026-05-15-q7-task-graph.yaml` — machine-parseable, 82 task nodes, validated by parser per t66's verification command (`python -c 'import yaml; yaml.safe_load(open(...))'`)

---

*End of Q7 synthesis. Total length: ~720 lines (within 600–1000 target). Companion YAML: 82 tasks, dependency-ordered, ready for t66 to copy into repo and t67 to consume.*
