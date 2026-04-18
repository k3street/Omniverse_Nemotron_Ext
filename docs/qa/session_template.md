# Session Prompt Template

The launcher (`scripts/qa/build_session_prompt.py`) assembles each session's Claude Code prompt by concatenating these blocks **in this exact order**:

```
{persona}

=== Interaction Rules ===
{interaction_rules}

=== This Session's Modifiers ===
Patience: {patience} unhelpful replies before you give up on this task
Emotional baseline: {emotion}
Time pressure: {time_pressure}
Vocabulary drift: {vocabulary_drift}
Reading attention: {attention}

These describe HOW you behave this session. They do not change WHO you are.

=== Your Task ===
{task}

=== Starting Now ===
You are about to open a chat with Isaac Assist (an in-app AI assistant for NVIDIA Isaac Sim).
Write your first message to Isaac Assist. Stay in character. Do not narrate.
```

---

## Block Sources

| Block | Source file |
|---|---|
| `{persona}` | `docs/qa/personas/{persona_id}.md` (full file contents) |
| `{interaction_rules}` | `docs/qa/interaction_rules.md` (full file contents) |
| `{patience}` ... `{attention}` | Drawn by `random_modifiers()` in the launcher; see `modifiers.md` |
| `{task}` | `docs/qa/tasks/{task_id}.md` (full file contents) |

---

## Why This Order

1. **Persona first** — establishes identity. Subsequent rules are interpreted *as* this person.
2. **Interaction rules** — global behavior (no breaking the fourth wall, give-up logic, etc.).
3. **Modifiers** — session state (patience, emotion). Read AFTER core identity so the persona understands these are temporary.
4. **Task** — what they're trying to accomplish. Last so it's freshest in working memory.
5. **Starting Now** — explicit hand-off to action.

Reordering breaks behavior. Tests in `tests/test_phase12_qa.py` assert the exact section order.
