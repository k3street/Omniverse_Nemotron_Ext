# Research Spec — Flow, Canonicals, Autonomous Workflow

**Date:** 2026-05-15
**Status:** research-spec v1. Purpose: gather information needed to
write the final implementation spec + autonomous-workflow plan.
**Owner:** Anton + autonomous agents (Sonnet-default; Opus for
synthesis; Gemini-via-GCloud-credits as second LLM consumer for
end-to-end testing).
**Time horizon:** 10 days research + synthesis, then weeks of
autonomous execution.

---

## 0. Conceptual model (confirm or correct)

Layers of abstraction:

```
┌─────────────────────────────────────────────────────────┐
│ Canonical (Template)                                    │
│   = ordered tool-chain at the highest abstraction       │
│   = "build a 2-Franka pick-place cell with conveyor"    │
│   = sequence (or parallel) of N tool calls              │
│   = the unit the LLM matches against / instantiates     │
└─────────────────┬───────────────────────────────────────┘
                  │ composed of
                  ▼
┌─────────────────────────────────────────────────────────┐
│ Tools (L1, L2, L3)                                      │
│   L1 = atomic single-op (create_prim, set_attribute)    │
│   L2 = composed multi-op atomic (robot_wizard,          │
│         setup_pick_place_controller, build_scene_from_  │
│         blueprint)                                      │
│   L3 = async multi-phase (start_workflow)               │
│   "L" = atomicity/sync-async axis, NOT complexity       │
└─────────────────────────────────────────────────────────┘
```

**Task complexity** is emergent from canonical composition, not
declared per-tool. A canonical containing 100 L1 tools is "complex"
regardless of L-level on individual calls.

**Tool-chains** as discussed previously = canonicals. The
"sequencing" is encoded in the canonical's `code` field or its
declarative composition.

This research spec **confirms or corrects this model** as one of its
first outputs.

---

## 1. Why this research is needed

### 1.1 The problem
The current state is unclear in three places:

- **Flow uncertainty.** How should "prompt arrives" → "scene built"
  actually work? Single-shot retrieve+instantiate (today's default)
  vs. iterative Claude-Code-style explore+build vs. negotiator+plan+
  execute. We don't know which is right or when to use which.
- **Canonical format chaos.** 321 templates exist with inconsistent
  fields. 5/321 have `intent`, ~10/321 have `roles`, ~16/321 have
  `code_template`. Different authors created different shapes. No
  conformance check. No migration tool.
- **Autonomous viability gap.** User wants a plan that "chews
  autonomously for many weeks in optimal sequence." Today the
  autonomous-cron infrastructure exists (Kit Supervisor, cron runners
  in Spec 2 §18) but the task graph + ordering + halting conditions
  are not specified.

### 1.2 What this research must produce
A research report sufficient to write **two concrete deliverables
after the research lands**:

1. **Final implementation spec** for the standardized canonical
   format + iterative retrieval flow + autonomous-execution pipeline
2. **Autonomous workflow plan** that fans out into agent tasks
   running for weeks, in an optimal dependency-ordered sequence

The research spec itself does NOT produce those. It produces the
**inputs** to write them.

---

## 2. Research questions

Each question is scoped to be answerable by ONE agent in 5-15 min.
Each maps to a chunk of the eventual implementation spec.

### Q1. Flow architecture — what's right?

**Question:** What is the right prompt-to-execution flow for this
codebase? Compare:

| Mode | Description |
|---|---|
| A — Single-shot retrieve+instantiate | TODAY's default. classify_intent → top-K templates → hard-instantiate if confident, else few-shot |
| B — Iterative explore (Claude Code-style) | Agent sees a small starter prompt + tool catalog, calls `scene_summary` / `list_*` to build mental model, picks tools based on observed state |
| C — Plan-then-execute | classify_intent → write a plan (LLM-authored) → execute the plan step-by-step with checkpoints |
| D — Negotiator + canonical | classify_intent → ask clarifying questions if needed → match canonical → execute |
| E — Hybrid | Different modes for different intent/complexity classes |

What does the codebase support today? What should be the production
default per request type?

**Agent investigates:**
- Current orchestrator path (`chat/orchestrator.py`)
- Existing negotiator path (`chat/negotiator.py`)
- Existing spec_generator + gap_analyzer (already used at
  complexity=="complex")
- The 8 intent types + 3 complexity levels — which route to which mode?
- Pi0 / GR00T / OpenVLA — do their inference patterns suggest mode B?
- Empirical: which mode wins on which prompts? (existing baselines)

**Output:** ~500 LOC MD report. Decision recommendation + thresholds.

### Q2. Canonical format standardization

**Question:** What is the canonical template format we want to
standardize on? Then: how do we migrate 321 → standardized?

**Sub-questions:**
- Field-by-field: which fields are mandatory, which optional, which
  deprecated?
- Format examples: CP-01 (role-based, 5 templates) vs CP-NEW-amr-*
  (no roles, no intent) vs A-* / D-* / E-* (other prefixes)
- Conformance check: a script that validates every template against
  the schema and fails CI if drift
- Migration tool: semi-automated rewriter that adds `intent` /
  `roles` / `code_template` to legacy templates while preserving
  `code` behavior
- Backward compat: how long do both old and new fields coexist?

**Agent investigates:**
- Read 10 random templates across prefixes (CP, CP-NEW, A, D, E, F)
- Document what fields appear in which subset
- Cross-reference with `service/.../multimodal/types.py` (LayoutSpec,
  Intent, RoleBindings schemas)
- Phase 20/21/28 role-based migration plan (was for 10 templates;
  scale to 321)
- Cite the existing equivalence test pattern
  (`tests/test_role_template_equivalence.py`) as the migration gate

**Output:** ~500 LOC MD with:
- Canonical schema spec (TypeScript-style interface)
- Field-by-field migration checklist
- Conformance check + migration tool spec
- Risk + cost estimate for 321-template migration

### Q3. Retrieval quality + iterative retrieval

**Question:** How well does the current Top-K retrieval work? Should
it become iterative (search → evaluate → re-search) like Claude
Code's exploration loop?

**Sub-questions:**
- Empirical hit-rate today (calibration is 3 data points; need 30+)
- When does Top-K succeed vs fail? On what prompt types?
- Iterative pattern: prompt → search → "do these 3 candidates match
  the user's intent?" (LLM evaluates) → if yes execute, else
  refine query + re-search
- Multi-stage retrieval: structural filter (intent.pattern_hint) +
  similarity tiebreak (already partially built in
  `template_retriever.retrieve_with_intent_filter`)
- Role-retriever (Phase 20) is built but unwired — should it be the
  primary path?

**Agent investigates:**
- `service/.../chat/tools/template_retriever.py` 3 retrieval modes
- `service/.../chat/tools/role_retriever.py` (Phase 20)
- Empirical baselines from `workspace/baselines/*-baseline.json`
- How Claude Code itself retrieves (web search this — it's
  documented in public docs)
- Existing retrieval benchmarks in 2026 robotics-NLP papers

**Output:** ~500 LOC MD with:
- Current hit-rate (best-estimate from existing data + spot checks)
- Recommended retrieval flow: single-stage vs multi-stage vs
  iterative
- Concrete tool API for iterative retrieval (new tool or extension
  of existing?)
- Benchmark plan to measure improvement

### Q4. Multiple-session template drift audit

**Question:** Templates have been created across many sessions with
different authors. What inconsistencies exist? How can we identify
templates that need re-authoring vs templates that can be migrated
mechanically?

**Sub-questions:**
- 321 templates exist. By git blame, how many distinct sessions
  authored them?
- What fields are inconsistent (some have `failure_modes`, some
  don't; some have `verified_status`, some don't)?
- Which templates are "shippable" (verified, complete, tested) vs
  "draft" (incomplete, never verified)?
- Cluster templates by structural shape — how many distinct shapes
  exist? (Possibly using a tree-edit-distance or AST-similarity
  metric.)

**Agent investigates:**
- `git log --pretty="%h %s" workspace/templates/` (last ~200 commits
  to templates)
- Survey of `verified_status` / `verified_date` fields
- Field-presence frequency table (one row per field, columns =
  template count)
- Group templates into "shape clusters"

**Output:** ~500 LOC MD with:
- Template cohort table (cohort → count → status → fields)
- Migration vs re-author decision per cohort
- "Templates to delete" list (drafts that never verified)

### Q5. Gemini as tool-consumer (end-user LLM integration)

**Important clarifications (2026-05-15):**

1. Gemini is NOT a research helper. Gemini is meant to **use**
   Isaac Assist's tools as an end-user LLM consumer — same role
   Claude plays today via the agentic chat loop.
2. **Use a weaker model deliberately.** Gemini Flash (current) is
   weaker than Claude Sonnet. That's the point — running a weaker
   model through the same tool surface pressure-tests the
   **harness** and **determinism** of our tools. Bugs that Claude
   masks via judgment surface clearly when Flash hits them.
3. New Gemini model releases next week (~2026-05-22). Plan should
   accommodate model swap mid-test: existing tests re-run on the
   new model to measure "did our harness improvements help, or
   did the model just get smarter?"

The 1000 SEK GCloud credits fund the integration + end-to-end
tests where Gemini drives the tool.

**Question:** How do we integrate Gemini as a tool-consumer + what
does the end-to-end test look like?

**Sub-questions:**
- **MCP layer**: Isaac Assist exposes tools via MCP server (`mcp_server.py`).
  Gemini supports MCP via the `google-genai` SDK 2026 release. Does
  the current MCP server speak protocol Gemini can consume, or are
  we Claude-tool-schema-locked?
- **Tool-schema differences**: Anthropic's tool-use JSON schema vs
  OpenAI-style (which Gemini follows). Translation needed?
- **Agentic loop**: today's orchestrator is Claude-specific
  (`anthropic` SDK). Does Gemini need its own orchestrator-loop, or
  can we abstract the LLM provider?
- **Tool catalog visibility**: 437 tools is a lot. How does Gemini
  context-window handle the schema dump? Is the existing
  `context_distiller` LLM-agnostic?
- **End-to-end test plan**: 1000 SEK budget should fund ~N
  prompt→build→verify cycles with Gemini driving. Which N prompts?
- **Failure modes Gemini-specific**: what bugs are likely to surface
  only when a non-Claude LLM uses the tools? (e.g., schema
  ambiguity, tool description quality, error-message clarity)
- **Production rollout**: should Gemini become a supported alternative
  driver for the production tool, or is this purely a stress-test?

**Agent investigates:**
- Current LLM provider abstraction in `service/.../llm_providers/`
- Current MCP server (`service/.../mcp_server.py`)
- Current tool-schema format vs Gemini's expected format
- Cost calculations: how many prompt→build→verify cycles does
  1000 SEK buy on Gemini 2.5 Pro / Flash?
- Web search for Gemini 2.5 tool-use + MCP support 2026 state
- Existing tool-quality issues that would surface to a different LLM
  (e.g., the 12 `resolve_*` tools — does their description scan
  cleanly for a non-Claude LLM?)

**Output:** ~500 LOC MD with:
- Integration architecture: MCP-first vs SDK-first vs both
- Concrete files to add/modify, ~LOC estimate
- End-to-end test plan: N prompts × cost per cycle = burn rate
  fitting 1000 SEK in 10 days
- Day-by-day burndown plan
- **Harness + determinism stress-test plan**: which specific
  brittleness aspects we expect Flash to expose (ambiguous tool
  descriptions, error messages that assume context, schemas
  with implicit constraints, retry semantics) and how we measure
  the find-rate
- **Mid-stream model swap**: protocol for re-running the same
  test corpus on next-week's new Gemini, measuring delta
  attributable to harness changes vs model improvements
- Decision: is Gemini a permanent supported driver, or only a
  stress-test sample?

### Q6. Canonical creation pipeline (new canonicals at scale)

**Question:** We need many more canonicals (yrkesroll, industrial,
research). What's the right pipeline to produce them at quality?

**Sub-questions:**
- Source of new canonicals: user-authored / LLM-drafted / video-
  derived (Anton's CadCreator vision) / scraped from open robotics
  datasets?
- Quality bar: what makes a canonical "shippable"? (intent declared,
  roles declared, code_template substitutable, equivalence test
  passes, function-gate passes on N runs)
- Author workflow: how does a new canonical get from idea → drafted
  → verified → committed?
- Cron-friendly: can the autonomous agent generate new canonicals,
  or is that too high-judgment for autonomous Sonnet?

**Agent investigates:**
- `workspace/templates/CP-NEW-*` cohort — how were these added?
- Existing canonical-authoring tools (template_retriever has
  `save_template`-style? grep for it)
- IA Full Spec phases relating to canonical authoring (Phase 28 was
  role-based migration; others?)
- Web search for "robotics task corpus" / "robot scene benchmarks
  2026" — sources to mine

**Output:** ~500 LOC MD with:
- Recommended canonical-creation pipeline (manual + LLM-drafted +
  autonomous-verified)
- Yrkesroll source list (which roles to target next, why)
- Estimated rate: how many new canonicals/week is realistic?
- Cron-friendly subset: which steps can be autonomous, which need
  human review?

### Q7. Autonomous-execution dependency graph

**Question:** Lay out a dependency graph of work items that the
autonomous cron can execute over weeks. What's the optimal
sequence?

**Sub-questions:**
- Inputs: outputs of Q1-Q6 (decisions, schemas, tools to build)
- Each work item: task description, agent type (Sonnet / Opus /
  Gemini), input artifacts, output artifacts, verification
  command, estimated duration, dependency edges
- Halting criteria: when does the autonomous loop stop? (e.g., all
  templates standardized, retrieval hit-rate ≥85%, N new canonicals
  verified)
- Parallelizability: which tasks can run in parallel? Which serialize?
- Failure recovery: if a task fails, who decides whether to retry,
  re-author, or escalate to human?

**Agent investigates:**
- Existing autonomous cron infrastructure (Kit Supervisor §18-20 in
  Spec 2; the `cron_*.py` files in scripts/qa/)
- Task graph patterns in industry (DAG-based workflow engines like
  Airflow, Prefect)
- The Phase 33-43 workflow infrastructure (LANDED but unwired per
  the L1/L2/L3 audit)

**Output:** ~700 LOC MD with:
- Task graph as YAML/JSON (~50-100 tasks)
- Optimal sequence with dependency edges
- Per-task agent classification + verification command
- Halting + failure-recovery rules
- Time estimate: total weeks of autonomous compute

### Q8. Iterative-retrieval Claude-Code-style flow design

**Question:** Specifically, what would iterative retrieval look like
if we copy Claude Code's pattern? Concrete pseudocode + tool API.

**Sub-questions:**
- Claude Code pattern (publicly documented): "tool: glob/grep/read
  → reason → tool again" — agent iterates until confident
- For our use case: "tool: list_canonical_categories → narrow_by_
  intent → retrieve_top_k → evaluate_candidates → execute_or_refine"
- API for "the LLM asks for more": should it be a `refine_retrieval`
  tool that returns more candidates? Or a `re_search_with_hint`?
- How does this play with hard-instantiate (today's deterministic
  short-circuit)?
- Latency budget: iterative retrieval adds round-trips. Acceptable?

**Agent investigates:**
- Claude Code's public docs + blog posts (web search)
- Compare iterative-retrieval research papers 2024-2026 (RAG
  variations)
- Latency budgets in our existing orchestrator

**Output:** ~400 LOC MD with:
- Pseudocode for iterative-retrieval flow
- New tool APIs needed
- Cost vs. benefit estimate (latency, accuracy)
- A/B test plan: iterative vs single-shot on N prompts

---

## 3. Research execution sequence

### Phase 1 — Parallel discovery (Sonnet agents, ~30 min wall time)

Dispatch Q1, Q2, Q3 in parallel. Each is independent. Each produces
a ~500-LOC MD report.

### Phase 2 — Parallel deep-dive (Sonnet, ~30 min wall time)

After Phase 1 lands, dispatch Q4, Q5, Q6 in parallel.

### Phase 3 — Synthesis (Opus, ~15 min wall time)

After Phase 2 lands, dispatch Q7 and Q8. Both depend on Q1-Q6
outputs being readable. These are synthesis tasks that need cross-
report reasoning — Opus, not Sonnet.

### Phase 4 — Final spec authoring (Opus, ~30 min wall time)

After Q1-Q8 land, ONE Opus synthesis task: write the final
implementation spec + autonomous-execution plan based on all 8
research outputs.

Total research wall time: ~2 hours (most agents in parallel). User
reviews + iterates.

---

## 4. Output artifacts

### From research phase (this spec drives)
- `docs/research/2026-05-15-q1-flow-architecture.md`
- `docs/research/2026-05-15-q2-canonical-format.md`
- `docs/research/2026-05-15-q3-retrieval-quality.md`
- `docs/research/2026-05-15-q4-template-drift.md`
- `docs/research/2026-05-15-q5-gemini-integration.md`
- `docs/research/2026-05-15-q6-canonical-pipeline.md`
- `docs/research/2026-05-15-q7-task-graph.md`
- `docs/research/2026-05-15-q8-iterative-retrieval.md`

### From synthesis (after research)
- `docs/specs/2026-05-XX-canonical-flow-standardization-spec.md`
  (the final spec)
- `docs/specs/2026-05-XX-autonomous-execution-plan.md` (the
  multi-week task graph)
- `config/cron_task_graph.yaml` (the machine-readable plan)

---

## 5. Constraints

- **Research agents do NOT modify code.** Read-only audits +
  research reports.
- **Cite file paths + line numbers** for every code claim.
- **Cite URLs** for every web-research claim.
- **Each agent stays under 1500 lines** of report output.
- **No speculative future-features.** What's empirically true today
  + what's needed next, not "could be cool to have."
- **Honesty Charter applies** (per Phase 18c) — no inflated claims,
  no overcounting of "what exists."

---

## 6. Success criteria for the research phase

The research phase succeeds if:

1. ✅ All 8 research reports land + are readable in one sitting
   (~3 hours user reading time)
2. ✅ The final-spec author (Opus) can write a coherent
   implementation spec from the 8 reports without further
   investigation
3. ✅ The autonomous-execution plan has ≥30 concrete tasks each
   with agent-type, verification command, dependency edges
4. ✅ Gemini integration plan has a concrete day-by-day burndown
   that uses ~1000 SEK in 10 days
5. ✅ At least 3 of the 8 research reports recommend a CONCRETE
   change to the codebase (not just analysis)

---

## 7. Open questions for the user

Before dispatching agents, confirm:

1. **Conceptual model** (§0) — canonicals as highest abstraction,
   tools as L1/L2/L3 below. Correct, or refine?
2. **Mode preference for Q1** — do you have a gut preference for
   iterative-explore (B) vs prescribed-catalog (A) vs hybrid (E)?
3. ~~Gemini scope for Q5~~ **Resolved 2026-05-15:** Gemini is a
   tool-consumer (end-user LLM driving Isaac Assist), not a research
   helper. Q5 reframed accordingly.
4. **Yrkesroll priorities for Q6** — any specific roles to target
   first?
5. **Halting criteria for Q7** — when should the autonomous loop
   stop? (e.g., a quality target like "85% retrieval hit-rate" or
   a quantity target like "all 321 templates standardized" or a
   time target like "stop after 6 weeks")
6. **Approval cadence** — should the autonomous cron pause for
   human review at named checkpoints, or run continuously with
   reports?

---

## 8. After /compact (user's next step)

User compacts this conversation. New session begins. The next
session should:

1. Read this spec (`docs/specs/2026-05-15-research-spec-flow-
   canonicals-autonomous.md`) as the brief
2. Read the Opus reviews from 2026-05-13:
   - `docs/research/2026-05-13-specs-2-3-4-review.md`
   - `docs/research/2026-05-14-l-levels-discovery-audit.md`
   - `docs/research/2026-05-11-composition-research-report.md`
   - `docs/research/2026-05-14-canvas-ux-research.md`
3. Dispatch Phase 1 research agents (Q1, Q2, Q3 in parallel)
4. Wait for completion, dispatch Phase 2 (Q4, Q5, Q6)
5. Dispatch Phase 3 synthesis (Q7, Q8)
6. Dispatch Phase 4 final-spec authoring
7. Present everything to user for approval before any code changes

The conversation history that survives /compact should include:
- The conceptual model (§0)
- The 8 research questions (§2)
- The dispatch sequence (§3)
- The success criteria (§6)
