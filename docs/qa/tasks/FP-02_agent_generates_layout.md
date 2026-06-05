# Task FP-02 [MEDIUM] — Agent generates LayoutSpec from text prompt

**Modality:** text-prompt LLM (multimodal foundation §7.3)

**Goal:** User types a free-form description in chat; LLM extracts
`LayoutSpec.intent`; canonical pipeline matches and executes the right
template. No mid-loop hallucination of prim paths.

**Starting state:**
- `MULTIMODAL_TEXT_INTENT=on` env flag
- Empty session

**Prompts to score (per persona variant):**

| ID | Prompt | Expected match |
|---|---|---|
| FP-02a | "Franka picks four cubes off a conveyor and drops them in a bin" | CP-01 (T1) |
| FP-02b | "Two Frankas on a line, first hands cubes to second, second drops in bin" | CP-02 (T1) |
| FP-02c | "Sort red and blue cubes into matching bins" | CP-03 (T1) |
| FP-02d | "Compact pick-and-place inside a 2m footprint" | CP-04 (T1) |
| FP-02e | "Cube on its side; flip upright then deliver" | CP-05 (T1) |

**Success criterion (per prompt):**
- `produce_layout_spec_from_text` yields the correct `pattern_hint`
  (`pick_place` for a-d, `reorient` for e)
- `structural_features` flags fire correctly:
  - FP-02a: `uses_conveyor_transport=True`
  - FP-02b: `n_robot_stations=2`, `uses_conveyor_transport=True`
  - FP-02c: `has_color_routing=True`
  - FP-02d: `has_bounded_footprint=True`
  - FP-02e: `has_orientation_requirement=True`
- `retrieve_with_intent_filter` returns the expected CP as top hit (T1)
- LLM extractor (when wired) MUST emit `pattern_hint` from closed enum
  (no `"custom"`)

**Failure modes to catch:**
- LLM proposes a non-enum `pattern_hint` → JSON schema constraint rejects
- LLM proposes a 7-cube count but no template fits → fallback to legacy
  retrieval; warning logged
- Anthropic 503 mid-extraction → `extract_intent_rules` deterministic fallback

**Telemetry:**
- `modality_invoked` with `modality=text`, `n_chars`
- `intent_extracted` with `modality=text`, `intent_summary={pattern_hint, ...}`
- `retrieval_completed` with `tier` set per match
- `canonical_match_resolved` action=accept/reject/refine

**Test harness:** `tests/test_fp_02_agent_layout.py` parametrized over
all five prompt variants; uses rule-based extractor (deterministic) until
LLM extractor is wired with a real client.
