# Phase 5 Addendum — Pedagogy & Uncertainty

**For:** The session building Phase 5 (polish, fine-tuning loop, multi-turn
memory, template library).
**Priority:** Add alongside the Phase 5 polish work — these tools let the
assistant *teach* the user what it is doing and *admit* what it does not
know. Both are load-bearing for the fine-tune flywheel (5.1–5.2): the
collected `(user_message, context, tool_calls, result)` tuples are only
useful for training if the model's confidence and rationale are captured
with them.
**Effort:** Small — five tool handlers, no new Kit RPC endpoint.

---

## Motivation

Phase 5 is where Isaac Assist stops feeling like a black-box code
generator and starts feeling like a collaborator. Three failure modes show
up at this stage and none of the existing tools address them:

1. **"Why did it do *that*?"** — the assistant applies a plausible patch
   and the user has no idea whether the choice was driven by retrieved
   documentation, a cached pattern, or pure LLM guesswork. Users cannot
   course-correct without that signal.
2. **Silent over-confidence** — the model answers "the robot's reach is
   855 mm" with the same tone whether that number came from a vetted
   sensor spec or a hallucinated training prior. There is no honest
   uncertainty channel.
3. **Repeat-explain fatigue** — the user asks the same conceptual
   question ("what is an articulation root?") in three different
   sessions. The assistant re-derives the answer each time because the
   knowledge base indexes code, not explanations.

All five new tools are pure data / code-gen — no Kit RPC, no subprocess,
no network. They sit alongside the existing `lookup_knowledge` and
`explain_error` handlers.

---

## Tools

### 5-A.1 `explain_tool_choice(tool_name, user_message, matched_signals)`

**Type:** DATA handler (no code gen).

**Logic:**

1. Look up `tool_name` in a module-level table of known tools (`create_prim`,
   `import_robot`, `lookup_knowledge`, etc.). Missing tool → returns
   `{"known": False, ...}` with a suggestion list.
2. Build a rationale from `matched_signals` — a list of strings the
   caller (the LLM itself, or the orchestrator) believed led to this
   choice. Each signal is categorised as `keyword`, `pattern`, `context`,
   or `fallback`.
3. Return a structured explanation the chat UI can surface as a small
   "why this tool?" popover.

**Returns:**
```python
{
    "known": True,
    "tool": "import_robot",
    "category": "scene_construction",
    "rationale": [
        {"signal": "franka panda", "kind": "keyword", "weight": 0.8},
        {"signal": "user said 'bring in the robot'", "kind": "context", "weight": 0.6},
    ],
    "alternatives_considered": ["add_reference", "create_prim"],
    "confidence": "high",
    "user_message_echo": "bring in the Franka",
}
```

**Why DATA:** the result is a UI payload, not an action. The LLM needs it
back in-context so it can fold the rationale into its own answer ("I
picked `import_robot` because you said 'bring in' and the catalog
matched Franka").

### 5-A.2 `assess_answer_confidence(claim, sources)`

**Type:** DATA handler (no code gen).

**Logic:**

1. Take a `claim` string (the model's assertion) and a list of `sources`
   — each source a dict with `kind` (`sensor_spec`, `knowledge_base`,
   `code_pattern`, `user_provided`, `llm_prior`), `id`, and optional
   `excerpt`.
2. Apply a deterministic scoring table: vetted sources
   (`sensor_spec`, `user_provided`) weight 1.0; indexed
   (`knowledge_base`, `code_pattern`) weight 0.7; `llm_prior` weight
   0.2; empty sources list caps the score at 0.1.
3. Detect hedges already present in the claim ("about", "roughly",
   "approximately") and note them — a hedged claim with no sources is
   still honest; an un-hedged claim with no sources is dangerous.
4. Return a confidence band (`high`, `medium`, `low`, `guess`) plus a
   recommended rewrite ("Prepend 'According to the D435i datasheet,'")
   when sources exist and the claim reads as raw fact.

**Returns:**
```python
{
    "claim": "The Franka Panda has a reach of 855 mm.",
    "confidence": "high",
    "score": 0.92,
    "has_hedge": False,
    "sources_used": ["sensor_spec:franka_panda"],
    "suggested_prefix": "According to the Franka Panda datasheet,",
    "warnings": [],
}
```

**Why DATA:** the LLM uses the returned band to decide whether to present
the claim as fact, hedge it, or ask a follow-up question. Pure function —
no I/O.

### 5-A.3 `generate_teaching_snippet(concept, audience, max_chars)`

**Type:** DATA handler (no code gen).

**Logic:**

1. Look up `concept` (e.g., `articulation_root`, `rigid_body_api`,
   `xform_op_order`, `physics_scene`, `usd_variant_set`) in a
   module-level curated glossary. Unknown concept → returns
   `{"known": False, "suggestions": [...closest keys...]}`.
2. Pick the variant that matches `audience` — one of `beginner`,
   `intermediate`, `advanced`. Default `intermediate`. Variants differ
   in jargon density and length.
3. Truncate at `max_chars` (default 400) on a sentence boundary so the
   chat card never overflows.

**Returns:**
```python
{
    "known": True,
    "concept": "articulation_root",
    "audience": "beginner",
    "snippet": "An articulation root marks the top of a chain of linked joints...",
    "see_also": ["rigid_body_api", "physics_scene"],
    "truncated": False,
}
```

**Why DATA:** teaching snippets are the same kind of read-only fact as
`lookup_product_spec`. Keeping them as a DATA handler means the LLM can
decide when to surface the snippet verbatim versus paraphrase it.

### 5-A.4 `generate_uncertainty_report_script(session_id, output_path)`

**Type:** CODE_GEN handler (returns a Python script).

**Output:** A standalone script that, when executed, reads the session
audit log (`workspace/telemetry/audit.jsonl`), aggregates the confidence
scores recorded by `assess_answer_confidence`, and writes a Markdown
report to `output_path`. The report lists:

- Total claims made in the session
- Breakdown by confidence band
- Top 5 low-confidence claims with source lists
- Suggested rewrites for each low-confidence claim

The script must `print(f"Wrote uncertainty report to {path}")` on
success so the `tool_result` loop can confirm.

**Why CODE_GEN:** the report has to land on disk so the user can review
it outside the chat, diff across sessions, and feed it into the Phase 5
fine-tune flywheel (5.1). The Kit process should never own this file.

### 5-A.5 `generate_pedagogy_card_script(concept, output_path, include_code_example)`

**Type:** CODE_GEN handler (returns a Python script).

**Output:** A runnable script that writes a self-contained Markdown
"teaching card" for a concept — section for what/why/example/when-wrong
— at `output_path`. If `include_code_example` is true, the script pulls
a matching snippet from the code-pattern store (soft import; missing
store → script writes a stub that says "no example available"). The
script prints the destination path on success.

**Why CODE_GEN:** cards are artifacts the user keeps, shares, and commits
alongside their scene. Having the tool output a script (not a finished
file) lets the approval engine inspect what will be written before any
disk I/O happens.

---

## Code patterns

- `explain_tool_choice` uses a module-level constant `_TOOL_CATEGORIES`
  with the canonical category for every registered tool. Unknown tools
  fall through to a safe `{"known": False}` branch.
- `assess_answer_confidence` scoring lives in a module-level
  `_SOURCE_WEIGHTS` dict so the logic is transparent. Missing source
  entries use the `llm_prior` weight.
- `generate_teaching_snippet` pulls from a module-level
  `_TEACHING_GLOSSARY` dict keyed by concept → dict of audience →
  string. Adding a new concept is a pure data change, no code change.
- `generate_uncertainty_report_script` and `generate_pedagogy_card_script`
  follow the existing code-gen pattern (`_gen_*` returning `str` of
  Python source). Use `repr()` for user-supplied paths / strings to
  avoid injection.
- Register under `DATA_HANDLERS` / `CODE_GEN_HANDLERS` at the end of
  `tool_executor.py`, mirroring the Phase 7A and 7G addendum layout.

---

## Schemas (tool_schemas.py)

Five entries appended to `ISAAC_SIM_TOOLS`, under a header comment:

```python
# ─── Phase 5 Addendum: Pedagogy & Uncertainty ────────────────────────────
```

All five are `type: function` entries with required-args enforcement.

---

## Test Strategy

| Test                                                              | Level | What                                                      |
|-------------------------------------------------------------------|-------|-----------------------------------------------------------|
| `explain_tool_choice` — known tool                                | L0    | Returns `known=True`, category matches table              |
| `explain_tool_choice` — unknown tool                              | L0    | Returns `known=False` with suggestions                    |
| `explain_tool_choice` — no signals                                | L0    | Returns rationale=[], confidence="low"                    |
| `assess_answer_confidence` — vetted spec source                   | L0    | Returns `confidence="high"`, score >= 0.9                 |
| `assess_answer_confidence` — no sources                           | L0    | Returns `confidence="guess"`, score <= 0.1                |
| `assess_answer_confidence` — hedged claim no sources              | L0    | Returns `has_hedge=True`, no warnings                     |
| `assess_answer_confidence` — unknown source kind                  | L0    | Falls back to llm_prior weight                            |
| `generate_teaching_snippet` — known concept, beginner             | L0    | Returns `known=True`, snippet non-empty                   |
| `generate_teaching_snippet` — unknown concept                     | L0    | Returns `known=False`, suggestions list present           |
| `generate_teaching_snippet` — truncation                          | L0    | max_chars=50 yields snippet <= 50 and `truncated=True`    |
| `generate_uncertainty_report_script` — compiles                   | L0    | `compile()` success + references `output_path`            |
| `generate_uncertainty_report_script` — path injection safe        | L0    | `repr()` used, path with quote doesn't break syntax       |
| `generate_pedagogy_card_script` — compiles                        | L0    | `compile()` success + references the concept name         |
| `generate_pedagogy_card_script` — include_code_example flag       | L0    | Flag appears in the generated script body                 |

All fourteen tests are L0 — no Kit, no network, no LLM call.

---

## Known Limitations

- `assess_answer_confidence` does not verify that the excerpt actually
  supports the claim. That would require a second model call; it is a
  Phase 9 concern (fine-tune flywheel).
- `generate_teaching_snippet` uses a static glossary. A future pass can
  synthesise snippets from the FTS index at runtime, but static keeps
  the behavior deterministic for now.
- `generate_uncertainty_report_script` assumes the audit log format
  from Phase 1 (`audit.jsonl`). If the format changes, bump the script.
