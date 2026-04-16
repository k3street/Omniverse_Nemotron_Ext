# Behavioral Modifier Library

Each QA session draws one value from each dimension below at random. The same persona feels different across sessions because the modifier deck is reshuffled.

The launcher injects the chosen modifiers into the persona+task prompt; see `session_template.md` for assembly.

---

## Dimensions

| Dimension | Values | Notes |
|---|---|---|
| `patience` | `2`, `3`, `5`, `10` | Number of unhelpful Isaac Assist replies before the persona gives up on this task |
| `emotion` | `baseline`, `frustrated`, `stressed`, `excited` | Emotional baseline at session start. May drift during the session. |
| `time_pressure` | `relaxed`, `deadline_today`, `3_weeks_out`, `panic` | Affects message tone, willingness to read long answers, willingness to accept partial answers |
| `vocabulary_drift` | `consistent`, `slang_when_tired`, `swearing_when_frustrated` | How vocabulary degrades as the session lengthens or frustration rises |
| `attention` | `reads_fully`, `first_sentence_only`, `skips_to_code` | How the persona reads Isaac Assist's responses |

---

## Distributions

The default distribution is **uniform** within each dimension, sampled independently. Some persona+task combinations override this — for example, an Alex (`08_alex`) session should never roll `attention=reads_fully` because it breaks character.

**Per-persona overrides** live in `interaction_rules.md` (section "Persona-specific clamps").

---

## Why Randomize

Same Maya across 16 sessions should have the same core identity (code-first, hates OmniGraph, distrusts confident-wrong physics). But her *session-state* — frustrated by prior crashes, on a deadline, three coffees in — varies. Real users are not deterministic. If every Maya-session looks identical, the QA campaign will miss the failure modes that only appear when a user is tired or rushed.

---

## Modifier Injection Block (template)

The launcher renders this block into the prompt:

```
=== This Session's Modifiers ===
Patience: {patience} unhelpful replies before you give up on this task
Emotional baseline: {emotion}
Time pressure: {time_pressure}
Vocabulary drift: {vocabulary_drift}
Reading attention: {attention}

These describe HOW you behave this session. They do not change WHO you are.
```
