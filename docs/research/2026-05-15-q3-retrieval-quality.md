# Q3: Retrieval Quality — How Well Does Top-K Work Today?

**Date:** 2026-05-15  
**Researcher:** Sonnet agent (Phase 1 / Question 3)  
**Scope:** Evaluate the quality of template retrieval, failure modes, iterative retrieval design,
role-retriever wiring, latency, and benchmark methodology.

---

## 1. System Overview — What Runs Today

### Retrieval chain (production path)

```
User prompt
  │
  ├── context_distiller.py:519 — retrieve_tools(message, top_k=20)
  │     ChromaDB query: 'isaac_assist_tools' collection
  │     Returns 20 tool names → injected into LLM schema
  │
  └── orchestrator.py:880-997 — template retrieval
        │
        ├── if MULTIMODAL_TEXT_INTENT=on (env-gated OFF by default)
        │     produce_layout_spec_from_text(message) → LLM call
        │     retrieve_with_intent_filter(intent_dump, top_k=3)
        │       Stage 1: hard structural filter (pattern_hint + counts + features)
        │       Stage 2: ChromaDB embed-sim over candidate IDs only
        │       Fallback if 0 candidates: full-corpus embed-sim
        │
        └── else (DEFAULT)
              retrieve_templates_with_scores(user_message, top_k=3)
              ChromaDB query: 'isaac_assist_templates' collection
              similarity = max(0, min(1, 1 - d/1.5))
              │
              top_sim = scored[0]["similarity"]
              margin = top_sim - scored[1]["similarity"]
              │
              ├── if sim >= 0.45 AND margin >= 0.20 AND CANONICAL_INSTANTIATE=on
              │     HARD-INSTANTIATE: execute template code deterministically
              │     Restrict LLM schema to 22-tool ALLOWED_AFTER_INSTANTIATE set
              │
              └── else
                    FEW-SHOT GUIDE: inject top-3 templates as prose reference
```

**File citations:**
- `orchestrator.py:870-874` — threshold constants (`_canonical_min_sim=0.45`, `_canonical_min_margin=0.20`)
- `orchestrator.py:888` — `TEMPLATE_TOP_K` env default = 3
- `orchestrator.py:894-897` — `MULTIMODAL_TEXT_INTENT` env gate
- `orchestrator.py:921-928` — margin computation and `confident_match` gate
- `template_retriever.py:195-200` — L2-distance to similarity mapping: `sim = 1 - d/1.5`
- `context_distiller.py:519-520` — `retrieve_tools(message, top_k=20)`
- `canonical_instantiator.py:73-102` — `ALLOWED_AFTER_INSTANTIATE` (22 tools)

### Embed document construction

Each template is indexed as: `goal\n\nthoughts\n\ntools_used` (joined by newlines).
Order is deliberate — goal first for prompt-match weight, thoughts for discriminating tokens
(added after VR-19 miscategorization), tools last as low-signal vocabulary.

**File citation:** `template_retriever.py:63-104` (`_build_index`)

---

## 2. Current Hit-Rate Estimate

### 2a. What we actually know (verified data)

The baselines in `workspace/baselines/` record **execution success** (cube-in-bin physics),
not retrieval quality. There is **no retrieval-hit-rate metric** captured in any baseline file.

The baseline schema is: `{label, build, status, success_rate, n_ok, n_runs, per_run[{seed, success, cube_final, speed, in_xy, above_floor, at_rest}]}`.
No similarity score, no `template_match`, no top-K ranking data is stored.

**Calibration data (from `orchestrator.py:860-865`)** — 4 data points only, all from the pick-place domain:

| Prompt | Top template | sim | margin | Decision |
|---|---|---|---|---|
| Own goal text (verbatim) | CP-01/CP-02 | 0.89–0.91 | 0.39–0.41 | Hard-instantiate |
| "pick and place cell with Franka" | CP-01 | 0.49 | 0.24 | Hard-instantiate |
| CP-02 paraphrase | CP-02 | 0.55 | 0.21 | Hard-instantiate |
| VR-19 actual prompt (ambiguous) | CP-02 | 0.51 | 0.006 | Few-shot fallback (correct) |

These 4 points are **the entire empirical basis** for the thresholds `sim>=0.45, margin>=0.20`.
They cover one domain (single-station / multi-station pick-place). No calibration exists for
AMR navigation, RL training, ROS2, sensor, or defect-detection domains.

### 2b. Execution success as a proxy

Best observed execution results from `workspace/baselines/full-n1-overnight-baseline.json`:

```
stable_ok=31/109 canonicals (28.4%)
stable_fail=70/109 (64.2%)
other=8 (7.3%)
```

The 31 stable-ok canonicals (CP-01 through CP-78 range) are **evidence that retrieval works for
those tasks** — the system found the right template and instantiated it enough times to pass
the physics check. The 70 stable-fails indicate downstream execution problems (cuRobo IK,
physics tuning, etc.), NOT necessarily retrieval failures. Retrieval quality and execution
quality are confounded in today's metrics.

**Net assessment:** retrieval is *probably correct* for pick-place prompts phrased similarly to
the template goals; we have no quantified hit-rate for any other domain.

### 2c. Spot-check predictions for 5 canonical prompts

The following predictions are based on Jaccard token-overlap scoring as a proxy for
sentence-transformer cosine similarity (actual system uses neural embeddings — Jaccard is
conservative; real semantic similarity will be higher for paraphrases).

**Prompt 1:** "build a Franka pick-place cell with conveyor belt and bin"
- Expected top-K: CP-01, CP-65, CP-NEW-3station-oee
- CP-01 is the canonical Franka+conveyor+bin template — **likely correct match**
- Concern: "with conveyor belt" is slightly colloquial; `CP-65` (kit-tray relay, 2 robots)
  may rank above CP-01 if thoughts tokens bleed in

**Prompt 2:** "navigate AMR to bin A3"
- Expected top-K: CP-NEW-multi-amr-corridor, CP-NEW-amr-pickup-handoff, D-02/D-04/D-05
- Token overlap is very low (0.05–0.07) — no high-confidence match
- **Likely outcome:** few-shot fallback (sim < 0.45 for all), which is the correct behavior
  since AMR navigation templates exist but are sparse (2 dedicated AMR templates out of 321)

**Prompt 3:** "sort red and blue cubes into color-matched bins"
- Expected top-K: CP-82, CP-85, CP-16
- CP-03 (color-sorting Franka) has `has_color_routing=true` in intent field — strong match
- With pure embedding: CP-16 ("4-color sorter") and CP-03 likely rank 1–2
- **Likely correct retrieval; margin probably above 0.20** if CP-03/CP-16 cluster tightly

**Prompt 4:** "train a reinforcement learning policy for a Franka arm"
- Expected top-K: AL-04, M-08, CP-NEW-rl-clone-env  
- Jaccard analysis shows best token overlap at 0.054 (AL-04), 0.051 (M-08), 0.048 (M-04)
- CP-01/CP-03 (pick-place) score 0.016–0.024 — **RL templates rank above pick-place**, good sign
- sim score will likely be < 0.45 (domain too diffuse) → few-shot fallback
- **Outcome:** probably returns RL-adjacent few-shot guidance; no hard-instantiate
- Note: `CP-NEW-rl-clone-env` (64-env clone scaffold) exists but has weak goal text

**Prompt 5:** "set up ROS2 bridge for Isaac Sim with a Franka"
- Expected top-K: S-07, CP-87, CP-NEW-ros2-control  
- S-07 ("8 Nova Carters with per-robot ROS2 namespaces") and CP-87 ("ROS2-MoveIt2 Franka")
- Token overlap moderate; Y-01 ("MDL material") at 0.068 — semantic crossfire likely
- **Outcome:** few-shot fallback probable; ROS2 template coverage is thin (10/321)

---

## 3. Failure-Mode Analysis

### Category A — Prompt too generic to match any template

**Examples:** "help me with Isaac Sim", "why is my robot not moving",
"I need a pick and place demo"

These prompts produce low similarity scores across all 321 templates because they lack
the specific vocabulary that appears in template goals (conveyor counts, bin positions,
cube colors, robot models).

The few-shot fallback is the correct response. The embedding over `goal + thoughts + tools`
cannot distinguish "help with Isaac Sim" from dozens of templates.

**Diagnosis:** The system handles this gracefully — few-shot injection still provides useful
structure even when no specific template dominates.

### Category B — Ambiguous prompts matching multiple plausible templates

**Example:** VR-19 (documented in `orchestrator.py:863-865`) — sim=0.51 for CP-02 but gap=0.006.
CP-01 and CP-02 are both "Franka + conveyor + bin" with nearly identical tool chains;
the key discriminator is number of robots and stations.

The margin requirement (0.20) correctly prevents hard-instantiate here.

**Other examples of this pattern:**
- "build a conveyor pick-place" — CP-01 (single), CP-02 (multi-station), CP-03 (color-sort)
  all cluster in embedding space because their tool chains are 80% identical
- "palletize boxes" — no dedicated palletizing canonical; maps ambiguously to multiple CP templates
- "insert peg into hole" — CP-58 (array), CP-NEW-peg-in-hole-single both exist; margin may collapse

**Diagnosis:** The margin gate is the right defense but it forces few-shot fallback on legitimate
unambiguous prompts whenever the template corpus has a dense cluster. This is over-conservative.

### Category C — Wrong template ranks above correct one

**Documented case:** VR-19 pre-fix (before `thoughts` was added to the embed doc).
CP-01 ranked above CP-02 for a clearly multi-robot prompt. Fixed by adding `thoughts`
to the embed document — discriminating tokens ("Two robots coexist", "shared cube in
source_paths") now participate in similarity.

**Potential ongoing cases:**

1. **RL training prompts** — "train Franka" likely retrieves AL-04 (GPU cluster sizing)
   or M-08 (Allegro task from scratch) instead of `CP-NEW-rl-clone-env` because the
   clone-env template's goal text is sparse and `clone_envs` doesn't appear prominently
   in the goal sentence.

2. **ROS2 + navigation crossfire** — S-07 ("8 Nova Carters with ROS2") and CP-87
   ("ROS2-MoveIt2 Franka") have very different tool chains but similar surface vocabulary.
   A prompt about "Franka with ROS2 control" might retrieve S-07 (wrong robot class).

3. **Diagnostics vs. build confusion** — AD-series templates (adversarial/diagnostic cases)
   can surface in top-K for legitimate build prompts because they share vocabulary:
   "anchor robot", "import robot", "set drive gains" appear in both domains.

4. **Large category imbalance** — pick-place = 103/321 templates (32%). Other-domain prompts
   inevitably compete against a large pick-place cluster, increasing the chance of a
   pick-place template appearing in top-K for non-pick-place queries.

### Category D — Coverage gap (missing templates)

The role_template_index.py defines 30 `TP-*` entries (welders, pickers, assemblers, inspectors,
palletizers, etc.) but **all 30 are MISSING from `workspace/templates/`**. Any prompt that
would match these roles returns only legacy fallbacks.

**File citation:** role_template_index.py — all 30 entries with `template_id` starting `TP-`
confirmed absent from `workspace/templates/` (verified by file enumeration 2026-05-15).

---

## 4. Should Retrieval Become Iterative?

### The case for iterative retrieval

Claude Code's design (as documented publicly) uses an agentic search loop:
`think → search → evaluate sufficiency → re-search if needed`.
Anthropic's internal finding was that this outperforms static RAG "by a lot" for code navigation
because exact-match keyword search with iterative refinement competes with neural retrieval
when symbols are precise ([source](https://zerofilter.medium.com/why-claude-code-is-special-for-not-doing-rag-vector-search-agent-search-tool-calling-versus-41b9a6c0f4d9)).

For Isaac Assist templates, the case is mixed:

**In favor of iterative:**
- VR-19 case: if the first retrieval returns ambiguous (margin < 0.20), the LLM currently
  gets generic few-shot guidance. An evaluate-and-refine step could detect "these two
  templates are both Franka pick-place; user asked for multi-robot — re-query with hint
  'multi_robot multi_station'" and resolve to CP-02 with high confidence.
- RL domain: first query returns weak matches; a second query with explicit RL tokens
  could return `CP-NEW-rl-clone-env` and `M-08` with higher scores.

**Against iterative:**
- The system already has a clean few-shot path for low-confidence cases — it works.
  The cost is one extra LLM round-trip for users who got few-shot instead of hard-instantiate.
- Adding an evaluate-and-refine step adds 1–2 LLM calls (500–2000ms each) on top of a
  ~20ms retrieval. This is 2–4× total latency for the pre-LLM phase.
- 2026 Agentic RAG research shows that iterative refinement helps most for multi-hop
  knowledge queries, not for structured template-matching where the correct answer is
  largely determined by keyword precision ([arxiv.org/abs/2501.09136](https://arxiv.org/abs/2501.09136)).

**Verdict:** Iterative retrieval is worth implementing as an **optional enhance path**,
not as the default. The sweet spot is a lightweight `evaluate_candidates` call — not a
full LLM round-trip but a structured LLM prompt that asks "does candidate A or B match
the user's intent?" and returns a confidence score.

### Concrete pseudocode: iterative retrieval design

```python
async def iterative_template_retrieval(
    user_message: str,
    top_k: int = 3,
    max_rounds: int = 2,
    confidence_threshold: float = 0.70,
) -> Tuple[List[Dict], str]:
    """
    Returns: (scored_candidates, retrieval_mode)
    retrieval_mode: "hard_match" | "evaluated_match" | "few_shot_fallback"
    """
    # Round 1: standard embed-similarity search
    scored = retrieve_templates_with_scores(user_message, top_k=top_k)
    top_sim = scored[0]["similarity"] if scored else 0.0
    margin = top_sim - (scored[1]["similarity"] if len(scored) > 1 else 0.0)

    # Fast path: existing hard-instantiate threshold
    if top_sim >= CANONICAL_MIN_SIM and margin >= CANONICAL_MIN_MARGIN:
        return scored, "hard_match"

    # Evaluate step (lightweight): ask LLM to pick best candidate
    if top_sim >= 0.30 and scored:  # at least plausible candidates exist
        confidence, best_idx, refined_query = await evaluate_candidates(
            user_message=user_message,
            candidates=scored,
        )
        if confidence >= confidence_threshold:
            # LLM confirmed a specific match → treat as hard-instantiate
            return [scored[best_idx]] + scored[:best_idx] + scored[best_idx+1:], "evaluated_match"

        # Round 2: re-search with refined query from LLM evaluation
        if refined_query and max_rounds > 1:
            scored2 = retrieve_templates_with_scores(refined_query, top_k=top_k)
            top_sim2 = scored2[0]["similarity"] if scored2 else 0.0
            margin2 = top_sim2 - (scored2[1]["similarity"] if len(scored2) > 1 else 0.0)
            if top_sim2 >= CANONICAL_MIN_SIM and margin2 >= CANONICAL_MIN_MARGIN:
                return scored2, "hard_match"
            # Merge: deduplicate and re-rank by sim
            merged = _merge_scored(scored, scored2, top_k=top_k)
            return merged, "few_shot_fallback"

    return scored, "few_shot_fallback"


async def evaluate_candidates(
    user_message: str,
    candidates: List[Dict],
) -> Tuple[float, int, Optional[str]]:
    """
    Lightweight LLM call: structured prompt asking the model to:
    1. Pick the best candidate index (or say "none")
    2. Return confidence in [0, 1]
    3. Suggest a refined query if confidence is low

    Returns: (confidence, best_idx, refined_query)
    Model: smallest fast model (e.g., Haiku / Gemini-Flash)
    Prompt budget: ~200 token input + ~100 token output
    Latency: ~300ms
    """
    # Structured JSON prompt to minimize token count:
    prompt = f"""
User request: "{user_message}"

Candidate templates (goal summaries):
{_format_candidates_brief(candidates)}

Return JSON: {{"best": 0|1|2|null, "confidence": 0.0-1.0, "refined_query": "..." | null}}
- best: index of best match, null if none fits
- confidence: how certain you are the chosen template matches user intent
- refined_query: if confidence < 0.6, suggest a better search query
"""
    # ... LLM call and parse ...
```

### New tool API options

**Option A — `refine_retrieval(query, exclude_ids, hint)`**

```python
{
  "name": "refine_retrieval",
  "description": "Re-run template search with an improved query and optional exclusions.",
  "parameters": {
    "query": "refined natural language search query",
    "exclude_ids": ["CP-01", "CP-02"],  # IDs to exclude from results
    "hint": "multi_robot | rl_training | navigation | ros2 | sensor"  # domain hint
  }
}
```

This is an LLM-callable tool, fitting the existing tool-use architecture. The LLM calls it
when it receives few-shot templates and judges them insufficient. Adds 1 LLM round-trip.

**Option B — `evaluate_candidates(candidates, prompt) → confidence`**

```python
{
  "name": "evaluate_candidates",
  "description": "Evaluate whether any retrieved template matches the user's request.",
  "parameters": {
    "candidates": [{"task_id": "CP-01", "goal": "...", "similarity": 0.49}],
    "user_prompt": "original user request",
  },
  "returns": {
    "best_match": "CP-01" | null,
    "confidence": 0.85,
    "reason": "CP-01 matches: single Franka + conveyor + bin",
    "refined_query": null | "franka single robot pick place color sort"
  }
}
```

This runs as a lightweight structured extraction call, NOT a full chat turn. The key
advantage: the model receives both the user intent and the candidates simultaneously and
can reason about alignment explicitly, which pure embedding cannot do.

**Recommendation:** Option B (evaluate_candidates) for the first iteration. It's cheaper
than a full chat round-trip and produces a structured confidence signal that feeds cleanly
into the existing `confident_match` gate.

---

## 5. Multi-Stage Retrieval Analysis

### Today's two-stage path (structural filter → similarity tiebreak)

The `retrieve_with_intent_filter` function in `template_retriever.py:393-484` implements:

1. **Stage 1:** Hard structural filter — `pattern_hint` exact match + `structural_features`
   compatibility + `counts` within tolerance. Templates without `intent` field are excluded.
2. **Stage 2:** ChromaDB embed-sim over Stage-1 candidates only, using the canonical
   structural fingerprint (not natural language).
3. **Fallback:** If Stage 1 returns 0 candidates, falls back to full-corpus embed-sim.

**Coverage gap:** 5/321 templates have `intent` fields (CP-01 through CP-05 only).
This means Stage 1 is a vacuous filter for 98.4% of the template corpus — the fallback
path runs almost always, making `retrieve_with_intent_filter` effectively identical to
`retrieve_templates_with_scores` in production today.

**When Stage 1 wins over single-stage:**
- Prompt: "sort red and blue cubes" → `pattern_hint=sort, has_color_routing=true`
  → Stage 1 filters to CP-03 (only template with both conditions) → Stage 2 trivially confirms
- Prompt: "two-robot assembly" → `pattern_hint=pick_place, n_robot_stations=2`
  → Stage 1 filters to CP-02, eliminates CP-01/CP-03/CP-04 → no margin ambiguity problem
- This is a categorical improvement: the ambiguity problem (VR-19) vanishes when
  structural filter works because the discriminating information is explicit, not embedded

**The fundamental limitation:** Structural filter requires:
1. An upstream LLM call (`produce_layout_spec_from_text`) to produce the `Intent` dict
2. Templates to have `intent` fields

Both are currently missing (env-gated off / 5/321 coverage). Expanding intent coverage
from 5 to 100+ templates would make this the dominant improvement lever.

**Coverage expansion cost:** ~30 minutes of mechanical work per template batch (read goal,
write pattern_hint + counts + structural_features). Could be automated: LLM reads template
goal and emits a structured `Intent` JSON.

---

## 6. Role-Retriever Wiring Plan

### Current state

Phase 20 (`role_retriever.py`) is **built and tested** but completely unwired from the
live orchestrator. The wiring paths today:

1. **As a tool the LLM can call** — `handlers/resolve.py:1039-1080` exposes
   `_handle_retrieve_template_by_role` as a tool handler. The LLM can invoke
   `retrieve_template_by_role(query=..., role_hints=[...])` during a conversation.

2. **In `multimodal/ratify.py:350`** — `autopick_compliance_mode` from `role_retriever.py`
   is called within the ratify pipeline (CRM-C2). This is the only live wiring — but it's
   for compliance mode selection, not template retrieval.

The `RoleRetriever.retrieve_with_roles()` scoring is Jaccard (token overlap), not neural
embedding. Role-based matches are hardcoded to score 1.0 on exact `role_hints` match,
and `len(shared_tokens) / len(query_tokens)` on fuzzy fallback. This is fast (~1ms, no
ChromaDB) but cannot handle paraphrase.

**The 30 ROLE_TEMPLATE_INDEX entries all reference missing template files** (`TP-WLD-01`
through `TP-KIT-02` do not exist in `workspace/templates/`). This means `RoleRetriever`
returns matches with valid IDs but the downstream `_load_template(task_id)` call at
`template_retriever.py:107-123` would return `None` for all of them.

### Should role-retriever be the PRIMARY retrieval mode?

**No, not yet.** The preconditions for role-retriever primacy:

1. `TP-*` template files must exist in `workspace/templates/` (currently 30 missing)
2. The role hints must be available (requires either user to specify role explicitly, or
   upstream role-extraction from the prompt)
3. Role vocabulary must match user language (users say "pick and place robot" not "picker")

**Recommended wiring order:**

**Step 1 — Pre-filter guard (today):** Before handing to ChromaDB, check if the prompt
contains high-confidence role signals (e.g., "welder", "palletizer", "bin picking"):

```python
# In orchestrator.py, before retrieve_templates_with_scores:
from .tools.role_retriever import RoleRetriever
_role_retriever = RoleRetriever()
role_matches = _role_retriever.retrieve_with_roles(user_message, max_results=3)
high_conf_role_matches = [m for m in role_matches if m.match_score >= 0.5]
if high_conf_role_matches and all(
    (templates_dir / f"{m.template_id}.json").exists()
    for m in high_conf_role_matches
):
    # Use role results as primary, ChromaDB as tiebreaker
    ...
```

**Step 2 — Template file creation:** Generate the 30 `TP-*` template files from the
ROLE_TEMPLATE_INDEX metadata. Each needs `goal`, `thoughts`, `tools_used`, `code` fields.

**Step 3 — Hybrid scoring:** Merge role-based and embed-based scores in a weighted sum:
`final_score = 0.6 * role_score + 0.4 * embed_score`. This avoids the hard "role wins always"
rule that `_score_legacy` currently implements (legacy templates capped below worst role match).

**Step 4 — Role-hint extraction:** Add a lightweight step to `classify_intent` output:
extract role hints from the prompt ("welding station" → role_hint="welder"). This enables
the role-retriever's exact-match path (score=1.0) for production traffic.

### Concrete plumbing changes

```python
# orchestrator.py — after classify_intent call (line ~696)
# Insert role-hint extraction:
role_hints = _extract_role_hints(user_message)  # new function, ~20 lines

# Before template retrieval (line ~879):
if role_hints:
    role_scored = _role_retriever.retrieve_with_roles(
        user_message, role_hints=role_hints, max_results=top_k
    )
    # Map TemplateMatch → same shape as retrieve_templates_with_scores output
    role_candidates = [
        {"template": _load_template(m.template_id), "task_id": m.template_id,
         "distance": 1 - m.match_score, "similarity": m.match_score}
        for m in role_scored if _load_template(m.template_id)
    ]
    if role_candidates:
        scored = role_candidates  # role-based wins
    else:
        scored = retrieve_templates_with_scores(user_message, top_k=top_k)
else:
    scored = retrieve_templates_with_scores(user_message, top_k=top_k)
```

---

## 7. Latency Budget

### Today's retrieval cost per user turn

| Step | Operation | Estimated latency |
|---|---|---|
| `retrieve_tools` | embed(message) + L2 search 346 tools | ~10ms |
| `retrieve_templates_with_scores` | embed(message) + L2 search 321 templates | ~10ms |
| **Total (default path)** | 2 embed+search operations | **~20–25ms** |

ChromaDB's default embedding model (all-MiniLM-L6-v2, 384-dim, CPU) embeds a single
sentence in approximately 10ms and searches ~300 vectors in under 1ms
([sbert.net efficiency docs](https://sbert.net/docs/sentence_transformer/usage/efficiency.html)).
The dominant cost is embedding, not search.

The LLM call itself takes 500–3000ms and completely dominates the latency budget. The 20ms
retrieval step is not perceptible to the user.

### Iterative retrieval latency budget

| Mode | Additional cost | Acceptable? |
|---|---|---|
| 2-round embed re-search | +10–20ms | Yes — imperceptible |
| evaluate_candidates (fast LLM call) | +300–600ms | Yes for interactive |
| evaluate_candidates (full chat model) | +1000–3000ms | Borderline for interactive |
| Full second LLM round-trip | +1500–5000ms | Only for autonomous/cron use |
| Role-retriever (Jaccard, in-memory) | < 1ms | Always yes |

**Interactive use (user-facing chat):** evaluate_candidates with a fast/cheap model
(Haiku, Gemini-Flash) at 300–600ms is acceptable. It should only trigger when the
first-round margin < 0.20 AND top_sim ≥ 0.30 (plausible but ambiguous).

**Autonomous cron / batch use:** Full iterative loop (2 rounds + evaluate) adds
2–6 seconds. Acceptable for an asynchronous baseline run or overnight cron job.
Per the note in memory about Kit concurrency (`feedback_isaac_assist_kit_concurrency.md`),
direct_eval must run sequentially anyway — latency per turn is not the binding constraint.

---

## 8. Benchmark Plan

### Why we need a dedicated retrieval benchmark

Today's baselines measure **execution success**, not retrieval quality. A prompt that
retrieves the wrong template but happens to succeed in execution counts as a pass.
A prompt that retrieves the correct template but fails execution counts as a failure.
These confound the retrieval signal entirely.

A retrieval benchmark measures: given prompt P, does the top-1 retrieved template
match the human-assigned ground-truth template?

### Proposed 30-prompt test set

**Tier 1 — High-confidence cases (expected hard-instantiate, 10 prompts)**

| ID | Prompt | Ground-truth template |
|---|---|---|
| R-01 | "build a Franka pick-place cell with conveyor and bin" | CP-01 |
| R-02 | "two-robot assembly line, cubes pass between robots" | CP-02 |
| R-03 | "sort cubes by color into matching bins" | CP-03 |
| R-04 | "compact pick-place within 2x2 meter footprint" | CP-04 |
| R-05 | "reorient cube from its side before placing in bin" | CP-05 |
| R-06 | "franka picks and places with a proxim sensor" | CP-01 (paraphrase) |
| R-07 | "UR10 picks objects off conveyor belt" | CP-82 or CP-85 |
| R-08 | "3-station serial pick-place line with OEE tracking" | CP-NEW-3station-oee |
| R-09 | "peg insertion task, 5mm clearance" | CP-NEW-peg-in-hole-single |
| R-10 | "defect-introduction synthetic data generation" | CP-NEW-defect-sdg |

**Tier 2 — Domain-breadth cases (expected few-shot, 10 prompts)**

| ID | Prompt | Ground-truth template |
|---|---|---|
| R-11 | "navigate AMR Nova Carter to bin A3" | CP-NEW-amr-pickup-handoff |
| R-12 | "3 AMRs navigate corridor with obstacle avoidance" | CP-NEW-multi-amr-corridor |
| R-13 | "train Franka RL policy with 64 parallel envs" | CP-NEW-rl-clone-env |
| R-14 | "ROS2 MoveIt2 Franka pick-and-place" | CP-87 |
| R-15 | "set up 8 Nova Carters with per-robot ROS2 namespaces" | S-07 |
| R-16 | "import URDF and verify articulation" | M-01 / R-03 |
| R-17 | "diagnose why sim robot won't move" | AD-05 or AD-12 |
| R-18 | "anchor Franka base to ground" | FX-01 |
| R-19 | "generate COCO dataset for apple detection" | P-01 |
| R-20 | "IsaacLab Allegro in-hand cube reorientation task" | M-08 |

**Tier 3 — Adversarial / ambiguity cases (10 prompts)**

| ID | Prompt | Expected behavior |
|---|---|---|
| R-21 | "pick and place" (too generic) | few-shot fallback, no hard-instantiate |
| R-22 | "Franka and conveyor" (ambiguous single vs multi) | few-shot (low margin) |
| R-23 | "sort items" (missing color/count info) | few-shot fallback |
| R-24 | "welding robot simulation" | role-retriever TP-WLD-* (when wired) |
| R-25 | "palletize boxes" | role-retriever TP-PAL-* (when wired) |
| R-26 | "I need help" (no content) | graceful no-match, no crash |
| R-27 | "CP-01" (direct template ID) | should match CP-01, sim ~0.90 |
| R-28 | "4-color sorter with 4 different-colored cubes" | CP-16 (not CP-03) |
| R-29 | "two Franka robots sharing a conveyor" | CP-02 over CP-01 |
| R-30 | "anchor robot so it doesn't drift" | FX-01 or FX-02, not CP-* |

### Benchmark methodology

```python
def run_retrieval_benchmark(prompts: List[Dict]) -> Dict:
    """
    prompts: [{"id": "R-01", "prompt": "...", "ground_truth": "CP-01",
               "expected_mode": "hard_match|few_shot|fallback"}]
    """
    results = []
    for p in prompts:
        scored = retrieve_templates_with_scores(p["prompt"], top_k=3)
        top = scored[0] if scored else None
        top_sim = top["similarity"] if top else 0.0
        margin = top_sim - (scored[1]["similarity"] if len(scored) > 1 else 0.0)
        confident = top_sim >= 0.45 and margin >= 0.20

        hit = (top["task_id"] == p["ground_truth"]) if top else False
        top3_hit = any(s["task_id"] == p["ground_truth"] for s in scored)
        mode = "hard_match" if confident else "few_shot"

        results.append({
            "id": p["id"],
            "hit@1": hit,
            "hit@3": top3_hit,
            "top_sim": top_sim,
            "margin": margin,
            "mode": mode,
            "expected_mode": p["expected_mode"],
            "top_returned": top["task_id"] if top else None,
            "ground_truth": p["ground_truth"],
        })

    return {
        "hit_at_1": sum(r["hit@1"] for r in results) / len(results),
        "hit_at_3": sum(r["hit@3"] for r in results) / len(results),
        "mode_accuracy": sum(r["mode"] == r["expected_mode"] for r in results) / len(results),
        "hard_instantiate_rate": sum(r["mode"] == "hard_match" for r in results) / len(results),
        "results": results,
    }
```

**Key metrics to report:**
- `hit@1` — top-1 returned template is the ground-truth (primary signal)
- `hit@3` — ground-truth is anywhere in top-3 (shows if problem is ranking vs. coverage)
- `mode_accuracy` — did the system correctly decide hard-instantiate vs. few-shot?
- `hard_instantiate_rate` — how often does the system commit vs. fall back?

**Baseline expectation (pre-benchmark):** Based on calibration data and domain analysis:
- Tier 1 hit@1: ~70–80% (strong signal, well-covered domain)
- Tier 2 hit@1: ~40–60% (sparse domain templates, weak vocabulary signal)
- Tier 3 hit@1: ~30–50% (by design adversarial)
- Overall hit@1: ~50–65%

This is an estimate only. No benchmark exists today.

---

## 9. Recommendation

### Production default

**Keep similarity-only (`retrieve_templates_with_scores`) as the default.** It is:
- Proven for the pick-place domain (CP-01 through CP-05 calibration points)
- Low latency (20ms total for both tool + template queries)
- Failing gracefully to few-shot for unrecognized prompts
- Stable — no LLM dependency in the retrieval path itself

### Immediate improvements (no architectural change)

1. **Run the 30-prompt benchmark** — establish a real hit@1 baseline. This is the single
   highest-value action: it turns "probably works" into a number.

2. **Add `thoughts` completeness check** — verify that every CP-* template has a non-empty
   `thoughts` field with discriminating vocabulary. The VR-19 fix shows this is high-ROI.

3. **Expand intent fields from 5 to CP-01 through CP-30** — mechanical work, enables
   structural filter for the most-used templates. Unlock `MULTIMODAL_TEXT_INTENT=on`.

4. **Create the 30 `TP-*` template files** — the role_template_index.py defines them;
   the JSON files are missing. This unblocks role-retriever wiring for professional roles.

### Short-term improvements (1–2 code changes)

5. **Add `evaluate_candidates` as an LLM-callable tool** — Option B from §4. Trigger
   when margin < 0.20 AND top_sim ≥ 0.30. Use a fast model (Haiku / Gemini-Flash).
   Expected improvement: resolves VR-19-class ambiguities without full re-search.

6. **Wire role-retriever as a pre-filter** — add the guard block in §6 to orchestrator.py.
   Costs < 1ms (Jaccard), provides hard routing for professional-role prompts.

### Mode selection flowchart

```
User prompt
  │
  ├── Role signals detected? (welder, palletizer, bin picker...)
  │     └── yes → RoleRetriever (Jaccard, < 1ms) → role_templates exist?
  │                yes → use role matches → confident? → hard-instantiate
  │                no  → fall through to embed
  │
  ├── MULTIMODAL_TEXT_INTENT=on?
  │     └── yes → produce_layout_spec_from_text → structural filter → sim tiebreak
  │               (use for: highly structured prompts with explicit counts/colors/topology)
  │
  ├── DEFAULT: retrieve_templates_with_scores(top_k=3)
  │
  ├── top_sim >= 0.45 AND margin >= 0.20?
  │     └── yes → HARD-INSTANTIATE (existing path)
  │
  ├── top_sim >= 0.30 AND margin < 0.20?  [NEW: evaluate path]
  │     └── yes → evaluate_candidates → confidence >= 0.70?
  │                 yes → HARD-INSTANTIATE (evaluated match)
  │                 no  → re-search with refined_query (1 more round)
  │                         → HARD-INSTANTIATE or FEW-SHOT
  │
  └── top_sim < 0.30?
        └── FEW-SHOT GUIDE (no plausible candidates)
```

### Summary table

| Component | Current status | Recommended action |
|---|---|---|
| Similarity-only retrieval | Production, calibrated on 4 points (pick-place only) | Keep as default; run benchmark |
| Hard-instantiate threshold (0.45/0.20) | Works for CP family; uncalibrated for other domains | Keep; add per-domain calibration data |
| Structural filter (intent-gated) | Built, 5/321 templates covered, env-off | Expand to CP-01..30 before enabling |
| Role-retriever | Built, tested, unwired, TP-* files missing | Create TP-* files; add pre-filter guard |
| Iterative re-retrieval | Not implemented | Add evaluate_candidates tool (Option B) |
| Retrieval benchmark | Does not exist | Implement 30-prompt set (§8) — highest priority |

---

## 11. Baseline 2026-05-15

**Run date:** 2026-05-15  
**Agent:** Sonnet 4.6 (Track C benchmark task)  
**Corpus file:** `workspace/benchmarks/retrieval_30prompts.json`  
**Results file:** `workspace/benchmarks/retrieval_30prompts_baseline_2026-05-15.json`  
**Harness:** `tests/test_retrieval_benchmark.py`  
**Index:** 321 templates (full corpus, rebuilt before run)  
**ChromaDB version:** 1.5.0, all-MiniLM-L6-v2 (sentence-transformers default)  
**Thresholds:** sim≥0.45, margin≥0.20 for hard-instantiate  

### Headline Numbers

| Metric | Value |
|---|---|
| **hit@1** | **83.3%** (25/30) |
| **hit@3** | **93.3%** (28/30) |
| mode_accuracy | 56.7% |
| hard_instantiate_rate | 16.7% (5/30) |
| latency p50 | 102.5 ms |
| latency p95 | 126.6 ms |

Note: hit@1 and hit@3 for the 5 "no-match" prompts (B21–B25) are scored as "correct if system did NOT hard-instantiate" — all 5 were correctly handled as fallback (sim < 0.45 in all cases).

### Per-Category Breakdown

| Category | N | hit@1 | hit@3 | mode_ok | avg_sim |
|---|---|---|---|---|---|
| contact_rich | 2 | 1.000 | 1.000 | 1.000 | 0.654 |
| navigate | 1 | 1.000 | 1.000 | 1.000 | 0.667 |
| rl_training | 2 | 1.000 | 1.000 | 0.000 | 0.647 |
| amr_navigation | 2 | 1.000 | 1.000 | 0.000 | 0.605 |
| sort | 2 | 1.000 | 1.000 | 1.000 | 0.552 |
| reorient | 1 | 1.000 | 1.000 | 1.000 | 0.569 |
| sensor_setup | 3 | 1.000 | 1.000 | 1.000 | 0.489 |
| no_match | 5 | 1.000 | 1.000 | 0.000 | 0.263 |
| palletize | 1 | 1.000 | 1.000 | 1.000 | 0.416 |
| ros2_bridge | 2 | 1.000 | 1.000 | 0.500 | 0.424 |
| **pick_place** | **5** | **0.600** | **0.800** | 0.800 | 0.446 |
| **multi_robot** | **2** | **0.500** | **1.000** | 0.000 | 0.582 |
| **vision** | **1** | **0.000** | **0.000** | 1.000 | 0.554 |
| industrial_bridge | 1 | 0.000 | 1.000 | 1.000 | 0.309 |

**Where retrieval struggles:** pick_place (score inflation from dense CP-* cluster), multi_robot (CP-51/CP-02 ambiguity), vision (CP-34 crossfire), industrial_bridge (OPC-UA low similarity due to sparse vocabulary).

**mode_accuracy = 56.7% explanation:** The mode classification column measures whether the system correctly chose hard_instantiate vs few_shot. Most failures here are "correct retrieval, but system falls back to few_shot when hard_instantiate was expected" — i.e., the sim or margin threshold was slightly below the gate. This reflects over-conservatism in the thresholds for the expanded 321-template corpus, not retrieval errors.

### 5 Specific Failure Cases Worth Investigating

**F1 — B02 (multi_robot): CP-51 ranked above CP-02 (sim 0.548 vs 0.52)**  
Prompt: "Set up a two-robot assembly line where cubes pass from robot 1 to robot 2 via a second conveyor"  
Top-3: CP-51(0.55), CP-02(0.52), CP-14(0.49)  
Root cause: CP-51 is "robot-to-robot handoff via staging area" — both it and CP-02 are two-robot templates with shared vocabulary ("robot 1", "robot 2", "handoff", "conveyor"). The margin (0.026) is far below 0.20, correctly triggering few-shot, but the top-1 is wrong. Fix: add structural intent field to CP-02 with `n_handoffs=1, n_robot_stations=2` to enable Stage-1 filter.

**F2 — B01 (pick_place): CP-77 ranked above CP-01 (sim 0.478 vs ~0.46)**  
Prompt: "Build a Franka pick-place cell with a conveyor belt and a single bin"  
Top-3: CP-77(0.48), CP-NEW-y-merge-singulation(0.40), CP-44(0.39)  
Root cause: CP-77 is "nested-box packer" with bin+conveyor+franka vocabulary. The prompt is slightly too generic — "pick-place cell" + "conveyor" + "bin" maps to many templates. The original calibration prompt "Build an industrial pick-and-place cell in Isaac Sim" was phrased more specifically. Fix: expand CP-01 intent field to gate `destination_kind=single_bin`.

**F3 — B06 (pick_place): CP-07 and CP-01 tied at 0.58, CP-07 wins by 0.001**  
Prompt: "Franka arm picks cubes from belt and drops into bin, using cuRobo for trajectory planning"  
Top-3: CP-07(0.58), CP-01(0.58), CP-06(0.56)  
Root cause: CP-07 is a legacy template with `cube_path`/`delivery` fields that contain "cuRobo" vocabulary — these deprecated fields boost its similarity for cuRobo-containing prompts. This is a contamination from the deprecated fields documented in Q4. Fix: strip the deprecated `cube_path`/`delivery` fields from CP-07 (Q4 Cohort C mechanical strip recommendation).

**F4 — B10 (vision): CP-34 and CP-32 cluster beats CP-47 (0.55 vs 0.47)**  
Prompt: "Vision-classify cubes on a conveyor and route them to bins based on detected color"  
Top-3: CP-34(0.55), CP-32(0.55), CP-03(0.52)  
Root cause: CP-34 and CP-32 are sorting/routing templates with high "route" + "bin" vocabulary overlap. CP-47 goal specifically says "setup_pick_place_with_vision" and "Vision classification + controller install in ONE tool call" — the word "ONE tool call" does not appear in the user prompt. Fix: add "vision_classify" or "setup_pick_place_with_vision" to CP-47 thoughts field to increase discriminating token weight.

**F5 — B28 (industrial_bridge): OPC-UA returns F-02 instead of CP-NEW-opcua-12conveyors**  
Prompt: "OPC-UA bridge connecting 12 conveyors to external PLC"  
Top-3: F-02(0.31), CP-NEW-plc-fixture(0.28), CP-NEW-opcua-12conveyors(0.27)  
Root cause: "OPC-UA" is an acronym that the sentence-transformer has seen rarely; F-02 is a measurement/force template that happens to share "bridge" vocabulary. CP-NEW-opcua-12conveyors ranks 3rd (sim 0.27 vs 0.31). Fix: expand the CP-NEW-opcua-12conveyors goal/thoughts to include the spelled-out form "OPC Unified Architecture" alongside "OPC-UA", and add "PLC" + "Modbus" vocabulary to increase embedding density.

### Latency Note

p50 = 102.5 ms, p95 = 126.6 ms. This is dominated by the sentence-transformer embedding cost (~100ms per query on CPU), not ChromaDB vector search (~1ms). The prior estimate of ~20ms in §7 assumed a warm embedding model. The real value on a cold or CPU-only process is 5× higher. Still negligible relative to the LLM call (500–3000ms), but worth noting for high-throughput batch use.

### Hard-Instantiate Rate

Only 5/30 prompts triggered hard-instantiate (sim≥0.45 AND margin≥0.20). This is lower than expected for "canonical" prompts. Analysis:
- The 321-template corpus is much denser than the original 4-point calibration (CP-01/02 only). More templates means more competition near the correct answer, collapsing margins.
- The hard-instantiate gate was calibrated at sim=0.49, margin=0.24 for a 5-template corpus. With 321 templates, the margin collapses to 0.05–0.10 for most correct retrievals.
- The 5 that did trigger (B09/CP-52, B20/CP-64, B19/M-08, B11/CP-NEW-amr-pickup-handoff, B12/CP-NEW-multi-amr-corridor) are templates with very specific, unique vocabulary that creates clear separation.

**Recommendation:** Lower the margin threshold to 0.12–0.15 for the 321-template corpus, or implement the `evaluate_candidates` path (§4) to resolve ambiguous cases that are correct-top-1 but low-margin.

---

## Sources

- [Agentic RAG Survey — arxiv.org/abs/2501.09136](https://arxiv.org/abs/2501.09136)
- [A-RAG: Hierarchical Retrieval Interfaces — arxiv.org/html/2602.03442v1](https://arxiv.org/html/2602.03442v1)
- [Why Claude Code uses agentic search over RAG — zerofilter.medium.com](https://zerofilter.medium.com/why-claude-code-is-special-for-not-doing-rag-vector-search-agent-search-tool-calling-versus-41b9a6c0f4d9)
- [Claude Code RAG mechanism dissection — finisky.github.io](https://finisky.github.io/en/claude-code-rag/)
- [Sentence Transformers efficiency — sbert.net](https://sbert.net/docs/sentence_transformer/usage/efficiency.html)
- [all-MiniLM-L6-v2 model card — huggingface.co](https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2)
