# Phase 12 — Agent-Driven QA & Usability Testing

**Status:** Not implemented  
**Depends on:** Phase 1-10 functional + MCP server active  
**Research:** `rev2/research_llm_judge.md`, `rev2/research_llm_regression_testing.md`, `rev2/research_real_questions_corpus.md`  
**Task library:** `research_reports/qa_tasks/` (243 tasks)

---

## Purpose

Two parallel goals from one infrastructure:

1. **Gap discovery (primary)** — identify what features are missing by watching simulated users get stuck. Data-driven feature prioritization.
2. **Regression prevention (secondary)** — catch when fixes break previously-working flows.

---

## Architecture: Separate Sessions per Task

One Claude Code session per (persona × task) combination. Sessions run in parallel. Transcripts saved to disk. A final judge-aggregator reads all transcripts and produces a report.

```
┌─────────────────────────────────────────────────┐
│  Campaign Launcher (Python script)              │
│  └─> For each (persona, task) in library:       │
│      launch Claude Code session                  │
└─────────────────────────────────────────────────┘
              ↓ spawns 243 parallel sessions
┌─────────────────────────────────────────────────┐
│  Session N (one per task):                      │
│    - Loads persona_spec + task_spec + modifiers │
│    - Claude Code role-plays persona             │
│    - Connects to Isaac Assist MCP server        │
│    - Uses REAL tools → real Isaac Sim           │
│    - Saves transcript + metadata to disk        │
└─────────────────────────────────────────────────┘
              ↓ 243 transcripts saved
┌─────────────────────────────────────────────────┐
│  Judge Aggregator (separate sessions):          │
│    - Reads each transcript                      │
│    - Evaluates with 5-criterion rubric          │
│    - Produces campaign report                   │
└─────────────────────────────────────────────────┘
```

---

## Why This Architecture

**Why separate sessions per task (not one master session with sub-agents):**
- Parent session context would overflow at 243 tasks
- Sessions can run in parallel
- Individual sessions can be replayed/debugged
- Failures in one session don't affect others

**Why Claude Code (not persona-agent via API + Isaac Assist via API):**
- $170 extra usage credits work for Claude Code, NOT for API calls
- Claude Code connects to Isaac Assist MCP server → gets real tool access
- All LLM costs stay within extra usage budget

**Why real MCP tools (not simulation):**
- Real tool outputs reveal real failure modes
- Claude Code orchestrator using real tools → if IT can't solve task, production LLM definitely can't → stronger gap signal

---

## File Structure

```
docs/qa/
├── personas/          # 15 persona base prompts (from persona docs)
│   ├── 01_maya.md
│   ├── 02_erik.md
│   └── ...
├── tasks/             # 243 task specs (already written in qa_tasks/)
│   ├── M-01.md
│   ├── K-05.md
│   └── ...
├── modifiers.md       # Behavioral modifier library
├── interaction_rules.md  # Common session behavior rules
├── session_template.md   # How prompts combine
└── judge_rubric.md       # 5-criterion grading rubric
```

---

## Session Prompt Assembly

Each session prompt = static base + random modifiers:

```python
def build_session_prompt(persona_name, task_name):
    persona = read(f"docs/qa/personas/{persona_name}.md")
    task = read(f"docs/qa/tasks/{task_name}.md")
    rules = read("docs/qa/interaction_rules.md")
    modifiers = random_modifiers()  # patience, emotion, time_pressure, attention
    
    return f"""
    {persona}
    
    === Interaction Rules ===
    {rules}
    
    === This Session's Modifiers ===
    Patience: {modifiers.patience}
    Emotional baseline: {modifiers.emotion}
    Time pressure: {modifiers.time_pressure}
    Reading attention: {modifiers.attention}
    
    === Your Task ===
    {task}
    
    === Starting Now ===
    Write your first message to Isaac Assist. Stay in character.
    """
```

---

## Behavioral Modifiers (randomized per session)

| Dimension | Values |
|-----------|--------|
| Patience | 2, 3, 5, or 10 attempts before give up |
| Emotion | baseline, frustrated, stressed, excited |
| Time pressure | relaxed, deadline_today, 3_weeks, panic |
| Vocabulary drift | consistent, slang_when_tired, swearing_when_frustrated |
| Attention | reads_fully, first_sentence_only, skips_to_code |

**Why randomize:** Same Maya across sessions should have same core identity, but different emotional states. Real users are not deterministic.

---

## Judge Rubric (from research)

**Separate judge agent** grades each completed session. 5 criteria, absolute 1-5 integer scoring, weighted:

| Criterion | Weight | What |
|-----------|:-----:|------|
| Technical Accuracy | 30% | Correct API names, Isaac Sim 4.5+ conventions |
| Actionability | 25% | User knows what to do next, even after first sentence only |
| Persona Calibration | 20% | Vocabulary + emotional register match persona |
| Response Economy | 15% | Verbosity is failure, not thoroughness |
| Hallucination Absence | 10% | No invented parameters/features/APIs |

**Judge constraints (from research):**
- Judge uses DIFFERENT model family than system under test (Claude 3.5 Sonnet recommended)
- Chain-of-thought BEFORE verdict (mandatory — +12-18pts human agreement)
- Integer scores only (no fractional)
- Persona-agent's own verdict filtered OUT before judge sees transcript (too generous)

**Cost:** ~$0.012 per session judged. 243 sessions = ~$3.

---

## Pilot Before Scale (Critical)

**Never run full 243-session campaign blind.** Validate methodology first:

**Step 1 — Single session pilot (cost: ~$0.10-0.50)**
- Pick one persona + task
- Run manually in Claude Code
- Review transcript: realistic? Useful gap signal?
- Iterate on persona prompt if behavior is off

**Step 2 — Mini campaign (5 sessions, cost: ~$1-2)**
- Run 5 sessions across different personas
- Human reviews all 5 transcripts
- Compare human judgment vs LLM-judge grading
- If agreement ≥80% → judge calibrated. If not → fix rubric first.

**Step 3 — Full campaign (243 sessions, cost: ~$25-50)**
- Only after Steps 1-2 succeed
- Run in parallel batches (10-20 concurrent sessions)
- Hard budget cap in launcher script

**If pilot reveals fundamental methodology flaw:** stop, fix, re-pilot. Do NOT scale a broken test.

---

## Campaign Execution

### Launcher (Python script)

```python
import subprocess, json, random
from pathlib import Path

PERSONAS = load_all_personas()  # 15
TASKS = load_all_tasks()  # 243
BUDGET_USD = 50  # hard cap

spent = 0
for persona in PERSONAS:
    for task in TASKS:
        if not task_applies_to_persona(task, persona):
            continue
        
        modifiers = randomize_modifiers()
        prompt = build_session_prompt(persona, task, modifiers)
        
        # Launch Claude Code session
        result = subprocess.run(
            ["claude", "-p", prompt, "--output-format", "json"],
            capture_output=True, timeout=1800,  # 30 min max per session
        )
        
        save_transcript(result.stdout, persona, task, modifiers)
        spent += estimate_cost(result.stdout)
        
        if spent >= BUDGET_USD:
            break
```

### Judge Aggregator (runs after campaign)

```python
transcripts = load_all_transcripts()
report = {}

for transcript in transcripts:
    verdict = run_judge_session(transcript)  # separate Claude Code session with rubric
    report[transcript.id] = verdict

# Aggregate
print(f"Campaign: {len(transcripts)} sessions")
print(f"Completion rate: {completion_rate(report):.0%}")
print(f"Top failure modes:")
for mode, count in top_failures(report):
    print(f"  {count}x: {mode}")
print(f"Tools requested but missing:")
for tool in missing_tools(report):
    print(f"  {tool}")
```

---

## What the Report Tells You

After a campaign:

```
Campaign: 243 sessions across 15 personas, 243 tasks
Completion rate: 58%
Average session length: 12 turns
Average session cost: $0.18

Top failure modes:
  47 sessions: "User couldn't phrase OmniGraph setup in their vocabulary"
  34 sessions: "Tool output too verbose, user stopped reading"
  28 sessions: "No tool exists for [specific operation]"
  22 sessions: "PhysX error not explained, user abandoned"

Tools most requested but missing:
  1. [specific tool] — requested in 18 sessions
  2. [...]

Personas with lowest completion rate:
  1. Kenji (manufacturing): 32% — vocabulary mismatch pervasive
  2. Alex (indie): 40% — gives up after 30 min
  
Regression from previous campaign:
  3 sessions that passed last time failed now (added to regression corpus)
```

This is **datadriven feature prioritization.** Build the tools that unblock the most sessions.

---

## Regression Corpus

**After each campaign:**
- All failed sessions → added to `regression_corpus/`
- All successful sessions that exercised new features → added too
- Next campaign: re-run the corpus first, compare to previous run
- If a session that passed last time now fails → regression detected

**Flakiness handling (from research):** Run each corpus session 3×, require 2/3 pass. LLM outputs are non-deterministic even at temp=0.

---

## Integration with Other Phases

- **Phase 9 (fine-tune):** 243 sessions × 15 turns = ~3600 training examples. Bootstraps fine-tune corpus before real users exist.
- **Phase 10 (autonomous workflows):** A/B test proactive mode on/off. Same tasks, measure completion rate difference.
- **Product roadmap:** Campaign report IS the feature backlog prioritization.

---

## Test Strategy

| Test | Level | What |
|------|-------|------|
| Prompt template assembly | L0 | Known inputs → correct concatenation |
| Modifier randomization | L0 | Distribution check across 1000 runs |
| Transcript parsing | L0 | Mock output → correct extraction |
| Budget cap enforcement | L0 | Hits cap → stops before next session |
| Single pilot session | Manual | Human review of one full session |
| Mini campaign | Manual | Human review of 5 sessions, judge calibration |
| Full campaign | Manual | Report review, action on findings |

---

## Known Risks

- **Claude Code extra usage** may be subject to rate limits or caps — verify before scaling
- **Agent-simulated users are approximate** — real users still surprise you
- **Judge bias** — self-preference real; mitigated by using different model family
- **Flakiness** — 3x replay to filter noise
- **MCP server stability** — 243 parallel sessions stress-test the MCP server; may need throttling

---

## Build Order

1. Write 15 persona base prompts (extract from existing persona docs)
2. Write `interaction_rules.md`, `modifiers.md`, `session_template.md`, `judge_rubric.md`
3. Implement Python launcher + judge aggregator
4. Pilot: 1 session, human review
5. Mini campaign: 5 sessions, calibration
6. Full campaign: 243 sessions, parallel execution
7. Analyze report, build highest-signal missing tools
8. Iterate: campaign → build → re-campaign
