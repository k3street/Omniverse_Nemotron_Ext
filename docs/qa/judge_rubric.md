# Judge Rubric — 5-Criterion Scoring

Each completed QA session is graded by a **separate** judge agent (different model family from the system under test, ideally Claude 3.5 Sonnet given the LLM under test is Nemotron). The judge reads the full transcript, the persona, the task, and applies these five criteria.

---

## Hard Constraints

1. **Chain-of-thought BEFORE the verdict.** The judge MUST reason out loud per criterion before assigning scores. (Research shows +12-18 points of human-agreement when CoT precedes the score.)
2. **Integer scores only**, 1–5 per criterion. No fractional, no "4.5".
3. **Filter persona self-verdicts** out of the transcript before judging. Persona-side comments like "that was great" or "this is useless" are too generous / too cynical and bias the judge.
4. **One judge call per session.** Aggregate across sessions in `aggregate_results.py`, not inside the judge.

---

## Criteria

| # | Criterion | Weight | What the judge looks for |
|---|---|:---:|---|
| 1 | **Technical Accuracy** | 30% | Correct API names, real Isaac Sim 4.5+ / 5.x conventions, no version-mixing, no hallucinated functions, math/physics correct where applicable |
| 2 | **Actionability** | 25% | After reading Isaac Assist's response (under the persona's `attention` setting), does the user know exactly what to do next? Does it survive `first_sentence_only`? |
| 3 | **Persona Calibration** | 20% | Vocabulary register matches the persona (Maya's IsaacLab jargon vs. Kenji's "robot cell" / "TCP" vs. Alex's casual "just click play"). Emotional tone matches. |
| 4 | **Response Economy** | 15% | Verbosity is a failure mode, not a virtue. Long answers when short ones suffice → penalize. Brutally short when context is needed → also penalize. |
| 5 | **Hallucination Absence** | 10% | No invented APIs, parameter names, file paths, or features. Quantified: any single hallucinated symbol caps this criterion at 2. |

**Weighted total:** sum of `score_i * weight_i`, scaled to 0-100.

---

## Score Anchors (each 1–5)

### 5 — Excellent
The persona's task is solved with clear next steps, correct APIs, and tone that matches the persona. Even a skim-only persona could act on it.

### 4 — Good
Mostly correct and useful, with one small slip (verbose paragraph, minor API quibble, slightly wrong tone).

### 3 — Acceptable
Gets the persona partway there. Not actively wrong, but the persona has to do extra work or guess.

### 2 — Poor
Has at least one substantive problem (wrong API, wrong version, wrong vocabulary register, hallucination, or a wall of text the `attention` modifier guarantees the persona missed).

### 1 — Failing
Confidently wrong, hallucinated, or so misaligned with the persona that the user would close the tab.

---

## Required Output Schema (JSON)

```json
{
  "session_id": "01_maya__M-01",
  "reasoning": {
    "technical_accuracy": "...chain of thought, 2-5 sentences...",
    "actionability": "...",
    "persona_calibration": "...",
    "response_economy": "...",
    "hallucination_absence": "..."
  },
  "scores": {
    "technical_accuracy": 4,
    "actionability": 5,
    "persona_calibration": 3,
    "response_economy": 4,
    "hallucination_absence": 5
  },
  "weighted_total": 82,
  "completion": "completed | partial | abandoned",
  "missing_tools": ["tool_name_user_asked_for_that_doesnt_exist"],
  "failure_modes": ["short label, free text"],
  "notes": "one paragraph free text for the human reviewer"
}
```

`weighted_total` must be computed as: `30*tech + 25*action + 20*persona + 15*economy + 10*hall) / 5`, rounded to integer.

---

## Calibration

Before scaling the campaign past five sessions, a human reviewer reads each transcript blind and scores it independently. If human-vs-judge agreement on the weighted total is below 80% (within ±10 points), the rubric needs revision before the full campaign runs.
