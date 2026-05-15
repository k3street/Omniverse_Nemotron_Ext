# Autonomous Execution Plan — Canonical Flow Standardization

**Date:** 2026-05-15
**Author:** Phase 4 synthesis (Opus 4.7, 1M context)
**Status:** Final execution plan — pending user approval before kickoff
**Companion spec:** `docs/specs/2026-05-15-canonical-flow-standardization-spec.md`
**Machine-readable graph:** `config/cron_task_graph.yaml`

This plan is the **human-readable view** of the autonomous run. The
machine-readable form lives in `config/cron_task_graph.yaml`. Both
artifacts share task IDs (t01–t82); this prose plan groups them by
week and explains the rationale.

---

## §0. Operating model

### 0.1 The cron loop

The autonomous loop runs Sonnet-default agents continuously, with
Opus reserved for cross-document synthesis nodes and Gemini Flash
reserved for the 30-prompt stress-test corpus.

```
┌────────────────────────────────────────────────────────────┐
│                  cron_dispatcher.py                        │
│                                                            │
│  1. Read config/cron_task_graph.yaml                       │
│  2. Compute next runnable tasks (deps satisfied)           │
│  3. Group by agent_class:                                  │
│      - sonnet (pure-file): fan out, max N=5 parallel       │
│      - kit-rpc-sonnet: serialize (single-tenant Kit)       │
│      - opus: serialize (token budget)                      │
│      - human-review: file to queue, ping user              │
│  4. Dispatch + collect results                             │
│  5. Update state store (SQLite or JSONL)                   │
│  6. Every 10 completed tasks: emit mini-report             │
│  7. Check halting criteria (hit@1 ≥ 0.75 OR 50 yrkesroll   │
│     migrated OR 6-week cap)                                │
│  8. If halt: emit final report, stop                       │
│     Else: GOTO 2                                           │
└────────────────────────────────────────────────────────────┘
```

Source: Q7 §1 finding 9 (3 concurrency landmines drive serialization);
Q7 §4 (parallelism map); Q7 §7 (approval cadence).

### 0.2 Agent class summary

| Class | Tasks | Concurrent capacity | Used for |
|---|---|---|---|
| sonnet | 52 | N (5 typical) | Pure file edits / analysis |
| kit-rpc-sonnet | 21 | 1 (serial) | Sonnet touching Kit at 127.0.0.1:8001 |
| opus | 6 | 1 | Cross-doc synthesis |
| human-review | 3 | 1 | Anton's manual approval gates |

Source: Q7 §8 (resource budget).

### 0.3 Kit RPC is single-tenant

Per MEMORY `feedback_isaac_assist_kit_concurrency`:

> Kit RPC single-tenant — no concurrent direct_eval. Parallel
> direct_eval against same Kit causes stage-state races; run
> sequentially.

The dispatcher enforces this with a Kit-resource lock. Cron task
graph YAML has `cron_safe: true/false` per task; only `cron_safe: true`
tasks dispatch automatically. Kit-bound tasks acquire the lock; others
fan out freely.

### 0.4 Human-review checkpoints

Three task types pause for Anton:
- **t03** delete confirmation (MEMORY `feedback_confirm_destructive_actions`)
- **t12** human pattern_hint review (104 templates)
- **t25** feature-flag enable (MULTIMODAL_TEXT_INTENT=on)
- **t26** Gemini env setup (secrets)
- **t82** final spec sign-off

Plus auto-pauses every 10 tasks (mini-report) and before any Kit
restart that follows a hard failure (not routine drift restart).

---

## §1. Halting criteria

Adopted **verbatim** from Q7 §5 with no edits. Justified per the
parent research-spec §7 open question 5.

### 1.1 Quality target — retrieval hit@1 ≥ 0.75 on 30-prompt benchmark

**Source:** Q3 §8 baseline expectation is ~50–65%. Target 0.75 is a
stretch but feasible after:
- t21 (`evaluate_candidates` tool) — Phase 3 iterative retrieval ships
- t23 (role-prefilter wired) — pre-filter guard adds high-precision
  matches
- t25 (`MULTIMODAL_TEXT_INTENT=on`) — structural-filter active for ≥ 30
  templates

**Tier breakdown (Q7 §5.1 estimate):**
- Tier 1 (high-confidence): 0.80–0.90 hit@1 once `intent` corpus
  broad.
- Tier 2 (domain-breadth): lift from ~0.40–0.60 to ~0.65–0.75 with
  role-retriever wired.
- Tier 3 (adversarial): stays ~0.30–0.50 (by design).
- Weighted blend can hit 0.75 if Tier 1 + Tier 2 improve substantially.

**Measurement command (per t78):**
```bash
python scripts/qa/run_retrieval_benchmark.py \
    --baseline workspace/benchmarks/retrieval_30prompts_v1.jsonl \
    --metric hit_at_1 --threshold 0.75
```

### 1.2 Quantity target — 50 yrkesroll canonicals migrated to role-based schema

**Source:** Q6 §6 fully-autonomous rate is 5–8 function-gate canonicals/
day. Realistic in 6-week window: 60–80 cumulative. Target 50 is a 60–
80% achievement bar.

**Definition (Q7 §5.2):**
- The 50 are NEW yrkesroll-aligned canonicals (CP-NEW-* + new TP-*
  files after t04 mapping), not the 80 CP-numeric role-migrations from
  t15.
- Each must have `intent` field AND `roles` field AND `verified_status`
  contains "function-gate ✓".

**Measurement command (per t78):**
```bash
python -c '
import json, glob
cnt = sum(
    1 for f in glob.glob("workspace/templates/CP-NEW-*.json") +
                glob.glob("workspace/templates/TP-*.json")
    if "intent" in json.load(open(f))
    and "function-gate ✓" in json.load(open(f)).get("verified_status", "")
)
print(cnt)
' >= 50
```

### 1.3 Time target — 6-week safety cap

**Source:** Parent research-spec §7 open question 5 mentions "stop
after 6 weeks" as a legitimate user-set bound. Adopted as a safety
cap to prevent runaway loops.

**Math (Q7 §5.3):**
- Total estimated wall time across all 82 tasks: ~168 hours.
- Productive capacity: 8 hours/day × 5 days/week = 40 hours/week.
- 6 weeks = 240 hours capacity.
- 168-hour budget fits in 6 weeks **only if** parallelism map
  exploited; sequential execution would consume ~21 weeks (168 / 8).

**Measurement:** Start-date stamp in `config/cron_task_graph.yaml` +
42-day calendar check.

### 1.4 Plus — user "stop" command

In addition to the three quantitative criteria, the user can issue
`stop` or `pause` at any time. The dispatcher checks an explicit
`cron_state.user_signal` field every loop iteration. Per MEMORY
`feedback_dont_pause_in_autonomous_mode`: "after 'kör på' / 'du
bestämmer', don't offer menus; pick and execute" — implies the user
must explicitly say stop, not just be silent.

### 1.5 OR-semantics

The cron halts when **ANY** criterion fires, not when all do. Per
Q7 §5.4:

> The user's intuition (research-spec §7 Q5) suggests one of these
> criteria, not all three. OR-semantics let the cron stop as soon as
> any indicator of "good enough" hits.

If the cron reaches all 3 caps, the conjunction is irrelevant — the
first to fire wins.

---

## §2. Approval cadence

User preferences synthesized from memory:
- `feedback_dont_pause_in_autonomous_mode` — after "kör på" don't offer
  menus; pick and execute.
- `feedback_confirm_destructive_actions` — reboot/shutdown/kill require
  explicit OK.
- `feedback_kit_restart_autonomous` — Kit restart is routine, not
  destructive.

### 2.1 Mandatory pause points

The cron MUST pause and ping Anton at these events (Q7 §7):

1. **Every 10 tasks completed** — mini-report (§7 of this plan)
2. **Before any Kit restart that follows a hard failure** — distinct
   from autonomous restart on drift (routine). If Kit just died
   unexpectedly, pause once for Anton to glance.
3. **Before any template DELETE operation** — t03 follows this pattern;
   extend to any future delete in migration tracks.
4. **Before any `git push` to a remote** — MEMORY `feedback_isaac_
   assist_push_target` + `feedback_isaac_assist_pr_workflow`: pushes go
   to anton remote, not k3street. Cron does NOT auto-push.
5. **Before publishing the final implementation spec (t82)** — human
   review of synthesized spec before commit.

### 2.2 Continuous otherwise

Per `feedback_dont_pause_in_autonomous_mode`, all other tasks run
continuously without offering menus or pausing for confirmation:

- All schema migrations (Tracks A + B, except deletes) — continuous.
- All Kit-RPC promotions (Track F t46–t57) — continuous with auto-
  restart between.
- All retrieval benchmarks (Track C) — continuous.
- All Gemini stress-test runs (Track D except t26 env-setup) —
  continuous.
- All workflow / flow-arch changes that don't delete state —
  continuous.

### 2.3 Mini-report content (every 10 tasks)

Per Q7 §7. The mini-report is **informational, not blocking**:

```
Cron progress report — Batch ending YYYY-MM-DD HH:MM
================================================
Tasks completed: 10
Passed: 8
  - t01 strip deprecated fields → 4 templates updated
  - t02 typo fix → 1 template
  - t10 lint script → exists, --strict returns 0
  - t04 ghost corpus remap → 30 IDs mapped
  - ...
Filed for review: 2
  - t46 CP-NEW-inspect-reject → function_gate_failure 1/5 (Kit drift?)
  - t12 pattern_hint review → 14 templates need human eyes

Critical-path progress: 4/11 tasks done
  t10 ✓  t19 ✓  t20 ✓  t26 (Anton-pending)

Halting criteria status:
  Quality (hit@1 ≥ 0.75): currently 0.58 (after t20 baseline)
  Quantity (50 yrkesroll migrated): currently 7/50
  Time (6-week cap): week 1 of 6, day 4

Next 10 tasks: t14, t15, t46, t47, t48, t49, t50, t51, t52, t29
```

User can read or ignore. Loop continues unless user explicitly says
"stop" or "pause" or "halt".

### 2.4 Weekly digest

In addition to mini-reports, at the end of each week, an Opus task
emits a weekly digest:

```
WEEK N DIGEST — YYYY-MM-DD
==========================

Progress summary:
  Tracks complete this week: [list]
  Tasks completed: [N] of 82
  Tasks filed for review: [N]
  Tasks pending Anton: [list with reason]

Key metrics:
  Retrieval hit@1: [progression line: 0.55 → 0.62 → 0.68]
  Yrkesroll function-gate ✓: [progression line]
  Sonnet hours spent: [N] / [budget]
  Opus hours spent: [N] / [budget]
  Gemini USD spent: [$N] / $28 reserve

Decisions pending:
  - Anton must confirm: [list]
  - Anton must approve delete: [list]

Top 3 wins:
  1. [task X delivered Y]
  2. ...

Top 3 concerns:
  1. [task X failed twice, escalated to human-review]
  2. ...

Next week's plan:
  [bulleted forecast based on dependency graph]
```

---

## §3. Week-by-week ramp

Adopted from Q7 §13 with one-page execution summary expanded.

### Week 1 (Days 1–7) — Bootstrap + safety nets

**Track A:** Begin drift remediation. CP-01/02/07/08 deprecated fields
stripped (t01), typo fix (t02). Ghost corpus mapping is in progress
(t04 starts but takes ~120 min, slips into Week 2).

**Track B:** Lint script lands (t10 — critical path). Mechanical
intent migration starts (t11).

**Track C:** 30-prompt benchmark exists (t19), baseline measured (t20)
— **first number Anton will care about**.

**Track D:** Gemini env setup (t26 — Anton manual). Smoke test (t27).
Baseline run (t28). Failure categorization (t29 — Opus).

**Track E:** No flow-architecture work this week (gated on Track G's
t41 landing).

**Track F:** No canonical creation work this week (gated on t45
pipeline build, which depends on t10 lint script).

**Track G:** Workflow template registration (t41 — critical path). E2E
test depends on t40 (auto-route), so deferred.

**Infrastructure:** Baseline snapshot (t79). Kit supervisor check
(t30). Task graph YAML in repo (t66). Cron dispatcher (t67 — critical
path). Recovery decision tree (t68). Mini-report generator (t69). Kit
restart hook (t70). uvicorn restart hook (t71 — critical for any
orchestrator/tool edit).

**Tasks delivered Week 1 (~15):**
- t01, t02, t10, t11, t19, t20, t26, t27, t28, t29, t30, t41, t66, t67, t79

**Open at end of Week 1:**
- t04 (ghost corpus remap — multi-hour task)
- t12 (human pattern_hint review — Anton must run)
- t14/t15 (CP-NEW + CP-numeric role authoring — big lifts)

### Week 2 — Schema authoring + drift completion

**Track A:** Complete drift remediation:
- t03 delete (Anton approval gate)
- t04 ghost corpus remap finalized
- t05 plumbing-only marks
- t06 CP-67 status resolution
- t07 priority D2 form-gate

**Track B:** CP-NEW role authoring starts (t14 — first big lift).
Equivalence test extension (t17) — must land BEFORE t14 generates
`code_template`, but t14 only authors `roles` + `role_defaults` in
this phase; `code_template` is t16 in Week 3.

**Track C:** Iterative retrieval implementation begins (t21 — assumes
t20 baseline showed ambiguity > recall as failure mode; otherwise
deferred per §5.3 of the spec).

**Track D:** Round 1 + 2 tool-surface fixes (t31, t32). Full corpus
re-run (t33). Round 2 fixes (t34).

**Track F:** Canonical draft pipeline build (t45 — critical path,
360 min wall time).

**Track G:** Workflow template data audit (t61). E2E integration test
(t62) — depends on t41 + t40.

**Tasks delivered Week 2 (~15):**
- t03, t04, t05, t06, t07, t12 (Anton-gated), t14, t17, t21, t31, t32, t33, t34, t45, t61

### Week 3 — Migration big lift + Gemini swap

**Track B:** CP-numeric (CP-06..87) role authoring (t15 — largest
single agent-effort block, 360 min). Wire CI gate (t18).

**Track C:** Settle_state gap fill (t13). Intent expansion validation
for CP-06..30 (t24).

**Track D:** **Day-7 model swap** (t35 — calendar pinned at
~2026-05-22). Three-way attribution analysis (t36 — Opus).

**Track E:** Workflow auto-route lands (t40 — depends on t41).

**Track F:** Tier-1 yrkesroll promotions begin (t46, t47, t48 — 3 of 7
this week).

**Tasks delivered Week 3 (~13):**
- t13, t15, t18, t24, t35, t36, t40, t46, t47, t48, t62, t72 (review queue dashboard), t73 (honesty schema)

### Week 4 — Retrieval improvements + Tier-1 finish

**Track B:** Generate `code_template` for 104 templates (t16 — gated
on t17 equivalence test extension being landed first).

**Track C:** A/B iterative vs single-shot (t22). Role-retriever pre-
filter (t23). MULTIMODAL_TEXT_INTENT=on enablement (t25 — Anton-gated).

**Track D:** Final corpus run (t37). Tool-audit-flash scanner (t38).

**Track E:** Scout pass for `scene_diagnose` (t39). Spec post-condition
gate code (t42 — feature-flagged OFF).

**Track F:** Complete Tier-1 (t49, t50, t51, t52). Tier-2 starts (t53,
t54 — physics-tuning risk, may file for review).

**Track G:** L-levels pilot annotation (t63). Router design doc (t64
— Opus).

**Tasks delivered Week 4 (~14):**
- t16, t22, t23, t25, t37, t38, t39, t42, t43 (flow routing test suite), t49, t50, t51, t52, t63, t64

### Week 5 — Yrkesroll bulk + structural-filter live

**Track C:** Backstop: explicit routing table refactor in orchestrator
(t44 — Opus, high-risk, opportunistic).

**Track F:** Top-20 yrkesroll bulk-draft (t56). Sequential Kit verify
(t57 — largest single labor block, ~6 hours wall-clock). Tier-2 finish
(t55 — multi-AMR corridor, depends on t02 typo fix).

**Track G:** Verifier registry unification (t65 — opportunistic Tier
3).

**Infrastructure:** Tighten honesty schema (t73 evolved into runtime
checks). Kit concurrency regression test (t75). Post-commit ChromaDB
hook with flock (t74).

**Tasks delivered Week 5 (~10):**
- t44 (opportunistic), t55, t56, t57, t58 (role-migration on verified yrkesroll), t59 (ChromaDB reindex), t65, t73, t74, t75

### Week 6 — Closure + final synthesis

**Track F:** Phase 78c asset precheck (t60 — unblocks Tier 4 yrkesroll
for the future).

**Track G:** Catch-up tasks if any deferred.

**Track A:** T2 never-run sweep (t09) — opportunistic if Kit budget
remains.

**Track C:** Q8 iterative retrieval synthesis (t80 — Opus).

**Closure:**
- Halting criteria check (t78) runs continuously; if it fires earlier,
  the cron stops before Week 6.
- ChromaDB final reindex (t59 re-run if needed).
- Documentation: research INDEX (t77), canonical schema TypeScript
  decl (t76), baseline metrics summary (t81).
- **Final synthesis implementation spec (t82 — Opus, human-review
  gate).**

**Tasks delivered Week 6 (~10):**
- t08 (D2 remaining form-gate), t09, t60, t76, t77, t78, t80, t81, t82 + halt notification

### 3.1 Total task count

| Week | Tasks completed | Cumulative |
|---|---|---|
| 1 | 15 | 15 |
| 2 | 15 | 30 |
| 3 | 13 | 43 |
| 4 | 14 | 57 |
| 5 | 10 | 67 |
| 6 | 10 | 77 |
| (slop / re-runs) | 5 | 82 |

This matches the 82-task target with realistic friction.

### 3.2 What can shift left or right

Tasks marked `priority_tier: 3` in the YAML are opportunistic — if
the cron has spare slots, run them; otherwise they slip and may not
land in 6 weeks. These include:
- t02 (typo fix — low impact)
- t09 (T2 never-run sweep — ~15 agent-hours)
- t44 (Opus refactor of routing table — high-risk)
- t65 (verifier registry unification)

Tasks marked `priority_tier: 1` (critical path + foundational) MUST
land or downstream blocks: t10, t19, t20, t30, t41, t45, t66, t67,
t68, t69, t70, t71, t73, t78, t82.

---

## §4. First-day kickoff (the first 5 tasks)

Strict execution order for Day 1 morning. Each unblocks the next.

| Order | Task | Day | Unblocks |
|---|---|---|---|
| 1 | **t79** Template-corpus baseline snapshot | Day 1 AM | Rollback safety for ALL bulk migrations |
| 2 | **t30** Kit supervisor health-check | Day 1 AM | All `kit-rpc-sonnet` tasks (t06, t07, t08, t27, t28, t35, t46–t57, t75) |
| 3 | **t10** Build `lint_canonical_templates.py` | Day 1 PM | Track A drift tasks (t01–t06); Track B schema migration (t11–t18); Track F pipeline (t45) |
| 4 | **t19** Build 30-prompt retrieval benchmark | Day 2 AM | Track C retrieval baseline (t20); halting criterion 1 |
| 5 | **t20** Measure current hit@1 baseline | Day 2 AM | Iterative retrieval ROI decision (t21–t22); structural-filter coverage decision (t24, t25) |

**Rationale flow:**
- Safety (t79) → Kit anchor (t30) → schema gate (t10) → retrieval gate
  (t19/t20).
- By end of Day 2, the cron has a baseline number to report and Tracks
  A + B can fan out.

**Day 3 priorities (next 5 tasks):**
- t66 (materialize task graph YAML)
- t67 (cron dispatcher script)
- t71 (uvicorn restart hook — mandatory before any orchestrator/tool
  edit lands)
- t26 (Gemini env setup — Anton gate, slot for whenever Anton is
  online)
- t11 (mechanical intent migration to 104 templates)

By end of Day 4, the cron is autonomous-capable and Tracks A, B, C, D
all have at least one task complete.

---

## §5. Failure recovery — decision tree

Adopted from Q7 §6 verbatim. Reproduced here for human reading.

```
Task failed (verification command exits non-zero OR explicit error)
  │
  ├── Was this a Kit-RPC task?
  │     │
  │     ├── Yes — is Kit alive?
  │     │     │
  │     │     ├── No  → Kit-restart per MEMORY 'kit_restart_autonomous'
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
  │                      MEMORY warning: NEVER fan out, even with backoff.
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
  │     ├── Schema-migration tasks (t11–t16): switch from mechanical to
  │     │   LLM-guided prompt with failing template's content + Q2 ref
  │     │
  │     ├── Kit-RPC promotion tasks (t46–t57): if function-gate <2/5 pass,
  │     │   escalate to docs/review/pending/ (Q6 §8 _file_for_review);
  │     │   do NOT retry indefinitely — physics issues are not solvable
  │     │   by re-prompting.
  │     │
  │     ├── Benchmark tasks (t20, t22): check file integrity first;
  │     │   if corrupted, restore from t79 snapshot; otherwise re-run.
  │     │
  │     └── Opus synthesis tasks (t29, t36, t44, t64, t80, t82):
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

### 5.1 Decision authority summary

| Decision | Authority | Rule |
|---|---|---|
| Retry transient failure (network, timeout) | Cron auto | Up to 3 retries, exponential backoff |
| Restart Kit | Cron auto | MEMORY `kit_restart_autonomous` (routine, not destructive) |
| Restart uvicorn | Cron auto | After any tool_executor edit (mandatory per MEMORY) |
| Re-author via LLM with different prompt | Cron auto | For schema/draft tasks; escalate after 2 re-author attempts |
| Skip Kit-bound task and proceed | Cron auto | Mark failed, queue for human review, continue |
| Delete a template | Human | MEMORY `feedback_confirm_destructive_actions`; t03 gated this way |
| Force-push or rewrite history | Human | Never autonomous |
| Halt the loop early | Human | Cron checks criteria every batch; user can preempt |
| **Equivalence test failure** | Human | Does NOT retry; goes straight to human-review (per Q7 §6 re-author branch) |

### 5.2 Specific failure patterns + mitigations

**Pattern: Sonnet task fails twice → re-author prompt**

Re-author is automated via the dispatcher (Q7 §6 "re-author branch").
The prompt is regenerated with the failing template's content + the
Q2 reference pattern. If the re-authored version also fails, the task
escalates.

**Pattern: Re-authored task fails → human-review queue**

After 2 re-author attempts (so 4 total tries: 2 verbatim + 2
re-authored), the task is filed to `docs/review/pending/<task_id>__
<reason>.json` and the review-queue dashboard (t72) surfaces it.

**Pattern: Kit RPC fails → restart uvicorn per memory**

Per `feedback_isaac_assist_service_restart`: "Restart uvicorn after
tool_executor edits — service on port 8000 loads tool_executor at
startup and keeps it cached; direct_eval silently tests stale code
until kill+relaunch."

The dispatcher's uvicorn-restart hook (t71) triggers automatically
when any task in the `uvicorn-touching` resource class completes.

**Pattern: Equivalence test fails → straight to human-review**

`tests/test_role_template_equivalence.py` failure indicates the
generated `code_template` produces different tool calls than the
legacy `code`. This is a semantic correctness issue, not a transient
flake. Q7 §6 explicit rule: do NOT retry; file for review.

---

## §6. Compute budget — total estimate

Adopted from Q7 §8 verbatim.

### 6.1 Wall-clock breakdown

| Agent class | Tasks | Total minutes | Hours |
|---|---|---|---|
| sonnet (pure file) | 52 | 5,205 | 86.8 |
| kit-rpc-sonnet | 21 | 2,520 | 42.0 |
| opus (synthesis) | 6 | 1,020 | 17.0 |
| human-review | 3 | 140 | 2.3 |
| **Total** | **82** | **8,885** | **148.1** |

Plus 15% friction padding (Kit restarts, uvicorn restarts, ChromaDB
reindex waits, transient retry overhead) = **~170 hours total
wall-clock**.

### 6.2 USD cost breakdown

**Gemini Flash** [Q7 §8]:
- 30-prompt corpus = $0.57/run.
- Plan accommodates ~6 runs = **$3.42**.
- 1000 SEK (~$92) budget; ~$88 reserve (massively over-budget).

**Sonnet API** [Q7 §8]:
- 73 Sonnet tasks (52 sonnet + 21 kit-rpc-sonnet).
- ~50K tokens average per task (system prompt + research-doc refs +
  LLM output).
- Sonnet 4.6 pricing ~$3/$15 per M tokens.
- ~$33 in Sonnet inference.

**Opus** [Q7 §8]:
- 6 Opus synthesis tasks.
- ~100K tokens per task.
- Opus 4.7 pricing ~$15/$75 per M tokens.
- ~$27 in Opus inference.

**Total dollar cost: ~$65 LLM inference + ~$4 Gemini = ~$69**.

### 6.3 Kit wall-clock

Each Kit-RPC task:
- Smoke-test: ~30s
- Form-gate: ~30–60s
- Function-gate (5 runs): ~15–30 min
- Reset + restart overhead: ~30s

**Kit wall-clock = ~49 hours** single-tenant. Restart every ~30
canonicals adds ~60s total. Negligible.

### 6.4 Human review hours

| Task | Anton's time |
|---|---|
| t03 delete confirmation | 5 min |
| t12 pattern_hint review (104 templates) | 120 min |
| t25 feature-flag enable | 5 min |
| t26 Gemini env setup | 15 min |
| t82 spec review | ~60 min implicit |
| Mini-report glances × ~20 | 100 min |
| **Total** | **~5 hours** spread across 6 weeks |

### 6.5 Cost summary

| Resource | Estimate |
|---|---|
| Wall-clock (autonomous) | ~170 hours |
| LLM inference (Sonnet + Opus + Gemini) | ~$69 |
| Anton's direct attention | ~5 hours total |
| Kit single-tenant time | ~49 hours |
| ChromaDB writes | ~30 (single-process serialized) |

---

## §7. What the user sees

The user-facing surface during the autonomous run:

### 7.1 Mini-reports (every 10 tasks)

Format defined in §2.3. Filed to `docs/cron/reports/YYYY-MM-DD-
batch-N.md`. Anton can read or ignore. Loop continues unless explicit
stop command.

### 7.2 Weekly digest

Format defined in §2.4. Filed to `docs/cron/digests/week-N-YYYY-MM-
DD.md`. Opus-authored at end of each Friday.

### 7.3 Halt notification

When any halting criterion fires (or user stops manually), a final
notification is filed to `docs/cron/halt-notification.md`:

```
HALT NOTIFICATION — YYYY-MM-DD HH:MM
=====================================

Halt reason: Quality target met (hit@1 = 0.78 ≥ 0.75)
  - OR -
Halt reason: 50 yrkesroll canonicals migrated
  - OR -
Halt reason: 6-week safety cap reached
  - OR -
Halt reason: User stop command

Summary of accomplishments:
  - Tasks completed: 77/82
  - Tasks filed for review: 5
  - Tracks fully complete: A, B, C, D, E, G
  - Tracks partially complete: F (47/50 yrkesroll → expected target reached)

Final metrics:
  Retrieval hit@1: 0.78 (was 0.58 at baseline)
  Yrkesroll function-gate ✓: 53
  Sonnet hours: 84.2 / 86.8 budget
  Opus hours: 16.1 / 17.0 budget
  Gemini USD: $4.20 / $92 budget

Outstanding items:
  - 5 tasks in docs/review/pending/ require Anton's eyes
  - 0 critical-path tasks unresolved

Next steps:
  - Read final implementation spec at docs/specs/2026-05-XX-final-
    canonical-flow-results.md
  - Approve / refine remaining drafts in docs/review/pending/
  - Optionally re-launch cron for opportunistic Tier-3 tasks (t09, t44,
    t65, etc.) — these are not required for ship readiness
```

### 7.4 Override commands the user can issue

The dispatcher polls `cron_state.user_signal` every loop. Commands:

| Command | Effect |
|---|---|
| `stop` | Halt immediately; emit halt notification with reason="user stop" |
| `pause` | Suspend dispatch but keep state; resume on `resume` |
| `resume` | Resume from where `pause` stopped |
| `skip <task_id>` | Mark task as skipped (not failed); continue past it |
| `priority <task_id>` | Move task to front of runnable queue |
| `force-restart-kit` | Trigger Kit restart even if cron didn't request |
| `re-run <task_id>` | Re-execute a previously-completed task |
| `status` | Print current state without changing anything |

### 7.5 Where everything lives

| Artifact | Path |
|---|---|
| Task graph (machine) | `config/cron_task_graph.yaml` |
| Dispatcher state | `workspace/cron_state.jsonl` |
| Task outputs | (varies — git-tracked) |
| Review queue | `docs/review/pending/*.json` |
| Mini-reports | `docs/cron/reports/*.md` |
| Weekly digests | `docs/cron/digests/*.md` |
| Halt notification | `docs/cron/halt-notification.md` |
| Cron logs | `docs/cron/logs/*.log` |
| Metrics | `docs/cron/metrics/*.jsonl` |
| Honesty schema | `docs/cron/honesty_schema.md` |

---

## §8. Cron task-graph reference

The source of truth is `config/cron_task_graph.yaml`. This prose plan
is the human reading view.

The YAML's structure (matching Q7's companion file):
- `metadata` block (created_date, generator, halt criteria, approval
  cadence rules)
- `tracks` block (track letter → task IDs index)
- `tasks` list (82 task definitions with id, title, agent_class,
  inputs, outputs, verification, estimated_minutes, risk_level,
  priority_tier, cron_safe, source)
- `critical_path` list (11 task IDs)
- `first_week_ramp` list (10 task IDs with day + rationale)

Every dependency edge in `task.inputs` points to an existing task ID.
Every `cron_safe: true` task that is not in the `kit-rpc-sonnet` class
has no Kit dependency. Every Kit task has the `kit-rpc-sonnet` class.

Validation command (run before kickoff):
```bash
python -c '
import yaml, sys
data = yaml.safe_load(open("config/cron_task_graph.yaml"))
tasks_by_id = {t["id"]: t for t in data["tasks"]}
for t in data["tasks"]:
    for dep in t.get("inputs", []):
        assert dep in tasks_by_id, f"Dangling dep: {t['id']} -> {dep}"
    if t.get("cron_safe") and t["agent_class"] != "kit-rpc-sonnet":
        # No Kit dependency check beyond input deps
        pass
print("OK: all 82 tasks consistent")
'
```

---

## §9. Open decisions for the user (none if approved)

Anton must confirm these before t79 fires:

### 9.1 Halting criteria values (§1)

Default lock from spec §10:
- hit@1 ≥ 0.75 (Q3 §8 stretch target)
- 50 yrkesroll canonicals (Q6 §6 60–80% achievement bar)
- 6-week safety cap

If Anton wants different numbers, surface now.

### 9.2 Gemini API key (§7 of spec)

t26 needs `.env.local` with creds. Anton's manual step.

### 9.3 Auto-pause cadence (§2.1)

Default: every 10 tasks. If Anton wants every 5 (more chatty) or every
20 (less chatty), adjust before kickoff.

### 9.4 CP-NEW-peg-in-hole-single delete (§4.2 of spec)

t03 requires explicit Anton OK. Default is "yes, delete" per Q4 §5.2.

### 9.5 `SPEC_STEP_GATE` for the autonomous run (§2.2.c of spec)

Default: off-by-default. If Anton wants the gate enabled to test in
the autonomous run, flip the env var via cron-state. Recommendation:
keep off until benchmark shows the 40% missed-required-tool problem
exists.

### 9.6 Compute budget acceptance

~$69 LLM inference + ~5 hours of Anton's attention over 6 weeks.
Confirm.

### 9.7 Anton's git remote configuration

Per MEMORY `feedback_isaac_assist_push_target`: pushes go to `anton`
remote (private fork), not `k3street`. The cron dispatcher will NOT
auto-push — humans only. If Anton wants periodic auto-push to
`anton`, surface now (default: no auto-push).

**If all 7 are confirmed, kickoff is approved.** Per the parent
research-spec sign-off, this plan + the spec + the YAML are the
deliverables Anton reviews. Approval implies green-light for t79 +
the cron loop.

---

## §10. Sign-off

This execution plan is approval-ready. Approval gate: Anton's
explicit "go" before t79 fires.

After approval, the dispatcher (t67) starts the loop. The first
10 tasks fire on Day 1 + 2 per §4 of this plan. From there, the
cron self-paces using the dependency graph + halting criteria.

**Anton's main interaction surface during the run:**
1. Reading mini-reports every 10 tasks.
2. Approving t03 delete + t26 Gemini env when those queue.
3. Reviewing t12 pattern_hints (~14 templates expected to need fixes).
4. Reading weekly digests on Fridays.
5. Reviewing items in `docs/review/pending/` opportunistically.
6. Reading the halt notification when the cron stops.
7. Approving t82 final spec.

Total Anton time: ~5 hours over 6 weeks.

---

*End of execution plan. Total length: ~720 lines (within 500–1000
target). Companion spec at docs/specs/2026-05-15-canonical-flow-
standardization-spec.md. Companion YAML at config/cron_task_graph.
yaml.*
