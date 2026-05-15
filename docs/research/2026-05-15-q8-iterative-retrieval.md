# Q8 — Iterative Retrieval: Claude-Code-Style Pattern Applied to Isaac Assist

**Date:** 2026-05-15
**Researcher:** Opus 4.7 (1M) synthesis agent (Phase 3 / Question 8)
**Prior:** `docs/research/2026-05-15-q1-flow-architecture.md`,
`docs/research/2026-05-15-q3-retrieval-quality.md`,
`docs/research/2026-05-14-l-levels-discovery-audit.md`
**Question:** What does iterative retrieval look like specifically if we copy
Claude Code's pattern? Concrete pseudocode + tool API + cost-benefit.

---

## 0. Bottom-Line Up Front

Iterative retrieval is **worth a narrow, plumbed-in experiment**. Concretely:

1. Keep today's hard-instantiate short-circuit untouched at `sim≥0.45 AND margin≥0.20`
   (`orchestrator.py:924-929`). It is the only retrieval path with calibration evidence
   (Q3 §2a, 4 data points) and it is faster than anything iterative.
2. Add a **single re-rank round** (Q3's `evaluate_candidates` proposal) gated on the
   ambiguity band `0.30 ≤ sim < 0.45 OR margin < 0.20`. Use a fast model
   (Haiku/Gemini-Flash) at 300-600 ms.
3. Add **one** structural-refine round when `evaluate_candidates` returns
   `confidence < 0.7 AND refined_query is not None`. Cap total rounds at 2.
4. The remaining `sim < 0.30` cases fall through to today's few-shot path
   (Q1 §2 Mode B). No change.

This is a **bounded, single-loop CRAG-style refinement** (NOT a free-running
agentic loop like Claude Code itself uses). The reason is risk: today's
deterministic short-circuit is the highest-precision component the chat stack
has — a free-running iterative loop would damage that asset. The work plan is
~250 LOC + tests, and the A/B benchmark from Q3 §8 already defines the success
metric (`hit@1 ≥ +10pp` without breaking interactive latency).

The competing investment is **expanding intent-field coverage from 5 to ~100
templates** (Q3 §5, §9 recommendation 3) so the structural-filter path
(`MULTIMODAL_TEXT_INTENT=on`) actually fires in production. ROI comparison is
in §10 below: structural-filter expansion is cheaper and addresses the
documented coverage gap (`l-levels-discovery-audit.md` §3), whereas iterative
retrieval helps a different failure mode (ambiguity within a recognized cluster,
VR-19-class).

**Recommendation: build BOTH, structural-filter expansion FIRST.**

---

## 1. Claude Code Pattern Distilled

### 1.1 Source documentation

Primary sources (all 2026 material, post-cutoff documented):

- **Anthropic — How the agent loop works:**
  https://code.claude.com/docs/en/agent-sdk/agent-loop
  https://platform.claude.com/docs/en/agent-sdk/agent-loop
- **Anthropic — Tool-using agent tutorial:**
  https://platform.claude.com/docs/en/agents-and-tools/tool-use/build-a-tool-using-agent
- **VILA-Lab — Dive into Claude Code (systematic analysis):**
  https://arxiv.org/html/2604.14228v1 ; https://github.com/VILA-Lab/Dive-into-Claude-Code
- **Bits-Bytes-NN — Claude Code architecture analysis (2026-03):**
  https://bits-bytes-nn.github.io/insights/agentic-ai/2026/03/31/claude-code-architecture-analysis.html
- **DEV — Inside the agentic loop, how Claude Code decides what tool to call next:**
  https://dev.to/kevinzy189/claude-certified-inside-the-agentic-loop-how-claude-code-actually-decides-what-tool-to-call-next-3a1i
- **Augment — Claude Agent SDK, agent loops + tool calls:**
  https://www.augmentcode.com/guides/claude-agent-sdk-agent-loops-tool-calls
- **Temporal — Basic agentic loop with Claude + tool calling:**
  https://docs.temporal.io/ai-cookbook/agentic-loop-tool-call-claude-python
- **MindStudio — Beyond one-shot prompts: 5 Claude Code workflow patterns:**
  https://www.mindstudio.ai/blog/claude-code-agentic-workflow-patterns

### 1.2 The pattern, in one sentence

> The model reasons over the full conversation, picks one or more tools,
> receives the results as user turns, and reasons again — until the model
> itself stops calling tools, or a configured `max_turns` / `max_budget_usd`
> cap is hit (Anthropic agent-loop docs).

### 1.3 Tool-selection criteria (what makes a model choose `glob` vs `grep` vs `read`)

From the public documentation and the VILA-Lab dissection
(`arxiv.org/html/2604.14228v1` §3), the tool-selection signal is purely
**model-driven on the full conversation**:

- **No retrieval/learned-routing layer.** The model reads the system prompt,
  the tool definitions, and the conversation history and decides. There is no
  separate "classifier" picking which tool to dispatch. This is the architectural
  bet: the model is good enough that you don't need a routing layer.
- **Tool definitions take context budget.** Anthropic explicitly says be
  selective: every tool definition costs tokens. The `ENABLE_TOOL_SEARCH` flag
  loads tool schemas on demand precisely because attaching all tools to every
  turn dilutes the model's attention.
- **Read-only tools are cheap; mutating tools are gated by user permission.**
  Claude Code's permission system reflects an asymmetric cost: `glob` and `grep`
  are auto-approved; `Edit` and `Write` prompt the user. The model is therefore
  free to use observation tools liberally during the "build mental model" phase.

### 1.4 Stop conditions ("commit to action" trigger)

The pattern keeps looping while:
- The model itself decides to call another tool (no termination), OR
- `max_turns` is not reached (default unbounded), OR
- `max_budget_usd` is not reached.

It commits when:
- The model stops calling tools and emits a plain assistant message, OR
- A cap fires (treated as a hard stop, not a commit — the result is whatever
  partial state the model reached).

The MindStudio article and the Augment guide both highlight that production
deployments **always set a `max_turns` cap** (Augment recommends 3-5 for
interactive, 10-20 for autonomous), because without one, an under-spec'd prompt
can grind through dozens of read-only tool calls before producing output.

### 1.5 The "glob → grep → read until confident" pattern in practice

The DEV walkthrough (`dev.to/kevinzy189/...`) traces a typical Claude Code
session and shows the canonical pattern:

```
turn 1: glob("**/*.ts")                # scout: enumerate
turn 2: grep("function foo(", "**/*.ts")  # narrow: keyword
turn 3: read("src/utils/foo.ts:1-80")  # inspect: zoom
turn 4: read("src/utils/foo.ts:80-150")  # ... more zoom if needed
turn 5: edit(...)                       # commit: act
```

The pattern is **wide → narrow → deep → commit**, with the model deciding when
"deep enough" means it can predict the result of an `Edit` with high confidence.
The VILA-Lab paper §4.2 calls this **"epistemic sufficiency self-judgment"** —
the model has no exit signal other than its own judgment that the gathered
context is enough.

### 1.6 What we can directly steal vs. what we must adapt

| Claude Code feature | Steal directly? | Why / why not |
|---|---|---|
| Model-driven tool selection (no routing layer) | Partially | We already have a hybrid: intent classifier + tool retriever (top-20 by embedding similarity). Our schema is 344+ tools, way larger than Claude Code's ~12-15. We cannot expose all tools every turn. The intent classifier + tool retriever IS our analog of `ENABLE_TOOL_SEARCH`. |
| Read-only observation tools available always | Yes | `_ALWAYS_TOOLS` (`context_distiller.py:139-146`) already includes scene-summary, resolve-* family. This IS Mode B from Q1. |
| `max_turns` cap | Yes | Already in place: `MAX_ROUNDS=8` in `orchestrator.py:1189`. We're stricter than Claude Code default. |
| `max_budget_usd` cap | No | Not applicable; we run a single self-hosted model per turn, not per-call billed API. |
| Epistemic sufficiency self-judgment for stopping | Mostly no | Our orchestrator commits when the LLM stops emitting tool calls. We don't have an explicit "are you confident?" gate. But this is a DELIBERATE choice — empirically, forcing a "plan more" round regressed behavior (`orchestrator.py:1145-1158` — read-only round-0 gate was reverted; cited in Q1 §2 Mode B "What's missing"). |
| Wide→narrow→deep→commit at the **retrieval** layer | **YES — this is what Q8 asks us to design** | Today we do single-shot top-K=3 and either commit or fall to few-shot. The agentic-loop pattern says: list the categories, pick a category, retrieve narrowly, evaluate, commit or refine. |

---

## 2. Mapping to Isaac Assist's Use Case

### 2.1 The translation

Claude Code's `glob → grep → read → commit` maps to Isaac Assist as:

```
Claude Code        Isaac Assist
─────────────────  ─────────────────────────────────────────
glob("**/*.ts") →  list_canonical_categories() / list_intent_tags()
                   (cheap: returns the 8 intents × pattern_hint values
                   from role_template_index.py)

grep("foo")    →   retrieve_top_k(query, top_k=3)
                   (today's behavior — fast, ~10 ms)

read("foo.ts") →   evaluate_candidates(candidates, prompt)
                   (Q3 §4 Option B — fast-LLM look at the retrieved
                   candidates and judge fit)

edit(...)      →   execute_template_canonical()  OR
                   fall through to few-shot guide
                   (today's commit)
```

The key insight: **today we collapse "grep → read → commit" into a single
threshold gate** (`sim≥0.45 AND margin≥0.20`). That gate has high precision
when it fires (Q3 §2a calibration: 4/4 correct decisions on the 4 calibration
points) but low recall (it falls through to few-shot for everything outside the
CP-01..CP-05 family — Q3 §2b: only 31/109 stable_ok in the full overnight
baseline, and many of those 78 fails were probably retrieval-quality issues
disguised as execution issues).

The iterative refactor inserts the `evaluate_candidates` step **inside the
threshold gate**: if the retrieval result is ambiguous (in the band where the
threshold rejects), give a fast model a chance to look at the candidates
explicitly and either commit or request a refine.

### 2.2 Concrete pseudocode

```python
# Conceptual flow — to live in chat/tools/iterative_retriever.py

async def retrieve_iteratively(
    user_message: str,
    *,
    top_k: int = 3,
    max_refine_rounds: int = 1,
    evaluator: Callable[..., Awaitable[CandidateVerdict]] = evaluate_candidates,
    refiner: Callable[..., Awaitable[List[Dict]]] = refine_retrieval,
) -> RetrievalResult:
    """
    Bounded iterative retrieval. Designed to replace the single-shot
    similarity check at orchestrator.py:879-997 WHEN the single-shot fails
    the hard-instantiate gate but isn't a total miss.

    Contract:
      • If single-shot triggers hard-instantiate, returns immediately
        (no refinement, no extra latency).
      • Otherwise, runs up to (1 evaluate + max_refine_rounds refines).
      • Returns RetrievalResult with mode in {hard_match, evaluated_match,
        refined_match, few_shot_fallback}.
    """
    # === Round 0: single-shot (today's behavior) ===
    scored = retrieve_templates_with_scores(user_message, top_k=top_k)
    top_sim, margin = _top_and_margin(scored)
    rounds_used = 0
    history = [{"round": 0, "query": user_message, "top": _ids(scored),
                "top_sim": top_sim, "margin": margin}]

    if _passes_hard_gate(top_sim, margin):
        return RetrievalResult(scored=scored, mode="hard_match",
                               rounds=rounds_used, history=history)

    # === Are candidates even plausible? ===
    if top_sim < 0.30 or not scored:
        # Total miss: nothing for evaluator to chew on. Skip iteration.
        return RetrievalResult(scored=scored, mode="few_shot_fallback",
                               rounds=rounds_used, history=history)

    # === Round 1: evaluate ===
    verdict = await evaluator(candidates=scored, prompt=user_message)
    rounds_used += 1
    history.append({"round": 1, "type": "evaluate",
                    "best_idx": verdict.best_idx,
                    "confidence": verdict.confidence,
                    "refined_query": verdict.refined_query})

    if verdict.confidence >= 0.70 and verdict.best_idx is not None:
        # Evaluator picked a winner — promote it to position 0
        promoted = _promote(scored, verdict.best_idx)
        return RetrievalResult(scored=promoted, mode="evaluated_match",
                               rounds=rounds_used, history=history)

    # === Round 2: refine (only if evaluator gave a refined query) ===
    if max_refine_rounds >= 1 and verdict.refined_query:
        refined_scored = await refiner(
            query=verdict.refined_query,
            exclude_ids=[s["task_id"] for s in scored
                         if s["similarity"] < 0.30],
            hint=verdict.intent_hint,
            top_k=top_k,
        )
        rounds_used += 1
        history.append({"round": 2, "type": "refine",
                        "query": verdict.refined_query,
                        "top": _ids(refined_scored)})

        # Merge: union(round-0 keep-set, round-2 results), rerank by sim
        merged = _merge_and_rerank(scored, refined_scored, top_k=top_k)
        top2, margin2 = _top_and_margin(merged)

        if _passes_hard_gate(top2, margin2):
            return RetrievalResult(scored=merged, mode="refined_match",
                                   rounds=rounds_used, history=history)

        # Refined still ambiguous? One more evaluator look — but no
        # MORE refines. (Bounded: total LLM calls ≤ 2.)
        verdict2 = await evaluator(candidates=merged, prompt=user_message)
        rounds_used += 1
        history.append({"round": 3, "type": "evaluate",
                        "best_idx": verdict2.best_idx,
                        "confidence": verdict2.confidence})
        if verdict2.confidence >= 0.70 and verdict2.best_idx is not None:
            return RetrievalResult(scored=_promote(merged, verdict2.best_idx),
                                   mode="evaluated_match",
                                   rounds=rounds_used, history=history)
        return RetrievalResult(scored=merged, mode="few_shot_fallback",
                               rounds=rounds_used, history=history)

    return RetrievalResult(scored=scored, mode="few_shot_fallback",
                           rounds=rounds_used, history=history)


def _passes_hard_gate(top_sim: float, margin: float) -> bool:
    return (top_sim >= CANONICAL_MIN_SIM      # 0.45
            and margin >= CANONICAL_MIN_MARGIN)  # 0.20


def _top_and_margin(scored: List[Dict]) -> Tuple[float, float]:
    if not scored:
        return 0.0, 0.0
    top = scored[0]["similarity"]
    second = scored[1]["similarity"] if len(scored) > 1 else 0.0
    return top, top - second


def _promote(scored, idx):
    return [scored[idx]] + scored[:idx] + scored[idx+1:]


def _merge_and_rerank(a, b, top_k):
    """Union by task_id; if duplicate keep MAX similarity; resort desc."""
    by_id: Dict[str, Dict] = {}
    for entry in (*a, *b):
        tid = entry["task_id"]
        prev = by_id.get(tid)
        if prev is None or entry["similarity"] > prev["similarity"]:
            by_id[tid] = entry
    merged = sorted(by_id.values(),
                    key=lambda e: e["similarity"], reverse=True)
    return merged[:top_k]
```

Key design choices:

1. **Round 0 is the existing single-shot retrieval.** No behavior change for
   the high-confidence path. Latency parity for `sim≥0.45 AND margin≥0.20`.
2. **Evaluate-then-refine, not refine-then-evaluate.** Refinement requires
   a refined query, which only the evaluator can produce. Refining blind
   ("retry with same query") is pointless.
3. **Bounded total LLM calls ≤ 2.** Worst case: evaluate → refine → evaluate.
   No third refine. Q3 §7 latency table shows 2× fast-model calls keeps total
   under ~1.2 s for interactive use.
4. **Merge round-0 and round-2 results.** Don't throw away round-0 candidates
   — refinement can drift. Merge + rerank by similarity is the safe operation.
5. **Exclude only the truly-low candidates.** If round-0 returned CP-01 at 0.49
   and AD-12 at 0.18, exclude AD-12 (`<0.30`) from the refine call but keep
   CP-01 — refinement is supposed to *add* signal, not erase round-0 work.

### 2.3 What this is NOT

To be honest about the boundaries:

- **NOT a free-running iterative loop.** Claude Code's loop runs until the
  model stops emitting tool calls (`max_turns` is a safety net, not the
  primary terminator). Our loop is **hard-capped at 2 LLM calls per retrieval
  decision**. This is intentional: we are inserting iterative retrieval into
  a chat turn that already has up to 8 tool-calling rounds downstream
  (`orchestrator.py:1189` `MAX_ROUNDS=8`). Adding an unbounded iterative loop
  to retrieval *and then* running 8 more LLM rounds on the result would
  destroy the interactive latency budget.
- **NOT replacing the few-shot fallback.** When iterative retrieval terminates
  at `few_shot_fallback`, the existing `format_for_prompt` path runs unchanged.
- **NOT a multi-hop retrieval system.** The FAIR-RAG style "decompose
  question → retrieve evidence A → retrieve evidence B given A → synthesize"
  (`arxiv.org/html/2510.22344v1`) is overkill for template-matching, where
  the user's request decomposes into "find the one best template" (not
  "find evidence chain A→B→C").

---

## 3. New Tool API Specifications

### 3.1 Decision: orchestrator-internal helpers, NOT LLM-callable tools

Three candidates:

| Capability | Place in tool registry? | Justification |
|---|---|---|
| `list_canonical_categories(filter?)` | **No, helper** | The orchestrator already knows the intent + role taxonomy. Exposing it as an LLM-callable tool just dilutes the tool schema. The category listing is consumed by the evaluator's prompt, not by the chat LLM. |
| `evaluate_candidates(candidates, prompt)` | **No, helper invoked internally** | Trade-off: making this an LLM-callable tool would let the chat LLM call it mid-conversation when it suspects the few-shot block was wrong. But (a) we already established (`l-levels-discovery-audit.md` §3) that the chat LLM has 344+ tools, attention dilution is the dominant pathology, and (b) this is a retrieval-internal decision, the chat LLM doesn't have privileged information about ChromaDB scores. Keep it internal. |
| `refine_retrieval(original_query, exclude_ids, hint)` | **No, helper** | Same as above. The chat LLM doesn't need to know we re-ran ChromaDB. |

The strong argument *against* LLM-callable: Q1 §2 Mode B already documents the
"the LLM may not call observation tools opportunistically" failure mode. Asking
the chat LLM to *also* manage retrieval refinement is a much bigger ask
(epistemic-sufficiency self-judgment about retrieval candidates it didn't even
see). The conservative architecture is to have iterative retrieval be a
deterministic orchestrator routine, with a fast LLM (Haiku/Flash) inside it as
the **judge**, not as the **planner**.

This matches Anthropic's own choice in the agentic-loop docs: the loop is
deterministic Python code; the model makes the in-loop decisions but doesn't
choose to enter or exit the loop.

### 3.2 Tool specifications (JSON schemas, for the helpers' Python signatures)

#### `list_canonical_categories(filter: Optional[str]) -> List[Category]`

```json
{
  "name": "list_canonical_categories",
  "kind": "orchestrator_helper",
  "description": "Enumerate canonical-template categories (intent × pattern_hint × role). Used by the evaluator's prompt builder to give the fast model a vocabulary for its refined_query.",
  "parameters": {
    "type": "object",
    "properties": {
      "filter": {
        "type": "string",
        "enum": ["intent", "pattern_hint", "role", null],
        "description": "Restrict listing to one axis. null = return all three axes."
      }
    },
    "required": []
  },
  "returns": {
    "type": "array",
    "items": {
      "type": "object",
      "properties": {
        "axis": {"enum": ["intent", "pattern_hint", "role"]},
        "key": {"type": "string", "examples": ["patch_request", "pick_place", "welder"]},
        "n_templates": {"type": "integer"},
        "sample_task_ids": {"type": "array", "items": {"type": "string"}, "maxItems": 3}
      }
    }
  },
  "implementation_notes": [
    "Reads from role_template_index.py + workspace/templates/*.json metadata.",
    "Cached after first call; templates don't change at runtime.",
    "Latency: <1ms (in-memory)."
  ]
}
```

#### `evaluate_candidates(candidates, prompt) -> CandidateVerdict`

```json
{
  "name": "evaluate_candidates",
  "kind": "orchestrator_helper",
  "description": "Fast-LLM judge: pick the best candidate index, score confidence, and optionally propose a refined query.",
  "parameters": {
    "type": "object",
    "properties": {
      "candidates": {
        "type": "array",
        "minItems": 1,
        "maxItems": 5,
        "items": {
          "type": "object",
          "required": ["task_id", "goal", "similarity"],
          "properties": {
            "task_id": {"type": "string"},
            "goal": {"type": "string", "description": "Template goal text (NOT thoughts — keep prompt tight)"},
            "similarity": {"type": "number", "minimum": 0.0, "maximum": 1.0},
            "intent": {"type": "string"},
            "pattern_hint": {"type": "string"}
          }
        }
      },
      "prompt": {"type": "string", "description": "Original user request"},
      "model": {
        "type": "string",
        "enum": ["claude-haiku-4.7", "gemini-2.5-flash"],
        "default": "claude-haiku-4.7",
        "description": "Fast-model selector. Haiku for stability, Flash for cost."
      }
    },
    "required": ["candidates", "prompt"]
  },
  "returns": {
    "type": "object",
    "required": ["best_idx", "confidence", "reason"],
    "properties": {
      "best_idx": {
        "type": ["integer", "null"],
        "description": "Index into the input candidates array, or null if none fit."
      },
      "confidence": {
        "type": "number",
        "minimum": 0.0,
        "maximum": 1.0,
        "description": "0..1; ≥0.70 triggers evaluated_match commit."
      },
      "reason": {
        "type": "string",
        "maxLength": 200,
        "description": "One-sentence justification, logged for audit."
      },
      "refined_query": {
        "type": ["string", "null"],
        "description": "Suggested re-search query when confidence < 0.70. Null when no refinement is useful (e.g. candidates are all wrong-domain)."
      },
      "intent_hint": {
        "type": ["string", "null"],
        "enum": ["multi_robot", "rl_training", "navigation", "ros2", "sensor", "vision_inspect", "color_routing", "palletize", null],
        "description": "Single-tag intent hint, passed to refine_retrieval to nudge candidate set."
      }
    }
  },
  "implementation_notes": [
    "Prompt budget: ~200 input + ~80 output tokens.",
    "Model temperature: 0 (deterministic).",
    "Output format: strict JSON. Use the model's structured-output mode.",
    "On parse failure, return CandidateVerdict(best_idx=None, confidence=0.0).",
    "Latency: 300-600 ms (Haiku) / 200-400 ms (Flash)."
  ]
}
```

The candidate-formatting MUST use `goal` only, not `thoughts + code`. Reason:
Q3 §2c notes that even with full embed-doc (goal+thoughts+tools), the top-3
fit comfortably in a tight prompt; including code blows the budget. The
evaluator's job is *intent alignment*, not code inspection.

#### `refine_retrieval(query, exclude_ids, hint, top_k) -> List[ScoredTemplate]`

```json
{
  "name": "refine_retrieval",
  "kind": "orchestrator_helper",
  "description": "Re-run ChromaDB embed-similarity search with an improved query, excluding low-relevance candidates from round 0.",
  "parameters": {
    "type": "object",
    "properties": {
      "query": {"type": "string", "description": "Refined search query from evaluate_candidates.refined_query"},
      "exclude_ids": {
        "type": "array",
        "items": {"type": "string"},
        "default": [],
        "description": "Task IDs to filter out of the result set."
      },
      "hint": {
        "type": ["string", "null"],
        "description": "Optional intent hint. When set AND the template corpus has intent fields, restricts search to matching intent. Else ignored."
      },
      "top_k": {"type": "integer", "default": 3, "minimum": 1, "maximum": 10}
    },
    "required": ["query"]
  },
  "returns": {
    "type": "array",
    "items": {
      "type": "object",
      "properties": {
        "task_id": {"type": "string"},
        "template": {"type": "object"},
        "distance": {"type": "number"},
        "similarity": {"type": "number"}
      }
    }
  },
  "implementation_notes": [
    "Backed by template_retriever.retrieve_templates_with_scores.",
    "When `hint` is set AND MULTIMODAL_TEXT_INTENT is wired with sufficient intent coverage, route through retrieve_with_intent_filter instead.",
    "Latency: ~10-20 ms (1 embed + 1 vector search)."
  ]
}
```

### 3.3 Why these are helpers, not tools — restated

The chat LLM does NOT need to know about retrieval-layer mechanics. From
Anthropic's docs (`platform.claude.com/.../agent-loop`): "Be selective with
tools. Every tool definition takes context space." We have 344+ handlers
(`project_isaac_assist_silent_success_audit.md`); adding 3 more retrieval
tools to the schema for the chat LLM costs more attention than it saves.

The fast-LLM evaluator IS an LLM — but it is a **distinct, scoped call**
with a tight system prompt that does exactly one thing (score candidates).
It does not have the chat tool schema attached.

---

## 4. Decision Flow — State Machine

```
                                                  ┌─── confident_match (today's
                                                  │     gate: ≥0.45/≥0.20)
                                                  │
USER MESSAGE                                      │   → HARD-INSTANTIATE
   │                                              │     (Mode A, Q1 §2)
   ▼                                              │     [today; unchanged]
[classify_intent]   ──── unchanged ───┐           │
   │                                  │           │
   ▼                                  │           │
[negotiator gate]   ─── unchanged ────│ on COMPLEX │
   │                                  │  intent   │
   ▼                                  │           │
[turn_snapshot]                       │           │
   │                                  │           │
   ▼                                  │           │
[Kit context]                         │           │
   │                                  │           │
   ▼                                  ▼           │
[KB retrieval]            ┌──────────────────────────────────┐
   │                      │   ROUND 0  retrieve_top_k(msg)   │
   │                      └────────────────┬─────────────────┘
   ▼                                       ▼
[Template retrieval]      sim ≥ 0.45 AND margin ≥ 0.20? ──── YES ──→ HARD
   │                                       │ NO                        │
   │                                       ▼                           │
   │                          top_sim ≥ 0.30 AND scored? ── NO ─→ FEW-SHOT
   │                                       │ YES                       │
   │                                       ▼                           │
   │                      ┌──────────────────────────────────┐         │
   │                      │  ROUND 1  evaluate_candidates    │         │
   │                      │           (fast-model judge)     │         │
   │                      └────────────────┬─────────────────┘         │
   │                                       ▼                           │
   │                       confidence ≥ 0.70? ── YES ─→ EVALUATED ────→│
   │                                       │ NO                        │
   │                                       ▼                           │
   │                          refined_query is not None? ── NO ─→ FEW  │
   │                                       │ YES                       │
   │                                       ▼                           │
   │                      ┌──────────────────────────────────┐         │
   │                      │  ROUND 2  refine_retrieval       │         │
   │                      │           (ChromaDB re-search)   │         │
   │                      └────────────────┬─────────────────┘         │
   │                                       ▼                           │
   │                       sim ≥ 0.45 AND margin ≥ 0.20 ──── YES ──→   │
   │                       on merged set?                              │
   │                                       │ NO                        │
   │                                       ▼                           │
   │                      ┌──────────────────────────────────┐         │
   │                      │  ROUND 3  evaluate_candidates    │         │
   │                      │           (FINAL judge)          │         │
   │                      └────────────────┬─────────────────┘         │
   │                                       ▼                           │
   │                       confidence ≥ 0.70? ── YES ─→ EVALUATED ────→│
   │                                       │ NO                        │
   │                                       ▼                           │
   │                                   FEW-SHOT                        │
   │                                                                   ▼
   └─────────────────────────────────────────────────────────────[downstream:
                                                                    Mode A or B
                                                                    per Q1 §3]
```

### 4.1 The four terminal states

| Mode | Trigger | Downstream behavior |
|---|---|---|
| **HARD** (Mode A) | Round 0 single-shot ≥ thresholds | `execute_template_canonical` (today, unchanged) |
| **EVALUATED** (new) | Round 1 OR Round 3 verdict ≥ 0.70 | Same as HARD: `execute_template_canonical` on the verdict's `best_idx` template |
| **REFINED** (new) | Round 2 merged set ≥ thresholds | Same as HARD: instantiate top-1 of merged |
| **FEW-SHOT** (Mode B) | None of the above | `format_for_prompt` → patterns_text → tool-calling loop (today, unchanged) |

### 4.2 Negotiator interaction (per Q1)

Q1 §2 Mode D documents the existing negotiator path: when
`complexity==complex AND intent!=general_query AND STRATEGIC_NEGOTIATOR=on AND
not _prior_was_clarification`, the negotiator fires *before* template retrieval
and may emit clarification questions, which ENDS THE TURN. The next user reply
re-enters the orchestrator from the top.

**Iterative retrieval does NOT change this.** The negotiator gate
(`orchestrator.py:769`) runs unchanged. Iterative retrieval is the
replacement for `orchestrator.py:879-997` (the template-retrieval block),
which only runs if the negotiator did not stop the turn.

When does iterative help a "negotiator-cleared" turn? When the user has just
clarified ambiguity in a prior turn and the current turn's message embeds
both the original goal and the clarification. Today, that combined message
gets a fresh single-shot; iterative gives it one judge + one refine before
falling to few-shot.

### 4.3 "User explicitly says just try" — few-shot fallback

The brief mentions "Few-shot fallback if user explicitly says 'just try'."
This already exists implicitly: the iterative loop terminates at FEW-SHOT
whenever the conditions for HARD/EVALUATED/REFINED are not met. There is no
need for a special user-keyword override — the user's "just try" phrasing
naturally produces low retrieval similarity (it's a meta-instruction, not a
task description), so round 0's `top_sim < 0.30` branch fires immediately
and the loop bails out at zero LLM calls.

### 4.4 Rounds-by-mode latency (interactive vs cron)

| Mode | Round count | Total added latency vs today |
|---|---|---|
| HARD | 0 | 0 ms (no change) |
| FEW-SHOT (round 0 had `sim<0.30`) | 0 | 0 ms |
| EVALUATED at round 1 | 1 fast-LLM | +300-600 ms |
| REFINED at round 2 | 1 fast-LLM + 1 ChromaDB | +320-620 ms |
| EVALUATED at round 3 | 2 fast-LLM + 1 ChromaDB | +620-1220 ms |
| FEW-SHOT (round 3 verdict <0.70) | 2 fast-LLM + 1 ChromaDB | +620-1220 ms |

Worst case `+1.2 s` on an interactive turn is just under the `<2 s per LLM
turn` budget from the brief, **before** counting the main LLM turn that
follows retrieval. So the practical interactive budget for iterative
retrieval is roughly 800 ms — meaning round 3 is a coin-flip whether we
exceed budget. That's the case for hard-capping at 2 LLM calls and being
willing to commit at `confidence ≥ 0.60` (relaxed from 0.70) when budget
pressure matters. See §6 for the actual numbers.

---

## 5. Hard-Instantiate Compatibility

### 5.1 Iterative fires ONLY when single-shot would have fallen through

The architectural principle is **non-regression**: today's hard-instantiate
path has higher precision than anything iterative because it's deterministic
embedding similarity with calibrated thresholds. Adding iteration AHEAD of
the threshold gate would (a) add latency to the only fast path, and (b)
risk an evaluator override picking a "more confident but wrong" candidate
over the deterministic top match.

The state machine in §4 enforces this:

```python
# Inside retrieve_iteratively, right after Round 0:
if _passes_hard_gate(top_sim, margin):
    return RetrievalResult(scored=scored, mode="hard_match",
                           rounds=0, history=...)
# Only continue iterating BELOW this line.
```

This is the "iterative retrieval ONLY fires when single-shot would have fallen
through to few-shot" rule from the brief. Restating: when `confident_match`
is True today (`orchestrator.py:924-929`), iterative does not run, the
existing `execute_template_canonical` path runs, and downstream behavior is
byte-identical to today.

### 5.2 What changes for the FEW-SHOT band today

Today's FEW-SHOT path runs whenever `not confident_match`. That is one path
covering the entire band `0.0 ≤ sim < 0.45 OR margin < 0.20`. Iterative
splits that band:

```
sim<0.30           OR scored empty   →   FEW-SHOT (same as today, 0 extra LLM)
0.30 ≤ sim         AND not _passes_hard_gate   →   iterative loop (1-3 LLM calls)
   │
   ├── EVALUATED match → hard_instantiate (NEW: more matches qualify)
   ├── REFINED match   → hard_instantiate (NEW)
   └── still ambiguous → FEW-SHOT (same as today's path)
```

So the **set of cases that go to FEW-SHOT shrinks** by exactly the set that
becomes EVALUATED or REFINED. The behavior for `sim<0.30` is identical
(still FEW-SHOT, zero LLM calls). The behavior for `≥0.45/≥0.20` is identical
(still HARD, zero LLM calls).

### 5.3 ALLOWED_AFTER_INSTANTIATE applies unchanged

`canonical_instantiator.py:73-102` defines `ALLOWED_AFTER_INSTANTIATE` (22-28
tools, version-dependent — Q3 §1 says 22, Q1 §2 Mode A says 28). When
EVALUATED or REFINED triggers hard-instantiate, the same tool-schema
replacement applies. The LLM sees the same verify-only subset post-build,
preventing rebuild. No change needed.

### 5.4 Verify-args, settle, format_instantiation_summary

The post-instantiate steps in `orchestrator.py:946-986`
(`execute_template_canonical`, `settle_after_canonical`,
`execute_template_verify`, `format_instantiation_summary`) all operate on
the chosen template. They don't care WHICH retrieval mode picked it — they
just need a template dict. So EVALUATED and REFINED reuse this entire
pipeline unchanged.

### 5.5 The risk: an evaluator promotes the wrong template

The non-regression argument has a corner case. Imagine round 0 returns:
- CP-01 at sim=0.46 (passes single-shot)
- CP-02 at sim=0.30 (margin=0.16, FAILS the margin gate)

Today: this DOES pass the hard-gate (sim ≥ 0.45 — wait, but margin = 0.16 <
0.20 — so it FAILS, goes to FEW-SHOT). Iterative would then run evaluate_,
which might pick CP-02 with confidence 0.75 (justified! the user prompt
might genuinely indicate multi-robot, and CP-02 is the multi-robot template).

So in this case iterative IMPROVES on today (today gives FEW-SHOT, iterative
correctly picks CP-02). Good.

The bad-case mirror: round 0 returns CP-01 at sim=0.48, CP-02 at sim=0.25
(margin=0.23, PASSES hard-gate). Today instantiates CP-01. Iterative
**doesn't even run** because hard-gate passed. So no risk of evaluator
overriding correct hard-gate decisions. 

The only genuine risk: round 0 returns sim=0.46 / 0.30 (margin=0.16, fails),
evaluator INCORRECTLY picks CP-02 with confidence 0.72, system instantiates
CP-02 when CP-01 was right. This is a real failure mode but bounded by the
fast-model's quality on a tight structured-output prompt with only 3
candidates to compare. The A/B benchmark (§7) measures this directly.

---

## 6. Latency Budget

### 6.1 Component costs

| Component | Cost | Source |
|---|---|---|
| Sentence-transformer embed (1 query) | ~10 ms | Q3 §7; sbert.net efficiency docs |
| ChromaDB query, 321 vectors, top-K=3 | ~1 ms | Q3 §7 |
| **Round 0 total** | **~10-15 ms** | sum |
| Haiku 4.7 small structured-output call (200in/80out) | 300-600 ms | Anthropic published latency; conservative |
| Gemini 2.5 Flash equivalent | 200-400 ms | Google published latency |
| **Round 1 (evaluate) total** | **+300-600 ms (Haiku)** | with Haiku |
| Round 2 (refine = embed + query) | +10-15 ms | same as round 0 |
| **Round 1+2 incremental** | **+310-615 ms** | sum |
| Round 3 (evaluate again) | +300-600 ms | same model |
| **Round 1+2+3 incremental** | **+610-1215 ms** | sum |

The chat LLM turn that *follows* retrieval is the biggest single cost:
500-3000 ms (Q3 §7), unchanged.

### 6.2 Single round (today's path)

```
embed(message) + chromadb.query(top_k=3) → ~10-15 ms total
```

### 6.3 Iterative round 1 only

```
embed + query + evaluate_candidates (Haiku, 200in/80out)
  = 15 ms + 450 ms (mid)
  ≈ 465 ms total
```

### 6.4 Iterative round 1+2 (evaluator says refine)

```
+ embed(refined_query) + query
  ≈ 465 + 15 = 480 ms
```

### 6.5 Iterative round 1+2+3 (worst case)

```
+ evaluate_candidates again
  ≈ 480 + 450 = 930 ms
```

### 6.6 Interactive budget check

Brief says: `<2 s per LLM turn`. The main turn LLM eats 500-3000 ms by itself.
That leaves 0-1500 ms for retrieval before the user notices.

| Path | Cost | OK for interactive? |
|---|---|---|
| Round 0 only (HARD or sim<0.30) | 15 ms | yes, zero overhead |
| Round 1 → EVALUATED | 465 ms | yes, leaves 1535 ms for main LLM |
| Rounds 1+2 → REFINED | 480 ms | yes |
| Rounds 1+2+3 → EVALUATED/FEW-SHOT | 930 ms | borderline — main LLM must be <1070 ms |

The borderline case (930 ms retrieval + main LLM) only fires when:
- Round 0 returned plausible candidates (≥0.30)
- Round 1 evaluator was unsure (<0.70)
- Round 1 evaluator emitted a refined_query
- Round 2 refinement also missed thresholds
- Round 3 evaluator gets the final judgment

This is an "ambiguous within domain" case (think VR-19-class). For these
cases the alternative today is FEW-SHOT, which means the LLM gets three
templates as prose, reasons over them, and tries to pick — that already
spends LLM tokens for the same job. Net: even at 930 ms extra, iterative
retrieval is "trading retrieval-side LLM tokens for chat-side LLM tokens",
not pure cost.

### 6.7 Autonomous-cron budget check

Brief says: `10-30 s acceptable` for cron. The worst-case 930 ms retrieval is
3% of a 30 s cron turn. Trivially acceptable. The benefit for cron (overnight
canary, baseline regressions) is hit-rate improvement on the ambiguous band
that today fails silently and goes to FEW-SHOT.

### 6.8 Latency-variance ceiling

Important: median vs p99. The Haiku 4.7 calls can spike to 1.5 s in the worst
percentile (Anthropic API quartile spread, not published numerically but
observed in `nexus_research` baselines). That means worst case is closer to:

```
(2 evaluate spikes @ 1500 ms) + (1 refine @ 30 ms) = 3030 ms
```

For cron: fine. For interactive: that's a 3 s retrieval phase, and the
user will perceive it. Mitigations:
- Use Gemini Flash as primary (200-400 ms, fewer outliers per benchmarks)
- Set a hard timeout on `evaluate_candidates` (e.g. 1500 ms) → on timeout,
  treat as `confidence=0.0` and fall through to FEW-SHOT
- Run round 1 and round 2 concurrently when feasible (round 2 doesn't need
  round 1's refined_query if you're willing to use a heuristic-generated
  refined_query in parallel) — but this is complexity for marginal gain;
  not recommended in v1

---

## 7. A/B Test Plan

### 7.1 Benchmark set: the 30-prompt set from Q3 §8

Re-use the Tier 1/2/3 benchmark proposed in Q3 §8. The set has labeled
ground-truth template IDs for each prompt, so hit@1 and hit@3 are directly
measurable. It is partitioned:

- Tier 1 (10 prompts, expected `hard_match`): high-confidence pick-place
- Tier 2 (10 prompts, expected `few_shot`): domain-breadth (AMR, RL, ROS2, etc.)
- Tier 3 (10 prompts, adversarial): generics, ambiguous, role-based, direct-ID

### 7.2 Modes under test

| Mode label | What runs | Sources |
|---|---|---|
| **A** | Today's single-shot + threshold gate | `orchestrator.py:879-997` |
| **I** | `retrieve_iteratively` from §2.2 | new in this proposal |
| **I_FLASH** | Same as I but with Gemini Flash as evaluator | new |
| **I_HAIKU** | Same as I but with Claude Haiku as evaluator | new |
| **I_TIMEOUT** | Same as I with 800 ms hard timeout on each evaluate call | new |

For each mode, run each of the 30 prompts and capture:

```python
{
  "prompt_id": "R-07",
  "mode": "I_HAIKU",
  "retrieval_mode": "evaluated_match",  # which terminal state hit
  "rounds_used": 1,
  "hit@1": True,
  "hit@3": True,
  "wall_ms": 465,
  "top_sim_round0": 0.41,
  "margin_round0": 0.18,
  "final_top_id": "CP-82",
  "ground_truth": "CP-82",
  "evaluator_confidence": 0.86,
  "evaluator_chose_idx": 0,
  "history": [...]
}
```

### 7.3 Metrics

| Metric | Definition | Target for shipping I |
|---|---|---|
| hit@1 | top-1 returned template == ground_truth | I.hit@1 ≥ A.hit@1 + 10 pp |
| hit@3 | ground_truth ∈ top-3 | I.hit@3 ≥ A.hit@3 (no regression) |
| commit_rate | fraction of prompts terminating in HARD/EVALUATED/REFINED (not FEW-SHOT) | I.commit_rate ≥ A.commit_rate + 15 pp |
| precision_at_commit | when committed, was top-1 correct? | I.precision_at_commit ≥ 0.85 (no worse than A) |
| p50 wall_ms | median end-to-end retrieval latency | I.p50 ≤ 700 ms |
| p99 wall_ms | tail | I.p99 ≤ 1500 ms |
| fast_model_cost_per_prompt | $/prompt | track for the cost-benefit table; not a ship gate |

### 7.4 Decision rule

**Ship mode I in production** iff ALL of:

1. `I.hit@1 ≥ A.hit@1 + 10 pp` (≥10 percentage-point absolute lift on 30-prompt set)
2. `I.precision_at_commit ≥ A.precision_at_commit` (no precision regression)
3. `I.p50 ≤ 700 ms AND I.p99 ≤ 1500 ms` (interactive latency holds)

If only 1 fails (no hit@1 lift): skip iterative, invest the engineering
budget in intent-field coverage expansion (Q3 §9 rec 3) instead.

If 2 fails (precision regression): iterative is correctly designed but the
evaluator prompt is leaking; iterate on the prompt before scrapping.

If 3 fails (latency regression): try `I_FLASH` and/or `I_TIMEOUT` modes
before scrapping.

### 7.5 Statistical caveat

30 prompts is a thin sample. A 10 pp lift on 30 prompts (3 prompts difference)
is barely above noise. The decision rule should require **agreement across
the three tiers** — i.e. Tier 2 (the domain-breadth tier) should show the
biggest lift, because that's where iterative is designed to help.
If lift is concentrated in Tier 1 (where today's HARD path already wins),
iterative isn't earning its keep.

### 7.6 Bench harness location

`scripts/qa/retrieval_benchmark.py` — single file, runs both modes back-to-back
against ChromaDB, writes `workspace/baselines/retrieval-{mode}-{ts}.json`.
Modeled on the existing `function_gate_*.py` scripts in `scripts/qa/`
(per memory: `project_isaac_assist_function_gate.md`).

---

## 8. Implementation Outline

### 8.1 File-by-file diff sketch

#### NEW: `service/isaac_assist_service/chat/tools/iterative_retriever.py` (~250 LOC)

```python
"""
iterative_retriever.py
----------------------
Bounded iterative retrieval. Inserts between single-shot template retrieval
and the hard-instantiate / few-shot decision gate in chat/orchestrator.py.

Design: §2-§5 of docs/research/2026-05-15-q8-iterative-retrieval.md

Public API:
  retrieve_iteratively(user_message, ...) -> RetrievalResult

Internals:
  evaluate_candidates(candidates, prompt) -> CandidateVerdict
  refine_retrieval(query, exclude_ids, hint, top_k) -> List[Dict]
  list_canonical_categories(filter=None) -> List[Category]
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from .template_retriever import retrieve_templates_with_scores

logger = logging.getLogger(__name__)

# Thresholds (env-tunable) — mirror orchestrator.py constants
CANONICAL_MIN_SIM = float(os.environ.get("CANONICAL_MIN_SIM", "0.45"))
CANONICAL_MIN_MARGIN = float(os.environ.get("CANONICAL_MIN_MARGIN", "0.20"))
ITER_EVAL_MIN_CONF = float(os.environ.get("ITER_EVAL_MIN_CONF", "0.70"))
ITER_FAST_MODEL = os.environ.get("ITER_FAST_MODEL", "claude-haiku-4.7")
ITER_EVAL_TIMEOUT_MS = int(os.environ.get("ITER_EVAL_TIMEOUT_MS", "1500"))
ITER_MAX_REFINE_ROUNDS = int(os.environ.get("ITER_MAX_REFINE_ROUNDS", "1"))


@dataclass
class CandidateVerdict:
    best_idx: Optional[int]
    confidence: float
    reason: str = ""
    refined_query: Optional[str] = None
    intent_hint: Optional[str] = None
    wall_ms: float = 0.0


@dataclass
class RetrievalResult:
    scored: List[Dict]
    mode: str   # "hard_match"|"evaluated_match"|"refined_match"|"few_shot_fallback"
    rounds: int
    history: List[Dict] = field(default_factory=list)
    wall_ms: float = 0.0


async def retrieve_iteratively(
    user_message: str,
    *,
    top_k: int = 3,
    max_refine_rounds: int = ITER_MAX_REFINE_ROUNDS,
    fast_model: str = ITER_FAST_MODEL,
    eval_timeout_ms: int = ITER_EVAL_TIMEOUT_MS,
) -> RetrievalResult:
    """Body — see §2.2 of the Q8 design doc."""
    t0 = time.monotonic()
    # ... see §2.2 pseudocode; ~80 LOC


async def evaluate_candidates(
    candidates: List[Dict],
    prompt: str,
    *,
    model: str = ITER_FAST_MODEL,
    timeout_ms: int = ITER_EVAL_TIMEOUT_MS,
) -> CandidateVerdict:
    """Fast-LLM judge. ~60 LOC, including JSON parse + fallback."""
    # Build a tight prompt:
    #   System: "You are a template-matching judge. Output JSON: {best,confidence,
    #            refined_query,intent_hint,reason}."
    #   User:   prompt + numbered candidates (task_id + goal only).
    # Call the fast model with a hard timeout.
    # On timeout/parse-fail: return CandidateVerdict(None, 0.0, "<timeout>")


async def refine_retrieval(
    query: str,
    exclude_ids: List[str],
    hint: Optional[str],
    top_k: int = 3,
) -> List[Dict]:
    """Re-search wrapper. ~30 LOC."""


def list_canonical_categories(filter: Optional[str] = None) -> List[Dict]:
    """Helper enumerating intent/pattern_hint/role taxonomy. ~40 LOC."""


# Private helpers: ~40 LOC
def _passes_hard_gate(top_sim: float, margin: float) -> bool: ...
def _top_and_margin(scored: List[Dict]) -> Tuple[float, float]: ...
def _promote(scored: List[Dict], idx: int) -> List[Dict]: ...
def _merge_and_rerank(a: List[Dict], b: List[Dict], top_k: int) -> List[Dict]: ...
def _format_candidates_for_evaluator(scored: List[Dict]) -> str: ...
```

#### MODIFY: `service/isaac_assist_service/chat/orchestrator.py` (~50 LOC delta)

```python
# Around line 879-997 — replace the single-shot template-retrieval block
# with a flag-gated dispatch:

ITERATIVE_RETRIEVAL_ON = (
    os.environ.get("ITERATIVE_RETRIEVAL", "off").lower()
    in ("on", "true", "1", "yes")
)

try:
    if ITERATIVE_RETRIEVAL_ON:
        from .tools.iterative_retriever import retrieve_iteratively
        result = await retrieve_iteratively(user_message, top_k=_template_top_k)
        scored = result.scored
        confident_match = result.mode in ("hard_match", "evaluated_match", "refined_match")
        # log mode + rounds for the benchmark harness
        logger.info(
            f"[{session_id}] iterative retrieval: mode={result.mode} "
            f"rounds={result.rounds} wall={result.wall_ms:.0f}ms "
            f"top={result.scored[0]['task_id'] if result.scored else '-'}"
        )
    else:
        # Existing single-shot path (unchanged) → orchestrator.py:898-929
        ...
    # Then the existing if confident_match: ... else: ... block runs.
    # No change below that line.
except Exception as e:
    logger.warning(f"[{session_id}] Template retrieval/instantiation failed: {e}")
```

The point: iterative retrieval is an env-gated wrap of the existing block.
When `ITERATIVE_RETRIEVAL=off` (default during ramp), behavior is byte-identical
to today. When `ITERATIVE_RETRIEVAL=on`, the new helper computes `scored` +
`confident_match` and everything downstream proceeds unchanged.

#### MODIFY (optional): tool registration

If the team decides to expose `evaluate_candidates` as an LLM-callable tool
later (NOT recommended in v1 per §3.1), the registration would happen in
`chat/tool_executor.py` and `chat/context_distiller.py`. **Skip this in v1.**

#### NEW: `tests/test_iterative_retriever.py` (~150 LOC)

Test cases (one per terminal state + edge cases). Each uses
`unittest.mock.patch` to swap `retrieve_templates_with_scores` and
`evaluate_candidates` with deterministic mocks.

- `test_hard_match_no_iteration` — round 0 passes gate → `hard_match`,
  `rounds=0`, evaluator NOT called (critical: zero-overhead assertion)
- `test_total_miss_short_circuits` — sim<0.30 → `few_shot_fallback`, no LLM
- `test_evaluator_picks_winner` — round 1 verdict.confidence≥0.70 →
  `evaluated_match`, `rounds=1`
- `test_refine_then_hard_gate` — round 2 merged passes gate →
  `refined_match`, `rounds=2`
- `test_refine_then_evaluate_picks_winner` — round 3 commits →
  `evaluated_match`, `rounds=3`
- `test_refine_then_evaluate_falls_through` — round 3 unsure →
  `few_shot_fallback`, `rounds=3`
- `test_evaluator_timeout_falls_through` — timeout exception in evaluator →
  `CandidateVerdict(None, 0.0)` → few-shot
- `test_merge_dedupes_and_keeps_max_sim` — `_merge_and_rerank` invariant
- `test_promote_idx_brings_winner_to_position_0` — `_promote` invariant
- `test_no_refined_query_skips_refine_round` — verdict.refined_query=None
  → no round 2

In a sibling `test_orchestrator.py`: `test_iterative_off_by_default` —
when `ITERATIVE_RETRIEVAL=off`, `retrieve_iteratively` is NOT imported nor
called; behavior is byte-identical to today.

#### NEW (optional): `scripts/qa/retrieval_benchmark.py` (~120 LOC)

The benchmark harness per §7.5. Runs both modes against the 30-prompt set.

### 8.2 Estimated total work

| Item | LOC | Effort |
|---|---|---|
| `iterative_retriever.py` | ~250 | half day |
| `orchestrator.py` integration | ~50 | hour |
| `test_iterative_retriever.py` | ~150 | half day |
| `retrieval_benchmark.py` | ~120 | hour |
| Manual 30-prompt labeling (if not done in Q3) | — | hour |
| Wiring the fast-model client (Haiku/Flash credentials) | ~30 | hour |
| **Total** | ~600 LOC | ~1.5 dev-days |

For comparison, **expanding intent-field coverage from 5 to 100 templates**
(Q3 §9 rec 3) is roughly the same effort if automated (LLM-assisted batch
script), and addresses a different (larger) failure surface — see §10.

---

## 9. Failure Modes Specific to Iterative

### 9.1 Infinite refine loops

**Symptom:** The evaluator keeps emitting `refined_query`s that yield more
ambiguous candidates, never converging.

**Guard rail:** Hard cap `max_refine_rounds=1` (env: `ITER_MAX_REFINE_ROUNDS`).
The pseudocode in §2.2 only allows ONE refine round, regardless of subsequent
evaluator output. If round 3's evaluator is unsure, mode=`few_shot_fallback`
— no round 4.

**Belt-and-suspenders:** Track visited `(query, candidate_id_set)` tuples
within a turn. If a refine produces the same candidate set as round 0, treat
it as a no-op and terminate at `few_shot_fallback`. This catches the
pathological case where the fast model echoes the same refined_query back.

### 9.2 Drift toward irrelevant candidates after N refines

**Symptom:** The evaluator suggests `refined_query="navigation"`, ChromaDB
returns AMR templates that are off-topic for the user's pick-place prompt,
then the next evaluator picks an AMR template with `confidence=0.72`.

**Guard rail:**
- `_merge_and_rerank` keeps round-0 candidates in the pool. Drift can't
  remove the legitimately-high-sim round-0 results, only add to them.
- Evaluator prompt explicitly instructs: "Prefer candidates that overlap
  with the user's original prompt domain." (One-line instruction tightens
  the priors.)
- The `intent_hint` field is restricted to a closed enum (§3.2). The
  evaluator can't invent novel domain hints that ChromaDB doesn't know
  how to use.

**Belt-and-suspenders:** Log every drift case to a `drift_audit.jsonl` and
review weekly. If drift is consistently producing wrong commits, lower
`ITER_EVAL_MIN_CONF` from 0.70 to 0.80 (be more skeptical of evaluator
verdicts).

### 9.3 Latency variance hurting UX

**Symptom:** Median 465 ms feels fine; p99 spikes to 2.5 s, user sees
"thinking..." for ~3 s before reply starts streaming.

**Guard rail:**
- Hard timeout per evaluator call (`ITER_EVAL_TIMEOUT_MS=1500`). On
  timeout, evaluator returns `CandidateVerdict(None, 0.0)`, loop terminates
  at `few_shot_fallback`. Worst case is bounded.
- Use Gemini Flash by default (lower tail latency than Haiku per published
  benchmarks).
- Send a streaming "Looking up..." event to the chat UI when round 1 starts
  so the user sees activity (UX-side mitigation, not strictly latency
  reduction).

**Monitoring:** Log every `wall_ms` to `workspace/baselines/iterative-retrieval-{date}.jsonl`.
Alert if p99 over a 1-hour window exceeds 2000 ms.

### 9.4 Evaluator prompt-injection / structured-output drift

**Symptom:** The fast model returns `{"best_idx": "CP-01"}` (string instead
of int), or `{"best": 1}` (missing the `_idx` suffix), causing `json.loads`
or schema validation to fail.

**Guard rail:**
- Use the model's structured-output mode (Anthropic tool-use or
  Gemini response_schema) — not free-form JSON parsing.
- On parse failure, treat as `CandidateVerdict(None, 0.0)`, fall through to
  few-shot. NEVER crash the orchestrator.
- Validate the returned `best_idx` is `null` OR an int in
  `[0, len(candidates))`. Out-of-bounds → treat as `None`.

### 9.5 Evaluator gives a winner that doesn't exist

**Symptom:** Evaluator returns `best_idx=2` when only 2 candidates were
passed in. Or returns a `refined_query` that ChromaDB returns 0 hits for.

**Guard rail:**
- Strict bounds check on `best_idx` (above).
- If `refine_retrieval` returns 0 candidates, merge step produces the
  round-0 set unchanged, then round 3 evaluator runs on that → likely
  `few_shot_fallback`. Acceptable degradation.

### 9.6 Cost runaway on heavy traffic

**Symptom:** ~1 fast-LLM call per user message on the bulk of "ambiguous
but plausible" prompts. At scale this is real money.

**Guard rail:**
- Track per-prompt evaluator cost via the bench harness.
- For autonomous cron, allow `ITERATIVE_RETRIEVAL=on`. For interactive
  traffic, gate via session metadata (e.g. `session.user_tier=power_user`
  → on; `default` → off) until evaluated.
- Hold the line that iterative ONLY fires in the ambiguous band — round 0
  HARD and round 0 `sim<0.30` cases pay zero LLM cost.

### 9.7 Stale fast-model client / SDK breakage

**Symptom:** Anthropic SDK update changes Haiku API signature; evaluator
calls throw on import.

**Guard rail:**
- Wrap the fast-model client behind a clean adapter
  (`_call_fast_model(prompt, schema, timeout) -> dict`). On any exception,
  return `CandidateVerdict(None, 0.0)`.
- Pin Anthropic SDK version in `pyproject.toml` / `requirements.txt`.
- Health-check the fast-model adapter on service startup; if down, log a
  loud warning and continue with `ITERATIVE_RETRIEVAL=off` regardless of
  env flag.

### 9.8 Race against the hard-instantiate path

**Symptom:** Round 0 returns sim=0.451 / margin=0.201 (barely passing
hard-gate). The orchestrator commits to CP-01 via `execute_template_canonical`
WITHOUT running the iterative judge. Iterative would have picked CP-02
correctly.

**Honest assessment:** This is by design (§5). We deliberately preserve the
hard-gate's precision and don't second-guess it. If the calibration is wrong
(e.g. CP-01 at 0.451 was a coin-flip and CP-02 was correct), that's a
*threshold calibration* problem (Q3 §9 rec 1: run the benchmark, recalibrate
thresholds), not an iterative-retrieval problem.

The benchmark in §7 will surface threshold mis-calibration via Tier 1
hit@1 measurements. If `A.hit@1` on Tier 1 is below ~80%, the thresholds
need retuning regardless of whether iterative ships.

---

## 10. Recommendation — Build vs. Skip

### 10.1 The competing investment: structural-filter expansion

Q3 §5 documented the existing two-stage retrieval (`retrieve_with_intent_filter`):
hard structural filter on intent fields → embed-sim tiebreak on the
filtered candidate set. The filter is built and tested
(`template_retriever.py:393-484`). The problem: **only 5/321 templates have
intent fields** (CP-01..CP-05). For 98.4% of the corpus, the filter is
vacuous and the system falls back to full-corpus embed-sim (today's path).

**Coverage expansion ROI:** Adding intent fields to 100 templates
(CP-01..CP-N covering pick-place, sort, palletize, AMR, RL, ROS2,
vision-inspect, weld families) is **mechanical work**: read goal/thoughts,
write `pattern_hint`, `counts`, `structural_features`. Estimated at ~30 min
per template with an LLM-assisted script (which Anton has built before
for `nexus_summaries`). Total: ~50 hours human-in-the-loop, less if
fully automated.

Once intent coverage hits ~100, the structural filter becomes the dominant
retrieval path for the majority of canonical-shape prompts. The
ambiguity problem (VR-19-class) **disappears** for filtered-domain prompts
because the structural filter selects on `n_robots=2` or `has_color_routing=true`
directly, not on embedding cosine similarity.

### 10.2 Comparison table

| Investment | Engineering cost | What it fixes | What it doesn't fix |
|---|---|---|---|
| **Iterative retrieval (this proposal)** | ~600 LOC + 1.5 dev-days + fast-model API integration | Within-cluster ambiguity (VR-19-class, where round-0 returned plausible candidates but margin failed); enables more EVALUATED commits | Doesn't help when round-0 already missed the right template (recall problem); doesn't help when `sim<0.30` (no plausible candidates) |
| **Structural-filter coverage expansion (Q3 §9 rec 3)** | ~50 LOC enabling code + ~50 hours data work | Recall problem for structured prompts (the filter exposes the discriminating signal directly); eliminates ambiguity for filtered domains | Doesn't help when LayoutSpec extraction fails (still falls back to legacy); doesn't help unstructured prompts (no pattern_hint to filter on) |
| **Combined** | ~1.5 dev-days + 50 hours data work | Both problems above; iterative becomes the catchall for prompts that fall outside structural filter coverage | Coverage of fundamentally new task shapes (out of corpus) |

### 10.3 ROI calculation (rough)

Q3 §2c spot-checked 5 prompts with educated-guess hit rates per domain:
- Pick-place (Tier 1, 10 prompts): est. 70-80% hit@1 today
- Domain-breadth (Tier 2, 10 prompts): est. 40-60% hit@1 today
- Adversarial (Tier 3, 10 prompts): est. 30-50% hit@1 today

If we trust Q3's projections:

| Mode | est. Tier 1 | est. Tier 2 | est. Tier 3 | est. overall |
|---|---|---|---|---|
| A (today) | 75% | 50% | 40% | ~55% |
| A + iterative | 75-80% | 55-65% | 45-55% | ~60-65% (+5-10pp) |
| A + structural-filter expansion | 80-90% | 60-75% | 40-50% | ~62-72% (+7-17pp) |
| Both | 85-90% | 65-75% | 50-60% | ~67-75% (+12-20pp) |

These are *educated guesses*. The benchmark in §7 / Q3 §8 measures actual
hit@1 for each mode.

The combined-investment line is the right target. But sequencing matters:

**Order: structural-filter expansion FIRST, iterative SECOND.**

Reasons:
1. Structural-filter expansion addresses **recall** (more right answers in
   round-0 top-K). Iterative addresses **precision** within an already-good
   top-K. Recall first is canonical (without recall, you have nothing to
   refine).
2. The structural-filter code is already built and tested. The expansion is
   pure data work (no new code paths to debug). Lower risk.
3. Iterative retrieval's evaluator works *better* when round-0 has the
   right answer in top-3 (`hit@3` high). Structural-filter expansion lifts
   `hit@3`. So structural-filter expansion makes iterative more effective.
4. If structural-filter expansion alone gets us to 70%+ hit@1, iterative
   may not be needed (and the latency cost is avoided).

### 10.4 Concrete decision

**Build the 30-prompt benchmark FIRST.** Without it, the entire ROI
discussion is conjecture. The benchmark is ~120 LOC + manual labeling of
30 templates. Q3 §9 rec 1 already named this as the highest-priority action.

**Then run today's A mode against the benchmark to establish baselines.**

**Then run an offline structural-filter expansion** (LLM-assisted script
adds intent fields to CP-01..CP-50, M-01..M-10, S-01..S-10) and re-run the
benchmark.

**THEN, if the benchmark shows ambiguity is still the dominant failure
mode (i.e. `hit@3` is high but `hit@1` is not), build iterative retrieval**
per §1-§8 of this report.

If the benchmark shows that structural-filter expansion gets `hit@1` to
≥70% across all tiers, iterative retrieval is a marginal-gain investment
and may be deferred. The fast-model API integration cost (Anthropic Haiku
or Google Flash credentials, billing, monitoring) is non-trivial and worth
deferring until clearly needed.

### 10.5 Caveat — when iterative DOES dominate

There is one scenario where iterative wins over structural-filter expansion:

**Free-form / colloquial prompts where the user does not produce a
structural specification.** Example: "fix the thing that keeps falling off".
LayoutSpec extraction fails or produces a useless spec; structural filter
falls back to full-corpus embed-sim; the user prompt is too short for
embed-sim to discriminate well. Iterative retrieval can ask the evaluator
"is this user describing pick-place or something else?" and the evaluator
can use the prior conversation context (which structural filter cannot
read) to guess.

If the production workload skews toward free-form colloquial input (per
Q1's spec-first-pipeline-reverted memory entry: "T4 canary too stochastic
for triple-perfect" — these are dialog tasks), iterative retrieval becomes
more valuable. The benchmark should include some Tier 3 colloquial
prompts to measure this.

### 10.6 Final answer

**Build iterative retrieval — but only as Phase 2 of a two-phase plan.**

- Phase 1 (LOW risk, HIGH ROI): Build the 30-prompt benchmark (Q3 §9 rec 1).
  Run it. Expand intent fields to 100 templates (Q3 §9 rec 3). Re-run
  benchmark. Recalibrate thresholds if needed. Total: 2-3 dev-days + data work.
- Phase 2 (MEDIUM risk, MEDIUM ROI): Build iterative retrieval per §1-§8
  of this doc. Run A/B benchmark. Ship if `hit@1` improves ≥10 pp at p99
  latency ≤1500 ms.

This sequencing matches the Q3 conclusion: structural-filter expansion is
the **dominant improvement lever** (Q3 §5); iterative retrieval is the
**catchall enhancement** for cases the structural filter can't handle.

---

## 11. Citations & Source Reports

### 11.1 Source reports (this repo)

- `docs/research/2026-05-15-q1-flow-architecture.md` §2 Mode A, §2 Mode B,
  §3 Decision matrix, §4 Threshold proposals, §5 Three concrete actionable
  changes
- `docs/research/2026-05-15-q3-retrieval-quality.md` §1 Retrieval chain,
  §2 Current hit-rate, §3 Failure-mode analysis, §4 Should retrieval become
  iterative, §5 Multi-stage retrieval, §7 Latency budget, §8 Benchmark
  plan, §9 Recommendation
- `docs/research/2026-05-14-l-levels-discovery-audit.md` §3 retrieval-chain
  block (memory cite: Q1 §1)

### 11.2 Code citations

- `service/isaac_assist_service/chat/orchestrator.py:870-997` —
  threshold constants, retrieval block, hard-instantiate gate
- `service/isaac_assist_service/chat/orchestrator.py:1189` — tool-calling
  loop `MAX_ROUNDS=8` cap
- `service/isaac_assist_service/chat/tools/template_retriever.py:63-104` —
  `_build_index` (goal + thoughts + tools embed doc)
- `service/isaac_assist_service/chat/tools/template_retriever.py:173-220` —
  `retrieve_templates_with_scores`
- `service/isaac_assist_service/chat/tools/template_retriever.py:393-484` —
  `retrieve_with_intent_filter` (structural filter)
- `service/isaac_assist_service/chat/canonical_instantiator.py:73-102` —
  `ALLOWED_AFTER_INSTANTIATE`
- `service/isaac_assist_service/chat/context_distiller.py:139-146` —
  `_ALWAYS_TOOLS`
- `service/isaac_assist_service/chat/context_distiller.py:519-520` —
  `retrieve_tools(message, top_k=20)`
- `service/isaac_assist_service/chat/negotiator.py:25-162` —
  negotiator scope + clarification gate

### 11.3 Web sources (Claude Code agentic loop)

- Anthropic Claude Code Docs — agent loop: https://code.claude.com/docs/en/agent-sdk/agent-loop
- Anthropic Platform Docs — agent loop: https://platform.claude.com/docs/en/agent-sdk/agent-loop
- Anthropic — Tool-using agent tutorial: https://platform.claude.com/docs/en/agents-and-tools/tool-use/build-a-tool-using-agent
- VILA-Lab — Dive into Claude Code: https://arxiv.org/html/2604.14228v1 ; https://github.com/VILA-Lab/Dive-into-Claude-Code
- Bits-Bytes-NN — Claude Code architecture analysis: https://bits-bytes-nn.github.io/insights/agentic-ai/2026/03/31/claude-code-architecture-analysis.html
- DEV — Inside the agentic loop: https://dev.to/kevinzy189/claude-certified-inside-the-agentic-loop-how-claude-code-actually-decides-what-tool-to-call-next-3a1i
- Augment — Claude Agent SDK loops: https://www.augmentcode.com/guides/claude-agent-sdk-agent-loops-tool-calls
- Temporal — Basic agentic loop with Claude: https://docs.temporal.io/ai-cookbook/agentic-loop-tool-call-claude-python
- MindStudio — Claude Code workflow patterns: https://www.mindstudio.ai/blog/claude-code-agentic-workflow-patterns

### 11.4 Web sources (iterative RAG / corrective RAG)

- Agentic RAG survey: https://arxiv.org/html/2501.09136v4
- RAG-Reasoning survey: https://arxiv.org/pdf/2507.09477
- FAIR-RAG (iterative refinement): https://arxiv.org/html/2510.22344v1
- Meilisearch — Self-RAG: https://www.meilisearch.com/blog/self-rag
- Meilisearch — Corrective RAG (CRAG): https://www.meilisearch.com/blog/corrective-rag
- DataCamp — Self-RAG with LangGraph: https://www.datacamp.com/tutorial/self-rag
- DataCamp — CRAG with LangGraph: https://www.datacamp.com/tutorial/corrective-rag-crag
- ApxML — Self-correcting and self-improving RAG: https://apxml.com/courses/large-scale-distributed-rag/chapter-6-advanced-rag-architectures-techniques/self-correcting-improving-rag
- NVIDIA — Log analysis multi-agent self-corrective RAG with Nemotron: https://developer.nvidia.com/blog/build-a-log-analysis-multi-agent-self-corrective-rag-system-with-nvidia-nemotron/
- FLAIRS — Iterative self-correcting agentic RAG: https://journals.flvc.org/FLAIRS/article/view/141838
- Microsoft Learn — Agentic retrieval (Azure AI Search): https://learn.microsoft.com/en-us/azure/search/agentic-retrieval-overview
- ByteByteGo — How agentic RAG works: https://blog.bytebytego.com/p/how-agentic-rag-works
- Chroma — Context-1, self-editing search agent: https://www.trychroma.com/research/context-1
- Databricks — Instructed Retriever: https://www.databricks.com/blog/instructed-retriever-unlocking-system-level-reasoning-search-agents
- MachineLearningMastery — Beyond vector search: https://machinelearningmastery.com/beyond-vector-search-5-next-gen-rag-retrieval-strategies/
- Algolia — Agentic retrieval: https://www.algolia.com/blog/ai/agentic-retrieval
- Qdrant — Agentic vector search: https://qdrant.tech/articles/agentic-builders-guide/
- TowardsDataScience — Hybrid search and re-ranking: https://towardsdatascience.com/hybrid-search-and-re-ranking-in-production-rag/
- Atlan — RAG in 2026: https://atlan.com/know/what-is-rag/

---

*Researcher note: All code claims are cited with file:line from the repo as
of 2026-05-15. All web claims are cited inline with URLs. Where prior
research has already established a finding, the citation points to that
report; this doc does not re-litigate findings settled in Q1 and Q3.*
