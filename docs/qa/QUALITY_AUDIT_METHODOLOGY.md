# Quality audit methodology — reusable playbook

A reusable playbook for defining "is this codebase 100% clean?" as a
set of **deterministic, low-cost, repeatable checks**, with explicit
boundaries around what cannot be deterministic.

The methodology trades the vague question "is the code good?" for a
concrete data-gathering problem: list the questions that *actually*
matter, give each question a data source, and aggregate the answers.

This file is project-agnostic. The Isaac Assist instance referenced in
sibling docs (`PATH_TO_100PCT.md`, `AUDIT_VALIDATION.md`,
`post_migration_health_check.py`) is one concrete implementation.

---

## Core principle

> "Kvalitet är 100%" is **not** a measurement. It is a **proxy for a
> set of specific yes/no questions, each with its own data source**.

Drop the singular metric. Substitute a question-set.

For each candidate question, classify it on two axes:

1. **Determinism** — can a deterministic script answer the question
   without judgment? (Yes / Partial / No)
2. **Cost** — token-cost / runtime / external-API-cost to answer once
   (Cheap / Medium / Expensive / Requires-judgment)

This yields three tiers.

---

## Tier 1 — Deterministic CI gate

**Definition.** Questions with a yes/no answer, derivable purely from
static analysis (AST, regex on text, file size, import-graph reachability),
in seconds, with zero external dependencies.

**Examples (generalisable):**

| Category | Question |
|---|---|
| Deprecation hygiene | Are there zero calls to deprecated APIs (e.g. `datetime.utcnow()`)? |
| Security | Are there zero `eval`/`exec` in non-test code? Zero `shell=True`? Zero hard-coded secrets? |
| Type-safety | Does `mypy --strict` (or equivalent) pass? |
| Layer isolation | Do modules in layer A only import allowed peers? |
| Honesty contract | Do "handler"-style functions always return a structured result or raise? |
| Import hygiene | Zero circular imports? |
| Module size | All modules under N LOC (excluding generated code)? |
| Schema/handler drift | Every declared interface has a backing implementation? |
| Convention | Linter clean (ruff/eslint/golangci-lint)? |
| Docstring presence | All non-trivial functions have a docstring? |
| Pinned deps | All transitive deps pinned to exact versions? |

**Properties Tier 1 must have:**

- **Cheap.** Runs in <30 s. Zero network. Zero LLM tokens. Suitable
  for every commit / every PR hook.
- **Deterministic.** Same input → same output, byte-for-byte.
- **Pass/fail.** No "warning" level. Either the check passes or it
  fails with a list of offending locations.
- **Self-contained.** Stdlib only where possible. Reduces dependency
  drift.

**Implementation pattern.** A single script with one function per
check, each returning `List[Dict]` of hit locations. A wrapper aggregates
and emits JSON or human-readable summary. Exit code 0 if all checks
pass; 1 if any fails (under `--strict`).

```python
def check_no_deprecated_call() -> list[dict]: ...
def check_no_eval() -> list[dict]: ...
def check_handler_honesty() -> list[dict]: ...
# ...
```

---

## Tier 2 — Partial-determinism scanners

**Definition.** Questions where a heuristic can flag *candidates* but
classification requires manual review. The script's output is a sorted
list of suspects, not a binary verdict.

**Examples (generalisable):**

| Category | Question | Heuristic |
|---|---|---|
| Dead-read | Singletons read but never written | AST scan for module-level globals; trace read/write sites |
| Error-path coverage | Every handler tested under failure | For each handler, scan tests/ for both handler name AND failure-path assertions |
| Hot-path complexity | No O(n²) loops over large collections | Detect nested loops where both iterators look like collections (token match) |
| Observability | Every handler emits structured event | Scan for `telemetry.*` / `record_event(...)` calls inside handler bodies |
| Pure-function policy | Cached functions have no side effects | AST scan of `@lru_cache`-decorated functions; flag global mutations |

**Properties Tier 2 must have:**

- **Honest about uncertainty.** Output is "candidates", reviewed by
  a human. Never claim deterministic.
- **Useful even with false positives.** A scanner with 30% FP rate is
  fine if the alternative is "do nothing".
- **Re-runnable.** Run on demand or weekly, not per-commit.

**Implementation pattern.** Separate script per scanner, emits JSON.
Documentation explicitly states the heuristic's known FP/FN edges.
Acceptable target accuracy: 80%+ recall, 60%+ precision.

---

## Tier 3 — Judgment-data extractors

**Definition.** Questions that require **human judgment**, but whose
quality of judgment depends on **having the right structured data
extracted**. The script does not answer the question; it produces
the inputs that make the answer faster and cheaper.

**Examples (generalisable):**

| Judgment question | Data to extract |
|---|---|
| Is the architecture sound? | Module dependency graph, fan-in/fan-out, import cycles |
| Where should I refactor next? | Co-change hotspots (git log analysis), churn frequency |
| Which functions are too complex? | Cyclomatic complexity histogram, line-count distribution |
| Is the API stable? | Schema diff between releases, breaking-change frequency |
| Is the codebase onboarding-friendly? | Docstring coverage, file-size distribution, naming-consistency |

**Properties Tier 3 must have:**

- **Pure data, no judgment.** The script does not opine on whether
  CC=42 is too high. It just reports CC=42.
- **JSON output.** Designed to be piped to other tools or read by an
  LLM with a precise prompt ("highlight the top 5 outliers").
- **Run on demand.** Used when starting a refactor decision, not in CI.

**Implementation pattern.** One extractor per data dimension. Output
is canonical JSON: `{nodes, edges, stats, distributions, top_N}`.
Designed to be re-ingestable.

---

## The cost question: judgment ≠ free-form

Even Tier 3 (judgment) questions can be **made cheaper** by extracting
the right data upfront. Without data, judgment is expensive — you have
to re-explore the codebase every time.

> Judgment + structured data = minutes, low LLM cost.
>
> Judgment without data = hours, high LLM cost from re-exploration.

This is why Tier 3 is worth implementing **before** you face a
judgment decision. The extractors are invested once; the data is read
many times.

---

## Workflow

### 1. Define the question-set

Before writing any script:

- List every question that, if answered "yes" for all, would mean the
  codebase is in the target state.
- Be specific. "Is the code good?" → reject. "Does every handler
  return a dict with `success: bool`?" → accept.
- Brainstorm 20–40 candidate questions. Use sub-agents for parallel
  brainstorming if useful.
- Categorise each by determinism × cost. Sort into Tier 1, 2, 3.
- Drop questions that don't fit any tier — they're not measurable.

### 2. Validate the audit script before trusting its output

> A buggy audit script doesn't measure 100% — it produces noise.

For each Tier 1 check, build a **fixture pair**:
- `positive.py` — minimal code that the check MUST flag, with comments
  marking each expected hit.
- `negative.py` — minimal code that the check MUST NOT flag, with
  comments demonstrating legitimate patterns.

Write a unit test per check that:
- Runs the check against the positive fixture; asserts expected hits.
- Runs the check against the negative fixture; asserts zero hits.

Commit fixtures and check together. The fixture-pair is the
specification of what the check means.

### 3. Common false-positive sources

- **Scope isolation.** When walking an AST inside a function, beware:
  `ast.walk(func)` descends into **nested** function definitions and
  lambdas. A `return None` inside an inner helper is **not** a return
  of the outer function. Stop the walk at nested scope boundaries.
- **Aliased imports.** Static checks for `datetime.utcnow()` miss
  `from datetime import datetime as dt; dt.utcnow()`. Document this
  limitation; accept it or use AST-with-import-resolver.
- **String content vs code.** Grep-based checks falsely match
  docstring examples or log messages. AST-based checks are safer.
- **Convention variants.** A "binding pattern" may have 3 different
  forms in the same codebase (`data[X] = handler`, `handlers[X] = h`,
  `@router.post(X)`). Enumerate them or you'll produce false-positive
  orphans.

### 4. Run, baseline, iterate

Once the script is validated:

1. Run it. Record the baseline (e.g. "8 fails today").
2. For each fail: classify as **genuine** (project bug to fix) or
   **false positive** (heuristic bug to fix).
3. False positives → fix the script first. Add a fixture demonstrating
   the case so it never regresses.
4. Genuine fails → schedule a fix-pass.

### 5. Ratchet, not absolute

Some checks can't reach 0 (`# noqa: audit-Q9` for one legitimate
`exec()` call). Use **ratchets**:

- Record today's count (e.g. "1 eval/exec call").
- Check fails if count > today's baseline.
- New code can't add new violations; existing tolerated cases stay
  documented in `# noqa` comments.

Baselines are themselves data — re-record them in commit messages
or a separate file so the trend is visible.

---

## Artefact map

A complete audit setup produces these artefacts (names indicative):

```
scripts/qa/
├── post_migration_health_check.py      # Tier 1 gate (all checks)
├── tier2_dead_read_scan.py             # Tier 2 scanner
├── tier2_error_path_coverage.py        # Tier 2 scanner
├── tier2_quadratic_loops.py            # Tier 2 scanner
├── tier2_telemetry_coverage.py         # Tier 2 scanner
├── tier3_dependency_graph.py           # Tier 3 extractor
├── tier3_cochange_hotspots.py          # Tier 3 extractor
└── tier3_complexity_histogram.py       # Tier 3 extractor

tests/qa_audit_fixtures/
├── section_19_positive.py              # MUST flag
├── section_19_negative.py              # MUST NOT flag
├── deprecation_positive.py
├── deprecation_negative.py
└── ...                                  # one pair per check

tests/test_audit_script_correctness.py  # validates each check against fixtures

docs/qa/
├── PATH_TO_100PCT.md                    # checklist plan to drive baseline → clean
├── AUDIT_VALIDATION.md                  # per-check heuristic/FP/FN/baseline doc
└── QUALITY_AUDIT_METHODOLOGY.md         # this file
```

---

## Anti-patterns to avoid

1. **Chasing failures from an unvalidated script.** You'll spend hours
   fixing "bugs" that don't exist. Validate the script first.
2. **One mega-script.** Hard to test, hard to debug, hard to extend.
   One script per scanner with shared utilities.
3. **Judgment hidden inside a check.** If a check uses thresholds or
   heuristics that are subjective, it belongs in Tier 2, not Tier 1.
   Be explicit.
4. **CI gating on Tier 2/3.** Heuristic checks shouldn't block merges.
   They inform decisions.
5. **Skipping fixtures for "obvious" checks.** Even trivial regex
   checks have edge cases. The fixture costs 20 minutes and prevents
   weeks of confusion later.
6. **Hard-coding "should be zero" without `# noqa` support.** Real
   projects have legitimate exceptions. Build the suppression mechanism
   early or you'll fight your own tool.
7. **Treating Tier 3 data as conclusions.** "CC=312 is bad" is judgment.
   "CC=312 in `handle_message`" is data. Don't confuse the two.

---

## Cost discipline

Per-check cost should be telemetered:

- Tier 1 script: total runtime in CI. Target: <30 s.
- Tier 2 scanners: runtime per scan + memory peak.
- Tier 3 extractors: runtime + output size (JSON bytes).
- LLM-augmented smoke tests (if any): tokens-per-run, $-per-run.
  Default to mocked. Live runs are opt-in with hard budget caps.

If a check creeps above its cost target, refactor or move it to a
lower tier. CI gates are budget-sensitive.

---

## Summary

1. Trade "is it good?" for an explicit question-set.
2. Sort questions into Tier 1 (deterministic gate), Tier 2 (heuristic
   scanners), Tier 3 (judgment-data extractors).
3. **Validate the audit script before trusting its output** via
   positive/negative fixture pairs and unit tests per check.
4. Genuine fails → project fix. False positives → script fix +
   fixture. Track baselines as ratchets.
5. Cost-budget every check. CI must stay cheap; Tier 3 can be
   expensive but on-demand.

The audit infrastructure is a first-class part of the codebase, not
a one-off cleanup. Treat it accordingly.
