# Task / Scenario Format Audit — 2026-05-09

Status: research note. Reviews three coexisting task formats — **canary tasks**
(T/CW/T4/M/AD/... markdown specs), **CP canonicals** (`workspace/templates/CP-*.json`),
and **persona scenarios** (`docs/qa/personas/*.md` × `docs/qa/tasks/*.md` cartesian)
— and proposes a layered consolidation.

---

## 1. What's actually in the repo

### 1.1 Canary task specs — `docs/qa/tasks/*.md`

327 markdown files, ~31 prefix families. Each is a single-turn or scripted-multi-turn
spec with this shape:

```markdown
# Task CW-30 [MEDIUM] — Set camera intrinsics for a perspective camera
**Persona:** common workflow
**Goal:** Configure /World/Camera with focal length 24mm and horizontal aperture 36mm.
**Starting state:** ... (one-line list)
**Success criterion:** snapshot-measurable bullet list
**Expected tool chain:** numbered tool calls (illustrative)
**Friction points:** adversarial injects (optional)
**Time budget:** N minutes
## Pre-session setup
```python
# seeding code that runs against Kit RPC before the agent sees the task
```

Family breakdown (by prefix count):

| Prefix | Count | Character |
|--------|-------|-----------|
| CW | 50 | "common workflow" — atomic, easy, expected-tool-chain shape |
| AD | 23 | attribute / API-schema / data-driven |
| VR | 18 | verification-required (form/function dual-gate seeded) |
| M | 17 | Maya (RL researcher persona) |
| T4 | 15 | tier-4 high-level intent (negotiate-then-build) |
| T | 14 | tier-3 hard adversarial (e.g. T-14 safety-case joint limits) |
| D | 14 | dialog / scripted-followup |
| S, P, K, J, ... | 10–12 each | persona-bound long tail |
| Singletons | 4 | CONSTRAINT-01, REORIENT-01, SORT-01, MULTIMODAL-01 |

The auto-judge (`scripts/qa/auto_judge.py`) and `direct_eval.py` parse:
- `**Goal:**` block → user query (full text, not first sentence)
- `**Success criterion:**` → rubric for LLM judge
- `**Expected tool chain:**` → tool-set overlap heuristic (illustrative, not gating)
- `## Pre-session setup` → `kit_tools.exec_sync` before the agent sees the prompt
- `## Scripted followups` → next-in-list user replies for T4 negotiation tasks

**Verification model:** LLM judge with snapshot injection (`ground_truth_judge.py`).
Stage state is read after the agent finishes; the judge gets the actual prim list +
attrs + transforms, not the agent's claims. Final score is a 5-criterion weighted
total (technical_accuracy 30%, actionability 25%, persona_calibration 20%,
response_economy 15%, hallucination_absence 10%).

### 1.2 CP canonicals — `workspace/templates/CP-*.json`

86 JSON files (CP-01..CP-86). Each is a **deterministic build template** with this
shape (from CP-01):

```json
{
  "task_id": "CP-01",
  "goal": "<one-paragraph user-facing description>",
  "tools_used": ["create_prim", "robot_wizard", "create_conveyor", ...],
  "thoughts": "<numbered list of non-obvious patterns the agent must know>",
  "code": "<25-100 line Python-ish tool-call script>",
  "settle_state": { "cubes": {...}, "conveyors": {...} },
  "failure_modes": ["8 paragraphs of what goes wrong if you skip a step"],
  "verify_args":   { "stages": [...], "cube_path": "/World/Cube_1" },
  "simulate_args": { "cube_path": "...", "target_path": "...", "duration_s": 180 },
  "verified_status": "build-spec-2026-04-XX; form-gate ✓; function-gate ✓",
  "verified_metrics": { ... }
}
```

The orchestrator's hard-instantiate path (`canonical_instantiator.py`) executes the
`code` field directly when `template_retriever` returns a similarity ≥ 0.45 with
margin ≥ 0.20 over the runner-up. Verification is dual-gate:
- **form-gate:** `verify_pickplace_pipeline` (structural: pick path, place path,
  controller closures, robot articulation root)
- **function-gate:** `simulate_traversal_check` (physics: does the cube
  actually arrive at the bin?)

Both gates are deterministic Kit RPC tool calls — no LLM in the verification loop.

The whole-suite runner (`function_gate_suite.py`) executes 15 canonicals
sequentially, prints pass/probe/fail counts, returns rc=0 only when all
required canonicals deliver. Probes (CP-05, CP-09, CP-12, ...) are excluded
from the pass count via the `expect_pass=False` flag.

### 1.3 Persona scenarios — `docs/qa/personas/*.md` × tasks

15 personas (Maya, Erik, Kenji, Sarah, Priya, ...), each a 1–2 paragraph
voice/mental-model sketch. The campaign harness (`launch_campaign.py`) does
persona × task cartesian product, spawning a Claude Code subprocess per pair:

- Persona subprocess role-plays the user (sends messages, reacts, gives up)
- FastAPI Isaac Assist service (port 8000) is the system under test
- Kit RPC (port 8001) hosts the actual stage
- Transcript JSONL is written; auto-judge scores against the 5-criterion rubric

A scenario file like `docs/qa/scenarios/conveyor_pick_place.md` glues these
together: it picks a *target_source* mode per persona, declares 8 binary
verify-checks (C1–C8), and lists anti-patterns to flag in tool-call traces.

---

## 2. What works — empirical signal-to-noise

Drawing on the master plan + memory + smoke-test history:

### 2.1 CP canonicals are the highest-signal format

- **Determinism:** same `code` field → same scene, run after run. Hard-instantiate
  bypasses LLM re-authoring entirely; only verify + simulate + reply have agency.
- **Dual-gate verification is automatable:** `function_gate_suite.py` runs 15
  canonicals end-to-end with no LLM in the loop. 39/40 PASS on CP-07..CP-46
  sweep (2026-05-08, 1997 build calls, 140 cubes).
- **Failure modes are localized:** when CP-09 fails, the JSON template
  `verified_status` field captures *why* ("cuRobo seed × narrow placement
  margin"). The format has a place for the post-mortem.
- **Authoring overhead is real but bounded:** "reverse-engineer flow" produces a
  CP from a working build session in ~1 hour. The master plan ships 33 of them in
  Sprint 2 alone (52 working canonicals total today).

### 2.2 Canary CW/T tasks are good for atomic coverage and honesty probes

- **CW-NN tasks are cheap to author** (15-line markdown), measure exactly
  one tool call's behavior, and survive direct-mode eval ($0.05/task vs ~$1
  for persona harness).
- **T-NN hard adversarial tasks** (T-14 joint-limit safety case, T-01
  ISO/TS 15066 probe) catch *honesty* failures the CP canonicals can't —
  they don't have a buildable scene as ground truth, they have a *claim
  pattern* the agent must avoid. The 5-criterion rubric (especially
  hallucination_absence at weight 10%) is the right tool for these.
- **VR-NN dual-gate tasks** are the bridge: VR-19 = "build CP-02 from
  scratch via natural language" — measures whether the LLM can reconstruct
  a canonical from prose alone. Higher-noise but ecologically realistic.

### 2.3 Persona harness has lower signal-to-noise than direct-eval

Per `feedback_isaac_assist_qa` memory: **persona-harness inflates success
via user-rescue loops**. The persona Claude subprocess politely re-asks
when the agent stalls, smooths over hallucinations with "could you clarify
which API you mean?", and generally drags failed sessions across the
finish line. Direct-mode (single user message → single agent reply →
ground-truth snapshot) is the production-realistic measurement.

Persona scenarios remain useful for:
- **Voice/calibration testing** (does the agent over-explain to Alex the
  hobbyist? Use Maya's vocabulary register? Stop hand-holding Thomas?)
- **Refusal/honesty under pressure** (T-01-style adversarial — "just give
  me yes or no" requires a persona to apply pressure)
- **Long-horizon dialog tasks** where multi-turn negotiation is the
  capability (T4-NN, the 15 high-level-intent specs)

But for **tool-chain reliability and structural correctness**, direct-mode
+ snapshot judging is dominant.

---

## 3. Recommendation — Layered format

Three layers, each owning a distinct measurement axis. Don't deprecate
anything; specialize each format to what it does best.

### Layer 1 — CP canonicals (deterministic capability ceiling)

**Role:** "Can the system, given a perfect prompt match, construct and
verify a known-good scene?" Answers: capability ceiling, regression
prevention, hard-instantiate retrieval substrate.

**Going forward:**
- Continue the 33-scenario industrial expansion (master plan Sprint 1–5)
- Add `function_gate_consistency.py` 5-run pass-rate fields to
  `verified_metrics` for stochastic canonicals
- Add a `match_examples` array to each CP — 3 prompt phrasings that
  *should* trigger hard-instantiate, plus 1 that should *not* (margin test)
- **Promote CP from JSON to "JSON + sibling .md"** — author-facing markdown
  with the prose goal/thoughts, JSON with the executable build. Agents
  read either; humans only read .md.

### Layer 2 — CW/AD/T atomic canary tasks (handler honesty)

**Role:** "Does each registered tool succeed honestly when called
deterministically?" Answers: handler-level coverage, silent-success
detection (per the 2026-04-18 audit), API drift detection.

**Going forward:**
- Keep the CW-NN format. 50 today is roughly right; expand to ~150 to cover
  the 344-handler tool_executor surface 1:1.
- Run via `direct_eval.py` only — drop persona harness entirely for
  CW tasks.
- **Add a `verify_tool_chain` JSON sibling** alongside each CW-NN.md that
  declares: which tool(s) MUST be in the trace; which MUST NOT (e.g.
  `run_usd_script` for atomic tasks — fallback anti-pattern); which scene
  invariants must hold post-call. This makes auto-judge deterministic for
  CW tasks instead of LLM-rubric.

### Layer 3 — T/T4/Persona dialog tasks (judgment + voice)

**Role:** "Does the system handle adversarial / open-ended / multi-turn
inputs honestly and in-character?" Answers: refusal calibration, vocabulary
register, multi-turn negotiation, hallucination under pressure.

**Going forward:**
- Keep T4-NN scripted-followup format — it's the only format that exercises
  the negotiation_clarification intent path.
- Keep T-NN hard adversarial tasks — they're the only format where
  "honest 'I don't know'" is the correct answer and there's no scene
  ground-truth to fall back on.
- **Acknowledge T4 stochasticity** (per memory — T4 needs 5-run / N-of-M,
  not 3-run triple-perfect). Bake that into the harness.
- Persona × task cartesian remains the harness; rubric stays 5-criterion
  weighted. But fold M-NN, K-NN, P-NN, etc. into a single `persona_id`
  field on the task .md rather than naming the task by persona prefix.

### Layer 0 — VR-NN bridge tasks (LLM-from-prose canonicals)

**Role:** "Given a prose description of a CP canonical, can the agent
reconstruct an equivalent scene that passes the same form-gate +
function-gate?" Answers: LLM tool-chain reliability under the same
verification standard as CP, but without a `code` field to copy from.

**Going forward:**
- Keep the VR-NN format — already the right shape (Goal in prose,
  pre-session-setup seeds, then dual-gate verify of agent's build)
- Pair every CP-NN with a VR-NN that targets the same scene; report
  `cp_pass_rate / vr_pass_rate` ratio per scene as the prose-fidelity
  metric. Today CP-02 (assembly line) has VR-19 as its prose pair; the
  pairing should be systematic, not ad-hoc.

---

## 4. Concrete proposals (next 2-3 sessions of work)

1. **Drop persona × CW cartesian** in `launch_campaign.py`. CW tasks are
   atomic; pairing them with 15 personas multiplies cost ~15× for
   no-additional-signal. Restrict persona harness to T/T4/D/M/K/J prefixes.
2. **Add `verify_tool_chain` JSON to CW-NN tasks** — 50 small siblings,
   each with `must_call`, `must_not_call`, `scene_postcondition`. Switch
   CW auto-judge from LLM rubric to deterministic checker.
3. **Author `match_examples` for all 86 existing CPs.** Serves dual
   purposes: hard-instantiate retrieval test substrate, and prose-pair
   seed for VR-NN scaling.
4. **Pair-up CP↔VR systematically.** For each CP-NN, write VR-NN' with
   the goal phrased as a fresh user prompt (no copying from `goal`
   field — that biases similarity scoring) and the same
   `simulate_args.target_path`. Run both nightly; track ratio.
5. **Promote `function_gate_consistency.py` to default for stochastic
   probes.** CP-09 / CP-12 / CP-14 / CP-15 are marked probes (single-
   run failure expected); 5-run Wilson CI gives the actual pass rate
   (per the existing `_stats.wilson` helper) instead of "did it pass
   once today?"

---

## 5. What to deprecate

- **The cartesian persona × CW.md product in campaign plans.** Use
  direct_eval for CW.
- **LLM-only auto-judge for snapshot-measurable tasks.** CW + AD +
  most VR tasks can be fully deterministic — `ground_truth_judge.py`
  with rule-based postcondition matching is enough; don't burn
  Gemini credits on "did `/World/Camera.focalLength == 24`?"
- **Hand-rolled scenarios in `docs/qa/scenarios/`.** The two files
  there (`conveyor_pick_place.md`, `conveyor_pick_place_build.md`)
  are now subsumed by CP-01 / VR-19. Move them to an archive folder
  rather than letting them rot as a third format.

---

## 6. Why a layered format beats unification

A single unified format would have to carry: prose goal, deterministic
build code, expected tool chain, scene postcondition, persona voice
expectations, scripted followups, friction injects, and dual-gate args.
That's a 10-field schema where every field is optional for some task
types — i.e. an unused-field zoo.

The layered model says: each *measurement intent* (capability ceiling,
handler honesty, voice/judgment, prose fidelity) gets a format optimized
for it. The cross-cutting concerns (snapshot-based ground truth, LLM
judge for unverifiable claims, Wilson-CI for stochastic outcomes) live
in shared `scripts/qa/` infrastructure that all four layers call.

The 2026-05-08 dual-gate insight (form + function = automated
verification) is the reason CP works at all — and CP's success is the
existence proof that **deterministic per-template verification** beats
LLM-judged campaigns. Push that pattern down to CW (via
`verify_tool_chain`), keep LLM judging only where ground truth is a
*claim pattern* not a scene state (T-NN), and the harness's signal-to-
noise improves at every layer.
